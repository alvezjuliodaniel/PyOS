# 04 — Auditoría del Escritorio Web / Frontend (MicroOS v0.1.0)

> **Alcance**: Inspección completa de `frontend_src/index.html`, `css/desktop.css`, `js/rpc.js`, `js/wm.js`, `js/app.js` y las 9 aplicaciones nativas (`monitor.js`, `files.js`, `terminal.js`, `gpio.js`, `editor.js`, `calc.js`, `wifi.js`, `controlpanel.js`, `appstore.js`).  
> **Tamaño actual**: `163.5 KB` sin comprimir → **`40.2 KB` gzip** (14 archivos, cero frameworks externos).

---

## 1. Hallazgos del Frontend

### `FE-01` — Vulnerabilidades de XSS almacenado/reflejado por interpolación directa en `innerHTML`
- **Severidad**: **Alta**
- **Archivo y líneas**:
  - `frontend_src/js/apps/files.js:209, 217, 344, 360, 362` (nombres de archivo/carpeta `item.name` y `currentDir`)
  - `frontend_src/js/apps/wifi.js:61, 87` (`st.ssid` y `n.ssid` de redes Wi-Fi cercanas)
  - `frontend_src/js/apps/appstore.js:131-140` (`a.name`, `a.id`, `a.description`, `a.icon` del manifiesto de apps)
  - `frontend_src/js/apps/monitor.js:248` (`t.name` de tareas)
  - `frontend_src/js/app.js:274` y `frontend_src/js/wm.js:102` (`app.title`, `app.icon`)
- **Descripción técnica**: Múltiples vistas construyen HTML interpolando datos no confiables (como un SSID Wi-Fi cercano llamado `<img src=x onerror=alert(1)>` o un nombre de archivo malicioso en LittleFS) directamente en plantillas `innerHTML` sin escapar caracteres HTML (`&`, `<`, `>`, `"`, `'`).
- **Evidencia concreta**:
  - `frontend_src/js/apps/wifi.js:87`: ``row.innerHTML = `<span>${n.auth ? "🔒" : "🔓"} <strong>${n.ssid}</strong></span>...` `` — **un punto de acceso Wi-Fi vecino con un SSID malicioso puede ejecutar JavaScript arbitrario en el navegador del usuario al pulsar "Escanear"**, y desde ahí invocar `MicroOS.client.call("term", "exec", ...)` tomando control total de la ESP32!
  - `frontend_src/js/apps/files.js:344`: ``tr.innerHTML = `<td class="mono">${meta.ico} ${item.name}</td>...` ``.
- **Impacto**: **Ejecución remota de código en la ESP32 vía XSS desde un SSID Wi-Fi externo o nombre de archivo malicioso** (especialmente grave mientras `term.exec` esté disponible en el cliente).
- **Recomendación**: Añadir una utilidad global `MicroOS.escHtml(str)` (6 líneas en `app.js`) o construir nodos dinámicos exclusivamente con `textContent` en `wifi.js`, `files.js`, `appstore.js`, `monitor.js`, `wm.js` y `app.js`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Crear un archivo llamado `<img src=x onerror=alert(1)>.txt` en `/apps` y simular un SSID con etiquetas HTML; abrir Explorador de Archivos y Ajustes Wi-Fi y verificar que se renderiza como texto literal sin ejecutar ningún script.

---

### `FE-02` — Uso de `new Function(...)` en la Calculadora (`calc.js`)
- **Severidad**: **Baja-Media**
- **Archivo y líneas**: `frontend_src/js/apps/calc.js:15`
- **Descripción técnica**: `safeEvalExpr()` valida la expresión con `/^[0-9+\-*/().%]+$/` y luego usa `Function('"use strict"; return (' + clean + ');')()`. Aunque la expresión regular acota bien los caracteres permitidos, el uso de `Function()` impide aplicar una cabecera `Content-Security-Policy` estricta sin `'unsafe-eval'`.
- **Evidencia concreta**: `frontend_src/js/apps/calc.js:15`: `const val = Function(...)()`.
- **Impacto**: Bloquea el endurecimiento futuro mediante CSP (`script-src 'self'`) en v0.2.0.
- **Recomendación**: Reemplazar por un evaluador aritmético *shunting-yard* / descenso recursivo de ~35 líneas en Vanilla JS puro (cero `eval`/`Function`).
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Eliminar `Function(` de `calc.js` y verificar `(2+3)*4`, `10/4`, `15%4`, `x²` y `√`.

---

### `FE-03` — Fuga de *event listeners* globales si una ventana falla al montarse o usa `document.body`
- **Severidad**: **Media**
- **Archivo y líneas**: `frontend_src/js/apps/files.js:574-576, 800-807`, `frontend_src/js/wm.js:210-224`
- **Descripción técnica**:
  1. En `files.js`, `window.addEventListener("mousemove", ...)` y `mouseup`/`keydown`/`click` sí se limpian correctamente en el `return () => { ... }` al cerrar la ventana, **pero** en `wm.js:212-223`, si `app.mount()` lanza una excepción a mitad de ejecución después de registrar un `setInterval` o `addEventListener`, no se ejecuta ningún `cleanup`.
  2. En `app.js:35`, `setInterval(updateClock, 10000)` es permanente (correcto), y en `monitor.js`, `gpio.js`, `calc.js`, `terminal.js` y `appstore.js` todos devuelven función de `cleanup` (verificado en código).
  3. En `wm.js:90-112`, `_renderLaunchers()` reconstruye `innerHTML = ""` del dock y los iconos de escritorio cada vez que se registra/desregistra cualquier app dinámica (funciona bien, sin fugas de listeners porque los nodos anteriores se recolectan).
