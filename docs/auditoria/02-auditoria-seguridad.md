# 02 — Auditoría de Seguridad (MicroOS v0.1.0)

> **Prioridad**: Crítica.  
> **Metodología**: Inspección directa de `sys/server.py`, `sys/rpc.py`, `sys/fs.py`, `sys/net.py`, `sys/appmgr.py`, `config/wifi.json` y `frontend_src/`.  
> **Nota**: MicroOS v0.1.0 fue construido como prototipo funcional en red local; **NO debe exponerse a redes no confiables ni a Internet sin resolver los hallazgos críticos de este documento**.

---

## 1. Hallazgos Críticos

### `SEC-01` — Ausencia total de autenticación y autorización en HTTP, WebSocket y JSON-RPC
- **Severidad**: **Crítica**
- **Archivo y líneas**: `sys/server.py:424-483` (`_handle_websocket`), `sys/server.py:328-395` (`_handle_download`, `_handle_upload`), `sys/rpc.py:75-97` (`dispatch`)
- **Descripción técnica**: Cualquier dispositivo en la misma red Wi-Fi (o conectado al AP abierto `MicroOS-Setup`) puede conectarse a `ws://<ip>/ws` o llamar a `/api/fs/upload` y `/api/fs/download` sin token, sesión ni contraseña, obteniendo control total del microcontrolador (ejecución de código Python, lectura/escritura de cualquier archivo, control GPIO/PWM y reinicio).
- **Evidencia concreta**: `sys/server.py:424-441` completa el handshake WebSocket verificando únicamente la presencia de `sec-websocket-key`; `sys/rpc.py:75-97` despacha cualquier `app`/`cmd` sin validar identidad ni permisos.
- **Impacto**: Compromiso total del dispositivo (*Remote Code Execution*, robo de credenciales Wi-Fi, manipulación física de actuadores GPIO/PWM, destrucción del sistema de archivos).
- **Recomendación**: Implementar autenticación obligatoria en v0.2.0:
  - Contraseña de administrador almacenada como hash con *salt* (`hashlib.sha256` + *salt* aleatorio en `/config/auth.json`, inaccesible por HTTP/FS público).
  - Endpoint `POST /api/auth/login` que emite un token de sesión aleatorio (128 bits vía `os.urandom(16)`), validado por cookie `HttpOnly; SameSite=Strict` o cabecera/query de handshake en `/ws` y en `/api/fs/*`.
  - En primer arranque sin contraseña configurada, forzar asistente de configuración inicial o PIN impreso por consola serie / OLED.
- **Dificultad estimada**: Media.
- **Forma de verificar**: Intentar conectar a `/ws`, `GET /api/fs/download?path=/config/wifi.json` y `POST /api/fs/upload` sin cookie/token válido; todos deben responder `401 Unauthorized` o cerrar el WebSocket con código `4001`.

---

### `SEC-02` — Exposición directa de credenciales Wi-Fi en texto plano vía `fs.read` y `/api/fs/download`
- **Severidad**: **Crítica**
- **Archivo y líneas**: `sys/fs.py:25` (`_PROTECTED_PATHS`), `sys/fs.py:218-236` (`read_text`), `sys/server.py:328-364` (`_handle_download`), `config/wifi.json:1-17`
- **Descripción técnica**: `_PROTECTED_PATHS` solo protege contra **borrado y renombrado** (`remove`/`rename`), pero **NO bloquea la lectura ni la sobrescritura**. Cualquier cliente puede leer `/config/wifi.json` (con la contraseña Wi-Fi en texto plano) mediante `fs.read`, `GET /api/fs/download?path=/config/wifi.json` o `cat /config/wifi.json` en la Terminal, e incluso sobrescribir `/boot.py`, `/main.py` o `/sys/server.py` mediante `fs.write` o `POST /api/fs/upload`.
- **Evidencia concreta**: `sys/fs.py:25`: `_PROTECTED_PATHS = frozenset(("/", "/sys", "/config", "/boot.py", "/main.py"))` — no se consulta en `read_text()`, `write_text()`, `async_write_stream()` ni `_handle_download()`.
- **Impacto**: Robo inmediato de credenciales Wi-Fi (y futuras credenciales MQTT/auth) y persistencia maliciosa sobrescribiendo `/boot.py` o `/sys/*.py`.
- **Recomendación**:
  1. Separar secretos en `/config/secrets/` (o prefijo `.secret`) con bloqueo duro de lectura/descarga desde `fs.*` y `/api/fs/*`.
  2. Proteger `/boot.py`, `/main.py`, `/sys/*` y `/config/auth.json` contra escritura desde APIs de usuario salvo modo administrador explícito.
  3. En `net.status` / `wifi.json` API nunca devolver el campo `password` al frontend (solo `"has_password": true`).
