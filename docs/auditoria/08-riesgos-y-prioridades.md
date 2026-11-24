# 08 — Matriz Consolidada de Riesgos y Prioridades (PyOS v0.1.0)

---

## 1. Resumen Cuantitativo de Hallazgos

| Severidad | Cantidad | Identificadores |
| :--- | :---: | :--- |
| **Crítica** | **4** | `SEC-01`, `SEC-02`, `SEC-03`, `SEC-04` |
| **Alta** | **8** | `ARCH-01`, `ARCH-02`, `SEC-05`, `SEC-06`, `SEC-07`, `MEM-06` *(hipótesis)*, `FE-01`, `APP-01`, `APP-04` *(nota: 9 contando `APP-04`)* |
| **Media** | **10** | `ARCH-03`, `ARCH-04`, `SEC-08`, `SEC-09`, `MEM-01`, `MEM-02`, `MEM-03`, `MEM-04`, `MEM-05`, `FE-03`, `FE-04`, `APP-02`, `APP-03` |
| **Baja / Baja-Media** | **5** | `ARCH-05`, `ARCH-06`, `SEC-10`, `FE-02`, `FE-05`, `FE-06` |

---

## 2. Tabla Maestra Ordenada por Prioridad de Remediación

| Orden | ID | Severidad | Componente | Problema principal | Dificultad | Etapa |
| :-: | :--- | :---: | :--- | :--- | :---: | :---: |
| 1 | `SEC-01` | **Crítica** | `sys/server.py`, `sys/rpc.py` | Sin autenticación ni autorización en HTTP/WS/RPC | Media | A |
| 2 | `SEC-04` | **Crítica** | `sys/server.py` | WebSocket sin validación de `Origin` (CSWSH) | Baja | A |
| 3 | `SEC-02` | **Crítica** | `sys/fs.py`, `sys/server.py` | Lectura libre de `/config/wifi.json` y escritura sobre `/boot.py`, `/sys/*` | Baja-Media | A |
| 4 | `SEC-03` | **Crítica** | `sys/rpc.py`, `sys/appmgr.py` | Ejecución arbitraria de Python sin modelo de confianza/roles | Media | A |
| 5 | `APP-04` | **Alta** | `sys/appmgr.py` | *Path traversal* en `manifest.ui` / `manifest.backend` (`base="/"`) | Baja | A |
| 6 | `FE-01` | **Alta** | `wifi.js`, `files.js`, `appstore.js` | XSS vía `innerHTML` (SSID Wi-Fi o nombre de archivo malicioso) | Baja | A |
| 7 | `ARCH-02` | **Alta** | `sys/server.py`, `sys/fs.py` | Sin timeouts en lecturas HTTP/WS (riesgo Slowloris con `max_sockets=3`) | Baja | A |
| 8 | `SEC-09` | **Media** | `sys/server.py` | `reader.readline()` sin tope de longitud de cabecera (DoS RAM) | Baja | A |
| 9 | `SEC-06` | **Alta** | `sys/server.py` | `POST /api/fs/upload` sin límite de tamaño ni reserva de Flash | Baja | B |
| 10 | `SEC-05` | **Alta** | `sys/net.py`, `config/wifi.json` | AP de respaldo `PyOS-Setup` abierto (`AUTH_OPEN`) | Baja | B |
| 11 | `SEC-07` | **Alta** | `sys/server.py` | WebSocket sin fragmentación (`FIN=0`) ni rechazo de frames sin máscara | Baja-Media | B |
| 12 | `APP-01` | **Alta** | `sys/appmgr.py` | Instalación/actualización no atómica sin staging ni rollback | Media | B |
| 13 | `ARCH-01` | **Alta** | `sys/net.py`, `sys/rpc.py`, `sys/fs.py` | Operaciones síncronas bloqueantes en `dispatch()` (`sta.scan`, `copy_file`) | Media | B |
| 14 | `MEM-01..04` | **Media** | `boot.py`, `sys/server.py`, `sys/kernel.py` | Pool de buffers HTTP, copias WS, `gc.threshold()`, throttling de logs | Baja | B |
| 15 | `ARCH-03,04` | **Media** | `sys/appmgr.py`, `apps/oled_status.py` | Acoplamiento `kernel._tasks` y copia 1 KB/2s en `SSD1306.show()` | Baja | B |
| 16 | `APP-02,03` | **Media** | `sys/appmgr.py` | Compatibilidad v2 de `.mapp.json`, upload HTTP y hook `on_unload(ctx)` | Media | B |
| 17 | `MEM-06` | **Alta (Hipótesis)** | Fase 10 (`sys/mqtt.py`) | Consumo de DRAM interna de mbedTLS durante handshake MQTT-TLS | Media | C |
| 18 | `FE-02..06` | **Baja-Media** | `frontend_src/js/*` | `Function()` en `calc.js`, `ctx.onCleanup`, SDK JS, modales no bloqueantes | Baja-Media | D |

---

## 3. Riesgos Críticos para Fase 10 (MQTT sobre TLS)

1. **Dependencia de autenticación previa (`SEC-01`, `SEC-02`)**: Configurar credenciales de un broker MQTT sin proteger `/config/secrets/` ni autenticar `mqtt.configure` expondría inmediatamente las credenciales del broker AOS IoT. **Recomendación**: implementar primero Etapa A (seguridad base) o al menos proteger `/config/secrets/` y no devolver contraseñas al frontend desde el primer día.
2. **Validación estricta de certificados TLS en MicroPython**:
   - MicroPython `1.29.0` en ESP32 utiliza `mbedTLS` con `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)`, `ctx.verify_mode = ssl.CERT_REQUIRED`, `ctx.load_verify_locations(cadata=...)` y `server_hostname=...` (SNI + validación CN/SAN).
   - **Requisito de reloj (NTP)**: la validación de expiración de certificados X.509 falla si el RTC de la ESP32 está en el año 2000; es obligatorio sincronizar con `ntptime.settime()` (o validar año >= 2025) antes del handshake TLS.
3. **Presupuesto de RAM en perfil `strict`**:
   - En **ESP32-CAM (+PSRAM 4 MB)** hay margen holgado para HTTP + WebSocket + MQTT TLS.
   - En **ESP32-C3 (174 KB)** y especialmente **ESP32 DevKit v1 sin PSRAM (112 KB)**, se debe medir empíricamente el heap antes y después del handshake TLS antes de habilitar MQTT TLS por defecto en `strict`.
4. **Biblioteca MQTT asíncrona**:
   - `umqtt.simple` es síncrono (bloquea el bucle `uasyncio` durante `connect()` y `wait_msg()`). Para convivir con el servidor HTTP/WebSocket sin congelar el escritorio, `sys/mqtt.py` debe implementarse sobre sockets/streams no bloqueantes o corrutina cooperativa `uasyncio` con `ssl.SSLContext.wrap_socket` y manejo propio de paquetes MQTT 3.1.1 (CONNECT, CONNACK, PUBLISH QoS 0/1, PUBACK, SUBSCRIBE, SUBACK, UNSUBSCRIBE, PINGREQ/PINGRESP, DISCONNECT).
