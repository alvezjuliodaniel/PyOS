# 01 — Auditoría de Arquitectura Actual (PyOS v0.1.0)

> **Alcance**: Inspección estática y funcional del código fuente real de PyOS v0.1.0 sin modificar archivos del sistema.  
> **Hardware verificado**: ESP32-C3 SuperMini (sin PSRAM), ESP32 DevKit v1 (sin PSRAM) y ESP32-CAM (`PSRAM64H`, ~4 MB heap SPIRAM). ESP32-S3 soportada por configuración en `hal.py`/`boards.json`, **no validada físicamente todavía**.

---

## 1. Mapa de Componentes y Dependencias

| Componente | Archivo | Líneas | Responsabilidad principal | Dependencias directas |
| :--- | :--- | ---: | :--- | :--- |
| Arranque temprano | `boot.py` | 42 | Configura `sys.path`, `gc.threshold()` e inicializa `HAL`. | `gc`, `sys`, `hal` |
| Entrypoint Kernel | `main.py` | 73 | Instancia `Kernel`, `NetManager`, `MicroServer`, `AppManager` y `OLEDStatusApp`; arranca `uasyncio.run()`. | `uasyncio`, `kernel`, `net`, `server`, `appmgr`, `oled_status` |
| HAL | `sys/hal.py` | 549 | Autodetección de chip/PSRAM, mapa de pines seguros, GPIO/ADC1/PWM y telemetría. | `machine`, `esp32`, `ujson`, `gc`, `os`, `sys`, `time` |
| VFS / FS seguro | `sys/fs.py` | 331 | Normalización anti *path-traversal*, listado `ilistdir`, streaming por chunks y copia por bloques. | `os`, `ujson`, `micropython.const` |
| Kernel asíncrono | `sys/kernel.py` | 404 | Supervisor de tareas, Watchdog de RAM + GC, carga CPU cooperativa, ring buffer de logs y bus de eventos. | `uasyncio`, `gc`, `time`, `hal`, `fs` |
| Red Wi-Fi | `sys/net.py` | 396 | Modo dual STA + AP (`PyOS-Setup`), reconexión asíncrona, hostname `pyos` y `txpower`. | `uasyncio`, `network`, `gc`, `fs`, `kernel` |
| Router JSON-RPC | `sys/rpc.py` | 319 | Despachador de comandos `sys`, `gpio`, `fs`, `net`, `term` y apps dinámicas. | `gc`, `os`, `sys`, `hal`, `fs`, `kernel`, `net` |
| Servidor HTTP/WS | `sys/server.py` | 613 | Servidor `uasyncio.start_server`: estáticos `.gz`, upload/download streaming, `/api/apps/*` y WebSocket RFC 6455. | `uasyncio`, `hashlib`, `binascii`, `struct`, `ujson`, `fs`, `kernel`, `rpc` |
| Gestor de Apps | `sys/appmgr.py` | 256 | Descubrimiento, instalación, carga/recarga en caliente y desinstalación en `/apps/<id>/`. | `gc`, `sys`, `fs`, `kernel` |
| OLED SSD1306 | `apps/oled_status.py` | 298 | Driver I2C `framebuf` + tarea cooperativa de estado Wi-Fi/IP/RAM. | `framebuf`, `machine.SoftI2C`, `uasyncio`, `fs`, `kernel`, `net` |
| Frontend SPA | `frontend_src/` | ~2,950 | Escritorio estilo Win7, Window Manager, cliente WS JSON-RPC y 9 apps nativas en Vanilla JS. | Ninguna dependencia externa |
| Host Tools | `tools/build.py`, `tools/pyos_studio.py` | 1,082 | Minificador + gzip, compilación `.mpy`, despliegue `mpremote` / `--no-dtr` y GUI Tkinter. | Python 3 stdlib, `pyserial`, `mpremote` |

---

## 2. Evaluación de `uasyncio` y Operaciones Bloqueantes

En términos generales el backend respeta la multitarea cooperativa (no hay `time.sleep()` en `kernel.py`, `net.py`, `server.py` ni `oled_status.py`). Sin embargo, la inspección identificó **operaciones síncronas bloqueantes reales** que detienen el *event loop* completo mientras se ejecutan:

---

## 3. Hallazgos de Arquitectura