- **Dificultad estimada**: Baja-Media.
- **Forma de verificar**: Solicitar `fs.read("/config/wifi.json")` y `GET /api/fs/download?path=/config/wifi.json` y confirmar rechazo `403 Forbidden`; confirmar que `POST /api/fs/upload?path=/boot.py` devuelve `403 Forbidden`.

---

### `SEC-03` — Ejecución arbitraria de código Python sin aislamiento (`term.exec`, `term.run_file`, `appmgr`)
- **Severidad**: **Crítica**
- **Archivo y líneas**: `sys/rpc.py:283` (`exec(code, env, env)`), `sys/rpc.py:309-314` (`eval`/`exec`), `sys/appmgr.py:131` (`exec(code, ns, ns)`)
- **Descripción técnica**: `term.exec`, `term.run_file` y el backend Python de paquetes `.mapp.json` ejecutan código MicroPython con `exec()`/`eval()` dentro del mismo proceso e intérprete que el Kernel. MicroPython **NO dispone de sandbox de seguridad a nivel de lenguaje**: cualquier script puede importar `machine`, `os`, `network` o mutar módulos globales en `sys.modules`.
- **Evidencia concreta**: `sys/rpc.py:309-314`: `val = eval(code, self._term_env, self._term_env)` / `exec(code, self._term_env, self._term_env)`; `sys/appmgr.py:131`: `exec(code, ns, ns)`.
- **Impacto**: Cualquier app instalada o comando en terminal tiene privilegios equivalentes a `root` en el microcontrolador.
- **Recomendación — Modelo de Confianza Explícito**:
  1. **No pretender un sandbox imposible en MicroPython**: documentar explícitamente que el código Python ejecutado en la placa tiene confianza total (*trusted*).
  2. Restringir `term.exec`, `term.run_file` y `apps.install` (cuando incluya `backend.py`) exclusivamente al rol **`admin`** autenticado y con confirmación explícita del usuario.
  3. Diferenciar **Apps solo UI (`ui.js`)** (sin código Python en el MCU, operando solo vía RPC con permisos acotados) de **Apps con Backend Python (`backend.py`)** (marcadas como *"Privilegiadas / Código nativo en placa"*).
  4. Añadir un interruptor de configuración (`allow_remote_python: true/false` en `/config/system.json`) para entornos de producción donde deba deshabilitarse la ejecución remota de Python.
- **Dificultad estimada**: Media.
- **Forma de verificar**: Con `allow_remote_python: false` o sin sesión `admin`, verificar que `term.exec`, `term.run_file` y `apps.install` con `backend` sean rechazados con `PermissionError`.

---

