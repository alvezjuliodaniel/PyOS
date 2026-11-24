# 03 — Auditoría de Memoria y Rendimiento (MicroOS v0.1.0)

> **Base empírica**: Mediciones reales tomadas en ESP32-C3 SuperMini (`178,688 B` heap), ESP32 DevKit v1 sin PSRAM (`115,392 B` heap) y ESP32-CAM con PSRAM64H (`~4,073 KB` libres). No se asumen presupuestos definitivos sin medición en hardware real.

---

## 1. Línea Base Medida en Hardware Real

| Placa | Heap Total | RAM libre tras `boot.py` | RAM libre con Wi-Fi + HTTP + WS | RAM mínima bajo estrés | Estado |
| :--- | ---: | ---: | ---: | ---: | :---: |
| **ESP32-C3 SuperMini** | `178,688 B` | `162,784 B` | `111,088 B` | `72,528 B` (18 HTTP `.gz` + 20 RPC) | Verificado |
| **ESP32 DevKit v1** (sin PSRAM) | `115,392 B` | `102,336 B` | *Pendiente de estrés* | *Pendiente de estrés* | Parcial |
| **ESP32-CAM** (`SPIRAM`) | `~4,194 KB` | `4,073 KB` | *Pendiente de estrés* | *Pendiente de estrés* | Parcial |
| **ESP32-S3** | *No medida* | *No medida* | *No medida* | *No medida* | Hipótesis |

---

## 2. Hallazgos de Memoria y Rendimiento

### `MEM-01` — Asignación de nuevo `bytearray(self.chunk_size)` por cada petición HTTP
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/server.py:280`, `sys/server.py:319`, `sys/server.py:358`
- **Descripción técnica**: Cada llamada a `_serve_static`, `_handle_app_asset` y `_handle_download` crea un `buf = bytearray(self.chunk_size)` nuevo en el heap. Al cargar el escritorio SPA (14 peticiones HTTP simultáneas en ráfaga), se asignan y descartan hasta 14 buffers contiguos de 512/1024 B generando fragmentación innecesaria.
- **Evidencia concreta**: `sys/server.py:280`: `buf = bytearray(self.chunk_size)`.
- **Impacto**: Fragmentación progresiva del heap en ESP32 sin PSRAM tras múltiples recargas del navegador.
- **Recomendación**: Crear un pequeño pool preasignado de `max_sockets` buffers (`[bytearray(self.chunk_size) for _ in range(self.max_sockets)]`) en `MicroServer.__init__()` que se toman y devuelven en `try/finally`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Ejecutar `test_stress_c3.py` comparando `ram_min` antes y después del pool de buffers.

---

### `MEM-02` — Copias innecesarias de memoria en `_ws_read_frame` y `_ws_build_frame`
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/server.py:85`, `sys/server.py:114-126`
- **Descripción técnica**:
  1. `_ws_read_frame()` lee `await reader.readexactly(length)` (crea `bytes`), lo envuelve en `bytearray(...)` (segunda copia) y al final retorna `bytes(data)` (tercera copia), y `_handle_websocket` luego llama `payload.decode("utf-8")` (cuarta copia). Un paquete `.mapp.json` de 8 KB consume **~32 KB de pico temporal** en RAM.
  2. `_ws_build_frame()` hace `return header + payload`, copiando todo el payload en un nuevo buffer solo para anteponer 2–4 bytes de cabecera.
- **Evidencia concreta**: `sys/server.py:114`: `data = bytearray(await reader.readexactly(length))`; `sys/server.py:126`: `return opcode, bytes(data)`; `sys/server.py:85`: `return header + payload`.
- **Impacto**: Picos de RAM evitables en ESP32 DevKit v1 (112 KB heap) al instalar apps o enviar respuestas grandes (`fs.read`).
- **Recomendación**: Usar `reader.readinto(buf)` sobre un `bytearray` preasignado o decodificar `data` directamente sin `bytes(data)`, y en `_ws_writer_loop` escribir `header` y `payload` en dos `writer.write()` consecutivos antes de un único `await writer.drain()`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Enviar un frame JSON-RPC de 7 KB en ESP32-C3 y medir el pico de `gc.mem_alloc()`.

---

### `MEM-03` — `gc.threshold()` mal calibrado en `boot.py`
- **Severidad**: **Media**
- **Archivo y líneas**: `boot.py:20`
- **Descripción técnica**: `gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())` suma `gc.mem_alloc()` a un parámetro que MicroPython interpreta como *bytes nuevos asignados desde el último GC*, no como uso absoluto.
- **Evidencia concreta**: `boot.py:20`: `gc.threshold(gc.mem_free() // 4 + gc.mem_alloc())`.
- **Impacto**: Retrasa las recolecciones tempranas en ESP32 sin PSRAM, favoreciendo fragmentación antes del ciclo del watchdog.
- **Recomendación**: Cambiar a `gc.threshold(gc.mem_free() // 4)` (en PSRAM acotarlo a un tope razonable como `131072` B para evitar pausas largas de GC de 4 MB!).
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Medir tiempo de pausa de `gc.collect()` en ESP32-CAM con 4 MB PSRAM y confirmar que no supera 30–50 ms.