### `ARCH-01` — Llamadas síncronas bloqueantes dentro de `rpc.dispatch()` detienen el loop `uasyncio`
- **Severidad**: **Alta**
- **Archivo y líneas**: `sys/net.py:281-304` (`scan()`), `sys/rpc.py:75-97` (`dispatch()`), `sys/rpc.py:253-319` (`_handle_term`), `sys/fs.py:315-330` (`copy_file`)
- **Descripción técnica**: `RPCRouter.dispatch()` es una función síncrona invocada directamente desde el bucle WebSocket (`sys/server.py:482`). Varias operaciones que despacha bloquean el hilo principal durante cientos de milisegundos o varios segundos:
  1. `net.scan()` (`self.sta.scan()`, `sys/net.py:287`) bloquea toda la CPU/event loop entre **1.5 y 3.0 segundos** escaneando canales 2.4 GHz.
  2. `term.exec` / `term.run_file` (`sys/rpc.py:283, 309-314`) ejecuta código arbitrario de forma síncrona; un `time.sleep(10)` o bucle infinito congela HTTP, WebSocket, `mem_watchdog` y `net_manager`.
  3. `fs.copy_file()` (`sys/fs.py:315-330`) lee y escribe de forma síncrona sin ceder el control (`await asyncio.sleep_ms(0)`).
- **Evidencia concreta**: `sys/server.py:482`: `res = self.router.dispatch(req)` (síncrono); `sys/net.py:287`: `raw = self.sta.scan()`.
- **Impacto**: Durante un escaneo Wi-Fi o la ejecución de un script pesado, todos los clientes HTTP/WS experimentan *freeze* o *timeout* y la carga CPU cooperativa reporta falsos picos del 100%.
- **Recomendación**: Permitir manejadores `async` en `RPCRouter.dispatch()` (`await handler(...)` si es corrutina), ceder periódicamente el control en `copy_file` y `read_chunks`, y documentar/limitar el tiempo de ejecución en `term.run_file`.
- **Dificultad estimada**: Media.
- **Forma de verificar**: Ejecutar `net.scan` o copiar un archivo de 100 KB mientras un segundo cliente solicita `GET /` o envía `ping` por WebSocket; la latencia HTTP no debe exceder 300 ms en copias de archivos.

---

### `ARCH-02` — Ausencia de timeouts en lecturas HTTP y WebSocket (`readline`, `readexactly`)
- **Severidad**: **Alta**
- **Archivo y líneas**: `sys/server.py:514-527` (`_handle_client`), `sys/server.py:92-114` (`_ws_read_frame`), `sys/fs.py:195-214` (`async_write_stream`)
- **Descripción técnica**: Ninguna lectura de socket (`reader.readline()`, `reader.readexactly()`, `reader.read()`) está envuelta en `asyncio.wait_for(..., timeout)`. Un cliente que abra una conexión TCP y no envíe la línea HTTP completa (ataque *Slowloris* o conexión móvil colgada) retiene uno de los 3 slots de `_active_conns` indefinidamente.
- **Evidencia concreta**: `sys/server.py:514`: `req_line = await reader.readline()`; `sys/server.py:523`: `line = await reader.readline()`; `sys/fs.py:206`: `data = await reader.read(to_read)`.
- **Impacto**: En el perfil `strict` (`max_sockets = 3`), **3 conexiones colgadas dejan inaccesible todo el servidor HTTP/WS** con respuestas `503 Service Unavailable` hasta reiniciar la placa.
- **Recomendación**: Envolver la lectura de cabeceras HTTP en `asyncio.wait_for(..., 5)` y los bloques de `async_write_stream` en `asyncio.wait_for(..., 10)`. En WebSocket, aplicar *keepalive ping* periódico o *timeout* de inactividad configurable.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Abrir 3 conexiones TCP con `nc <ip> 80` sin enviar datos; verificar que el servidor las cierra automáticamente tras 5 s y que una 4.ª petición HTTP responde `200 OK`.

---