### `SEC-04` — WebSocket sin validación de `Origin` (vulnerable a Cross-Site WebSocket Hijacking — CSWSH)
- **Severidad**: **Crítica**
- **Archivo y líneas**: `sys/server.py:424-441` (`_handle_websocket`)
- **Descripción técnica**: Los navegadores no aplican CORS estándar a las conexiones WebSocket; en su lugar envían la cabecera `Origin`. `sys/server.py` ignora completamente `headers.get("origin")`. Si un usuario visita una página web maliciosa en otra pestaña mientras está en la misma LAN que su ESP32 (`http://microos.local` o `http://192.168.x.x`), esa página externa puede abrir `new WebSocket("ws://microos.local/ws")` y ejecutar `term.exec`, `gpio.set` o `sys.reboot` en silencio.
- **Evidencia concreta**: `sys/server.py:424-441`: solo lee `headers.get("sec-websocket-key")`; no existe ninguna referencia a `"origin"` en todo `sys/server.py`.
- **Impacto**: Control remoto total del ESP32 desde cualquier sitio web externo visitado por el usuario en su LAN.
- **Recomendación**: Validar que `Origin` (si está presente) coincida exactamente con `http://<Host>` (o `https://<Host>`) de la propia placa; rechazar con `403 Forbidden` cualquier otro origen, combinado con token de sesión obligatorio (`SEC-01`).
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Enviar un handshake WebSocket con `Origin: http://evil.example` hacia `/ws` y comprobar respuesta `403 Forbidden`.

---

## 2. Hallazgos Altos

### `SEC-05` — Modo AP de respaldo `MicroOS-Setup` abierto (sin contraseña WPA2)
- **Severidad**: **Alta**
- **Archivo y líneas**: `config/wifi.json:10-16`, `sys/net.py:149-155`
- **Descripción técnica**: Por defecto `ap.password` es `""`, por lo que `_start_ap()` levanta una red abierta `AUTH_OPEN` sin cifrado. Cualquier persona dentro del alcance Wi-Fi puede conectarse y tomar control del dispositivo.
- **Evidencia concreta**: `config/wifi.json:11`: `"password": ""`; `sys/net.py:150`: `auth = getattr(network, "AUTH_OPEN", 0)`.
- **Impacto**: Acceso físico/inalámbrico no autorizado durante el aprovisionamiento o cuando cae el router doméstico.
- **Recomendación**: Generar en primer arranque una contraseña WPA2 única por placa derivada de la MAC o `os.urandom` (ej. 8–12 caracteres alfanuméricos mostrados por consola serie y pantalla OLED), o exigir contraseña WPA2 obligatoria en el AP de respaldo.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Arrancar sin credenciales STA y verificar que el AP se anuncia con `AUTH_WPA_WPA2_PSK` y contraseña única no vacía.

---

### `SEC-06` — `POST /api/fs/upload` sin límite máximo de tamaño (`Content-Length`) ni validación de espacio libre
- **Severidad**: **Alta**
- **Archivo y líneas**: `sys/server.py:366-395` (`_handle_upload`), `sys/fs.py:195-214` (`async_write_stream`)
- **Descripción técnica**: `_handle_upload` acepta cualquier `Content-Length` positivo sin comprobar espacio libre en LittleFS ni un límite máximo, y si `Content-Length` es negativo o ausente crea archivos vacíos arbitrarios. Un cliente puede llenar el 100% de la Flash LittleFS impidiendo guardar configuraciones o logs.
- **Evidencia concreta**: `sys/server.py:372-383`: parsea `content_len` sin límite superior ni chequeo contra `hal.get_flash_stats("/")["free"]`.
- **Impacto**: Denegación de servicio por agotamiento de Flash (`ENOSPC`) y posible corrupción de estado.
- **Recomendación**: Validar `0 < content_len <= max_upload_bytes` (ej. `262144` B o 50% de Flash libre, manteniendo al menos `64 KB` de reserva en LittleFS) y devolver `413 Payload Too Large` si se excede.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Enviar `POST /api/fs/upload?path=/apps/big.bin` con `Content-Length: 5000000` y confirmar rechazo `413 Payload Too Large` sin escribir en Flash.

---

