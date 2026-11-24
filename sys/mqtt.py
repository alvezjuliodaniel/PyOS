"""
MicroOS - Cliente MQTT 3.1.1 Asincrono sobre TLS Estricto (Fase 10)
- Transporte obligatorio: TCP + TLS (ssl.CERT_REQUIRED + CA + SNI/hostname).
- Prohibido CERT_NONE o fallback sin cifrado.
- Cooperativo sobre uasyncio (no bloquea HTTP ni WebSocket).
- QoS 0 y QoS 1 (con packet_id, PUBACK y reintentos DUP).
- Integracion nativa AOS IoT (telemetria, estado LWT y comandos en whitelist segura).
"""

import binascii
import gc
import machine
import socket
import ssl
import struct
import time

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    import ujson as json
except ImportError:
    import json

try:
    import ntptime
except ImportError:
    ntptime = None

import fs
import kernel
import net

_MQTT_CFG_PATH = "/config/mqtt.json"
_MQTT_SEC_PATH = "/config/secrets/mqtt.secret"
_DEFAULT_CA_BUNDLE = "/config/ca_bundle.pem"
_MAX_TOPIC_LEN = 128
_MAX_PAYLOAD_LEN = 4096
_MAX_HISTORY = 30
_MIN_TLS_FREE_RAM = 65536  # Minimo seguro antes de iniciar handshake mbedTLS
_HAS_SLEEP_MS = hasattr(asyncio, "sleep_ms")


def _sleep_ms(ms):
    if _HAS_SLEEP_MS:
        return asyncio.sleep_ms(ms)
    return asyncio.sleep(ms / 1000.0)