- **Evidencia concreta**: `frontend_src/js/wm.js:212-224`: `try { const cleanup = app.mount(...); ... } catch (err) { ... }`.
- **Impacto**: En apps de terceros mal escritas que fallen a mitad de `mount()`, podrían quedar `setInterval` o suscriptores `client.on()` activos en segundo plano.
- **Recomendación**: Pasar en el contexto de cada app un `ctx.onCleanup(fn)` / `ctx.setInterval(fn, ms)` / `ctx.onEvent(ev, fn)` gestionado por `WindowManager` para garantizar limpieza automática incluso ante errores parciales.
- **Dificultad estimada**: Baja-Media.
- **Forma de verificar**: Abrir y cerrar 50 veces seguidas `Monitor`, `Explorador`, `Terminal`, `GPIO` y `Calculadora`, verificando en DevTools que el número de *listeners* en `client._listeners` y timers activos regresa al valor base.

---

### `FE-04` — Falta de aislamiento entre aplicaciones JavaScript y el objeto global `window.MicroOS`
- **Severidad**: **Media**
- **Archivo y líneas**: `frontend_src/js/app.js:46-52`, `frontend_src/js/app.js:58-68` (`loadAppScript`)
- **Descripción técnica**: Todas las apps dinámicas cargadas vía `<script src="/api/apps/<id>/ui.js">` se ejecutan en el mismo contexto global `window` con acceso directo a `MicroOS.client` (pudiendo invocar cualquier RPC de cualquier otra app, leer `localStorage` o modificar el DOM de otras ventanas).
- **Evidencia concreta**: `frontend_src/js/app.js:46-52`: expone `client` crudo en `window.MicroOS`.
- **Impacto**: Una app de terceros instalada en `/apps/` puede leer el historial de la terminal en `localStorage` o invocar `term.exec` sin declarar permisos.
- **Recomendación**: Introducir el SDK de aplicaciones (ver `05-auditoria-appmgr.md`) entregando a cada app un `ctx.api` acotado a los permisos declarados en su `manifest.json`, respaldado por validación de capacidades en el backend.
- **Dificultad estimada**: Media.
- **Forma de verificar**: Verificar que una app que solo declara permiso `["sys.read"]` no pueda invocar `gpio.set` ni `fs.rm`.

---

### `FE-05` — Mensajes de error y reconexión WebSocket silenciosos en operaciones en segundo plano
- **Severidad**: **Baja**
- **Archivo y líneas**: `frontend_src/js/rpc.js:110-120`, `frontend_src/js/apps/monitor.js:264`, `frontend_src/js/apps/controlpanel.js:118`
- **Descripción técnica**: Cuando el WebSocket se desconecta y reconecta, `rpc.js` rechaza todas las promesas pendientes correctamente (`_rejectAllPending`), pero las apps ya abiertas (como `Explorador de Archivos` o `Panel de Hardware`) no se re-sincronizan automáticamente al emitirse `ws_open` tras una reconexión.
- **Evidencia concreta**: `frontend_src/js/apps/files.js:801`: solo llama `navigate("/")` al montar; `gpio.js:211`: solo llama `loadInfo()` al montar.
- **Impacto**: Tras un reinicio del ESP32 o microcorte Wi-Fi, las ventanas abiertas pueden quedar mostrando datos obsoletos hasta pulsar "Refrescar".
- **Recomendación**: Permitir que las apps reaccionen al evento `ws_open` para refrescar su estado suavemente al reconectar.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Reiniciar la ESP32 con `Monitor` y `Panel de Hardware` abiertos y verificar que se actualizan solos al reconectar el WebSocket.

---

### `FE-06` — Uso de `alert()`, `confirm()` y `prompt()` nativos del navegador
- **Severidad**: **Baja**
- **Archivo y líneas**: `frontend_src/js/apps/files.js:474, 490, 502, 513, 535`, `frontend_src/js/apps/appstore.js:154`, `frontend_src/js/apps/terminal.js:211`
- **Descripción técnica**: Varias acciones (renombrar, eliminar, propiedades, buscar en `nano`) usan `prompt()`/`confirm()`/`alert()`, que bloquean el hilo principal del navegador (congelando los *heartbeat* y eventos WebSocket mientras el diálogo está abierto).
- **Evidencia concreta**: `frontend_src/js/apps/files.js:474`: `const newName = prompt("Nuevo nombre:", it.name);`.
- **Impacto**: Si un usuario deja abierto un `prompt()` más de unos segundos, pueden acumularse frames WebSocket o parecer que el escritorio se congeló.
- **Recomendación**: Crear un pequeño modal no bloqueante reutilizable `MicroOS.dialog` (~40 líneas Vanilla JS) para reemplazar `prompt`/`confirm`/`alert` en v0.2.0.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Abrir diálogo de renombrado en Explorador de Archivos y comprobar que el reloj y el Monitor del Sistema siguen actualizándose en segundo plano.
