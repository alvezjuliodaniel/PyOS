"""
MicroOS - Kernel Asincrono (uasyncio)
Gestiona el ciclo de vida de tareas cooperativas (Task Manager), supervision
con reinicio automatico de servicios criticos, mantenimiento activo de memoria
(GC + alertas de RAM) y bus de eventos/logs ligero para el sistema.
"""

import gc
import time

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import hal
import fs

_HAS_SLEEP_MS = hasattr(asyncio, "sleep_ms")


def _sleep_ms(ms):
    if _HAS_SLEEP_MS:
        return asyncio.sleep_ms(ms)
    return asyncio.sleep(ms / 1000.0)


class _TaskEntry:
    """Descriptor ligero de una tarea administrada por el Kernel."""

    __slots__ = (
        "name",
        "coro_fn",
        "args",
        "critical",
        "restart_delay_ms",
        "max_restarts",
        "task",
        "state",
        "restarts",
        "last_error",
        "started_ms",
    )

    def __init__(self, name, coro_fn, args, critical, restart_delay_ms, max_restarts):
        self.name = name
        self.coro_fn = coro_fn
        self.args = args
        self.critical = critical
        self.restart_delay_ms = restart_delay_ms
        self.max_restarts = max_restarts
        self.task = None
        self.state = "pending"
        self.restarts = 0
        self.last_error = None
        self.started_ms = 0

    def to_dict(self):
        return {
            "name": self.name,
            "state": self.state,
            "critical": self.critical,
            "restarts": self.restarts,
            "last_error": self.last_error,
        }