def _encode_rem_len(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _encode_str(s):
    b = s.encode("utf-8") if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


def _default_client_id():
    uid = binascii.hexlify(machine.unique_id()).decode("ascii")[-8:]
    return "microos-" + uid


def _valid_topic(topic, allow_wildcards=False):
    if not isinstance(topic, str) or not (1 <= len(topic) <= _MAX_TOPIC_LEN):
        return False
    if "\x00" in topic:
        return False
    if not allow_wildcards and ("#" in topic or "+" in topic):
        return False
    return True


class MQTTTLSManager:
    """
    Cliente MQTT 3.1.1 sobre TLS verificado, administrado por el Kernel uasyncio.
    """

    def __init__(self, kern=None, net_mgr=None):
        self.kernel = kern if kern is not None else kernel.get_kernel()
        self.hal = self.kernel.hal
        self.net = net_mgr if net_mgr is not None else net.get_net()

        self._cfg = {}
        self._has_password = False
        self._has_ca = False
        self._has_client_cert = False

        # Estado runtime
        self.state = "disconnected"  # disconnected | connecting | connected | error
        self.last_error = None
        self._want_connected = False
        self._sock = None
        self._raw_sock = None
        self._connected_at_ms = 0
        self._last_tx_ms = 0
        self._last_rx_ms = 0
        self._ping_sent_ms = 0
        self._latency_ms = None
        self._pid = 1
        self._pending_puback = {}  # pid -> asyncio.Event

        # Metricas
        self.stats = {
            "reconnects": 0,
            "sent": 0,
            "received": 0,
            "tls_errors": 0,
            "ram_before_tls": 0,
            "ram_after_tls": 0,
            "tls_ram_delta": 0,
        }
        self._history = []  # Ultimos _MAX_HISTORY mensajes en RAM (nunca en Flash)
        self._active_subs = set()

        self._load_config()
        if self._cfg.get("autostart") and self._cfg.get("host"):
            self._want_connected = True

    # ------------------------------------------------------------------
    # Configuracion (no secreta en mqtt.json, secretos en mqtt.secret)
    # ------------------------------------------------------------------

    def _load_config(self):
        defaults = {
            "host": "",
            "port": 8883,
            "client_id": _default_client_id(),
            "username": "",
            "keepalive": 60,
            "autostart": False,
            "reconnect": True,
            "aos_enabled": True,
            "aos_base_topic": "aos/devices",
            "aos_telemetry_s": 30,
            "subscriptions": [],
        }
        loaded = fs.read_json(_MQTT_CFG_PATH, default=None, internal=True)
        if isinstance(loaded, dict):
            self._cfg = dict(defaults, **loaded)
        else:
            self._cfg = defaults
        if not self._cfg.get("client_id"):
            self._cfg["client_id"] = _default_client_id()

        sec = fs.read_json(_MQTT_SEC_PATH, default=None, internal=True) or {}
        self._has_password = bool(sec.get("password"))
        self._has_ca = bool(sec.get("ca_pem"))
        self._has_client_cert = bool(sec.get("client_cert") and sec.get("client_key"))

    def _load_secrets(self):
        return fs.read_json(_MQTT_SEC_PATH, default=None, internal=True) or {}

    def configure(self, args):
        """Actualiza configuracion y/o secretos (nunca devuelve secretos)."""
        for key in ("host", "client_id", "username", "aos_base_topic"):
            if key in args and args[key] is not None:
                self._cfg[key] = str(args[key]).strip().rstrip("/") if key == "aos_base_topic" else str(args[key]).strip()
        if "port" in args and args["port"] is not None:
            port = int(args["port"])
            if not (1 <= port <= 65535):
                raise ValueError("Puerto invalido")
            self._cfg["port"] = port
        if "keepalive" in args and args["keepalive"] is not None:
            ka = int(args["keepalive"])
            self._cfg["keepalive"] = max(15, min(ka, 600))
        for bkey in ("autostart", "reconnect", "aos_enabled"):
            if bkey in args and args[bkey] is not None:
                self._cfg[bkey] = bool(args[bkey])
        if "aos_telemetry_s" in args and args["aos_telemetry_s"] is not None:
            self._cfg["aos_telemetry_s"] = max(5, min(int(args["aos_telemetry_s"]), 3600))
        if not self._cfg.get("client_id"):
            self._cfg["client_id"] = _default_client_id()

        fs.write_json(_MQTT_CFG_PATH, self._cfg, "/", internal=True)

        # Actualizar secretos solo si fueron enviados (nunca loguearlos)
        sec_dirty = False
        sec = self._load_secrets()
        if "password" in args and args["password"] not in (None, ""):
            sec["password"] = str(args["password"])
            sec_dirty = True
        if "ca_pem" in args and args["ca_pem"] is not None:
            ca = str(args["ca_pem"]).strip()
            if ca and "BEGIN CERTIFICATE" not in ca:
                raise ValueError("Certificado CA PEM invalido")
            sec["ca_pem"] = ca
            sec_dirty = True
        if "client_cert" in args and "client_key" in args:
            sec["client_cert"] = str(args["client_cert"] or "").strip()
            sec["client_key"] = str(args["client_key"] or "").strip()
            sec_dirty = True

        if sec_dirty:
            fs.mkdir("/config/secrets", "/", parents=True)
            fs.write_json(_MQTT_SEC_PATH, sec, "/", internal=True)
            self._has_password = bool(sec.get("password"))
            self._has_ca = bool(sec.get("ca_pem"))
            self._has_client_cert = bool(sec.get("client_cert") and sec.get("client_key"))

        del sec
        gc.collect()
        self.kernel.log("INFO", "MQTT", "Configuracion MQTT actualizada (host={}:{})".format(
            self._cfg["host"], self._cfg["port"]
        ))
        self.kernel.emit("mqtt_status", self.get_status())
        return self.get_status()

    def get_status(self):
        """Retorna estado completo SIN exponer contrasenas ni claves privadas."""
        uptime_s = 0
        if self.state == "connected" and self._connected_at_ms:
            uptime_s = time.ticks_diff(time.ticks_ms(), self._connected_at_ms) // 1000
        return {
            "state": self.state,
            "connected": self.state == "connected",
            "tls_verified": self.state == "connected",
            "host": self._cfg.get("host", ""),
            "port": self._cfg.get("port", 8883),
            "client_id": self._cfg.get("client_id", ""),
            "username": self._cfg.get("username", ""),
            "keepalive": self._cfg.get("keepalive", 60),
            "autostart": self._cfg.get("autostart", False),
            "reconnect": self._cfg.get("reconnect", True),
            "aos_enabled": self._cfg.get("aos_enabled", True),
            "aos_base_topic": self._cfg.get("aos_base_topic", "aos/devices"),
            "aos_telemetry_s": self._cfg.get("aos_telemetry_s", 30),
            "has_password": self._has_password,
            "has_ca": self._has_ca or fs.exists(_DEFAULT_CA_BUNDLE, "/"),
            "ca_source": "custom" if self._has_ca else ("builtin" if fs.exists(_DEFAULT_CA_BUNDLE, "/") else "none"),
            "has_client_cert": self._has_client_cert,
            "subscriptions": sorted(list(self._active_subs or set(self._cfg.get("subscriptions", [])))),
            "connected_s": uptime_s,
            "latency_ms": self._latency_ms,
            "last_error": self.last_error,
            "profile": self.hal.limits.get("profile", "strict"),
            "stats": dict(self.stats),
        }

    def get_messages(self, limit=30):
        return self._history[-limit:]

    # ------------------------------------------------------------------
    # Control de Conexion (TLS estricto + NTP + Verificacion de RAM)
    # ------------------------------------------------------------------

    def request_connect(self):
        if not self._cfg.get("host"):
            raise ValueError("Configura el host del broker MQTT primero")
        if not self._has_ca and not fs.exists(_DEFAULT_CA_BUNDLE, "/"):
            raise ValueError("No se encontro CA personalizada ni bundle /config/ca_bundle.pem")
        self._want_connected = True
        self.last_error = None
        return self.get_status()

    def request_disconnect(self):
        self._want_connected = False
        self._close_socket(send_disconnect=True)
        self.state = "disconnected"
        self.kernel.emit("mqtt_status", self.get_status())
        return self.get_status()

    def _sync_ntp_if_needed(self):
        """Sincroniza el reloj RTC via NTP si el ano < 2025 para validar vigencia X.509."""
        yr = time.localtime()[0]
        if yr >= 2025:
            return True
        if ntptime is None:
            return False
        try:
            ntptime.settime()
            self.kernel.log("INFO", "MQTT", "Reloj sincronizado por NTP (ano={})".format(time.localtime()[0]))
            return time.localtime()[0] >= 2025
        except Exception as e:
            self.kernel.log("WARN", "MQTT", "No se pudo sincronizar NTP: {}".format(e))
            return False

    def _build_ssl_context(self, sec):
        """Construye SSLContext con CERT_REQUIRED usando CA personalizada o bundle integrado."""
        ca_pem = (sec.get("ca_pem") or "").strip()
        if not ca_pem and fs.exists(_DEFAULT_CA_BUNDLE, "/"):
            ca_pem = fs.read_text(_DEFAULT_CA_BUNDLE, max_bytes=16384, base="/", internal=True).strip()
        if not ca_pem:
            raise ValueError("Falta certificado CA confiable; TLS sin verificacion prohibido")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=ca_pem)
        if sec.get("client_cert") and sec.get("client_key"):
            ctx.load_cert_chain(sec["client_cert"], sec["client_key"])
        return ctx

    def _aos_topic(self, suffix):
        base = (self._cfg.get("aos_base_topic") or "aos/devices").strip("/")
        cid = self._cfg.get("client_id") or _default_client_id()
        return "{}/{}/{}".format(base, cid, suffix)

    def _set_blocking(self, flag, timeout=None):
        """En MicroPython, setblocking/settimeout se aplican al socket TCP subyacente."""
        rs = self._raw_sock
        if rs is None:
            return
        try:
            if timeout is not None:
                rs.settimeout(timeout)
            else:
                rs.setblocking(flag)
        except Exception:
            pass

    def _next_pid(self):
        pid = self._pid
        self._pid = 1 if self._pid >= 65535 else self._pid + 1
        return pid

    def _sock_write_all(self, data):
        if self._sock is None:
            raise OSError("Socket MQTT cerrado")
        mv = memoryview(data)
        sent = 0
        total = len(data)
        while sent < total:
            n = self._sock.write(mv[sent:])
            if n is None or n == 0:
                raise OSError("Escritura MQTT interrumpida")
            sent += n
        self._last_tx_ms = time.ticks_ms()

    def _sock_read_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.read(n - len(buf))
            if not chunk:
                raise OSError("Conexion MQTT cerrada por el broker")
            buf.extend(chunk)
        self._last_rx_ms = time.ticks_ms()
        return bytes(buf)

    async def _connect_once(self):
        host = self._cfg.get("host", "").strip()
        port = int(self._cfg.get("port", 8883))
        if not host:
            return False

        # Proteccion de memoria en perfil strict antes de mbedTLS
        gc.collect()
        ram_before = gc.mem_free()
        self.stats["ram_before_tls"] = ram_before
        if ram_before < _MIN_TLS_FREE_RAM:
            msg = "RAM insuficiente para TLS verificado ({} B < {} B)".format(
                ram_before, _MIN_TLS_FREE_RAM
            )
            self.last_error = msg
            self.state = "error"
            self.kernel.log("ERR", "MQTT", msg)
            self.kernel.emit("mqtt_error", {"error": msg})
            return False

        if self.net.mode != "sta" or not self.net.sta.isconnected():
            self.last_error = "Wi-Fi STA no conectado"
            self.state = "disconnected"
            return False

        self.state = "connecting"
        self.kernel.emit("mqtt_status", self.get_status())

        # Sincronizar reloj para validar expiracion de certificados X.509
        if not self._sync_ntp_if_needed():
            self.last_error = "Reloj RTC sin sincronizar (NTP requerido para validar TLS)"
            self.stats["tls_errors"] += 1
            self.state = "error"
            self.kernel.log("ERR", "MQTT", self.last_error)
            self.kernel.emit("mqtt_error", {"error": self.last_error})
            return False

        sec = self._load_secrets()
        raw_sock = None
        try:
            ctx = self._build_ssl_context(sec)
            ai = socket.getaddrinfo(host, port)[0]
            raw_sock = socket.socket(ai[0], ai[1], ai[2])
            raw_sock.settimeout(8.0)
            raw_sock.connect(ai[-1])

            # Handshake TLS estricto con SNI y validacion de hostname
            tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
            self._raw_sock = raw_sock
            self._sock = tls_sock
            self._set_blocking(True, timeout=5.0)

            gc.collect()
            ram_after = gc.mem_free()
            self.stats["ram_after_tls"] = ram_after
            self.stats["tls_ram_delta"] = ram_before - ram_after

            # Construir paquete MQTT 3.1.1 CONNECT
            cid = self._cfg.get("client_id") or _default_client_id()
            user = self._cfg.get("username") or ""
            pwd = sec.get("password") or ""
            ka = int(self._cfg.get("keepalive") or 60)

            flags = 0x02  # Clean Session
            payload = bytearray(_encode_str(cid))

            # Last Will & Testament para AOS IoT
            if self._cfg.get("aos_enabled"):
                will_topic = self._aos_topic("status")
                will_msg = json.dumps({"online": False, "device_id": cid})
                flags |= 0x04 | 0x20  # Will Flag + Will Retain (QoS 0)
                payload.extend(_encode_str(will_topic))
                payload.extend(_encode_str(will_msg))

            if user:
                flags |= 0x80
                payload.extend(_encode_str(user))
                if pwd:
                    flags |= 0x40
                    payload.extend(_encode_str(pwd))

            var_hdr = b"\x00\x04MQTT\x04" + bytes((flags,)) + struct.pack("!H", ka)
            pkt = b"\x10" + _encode_rem_len(len(var_hdr) + len(payload)) + var_hdr + bytes(payload)
            self._sock_write_all(pkt)

            # Leer CONNACK (4 bytes: 0x20 0x02 sp rc)
            resp = self._sock_read_exact(4)
            if resp[0] != 0x20 or resp[3] != 0x00:
                rc = resp[3]
                raise ValueError("CONNACK rechazado por broker (rc={})".format(rc))

            # Pasar a modo no bloqueante para cooperar con uasyncio
            self._set_blocking(False)
            self.state = "connected"
            self.last_error = None
            self._connected_at_ms = time.ticks_ms()
            self._last_rx_ms = self._connected_at_ms
            self._active_subs = set()

            self.kernel.log(
                "INFO",
                "MQTT",
                "Conectado TLS a {}:{} (RAM delta TLS: {} B)".format(
                    host, port, self.stats["tls_ram_delta"]
                ),
            )
            self.kernel.emit("mqtt_status", self.get_status())

            # Restaurar suscripciones guardadas + canal de comandos AOS IoT
            to_sub = list(self._cfg.get("subscriptions") or [])
            if self._cfg.get("aos_enabled"):
                cmd_topic = self._aos_topic("commands")
                if cmd_topic not in to_sub:
                    to_sub.append(cmd_topic)
                await self.publish(
                    self._aos_topic("status"),
                    json.dumps({"online": True, "device_id": cid, "board": self.hal.board_name}),
                    qos=0,
                    retain=True,
                )

            for t in to_sub:
                try:
                    await self.subscribe(t, qos=0, persist=False)
                except Exception:
                    pass

            return True
        except Exception as e:
            err_s = "{}: {}".format(type(e).__name__, e)
            self.last_error = err_s
            self.state = "error"
            if "MBEDTLS" in err_s.upper() or "CERT" in err_s.upper() or "SSL" in err_s.upper() or "TLS" in err_s.upper():
                self.stats["tls_errors"] += 1
            self.kernel.log("ERR", "MQTT", "Fallo conexion TLS: {}".format(err_s))
            self.kernel.emit("mqtt_error", {"error": err_s})
            self._close_socket(send_disconnect=False)
            if raw_sock is not None and self._sock is None:
                try:
                    raw_sock.close()
                except Exception:
                    pass
            return False
        finally:
            sec.clear()
            del sec
            gc.collect()

    def _close_socket(self, send_disconnect=False):
        if self._sock is not None:
            if send_disconnect:
                try:
                    self._set_blocking(True, timeout=2.0)
                    self._sock.write(b"\xe0\x00")
                except Exception:
                    pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._raw_sock is not None:
            try:
                self._raw_sock.close()
            except Exception:
                pass
            self._raw_sock = None
        for ev in self._pending_puback.values():
            ev.set()
        self._pending_puback.clear()

    # ------------------------------------------------------------------
    # Operaciones MQTT (Publish QoS 0/1, Subscribe, Unsubscribe)
    # ------------------------------------------------------------------

    async def publish(self, topic, payload, qos=0, retain=False):
        if self.state != "connected" or self._sock is None:
            raise RuntimeError("MQTT no conectado")
        if not _valid_topic(topic, allow_wildcards=False):
            raise ValueError("Topico invalido")
        qos = 1 if int(qos) == 1 else 0
        raw_p = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        if len(raw_p) > _MAX_PAYLOAD_LEN:
            raise ValueError("Payload excede limite ({} B)".format(_MAX_PAYLOAD_LEN))

        pid = self._next_pid() if qos == 1 else 0

        def _build_pub(dup=False):
            hdr_b = 0x30 | (0x08 if dup else 0) | (qos << 1) | (1 if retain else 0)
            vh = _encode_str(topic) + (struct.pack("!H", pid) if qos == 1 else b"")
            return bytes((hdr_b,)) + _encode_rem_len(len(vh) + len(raw_p)) + vh + raw_p

        if qos == 0:
            self._set_blocking(True, timeout=3.0)
            try:
                self._sock_write_all(_build_pub(False))
            finally:
                self._set_blocking(False)
            self.stats["sent"] += 1
            return {"topic": topic, "qos": 0, "ok": True}

        # QoS 1 con espera de PUBACK y hasta 2 reintentos (DUP=1)
        ev = asyncio.Event()
        self._pending_puback[pid] = ev
        try:
            for attempt in range(3):
                self._set_blocking(True, timeout=3.0)
                try:
                    self._sock_write_all(_build_pub(dup=(attempt > 0)))
                finally:
                    self._set_blocking(False)
                # Esperar PUBACK cooperativamente (hasta 2.5 s por intento)
                waited = 0
                while waited < 2500 and not ev.is_set() and self.state == "connected":
                    await _sleep_ms(50)
                    waited += 50
                if ev.is_set():
                    self.stats["sent"] += 1
                    return {"topic": topic, "qos": 1, "packet_id": pid, "ok": True}
            raise RuntimeError("Timeout esperando PUBACK QoS 1 (pid={})".format(pid))
        finally:
            self._pending_puback.pop(pid, None)

    async def subscribe(self, topic, qos=0, persist=True):
        if not _valid_topic(topic, allow_wildcards=True):
            raise ValueError("Topico de suscripcion invalido")
        qos = 1 if int(qos) == 1 else 0

        if persist:
            subs = self._cfg.setdefault("subscriptions", [])
            if topic not in subs:
                subs.append(topic)
                fs.write_json(_MQTT_CFG_PATH, self._cfg, "/", internal=True)

        if self.state != "connected" or self._sock is None:
            return {"topic": topic, "subscribed": False, "saved": persist}

        pid = self._next_pid()
        body = struct.pack("!H", pid) + _encode_str(topic) + bytes((qos,))
        pkt = b"\x82" + _encode_rem_len(len(body)) + body
        self._set_blocking(True, timeout=3.0)
        try:
            self._sock_write_all(pkt)
        finally:
            self._set_blocking(False)
        self._active_subs.add(topic)
        self.kernel.emit("mqtt_status", self.get_status())
        return {"topic": topic, "qos": qos, "subscribed": True}

    async def unsubscribe(self, topic, persist=True):
        if persist:
            subs = self._cfg.get("subscriptions", [])
            if topic in subs:
                subs.remove(topic)
                fs.write_json(_MQTT_CFG_PATH, self._cfg, "/", internal=True)

        self._active_subs.discard(topic)
        if self.state == "connected" and self._sock is not None:
            pid = self._next_pid()
            body = struct.pack("!H", pid) + _encode_str(topic)
            pkt = b"\xa2" + _encode_rem_len(len(body)) + body
            self._sock.setblocking(True)
            try:
                self._sock_write_all(pkt)
            finally:
                if self._sock:
                    self._sock.setblocking(False)
        self.kernel.emit("mqtt_status", self.get_status())
        return {"topic": topic, "unsubscribed": True}

    # ------------------------------------------------------------------
    # Recepcion no bloqueante + Integracion AOS IoT segura
    # ------------------------------------------------------------------

    def _try_read_packet(self):
        """Intenta leer un paquete MQTT entrante sin bloquear el event loop."""
        if self._sock is None:
            return None
        try:
            b0 = self._sock.read(1)
        except OSError as e:
            # EAGAIN / ETIMEDOUT normal en modo no bloqueante
            if e.args and e.args[0] in (11, 110, 116):
                return None
            raise
        if b0 is None:
            return None
        if len(b0) == 0:
            raise OSError("Broker cerro la conexion")

        # Tenemos inicio de paquete -> leer resto brevemente en modo bloqueante acotado
        self._set_blocking(True, timeout=3.0)
        try:
            ptype = b0[0]
            # Decodificar Remaining Length (variable 1..4 bytes)
            mult = 1
            rem_len = 0
            for _ in range(4):
                rb = self._sock_read_exact(1)[0]
                rem_len += (rb & 0x7F) * mult
                if (rb & 0x80) == 0:
                    break
                mult *= 128

            if rem_len > (_MAX_PAYLOAD_LEN + _MAX_TOPIC_LEN + 16):
                # Descartar payload excesivo para proteger RAM
                left = rem_len
                while left > 0:
                    chunk = self._sock.read(min(512, left))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.kernel.log("WARN", "MQTT", "Paquete descartado por exceder limite ({} B)".format(rem_len))
                return None

            body = self._sock_read_exact(rem_len) if rem_len > 0 else b""
            return (ptype, body)
        finally:
            self._set_blocking(False)

    async def _handle_aos_command(self, raw_payload):
        """
        Procesa comandos autorizados de AOS IoT desde aos/devices/<id>/commands.
        PROHIBIDO ejecutar codigo Python arbitrario por MQTT.
        """
        resp_topic = self._aos_topic("responses")
        try:
            cmd_obj = json.loads(raw_payload)
            cmd = cmd_obj.get("cmd")
            req_id = cmd_obj.get("id")
            args = cmd_obj.get("args") or {}

            if cmd == "ping":
                data = {"pong": True, "uptime_s": self.hal.get_uptime_ms() // 1000}
            elif cmd == "stats":
                data = self.hal.get_telemetry()
            elif cmd == "gpio_get":
                pin = int(args["pin"])
                data = {"pin": pin, "val": self.hal.gpio_read(pin)}
            elif cmd == "gpio_set":
                pin = int(args["pin"])
                val = 1 if args.get("val") else 0
                data = {"pin": pin, "val": self.hal.gpio_write(pin, val)}
            else:
                raise ValueError("Comando AOS no permitido: {}".format(cmd))

            await self.publish(resp_topic, json.dumps({"id": req_id, "ok": True, "data": data}), qos=0)
        except Exception as e:
            try:
                await self.publish(resp_topic, json.dumps({"ok": False, "error": str(e)}), qos=0)
            except Exception:
                pass

    async def _dispatch_packet(self, ptype, body):
        kind = ptype & 0xF0

        if kind == 0xD0:  # PINGRESP
            if self._ping_sent_ms:
                self._latency_ms = time.ticks_diff(time.ticks_ms(), self._ping_sent_ms)
                self._ping_sent_ms = 0
            return

        if kind == 0x40 and len(body) >= 2:  # PUBACK (QoS 1)
            pid = struct.unpack("!H", body[:2])[0]
            ev = self._pending_puback.get(pid)
            if ev is not None:
                ev.set()
            return

        if kind == 0x30:  # PUBLISH
            qos = (ptype >> 1) & 0x03
            tlen = struct.unpack("!H", body[:2])[0]
            topic = body[2 : 2 + tlen].decode("utf-8", "ignore")
            pos = 2 + tlen
            pid = None
            if qos > 0:
                pid = struct.unpack("!H", body[pos : pos + 2])[0]
                pos += 2
            payload = body[pos:].decode("utf-8", "replace")

            # Responder PUBACK si recibimos QoS 1
            if qos == 1 and pid is not None and self._sock is not None:
                self._set_blocking(True, timeout=2.0)
                try:
                    self._sock_write_all(b"\x40\x02" + struct.pack("!H", pid))
                finally:
                    self._set_blocking(False)

            self.stats["received"] += 1
            entry = {
                "t": self.hal.get_uptime_ms() // 1000,
                "topic": topic,
                "payload": payload,
                "qos": qos,
            }
            self._history.append(entry)
            if len(self._history) > _MAX_HISTORY:
                self._history.pop(0)
            self.kernel.emit("mqtt_msg", entry)

            # Canal de comandos AOS IoT
            if self._cfg.get("aos_enabled"):
                if topic == self._aos_topic("commands"):
                    await self._handle_aos_command(payload)

    # ------------------------------------------------------------------
    # Corrutina Principal (Reconexion Backoff + Keepalive + Telemetria AOS)
    # ------------------------------------------------------------------

    async def run(self):
        backoff_ms = 2000
        tel_elapsed = 0

        while True:
            if not self._want_connected:
                if self._sock is not None:
                    self._close_socket(send_disconnect=True)
                    self.state = "disconnected"
                await _sleep_ms(500)
                continue

            if self.state != "connected":
                ok = await self._connect_once()
                if ok:
                    backoff_ms = 2000
                    tel_elapsed = 0
                else:
                    if not self._cfg.get("reconnect", True):
                        self._want_connected = False
                        continue
                    self.stats["reconnects"] += 1
                    await _sleep_ms(backoff_ms)
                    backoff_ms = min(backoff_ms * 2, 60000)
                continue

            # Bucle conectado: lectura no bloqueante + keepalive + telemetria AOS
            try:
                pkt = self._try_read_packet()
                if pkt is not None:
                    await self._dispatch_packet(pkt[0], pkt[1])

                now = time.ticks_ms()
                ka_ms = int(self._cfg.get("keepalive", 60)) * 1000
                if time.ticks_diff(now, self._last_tx_ms) >= (ka_ms // 2):
                    self._ping_sent_ms = now
                    self._set_blocking(True, timeout=2.0)
                    try:
                        self._sock_write_all(b"\xc0\x00")  # PINGREQ
                    finally:
                        self._set_blocking(False)

                if time.ticks_diff(now, self._last_rx_ms) > (ka_ms * 2):
                    raise OSError("Keepalive timeout del broker")

                # Publicacion periodica de telemetria AOS IoT
                if self._cfg.get("aos_enabled"):
                    tel_elapsed += 100
                    tel_period = int(self._cfg.get("aos_telemetry_s", 30)) * 1000
                    if tel_elapsed >= tel_period:
                        tel_elapsed = 0
                        cid = self._cfg.get("client_id")
                        tel = self.hal.get_telemetry()
                        tel["cpu"]["load_pct"] = self.kernel.cpu_load_pct
                        tel["net"] = self.net.get_status()
                        await self.publish(
                            self._aos_topic("telemetry"),
                            json.dumps(tel),
                            qos=0,
                        )
            except Exception as e:
                self.last_error = str(e)
                self.kernel.log("WARN", "MQTT", "Conexion perdida: {}".format(e))
                self._close_socket(send_disconnect=False)
                self.state = "disconnected"
                self.kernel.emit("mqtt_status", self.get_status())
                await _sleep_ms(backoff_ms)
                backoff_ms = min(backoff_ms * 2, 60000)
                continue

            await _sleep_ms(100)


_mqtt_instance = None


def get_mqtt(kern=None, net_mgr=None):
    global _mqtt_instance
    if _mqtt_instance is None:
        _mqtt_instance = MQTTTLSManager(kern=kern, net_mgr=net_mgr)
    return _mqtt_instance