### `SEC-07` — WebSocket no valida bit `FIN`, fragmentación ni obligación de máscara cliente→servidor (RFC 6455)
- **Severidad**: **Alta**
- **Archivo y líneas**: `sys/server.py:92-127` (`_ws_read_frame`), `sys/server.py:451-485` (`_handle_websocket`)
- **Descripción técnica**:
  1. `_ws_read_frame()` ignora el bit `FIN` (`b0 & 0x80`) y los frames de continuación (`opcode == 0x0`). Si un navegador fragmenta un mensaje JSON-RPC grande (ej. instalación de un `.mapp.json`), el parser falla con `JSON invalido` o descarta fragmentos.
  2. RFC 6455 §5.1 exige cerrar la conexión con código `1002` si un cliente envía un frame **sin máscara** (`masked == False`); actualmente se acepta sin máscara.
  3. Un `length` de 64 bits (`length == 127`) se valida contra `_MAX_WS_PAYLOAD` (8192 B), lo cual es bueno, pero el payload se acumula como `bytearray` + `bytes(data)` duplicando temporalmente 8 KB en RAM (`sys/server.py:114, 126`).
- **Evidencia concreta**: `sys/server.py:97`: `opcode = b0 & 0x0F` (no lee `b0 & 0x80`); `sys/server.py:111`: acepta `mask_key = None`.
- **Impacto**: Fallos al instalar apps `.mapp.json` fragmentadas por proxies/navegadores y no conformidad estricta con RFC 6455.
- **Recomendación**: Requerir `masked == True` (si no, enviar Close `1002`), ensamblar frames fragmentados acotados por `_MAX_WS_PAYLOAD`, y decodificar directamente `data.decode("utf-8")` sin copiar a `bytes(data)`.
- **Dificultad estimada**: Baja-Media.
- **Forma de verificar**: Enviar un mensaje JSON-RPC dividido en 2 frames RFC 6455 (`FIN=0` + `opcode=0, FIN=1`) y confirmar respuesta válida; enviar un frame sin máscara y confirmar cierre `1002`.

---

## 3. Hallazgos Medios

### `SEC-08` — Sin verificación de integridad ni rollback en la instalación de paquetes `.mapp.json`
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/appmgr.py:175-210` (`install_app`), `sys/appmgr.py:112-154` (`load_app`)
- **Descripción técnica**: `install_app()` sobrescribe directamente `/apps/<id>/` y luego llama `load_app(app_id)`. Si `backend.py` tiene un error de sintaxis o excepción al cargar, la app queda parcialmente instalada en Flash y la versión anterior (si era una actualización) se pierde sin *rollback*. Tampoco existe verificación de hash SHA-256 ni tamaño máximo de `ui.js` / `backend.py` antes de escribir.
- **Evidencia concreta**: `sys/appmgr.py:199-207`: escribe archivos en `base_dir` antes de validar la carga.
- **Impacto**: Actualizaciones fallidas rompen apps instaladas; paquetes malformados pueden llenar Flash.
- **Recomendación**: Instalar primero en `/apps/.tmp_<id>/`, validar sintaxis/manifest y tamaño máximo, respaldar la versión previa y hacer intercambio atómico con *rollback* si `load_app` falla; añadir campo opcional `sha256` en `manifest.json`.
- **Dificultad estimada**: Media.
- **Forma de verificar**: Actualizar una app existente con un `backend.py` con `SyntaxError` y verificar que la versión anterior sigue operativa tras el rechazo.

---

### `SEC-09` — Cabeceras HTTP sin límite de longitud de línea (`reader.readline()`)
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/server.py:514`, `sys/server.py:523`
- **Descripción técnica**: Aunque existe `_MAX_HEADERS = 24`, cada llamada a `reader.readline()` no tiene tope de bytes. Un atacante puede enviar una única línea de 64 KB sin `\n` y provocar `MemoryError` en ESP32-C3 o ESP32 sin PSRAM.
- **Evidencia concreta**: `sys/server.py:514`: `req_line = await reader.readline()`; `sys/server.py:523`: `line = await reader.readline()`.
- **Impacto**: Agotamiento instantáneo de RAM (*DoS*) con una sola conexión TCP.
- **Recomendación**: Implementar `_read_line_limited(reader, max_len=1024)` leyendo en bloques acotados y devolviendo `431 Request Header Fields Too Large` si se supera.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Enviar una petición HTTP con una cabecera de 4 KB sin `\n` y verificar respuesta `431` sin caída de RAM.