### `ARCH-03` — Acoplamiento interno entre `AppManager` y atributos privados de `Kernel` (`_tasks`)
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/appmgr.py:143-146`, `sys/appmgr.py:163-164`
- **Descripción técnica**: `AppManager` accede y muta directamente el diccionario privado `self.kernel._tasks` (`del self.kernel._tasks[task_name]` y `self.kernel._tasks.pop(...)`), y `rpc.py:114` también lee `self.kernel._tasks.values()`.
- **Evidencia concreta**: `sys/appmgr.py:144`: `if task_name in self.kernel._tasks: ... del self.kernel._tasks[task_name]`.
- **Impacto**: Fragilidad ante refactorizaciones de `Kernel` y posibilidad de dejar referencias huérfanas si la tarea aún no terminó su cancelación asíncrona.
- **Recomendación**: Exponer un método oficial `Kernel.unregister_task(name)` y `Kernel.count_running_tasks()` en `sys/kernel.py`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Eliminar toda referencia a `._tasks` fuera de `sys/kernel.py` y ejecutar pruebas de instalación/desinstalación de apps con `autostart: true`.

---

### `ARCH-04` — Doble asignación de framebuffer en `SSD1306_I2C.show()` y `SoftI2C` bloqueante
- **Severidad**: **Media**
- **Archivo y líneas**: `apps/oled_status.py:85`, `apps/oled_status.py:144`
- **Descripción técnica**: En cada refresco (cada 2 s), `show()` ejecuta `b"\x40" + self.buffer`, lo que **crea un nuevo objeto `bytes` de 1,025 B** en el heap cada 2 segundos (fragmentando la SRAM en ESP32-C3 y ESP32 sin PSRAM). Además usa `SoftI2C` por software (bit-banging en CPU) en lugar de `machine.I2C` hardware cuando es posible.
- **Evidencia concreta**: `apps/oled_status.py:85`: `self.i2c.writeto(self.addr, b"\x40" + self.buffer)`.
- **Impacto**: 1,025 B de basura temporal cada 2 segundos + carga innecesaria de CPU en el núcleo principal.
- **Recomendación**: Reservar `self._raw = bytearray(1 + pages * width)`, fijar `self._raw[0] = 0x40` y pasar `memoryview(self._raw)[1:]` al `FrameBuffer` para lograr **cero asignaciones** en `show()`. Intentar `machine.I2C(0, ...)` con fallback a `SoftI2C`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Medir `gc.mem_alloc()` antes y después de 10 llamadas a `oled.show()` verificando delta ~0 B.

---

### `ARCH-05` — Riesgo de *race condition* al desconectar Wi-Fi o al detener el servidor en `main.py`
- **Severidad**: **Baja**
- **Archivo y líneas**: `main.py:61-68`, `sys/server.py:568-589`, `sys/kernel.py:190-200`
- **Descripción técnica**: `Kernel.stop_task()` cancela la tarea pero no hace `await` de su finalización. En `sys/server.py:490-499`, `writer_task.cancel()` se invoca pero el cierre del `StreamWriter` se delega al bloque `finally` de `_handle_client`, pudiendo producirse escrituras concurrentes si un evento push despierta a `_ws_writer_loop` durante el cierre.
- **Evidencia concreta**: `sys/server.py:490-495`: `client.alive = False; client.event.set(); writer_task.cancel()`.
- **Impacto**: Excepciones menores silenciadas (`except Exception`) o fuga momentánea de un descriptor de socket bajo reconexiones rápidas.
- **Recomendación**: Esperar brevemente o usar una bandera/lock de cierre coordinado antes de cerrar `writer`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Conectar y desconectar abruptamente 30 clientes WebSocket seguidos verificando que `_active_conns` vuelva siempre a `0`.

---

### `ARCH-06` — `boot.py` no reajusta `gc.threshold()` tras arrancar Wi-Fi y servicios
- **Severidad**: **Baja**
- **Archivo y líneas**: `boot.py:18-22`
- **Descripción técnica**: `gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())` se calcula una única vez en `boot.py` cuando solo está cargado `boot.py`. En MicroPython, `gc.threshold(amount)` espera **la cantidad de bytes asignados desde el último GC** (no `mem_free//4 + mem_alloc`), por lo que sumarle `mem_alloc()` hace el umbral más alto de lo pretendido.
- **Evidencia concreta**: `boot.py:20`: `gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())`.
- **Impacto**: En perfil `strict` (~112 KB), el GC automático se dispara menos frecuentemente de lo deseado, aumentando la fragmentación entre ciclos del `mem_watchdog`.
- **Recomendación**: Configurar `gc.threshold(gc.mem_free() // 4)` (sin sumar `gc.mem_alloc()`), y recalibrarlo en `Kernel.start()` según el perfil.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Verificar `gc.threshold()` en REPL tras el arranque (~25–30 KB en `strict`).
