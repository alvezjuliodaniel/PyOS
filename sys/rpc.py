"""
PyOS - Enrutador JSON-RPC para WebSocket
Protocolo:
  Req: {"id": 1, "app": "gpio", "cmd": "set", "args": {"pin": 0, "val": 1}}
  Res: {"id": 1, "ok": true, "data": ...} | {"id": 1, "ok": false, "error": "..."}
"""

import gc
import io
import os
import sys

import hal
import fs
import kernel
import net


def _url_decode(s):
    """Decodificador ligero de URL (%XX y '+') sin dependencias externas."""
    if not s or ("%" not in s and "+" not in s):
        return s
    s = s.replace("+", " ")
    parts = s.split("%")
    out = [parts[0]]
    for p in parts[1:]:
        if len(p) >= 2:
            try:
                out.append(chr(int(p[:2], 16)) + p[2:])
                continue
            except ValueError:
                pass
        out.append("%" + p)
    return "".join(out)


class RPCRouter:
    """Despachador modular de comandos JSON-RPC del Kernel y apps nativas."""

    def __init__(self, kern=None, net_mgr=None, mqtt_mgr=None):
        self.kernel = kern if kern is not None else kernel.get_kernel()
        self.hal = self.kernel.hal
        self.net = net_mgr if net_mgr is not None else net.get_net()
        self.mqtt = mqtt_mgr

        # Contexto persistente para la terminal Python web
        self._term_env = {
            "hal": self.hal,
            "fs": fs,
            "kernel": self.kernel,
            "net": self.net,
            "gc": gc,
            "os": os,
            "sys": sys,
        }

        # Apps registradas (extensible por plugins en /apps)
        self._apps = {
            "sys": self._handle_sys,
            "gpio": self._handle_gpio,
            "fs": self._handle_fs,
            "net": self._handle_net,
            "term": self._handle_term,
            "mqtt": self._handle_mqtt,
        }

    def register_app(self, app_name, handler):
        """Permite registrar manejadores RPC adicionales (ej. apps de usuario)."""
        self._apps[app_name] = handler

    def unregister_app(self, app_name):
        """Desregistra un manejador RPC dinamico."""
        if app_name not in ("sys", "gpio", "fs", "net", "term", "apps"):
            return self._apps.pop(app_name, None) is not None
        return False

    def dispatch(self, req, role="admin"):
        """
        Ejecuta una peticion JSON-RPC (dict) validando rol/capacidades (SEC-03).
        """
        req_id = req.get("id")
        app = req.get("app")
        cmd = req.get("cmd")
        args = req.get("args") or {}

        if not app or not cmd:
            return {"id": req_id, "ok": False, "error": "Faltan campos 'app' o 'cmd'"}

        # Autorizacion por rol minimo
        if role != "admin":
            if app == "term":
                return {"id": req_id, "ok": False, "error": "Permiso denegado: requiere rol admin"}
            if app == "apps" and cmd != "list":
                return {"id": req_id, "ok": False, "error": "Permiso denegado: requiere rol admin"}
            if app == "sys" and cmd in ("reboot", "task_restart"):
                return {"id": req_id, "ok": False, "error": "Permiso denegado: requiere rol admin"}
            if app == "net" and cmd == "connect":
                return {"id": req_id, "ok": False, "error": "Permiso denegado: requiere rol admin"}
            if app == "fs" and cmd in ("write", "mkdir", "rm", "mv", "cp"):
                return {"id": req_id, "ok": False, "error": "Permiso denegado: requiere rol admin"}
            if app == "mqtt" and cmd in ("configure", "connect", "disconnect"):
                return {"id": req_id, "ok": False, "error": "Permiso denegado: requiere rol admin"}

        if app == "term" and not getattr(self.kernel, "allow_remote_python", True):
            return {"id": req_id, "ok": False, "error": "Ejecucion remota Python deshabilitada en system.json"}

        handler = self._apps.get(app)
        if handler is None:
            return {"id": req_id, "ok": False, "error": "App desconocida: {}".format(app)}

        try:
            data = handler(cmd, args)
            return {"id": req_id, "ok": True, "data": data}
        except Exception as e:
            return {
                "id": req_id,
                "ok": False,
                "error": "{}: {}".format(type(e).__name__, e),
            }

    async def dispatch_async(self, req, role="admin"):
        """Despachador compatible con manejadores sync y async (ej. mqtt QoS 1)."""
        res = self.dispatch(req, role=role)
        if res.get("ok") and hasattr(res.get("data"), "send"):
            try:
                res["data"] = await res["data"]
            except Exception as e:
                return {
                    "id": req.get("id"),
                    "ok": False,
                    "error": "{}: {}".format(type(e).__name__, e),
                }
        return res

    # ------------------------------------------------------------------
    # App: sys (Sistema, Telemetria, Tareas, Logs)
    # ------------------------------------------------------------------

    def _handle_sys(self, cmd, args):
        if cmd == "info":
            info = self.hal.get_board_info()
            info["os_name"] = self.kernel.name
            info["os_version"] = self.kernel.version
            info["hostname"] = self.kernel.hostname
            return info
        if cmd == "stats":
            tel = self.hal.get_telemetry()
            tel["cpu"]["load_pct"] = self.kernel.cpu_load_pct
            tel["cpu"]["tasks_running"] = self.kernel.count_running_tasks()
            tel["net"] = self.net.get_status()
            return tel
        if cmd == "tasks":
            return self.kernel.list_tasks()
        if cmd == "task_restart":
            name = args.get("name")
            return {"name": name, "restarted": self.kernel.restart_task(name)}
        if cmd == "logs":
            limit = args.get("limit", 32)
            return self.kernel.get_logs(limit=limit)
        if cmd == "gc":
            gc.collect()
            return self.hal.get_memory_stats()
        if cmd == "reboot":
            import machine
            try:
                import uasyncio as asyncio
            except ImportError:
                import asyncio

            async def _delayed_reset():
                if hasattr(asyncio, "sleep_ms"):
                    await asyncio.sleep_ms(400)
                else:
                    await asyncio.sleep(0.4)
                machine.reset()

            asyncio.create_task(_delayed_reset())
            return {"rebooting": True}
        raise ValueError("Comando sys desconocido: {}".format(cmd))

    # ------------------------------------------------------------------
    # App: gpio (Control seguro de pines, ADC y PWM via HAL)
    # ------------------------------------------------------------------

    def _handle_gpio(self, cmd, args):
        if cmd == "info":
            b = self.hal.get_board_info()
            return {
                "board": b["name"],
                "chip_id": b["chip_id"],
                "pins": b["pins"],
                "pwm_max_channels": b["pwm_max_channels"],
                "states": self.hal.gpio_states(),
            }
        if cmd == "set":
            pin = int(args["pin"])
            val = 1 if args.get("val") else 0
            written = self.hal.gpio_write(pin, val)
            return {"pin": pin, "val": written}
        if cmd == "get":
            pin = int(args["pin"])
            return {"pin": pin, "val": self.hal.gpio_read(pin)}
        if cmd == "toggle":
            pin = int(args["pin"])
            return {"pin": pin, "val": self.hal.gpio_toggle(pin)}
        if cmd == "config":
            pin = int(args["pin"])
            mode = args.get("mode", "in")
            pull = args.get("pull")
            val = args.get("val")
            return self.hal.gpio_config(pin, mode=mode, pull=pull, value=val)
        if cmd == "adc":
            pin = int(args["pin"])
            return self.hal.adc_read(pin)
        if cmd == "pwm":
            pin = int(args["pin"])
            freq = int(args.get("freq", 1000))
            duty = float(args.get("duty", 50.0))
            return self.hal.pwm_set(pin, freq=freq, duty_pct=duty)
        if cmd == "pwm_stop":
            pin = int(args["pin"])
            return {"pin": pin, "stopped": self.hal.pwm_stop(pin)}
        if cmd == "release":
            pin = int(args["pin"])
            return {"pin": pin, "released": self.hal.gpio_release(pin)}
        raise ValueError("Comando gpio desconocido: {}".format(cmd))

    # ------------------------------------------------------------------
    # App: fs (Explorador de archivos LittleFS)
    # ------------------------------------------------------------------

    def _handle_fs(self, cmd, args):
        if cmd == "ls":
            path = args.get("path", "/")
            norm = fs.safe_path(path, "/")
            return {"path": norm, "entries": fs.list_dir(norm, "/")}
        if cmd == "read":
            path = args["path"]
            max_b = int(args.get("max_bytes", 8192))
            content = fs.read_text(path, max_bytes=max_b, base="/")
            return {"path": fs.safe_path(path, "/"), "content": content}
        if cmd == "write":
            path = args["path"]
            content = args.get("content", "")
            n = fs.write_text(path, content, base="/")
            return {"path": fs.safe_path(path, "/"), "written": n}
        if cmd == "mkdir":
            path = fs.mkdir(args["path"], base="/")
            return {"path": path}
        if cmd == "rm":
            path = args["path"]
            rec = bool(args.get("recursive", False))
            fs.remove(path, base="/", recursive=rec)
            return {"path": path, "deleted": True}
        if cmd == "mv":
            dst = fs.rename(args["src"], args["dst"], base="/")
            return {"src": args["src"], "dst": dst}
        if cmd == "cp":
            n = fs.copy_file(args["src"], args["dst"], base="/")
            return {"src": args["src"], "dst": args["dst"], "copied": n}
        raise ValueError("Comando fs desconocido: {}".format(cmd))

    # ------------------------------------------------------------------
    # App: net (Estado Wi-Fi, Escaneo y Credenciales)
    # ------------------------------------------------------------------

    def _handle_net(self, cmd, args):
        if cmd == "status":
            return self.net.get_status()
        if cmd == "scan":
            return self.net.scan()
        if cmd == "connect":
            ssid = (args.get("ssid") or "").strip()
            pwd = args.get("password") or ""
            if not ssid:
                raise ValueError("SSID vacio")
            self.net.save_credentials(ssid, pwd)
            return {"ssid": ssid, "status": "reconnecting"}
        raise ValueError("Comando net desconocido: {}".format(cmd))

    # ------------------------------------------------------------------
    # App: term (Consola Python interactiva controlada)
    # ------------------------------------------------------------------

    def _handle_term(self, cmd, args):
        if cmd == "run_file":
            raw_path = (args.get("path") or "").strip()
            if not raw_path:
                raise ValueError("Falta ruta de script .py")
            full_path = fs.safe_path(raw_path, "/")
            code = fs.read_text(full_path, max_bytes=16384, base="/")
            argv = args.get("argv") or []

            out_lines = []

            def _capture_print(*p_args, **p_kwargs):
                sep = p_kwargs.get("sep", " ")
                out_lines.append(sep.join(str(a) for a in p_args))

            argv_list = [full_path] + [str(a) for a in argv]
            env = {
                "__name__": "__main__",
                "__file__": full_path,
                "argv": argv_list,
                "print": _capture_print,
                "hal": self.hal,
                "fs": fs,
                "kernel": self.kernel,
                "net": self.net,
                "gc": gc,
                "os": os,
                "sys": sys,
            }
            try:
                exec(code, env, env)
            finally:
                env.clear()
                gc.collect()

            return {"path": full_path, "output": "\n".join(out_lines)}

        if cmd != "exec":
            raise ValueError("Comando term desconocido: {}".format(cmd))
        code = (args.get("code") or "").strip()
        if not code:
            return {"output": "", "result": None}

        # Capturar salida print() en MicroPython usando os.dupterm o reemplazo local
        out_lines = []

        def _capture_print(*p_args, **p_kwargs):
            sep = p_kwargs.get("sep", " ")
            out_lines.append(sep.join(str(a) for a in p_args))

        self._term_env["print"] = _capture_print

        result = None
        try:
            # Primero intentar evaluar como expresion (ej. "hal.get_temperature_c()")
            try:
                val = eval(code, self._term_env, self._term_env)
                if val is not None:
                    result = repr(val)
            except SyntaxError:
                # Si es sentencia (asignacion, bucle, import, etc.), usar exec
                exec(code, self._term_env, self._term_env)
        finally:
            self._term_env.pop("print", None)
            gc.collect()

        return {"output": "\n".join(out_lines), "result": result}

    # ------------------------------------------------------------------
    # App: mqtt (Cliente MQTT sobre TLS y puente AOS IoT - Fase 10)
    # ------------------------------------------------------------------

    def _handle_mqtt(self, cmd, args):
        if self.mqtt is None:
            import mqtt
            self.mqtt = mqtt.get_mqtt(kern=self.kernel, net_mgr=self.net)

        if cmd == "status":
            return self.mqtt.get_status()
        if cmd == "stats":
            return self.mqtt.stats
        if cmd == "messages":
            return self.mqtt.get_messages(limit=int(args.get("limit", 30)))
        if cmd == "configure":
            return self.mqtt.configure(args)
        if cmd == "connect":
            return self.mqtt.request_connect()
        if cmd == "disconnect":
            return self.mqtt.request_disconnect()
        if cmd == "publish":
            return self.mqtt.publish(
                args.get("topic", ""),
                args.get("payload", ""),
                qos=int(args.get("qos", 0)),
                retain=bool(args.get("retain", False)),
            )
        if cmd == "subscribe":
            return self.mqtt.subscribe(
                args.get("topic", ""),
                qos=int(args.get("qos", 0)),
                persist=bool(args.get("persist", True)),
            )
        if cmd == "unsubscribe":
            return self.mqtt.unsubscribe(
                args.get("topic", ""),
                persist=bool(args.get("persist", True)),
            )
        raise ValueError("Comando mqtt desconocido: {}".format(cmd))