---

### `SEC-10` — Inyección de cabeceras en `Content-Disposition` de `/api/fs/download`
- **Severidad**: **Baja-Media**
- **Archivo y líneas**: `sys/server.py:345-353`
- **Descripción técnica**: `fname = full.rsplit("/", 1)[-1]` se interpola directamente en `Content-Disposition: attachment; filename="{}"` sin sanear comillas ni `\r\n`.
- **Evidencia concreta**: `sys/server.py:352`: `.format(fname, info["size"])`.
- **Impacto**: Nombres de archivo con `"` o caracteres de control pueden malformar la cabecera HTTP.
- **Recomendación**: Sanear `fname` eliminando `"`, `\r`, `\n` y caracteres no ASCII.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Descargar un archivo con nombre seguro y comprobar que caracteres de control son filtrados.

---

## 4. Diseño Propuesto de Autorización por Capacidades (v0.2.0)

| Capacidad | Operaciones RPC / HTTP cubiertas | Rol mínimo |
| :--- | :--- | :--- |
| `sys.read` | `sys.info`, `sys.stats`, `sys.tasks`, `sys.logs`, `net.status` | `viewer` |
| `sys.admin` | `sys.gc`, `sys.task_restart`, `sys.reboot`, `net.connect`, `net.scan` | `admin` |
| `gpio.read` | `gpio.info`, `gpio.get`, `gpio.adc` | `operator` |
| `gpio.write` | `gpio.set`, `gpio.toggle`, `gpio.config`, `gpio.pwm`, `gpio.pwm_stop`, `gpio.release` | `operator` |
| `fs.read` | `fs.ls`, `fs.read`, `GET /api/fs/download` (excluyendo `/config/secrets/*`) | `operator` |
| `fs.write` | `fs.write`, `fs.mkdir`, `fs.rm`, `fs.mv`, `fs.cp`, `POST /api/fs/upload` (excluyendo `/sys/*`, `/boot.py`, `/main.py`) | `admin` |
| `apps.manage` | `apps.install`, `apps.uninstall`, `apps.reload` | `admin` |
| `term.exec` | `term.exec`, `term.run_file` | `admin` (+ `allow_remote_python`) |
| `mqtt.manage` | `mqtt.configure`, `mqtt.connect`, `mqtt.disconnect` | `admin` |
| `mqtt.pubsub` | `mqtt.publish`, `mqtt.subscribe`, `mqtt.unsubscribe`, `mqtt.status` | `operator` |

---

## 5. Evaluación Realista de TLS / HTTPS en MicroOS

- **Servidor HTTPS en la ESP32**: **NO recomendado como obligatorio en perfil `strict`** (ESP32-C3 / ESP32 sin PSRAM). Cada sesión TLS entrante con mbedTLS consume entre **35 KB y 50 KB de RAM interna** (buffers SSL de 16 KB + estado de handshake), lo que con 2 conexiones simultáneas agotaría el heap de 112–174 KB. Además los certificados autofirmados generan advertencias de navegador en redes LAN locales.
- **Cliente TLS saliente (ej. MQTT sobre TLS puerto 8883 — Fase 10)**: Una **única** sesión TLS cliente saliente es viable en perfil `standard` (ESP32 con PSRAM ~4 MB) y `extended` (ESP32-S3), y **debe medirse en hardware real** en perfil `strict` (ver documento `03-auditoria-memoria.md` y evaluación de Fase 10).
