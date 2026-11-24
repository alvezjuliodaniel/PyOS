# 05 — Auditoría del Gestor de Aplicaciones y Diseño del SDK (MicroOS v0.1.0)

> **Alcance**: Inspección de `sys/appmgr.py`, `sys/server.py` (`/api/apps/*`), `frontend_src/js/apps/appstore.js`, `frontend_src/js/app.js` (`syncDynamicApps`) y `examples/termometro.mapp.json`.

---

## 1. Estado Actual del Gestor de Aplicaciones

`AppManager` (`sys/appmgr.py`) permite descubrir, instalar, recargar en caliente y desinstalar apps bajo `/apps/<id>/` sin modificar el firmware ni reiniciar la placa:
- **Fortalezas verificadas**:
  - Validación de `app_id` (2–24 caracteres `[a-z0-9_-]`) y lista negra `_RESERVED_IDS` (`sys/appmgr.py:19-30`).
  - Ejecución de `backend.py` en diccionario `ns` aislado (sin contaminar `sys.modules`), facilitando la liberación de RAM en `unload_app()` (`ns.clear(); gc.collect()`).
  - Servicio dinámico de `ui.js` desde `/api/apps/<id>/ui.js` con `Cache-Control: no-cache` y soporte opcional `.js.gz`.
  - Sincronización en tiempo real en el escritorio y Menú Inicio vía evento `apps_changed`.

---

## 2. Hallazgos del Gestor de Aplicaciones

### `APP-01` — Instalación y actualización no atómicas (sin staging ni rollback)
- **Severidad**: **Alta**
- **Archivo y líneas**: `sys/appmgr.py:175-210` (`install_app`)
- **Descripción técnica**: `install_app()` descarga la versión anterior (`unload_app`), sobrescribe los archivos directamente en `/apps/<id>/` y finalmente invoca `load_app(app_id)`. Si `backend.py` falla al compilarse o ejecutarse, la app queda rota en Flash y la versión anterior funcional se ha perdido.
- **Evidencia concreta**: `sys/appmgr.py:195-207`.
- **Impacto**: Pérdida de funcionalidad en actualizaciones fallidas y restos de archivos en Flash.
- **Recomendación**: Escribir primero en `/apps/.staging_<id>/`, validar `manifest.json`, tamaño máximo y compilación (`compile(code, be_path, "exec")`), mover `/apps/<id>` → `/apps/.bak_<id>`, mover staging → `/apps/<id>`, cargar y solo entonces borrar `.bak_<id>` (o revertir si falla).
- **Dificultad estimada**: Media.
- **Forma de verificar**: Actualizar una app funcional con un `.mapp.json` que contenga un `SyntaxError` en `backend` y comprobar que el sistema restaura automáticamente la versión previa.

---