class Kernel:
    """
    Nucleo cooperativo de MicroOS sobre uasyncio.
    """

    def __init__(self, config_path="/config/system.json"):
        self.hal = hal.get_hal()
        cfg = fs.read_json(config_path, default={}) or {}

        self.name = cfg.get("name", "MicroOS")
        self.version = cfg.get("version", "0.2.0")
        self.hostname = cfg.get("hostname", "microos")
        self.allow_remote_python = bool(cfg.get("allow_remote_python", True))
        self._log_max = cfg.get("log_max_entries", 32)
        self._telemetry_ms = cfg.get("telemetry_interval_ms", 2000)

        self._tasks = {}            # name -> _TaskEntry
        self._logs = []             # Ring buffer compacto de logs
        self._subscribers = []      # Callbacks sync o async para eventos push
        self._low_mem_hooks = []    # Callbacks para liberar caches bajo presion RAM
        self._running = False
        self._stop_event = None
        self._last_mem_level = "ok"
        self.cpu_load_pct = 0.0

        # Registrar tareas nativas del kernel
        self.register_task(
            "mem_watchdog",
            self._memory_watchdog,
            critical=True,
            restart_delay_ms=2000,
            max_restarts=0,  # 0 = reinicio ilimitado
        )

    # ------------------------------------------------------------------
    # Logging compacto (Ring Buffer) y Bus de Eventos
    # ------------------------------------------------------------------

    def log(self, level, tag, msg):
        """Registra una entrada en el buffer circular y la imprime por consola."""
        entry = {
            "t": self.hal.get_uptime_ms() // 1000,
            "lvl": level,
            "tag": tag,
            "msg": str(msg),
        }
        self._logs.append(entry)
        if len(self._logs) > self._log_max:
            self._logs.pop(0)

        print("[{:>5}s][{}][{}] {}".format(entry["t"], level, tag, entry["msg"]))
        self.emit("log", entry)

    def get_logs(self, limit=None):
        """Retorna las ultimas entradas del buffer de logs."""
        if limit is None or limit >= len(self._logs):
            return list(self._logs)
        return self._logs[-limit:]

    def subscribe(self, callback):
        """Suscribe un callback (event_name, data) al bus de eventos del kernel."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback):
        """Elimina un suscriptor del bus de eventos."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def register_low_memory_hook(self, callback):
        """Registra una funcion a invocar cuando la RAM cae al umbral critico."""
        if callback not in self._low_mem_hooks:
            self._low_mem_hooks.append(callback)

    def emit(self, event_name, data):
        """Despacha un evento a los suscriptores activos sin bloquear el kernel."""
        if not self._subscribers:
            return
        for cb in list(self._subscribers):
            try:
                cb(event_name, data)
            except Exception:
                # Nunca permitir que un suscriptor defectuoso tumbe el kernel
                pass

    # ------------------------------------------------------------------
    # Gestor de Tareas (Task Manager + Supervisor)
    # ------------------------------------------------------------------

    def register_task(
        self,
        name,
        coro_fn,
        args=(),
        critical=False,
        restart_delay_ms=2000,
        max_restarts=5,
    ):
        """
        Registra una corrutina administrada por el Kernel.
        - coro_fn: funcion async (fabrica de corrutinas)
        - critical: si True, el supervisor la reinicia ante excepciones
        - max_restarts: 0 para reinicios ilimitados, >0 para tope maximo
        """
        if name in self._tasks:
            raise ValueError("Tarea ya registrada: {}".format(name))
        entry = _TaskEntry(name, coro_fn, args, critical, restart_delay_ms, max_restarts)
        self._tasks[name] = entry
        if self._running:
            self.start_task(name)
        return entry

    def start_task(self, name):
        """Inicia una tarea registrada si no esta ya en ejecucion."""
        entry = self._tasks.get(name)
        if entry is None:
            raise KeyError("Tarea desconocida: {}".format(name))
        if entry.task is not None and entry.state in ("running", "restarting"):
            return False
        entry.last_error = None
        entry.task = asyncio.create_task(self._supervise(entry))
        return True

    def stop_task(self, name):
        """Detiene (cancela) una tarea en ejecucion."""
        entry = self._tasks.get(name)
        if entry is None:
            return False
        if entry.task is not None:
            try:
                entry.task.cancel()
            except Exception:
                pass
            entry.task = None
        entry.state = "stopped"
        return True

    def unregister_task(self, name):
        """Detiene y elimina una tarea registrada del Kernel (ARCH-03)."""
        self.stop_task(name)
        return self._tasks.pop(name, None) is not None

    def count_running_tasks(self):
        """Retorna el numero de tareas actualmente en estado 'running'."""
        return sum(1 for e in self._tasks.values() if e.state == "running")

    def restart_task(self, name):
        """Reinicia manualmente una tarea registrada."""
        self.stop_task(name)
        entry = self._tasks.get(name)
        if entry is None:
            return False
        entry.restarts = 0
        return self.start_task(name)

    def list_tasks(self):
        """Retorna lista serializable del estado de todas las tareas."""
        return [entry.to_dict() for entry in self._tasks.values()]

    async def _supervise(self, entry):
        """Wrapper supervisor que monitorea excepciones y reinicia tareas criticas."""
        while self._running:
            entry.state = "running"
            entry.started_ms = self.hal.get_uptime_ms()
            try:
                await entry.coro_fn(*entry.args)
                # Finalizacion limpia
                entry.state = "stopped"
                entry.task = None
                return
            except asyncio.CancelledError:
                entry.state = "stopped"
                entry.task = None
                raise
            except Exception as exc:
                entry.last_error = "{}: {}".format(type(exc).__name__, exc)
                entry.restarts += 1
                self.log(
                    "ERR",
                    "TASK",
                    "{} fallo (#{}): {}".format(
                        entry.name, entry.restarts, entry.last_error
                    ),
                )

                can_restart = entry.critical and (
                    entry.max_restarts == 0 or entry.restarts <= entry.max_restarts
                )
                if not can_restart or not self._running:
                    entry.state = "failed"
                    entry.task = None
                    self.emit("task_failed", entry.to_dict())
                    return

                entry.state = "restarting"
                gc.collect()
                await _sleep_ms(entry.restart_delay_ms)

    # ------------------------------------------------------------------
    # Corrutina de Mantenimiento de Memoria (Watchdog de RAM + Telemetria)
    # ------------------------------------------------------------------

    async def _memory_watchdog(self):
        """
        Ejecuta gc.collect() periodico, vigila umbrales de RAM (critico en ESP32-C3)
        y emite telemetria cuando existen suscriptores activos.
        """
        gc_interval = self.hal.limits.get("gc_interval_ms", 5000)
        # Paso minimo para permitir emitir telemetria mas frecuente que el GC si hay clientes
        step_ms = min(gc_interval, self._telemetry_ms)
        if step_ms < 500:
            step_ms = 500

        elapsed_gc = 0
        elapsed_tel = 0

        while self._running:
            t0 = time.ticks_ms()
            await _sleep_ms(step_ms)
            dt = time.ticks_diff(time.ticks_ms(), t0)
            # Carga cooperativa estimada: exceso de latencia sobre step_ms + actividad GC/tareas
            busy = dt - step_ms
            if busy < 0:
                busy = 0
            inst_pct = (busy * 100.0) / step_ms
            if inst_pct > 100.0:
                inst_pct = 100.0
            # Suavizado exponencial para evitar saltos bruscos
            self.cpu_load_pct = round(self.cpu_load_pct * 0.65 + inst_pct * 0.35, 1)

            elapsed_gc += step_ms
            elapsed_tel += step_ms

            # Actualizar acumulador de uptime para prevenir wraparound de ticks_ms
            self.hal.get_uptime_ms()

            if elapsed_gc >= gc_interval:
                elapsed_gc = 0
                gc.collect()
                mem = self.hal.get_memory_stats()

                if mem["critical"]:
                    # Ejecutar hooks de liberacion de memoria y segundo GC
                    for hook in self._low_mem_hooks:
                        try:
                            hook()
                        except Exception:
                            pass
                    gc.collect()
                    mem = self.hal.get_memory_stats()

                    if self._last_mem_level != "critical":
                        self._last_mem_level = "critical"
                        self.log(
                            "ERR",
                            "MEM",
                            "RAM CRITICA: {} B libres ({}% usado)".format(
                                mem["free"], mem["used_pct"]
                            ),
                        )
                    self.emit("mem_alert", {"level": "critical", "mem": mem})

                elif mem["warn"]:
                    if self._last_mem_level != "warn":
                        self._last_mem_level = "warn"
                        self.log(
                            "WARN",
                            "MEM",
                            "RAM baja: {} B libres ({}% usado)".format(
                                mem["free"], mem["used_pct"]
                            ),
                        )
                    self.emit("mem_alert", {"level": "warn", "mem": mem})

                else:
                    if self._last_mem_level != "ok":
                        self._last_mem_level = "ok"
                        self.log(
                            "INFO",
                            "MEM",
                            "RAM recuperada: {} B libres".format(mem["free"]),
                        )

            # Solo construir snapshot de telemetria si alguien esta escuchando (WebSocket)
            if elapsed_tel >= self._telemetry_ms:
                elapsed_tel = 0
                if self._subscribers:
                    tel = self.hal.get_telemetry()
                    tel["cpu"]["load_pct"] = self.cpu_load_pct
                    tel["cpu"]["tasks_running"] = sum(
                        1 for e in self._tasks.values() if e.state == "running"
                    )
                    self.emit("sys_stats", tel)

    # ------------------------------------------------------------------
    # Arranque y Parada del Kernel
    # ------------------------------------------------------------------

    async def start(self):
        """Arranca el Kernel y todas las tareas registradas."""
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()

        self.log(
            "INFO",
            "KERNEL",
            "{} v{} iniciado en {} (perfil: {})".format(
                self.name,
                self.version,
                self.hal.board_name,
                self.hal.limits.get("profile", "strict"),
            ),
        )

        for name in list(self._tasks.keys()):
            self.start_task(name)

        gc.collect()
        # Mantener el bucle principal vivo hasta que se invoque stop()
        await self._stop_event.wait()

        # Cancelacion ordenada al detener
        for name in list(self._tasks.keys()):
            self.stop_task(name)
        await _sleep_ms(50)
        gc.collect()
        self.log("INFO", "KERNEL", "Kernel detenido.")

    def stop(self):
        """Solicita la detencion ordenada del Kernel."""
        self._running = False
        if self._stop_event is not None:
            self._stop_event.set()


# Singleton del Kernel
_kernel_instance = None


def get_kernel(config_path="/config/system.json"):
    """Obtiene o crea la instancia unica del Kernel."""
    global _kernel_instance
    if _kernel_instance is None:
        _kernel_instance = Kernel(config_path=config_path)
    return _kernel_instance