---

### `MEM-04` — Serialización JSON repetida y difusión de `sys_stats` / `log` sin throttling
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/kernel.py:118`, `sys/server.py:204-213`
- **Descripción técnica**: Cada llamada a `kernel.log()` dispara inmediatamente `emit("log", entry)` → `json.dumps(...)` → `cli.enqueue(...)`. Una ráfaga de errores en red o tareas puede saturar la cola (`_MAX_PUSH_QUEUE = 8`) y generar docenas de strings JSON desechables por segundo. Además en `hal.get_telemetry()` se construyen 4 diccionarios anidados cada 2 s.
- **Evidencia concreta**: `sys/server.py:207`: `payload = json.dumps({"event": event_name, "data": data})`.
- **Impacto**: Presión constante sobre el recolector de basura cuando hay clientes WebSocket conectados.
- **Recomendación**: Añadir *rate-limiting* para eventos `log` (máx. 5/s o agrupación) y reutilizar estructuras planas cuando sea posible.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Generar 50 logs en 1 segundo y medir consumo de RAM y latencia de respuesta WebSocket.

---

### `MEM-05` — Importación desde `.py` en lugar de bytecode precompilado `.mpy` en perfil `strict`
- **Severidad**: **Media**
- **Archivo y líneas**: `tools/build.py:109-128`, `sys/*.py`
- **Descripción técnica**: Aunque `tools/build.py --mpy` soporta compilación con `mpy-cross`, el despliegue por defecto sube archivos `.py` fuente. En ESP32 DevKit v1 (112 KB heap), compilar `server.py` (21.8 KB) y `hal.py` (19.5 KB) en tiempo de importación consume RAM interna valiosa y aumenta el tiempo de arranque.
- **Evidencia concreta**: `sys/server.py` = `21,876 B`, `sys/hal.py` = `19,513 B`.
- **Impacto**: Menor margen de RAM disponible en ESP32 sin PSRAM y mayor fragmentación inicial.
- **Recomendación**: Documentar e incentivar `--mpy` para producción en perfil `strict` (`-march=rv32imc` para C3, `-march=xtensawin` para ESP32/S3), midiendo el ahorro exacto de RAM en ambas placas.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Comparar `gc.mem_free()` tras `boot.py + main.py` entre despliegue `.py` y `.mpy` en ESP32-C3 y ESP32 DevKit v1.

---

### `MEM-06` — *Hipótesis pendiente*: Impacto de TLS (mbedTLS) en memoria interna vs PSRAM
- **Severidad**: **Alta (Hipótesis pendiente de medición en placa)**
- **Archivo y líneas**: N/A (aplica a Fase 10 — MQTT sobre TLS)
- **Descripción técnica**: En ESP32 con PSRAM (ESP32-CAM), el heap de MicroPython vive en PSRAM, pero los drivers de Wi-Fi, LWIP y parte de mbedTLS requieren **DRAM interna** (DMA-capable). En ESP32-C3 / ESP32 sin PSRAM, un handshake TLS con verificación de certificado CA consume ~35–50 KB de RAM.
- **Impacto**: En perfil `strict` (especialmente ESP32 DevKit v1 con 112 KB), MQTT sobre TLS podría provocar `MemoryError` o caídas de Wi-Fi si convive con HTTP + WebSocket.
- **Recomendación**: Medir `gc.mem_free()` antes, durante y después de un handshake TLS real en ESP32-CAM (PSRAM) y en ESP32-C3 (sin PSRAM). Si el margen en `strict` cae bajo 24 KB, deshabilitar MQTT TLS por defecto en perfil `strict` sin relajar nunca la verificación TLS.
- **Dificultad estimada**: Media.
- **Forma de verificar**: Ejecutar `tests/test_mqtt.py` midiendo `gc.mem_free()` en cada fase del ciclo de vida TLS.

---

## 3. Estrategia de Optimización por Perfil

| Parámetro / Estrategia | `strict` (C3 / ESP32 sin PSRAM) | `standard` (ESP32 + PSRAM) | `extended` (ESP32-S3 + PSRAM) |
| :--- | :--- | :--- | :--- |
| `http_chunk_bytes` | `512 B` (pool preasignado) | `1024 B` (pool preasignado) | `2048 B` (pool preasignado) |
| `max_sockets` | `3` | `5` | `6–8` |
| `gc_interval_ms` | `5000 ms` | `10000 ms` | `15000 ms` |
| `gc.threshold()` | `mem_free // 4` (~25 KB) | `min(mem_free // 4, 131072)` | `min(mem_free // 4, 262144)` |
| Compilación `.mpy` | **Altamente recomendada** | Opcional | Opcional |
| MQTT sobre TLS | **Condicionado a medición real** | Habilitado | Habilitado |
| Tamaño máx. frame WS | `8192 B` | `16384 B` | `32768 B` |