### `APP-02` — Sin validación de tamaño máximo de paquete, versión mínima de OS ni compatibilidad de placa
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/appmgr.py:175-194`, `frontend_src/js/apps/appstore.js:183-193`
- **Descripción técnica**: `manifest.json` no declara `schema_version`, `min_os_version`, `supported_boards`, `permissions` ni límites de tamaño para `ui` y `backend`. En ESP32-C3 (o ESP32 sin PSRAM), un `.mapp.json` de 30 KB enviado por WebSocket excede `_MAX_WS_PAYLOAD` (8192 B) y falla sin un canal de instalación por streaming HTTP.
- **Evidencia concreta**: `sys/server.py:33`: `_MAX_WS_PAYLOAD = const(8192)`; `sys/appmgr.py:201-204`: escribe `ui_code` y `backend_code` sin comprobar límites ni Flash libre.
- **Impacto**: No se pueden instalar apps mayores a ~7 KB por WebSocket, ni advertir si una app requiere PSRAM o una placa específica (ej. cámara en ESP32-CAM).
- **Recomendación**:
  1. Mantener **100% compatibilidad hacia atrás** con paquetes `.mapp.json` v1 (sin `schema_version` se asumen `schema_version: 1`).
  2. Añadir campos opcionales en v2: `schema_version: 2`, `min_os_version`, `requires_psram`, `permissions: [...]`, `sha256`.
  3. Añadir endpoint HTTP streaming `POST /api/apps/install` para paquetes mayores a 8 KB.
- **Dificultad estimada**: Media.
- **Forma de verificar**: Instalar un paquete v1 (`examples/termometro.mapp.json`) y un paquete v2 con `requires_psram: true` en ESP32-C3 (debe ser rechazado con error descriptivo).

---

### `APP-03` — Tareas `autostart` o recursos abiertos por `backend.py` sin hook `on_unload(ctx)`
- **Severidad**: **Media**
- **Archivo y líneas**: `sys/appmgr.py:156-173` (`unload_app`)
- **Descripción técnica**: Si una app dinámica configuró pines GPIO/PWM, suscribió un callback en `kernel.subscribe(...)` o abrió un socket, `unload_app()` detiene la tarea `app_<id>` y limpia `ns`, pero **no invoca ningún callback de limpieza de la app** (ej. `on_unload(ctx)`), pudiendo dejar suscriptores huérfanos en `kernel._subscribers` o canales PWM bloqueados.
- **Evidencia concreta**: `sys/appmgr.py:156-173`: no busca ni invoca `ns.get("on_unload")`.
- **Impacto**: Fugas de suscripciones en el bus de eventos del Kernel y canales LEDC/GPIO ocupados tras desinstalar una app.
- **Recomendación**: Soportar un hook opcional `def on_unload(ctx):` en `backend.py` y llamarlo dentro de `try/except` antes de `ns.clear()` en `unload_app()`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Instalar una app que registre un suscriptor en el kernel y libere el recurso en `on_unload(ctx)`; tras desinstalarla, comprobar que `len(kernel._subscribers)` regresa al valor inicial.

---

### `APP-04` — Falta validación de `manifest["ui"]` y `manifest["backend"]` contra *path traversal*
- **Severidad**: **Alta**
- **Archivo y líneas**: `sys/appmgr.py:93-95`, `sys/appmgr.py:124-125`, `sys/appmgr.py:201-204`
- **Descripción técnica**: `install_app()` usa `base_dir + "/" + manifest["ui"]` y `base_dir + "/" + manifest["backend"]` pasando `base="/"` a `fs.write_text()`. Si un manifiesto malicioso especifica `"ui": "../../boot.py"`, `fs.safe_path("/apps/evil/../../boot.py", "/")` se resuelve a `"/boot.py"` y **sobrescribe `/boot.py`**!
- **Evidencia concreta**: `sys/appmgr.py:202`: `fs.write_text(base_dir + "/" + manifest["ui"], ui_code, "/")` — el parámetro `base` es `"/"` en lugar de `base_dir`!
- **Impacto**: Escape de directorio durante la instalación de un paquete `.mapp.json` malicioso, permitiendo sobrescribir cualquier archivo del sistema (`/boot.py`, `/main.py`, `/sys/*.py`).
- **Recomendación**: Pasar siempre `base=base_dir` en `fs.write_text(..., base=base_dir)` y `fs.read_text(..., base=base_dir)`, y restringir `manifest["ui"]` y `manifest["backend"]` a nombres simples sin `/` ni `..`.
- **Dificultad estimada**: Baja.
- **Forma de verificar**: Intentar `apps.install` con `manifest.ui = "../../boot.py"` y confirmar rechazo con `ValueError: Path traversal bloqueado`.

---

## 3. Propuesta de SDK JavaScript para MicroOS v0.2.0 (Preparado para AOS)

Para estandarizar el desarrollo de apps sin introducir frameworks y preparar compatibilidad futura con **AOS**, se propone exponer un SDK ligero (~1.2 KB gzip) en `frontend_src/js/sdk.js` bajo `MicroOS.sdk` (y en el contexto `ctx.sdk` de cada app):

```javascript
// Ejemplo conceptual de uso en una app v0.2.0 (diseño propuesto, NO implementado aún)
MicroOS.defineApp({
  id: "mi_sensor",
  title: "Monitor Sensor",
  icon: "🌡️",
  permissions: ["sys.read", "gpio.read", "mqtt.pubsub"],
  mount(container, ctx) {
    // API limpia y validada contra permisos declarados
    const info = await ctx.sdk.system.info();
    const files = await ctx.sdk.fs.list("/apps");
    const adc = await ctx.sdk.gpio.adc(4);
    const unsub = ctx.sdk.events.on("sys_stats", (st) => { /* ... */ });
    // Limpieza garantizada automáticamente al cerrar ventana
    ctx.onCleanup(unsub);
  }
});
```

### Módulos del SDK propuesto
- `sdk.system`: `info()`, `stats()`, `tasks()`, `logs()`, `gc()`, `reboot()`
- `sdk.fs`: `list(path)`, `read(path)`, `write(path, content)`, `mkdir(path)`, `remove(path)`, `copy(src, dst)`, `move(src, dst)`
- `sdk.gpio`: `info()`, `read(pin)`, `write(pin, val)`, `toggle(pin)`, `adc(pin)`, `pwm(pin, freq, duty)`
- `sdk.net`: `status()`, `scan()`
- `sdk.mqtt`: `status()`, `publish(topic, payload, opts)`, `subscribe(topic)` (Fase 10)
- `sdk.ui`: `toast(msg, type)`, `escHtml(str)`, `confirm(msg)`, `prompt(msg, defVal)`
- `sdk.events`: `on(event, fn)` con registro automático de limpieza en el ciclo de vida de la ventana.

### Migración sin ruptura
- Las apps actuales que llaman a `MicroOS.wm.registerApp(...)` y `MicroOS.client.call(...)` seguirán funcionando sin cambios en v0.2.0; `MicroOS.defineApp(...)` actuará como capa recomendada con gestión automática de permisos y *cleanup*.
