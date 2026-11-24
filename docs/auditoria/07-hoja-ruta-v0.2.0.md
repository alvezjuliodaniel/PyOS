# 07 — Hoja de Ruta hacia PyOS v0.2.0 y Plan de Implementación Incremental

> **Enfoque**: Evolución incremental sin reescribir la arquitectura existente, preservando compatibilidad con ESP32-C3 (~100 KB libres), ESP32 DevKit v1 (112 KB), ESP32-CAM (+PSRAM 4 MB) y ESP32-S3.

---

## 1. Hoja de Ruta por Niveles de Prioridad

### Prioridad Crítica (Base obligatoria de v0.2.0 — Bloqueante para exposición en red)
1. **Autenticación y sesiones (`sys/auth.py` + pantalla de Login en el escritorio)**:
   - Hash SHA-256 con *salt* en `/config/secrets/auth.json` (inaccesible por `fs.*` y `/api/fs/*`).
   - Sesiones con token aleatorio de 128 bits (`Cookie: HttpOnly; SameSite=Strict`) validadas en `/ws` y `/api/*`.
   - Asistente de primer arranque o PIN inicial por consola serie / OLED.
2. **Autorización por capacidades en JSON-RPC (`sys/rpc.py`)**:
   - Roles `admin`, `operator`, `viewer` y verificación por capacidad (`term.exec`, `apps.manage`, `fs.write`, `gpio.write`, `mqtt.manage`, etc.).
   - Interruptor `allow_remote_python` para desactivar `term.exec`/`term.run_file` en despliegues productivos.
3. **Endurecimiento de HTTP, WebSocket y VFS (`sys/server.py`, `sys/fs.py`)**:
   - Validación estricta de `Origin` en WebSocket (mitiga CSWSH — `SEC-04`).
   - Timeouts en `readline`, `readexactly` y `async_write_stream` (`ARCH-02`).
   - Límite de longitud de línea en cabeceras HTTP (`431`) y límite de tamaño de subida (`413` + reserva de 64 KB Flash).
   - Validación de `FIN`, fragmentación y máscara obligatoria RFC 6455 (`SEC-07`).
   - Aislamiento de `/config/secrets/*` y protección de escritura sobre `/boot.py`, `/main.py` y `/sys/*` (`SEC-02`).
   - Contraseña WPA2 generada por dispositivo para el AP de respaldo `PyOS-Setup` (`SEC-05`).
4. **Seguridad del Gestor de Paquetes (`sys/appmgr.py`) y Frontend (`FE-01`)**:
   - Corregir *path traversal* en `manifest.ui`/`manifest.backend` confinando a `base=base_dir` (`APP-04`).
   - Instalación atómica en staging con validación de sintaxis y *rollback* (`APP-01`).
   - Escapado HTML (`PyOS.escHtml`) en `wifi.js`, `files.js`, `appstore.js`, `monitor.js`, `wm.js` y `app.js` para erradicar XSS (`FE-01`).

### Prioridad Alta (Estabilidad, Memoria y SDK v0.2.0)
1. **Optimización de memoria y `uasyncio`**:
   - Pool preasignado de buffers HTTP (`MEM-01`), eliminación de copias extra en WebSocket (`MEM-02`), calibración correcta de `gc.threshold()` (`MEM-03`) y cero asignaciones en `SSD1306_I2C.show()` (`ARCH-04`).
   - Soporte `async` en `RPCRouter.dispatch()` y `Kernel.unregister_task()` (`ARCH-01`, `ARCH-03`).
   - Hook `on_unload(ctx)` en `AppManager` (`APP-03`).
2. **SDK JavaScript (`frontend_src/js/sdk.js`)**:
   - `PyOS.defineApp(...)` con declaración de permisos, gestión automática de limpieza (`ctx.onCleanup`), diálogos no bloqueantes (`FE-06`) y eliminación de `Function()` en `calc.js` (`FE-02`).
3. **Batería de pruebas de regresión y burn-in**:
   - `tests/test_security.py`, `tests/test_appmgr.py` y ejecución de `test_stress_c3.py` en ESP32 DevKit v1 y ESP32-CAM.

### Prioridad Media (Capacidades IoT y Fase 10 — MQTT sobre TLS)
1. **Fase 10 — Cliente MQTT sobre TLS (`sys/mqtt.py`) + App `PyOS MQTT Center`**:
   - Cliente MQTT 3.1.1 asíncrono sobre `uasyncio` + `ssl.SSLContext` con `CERT_REQUIRED`, CA confiable, verificación de hostname/SNI y sincronización horaria NTP (`ntptime`) antes de validar certificados.
   - QoS 0 y QoS 1 con `packet_id`, confirmación `PUBACK` y reintentos; reconexión con *backoff* exponencial.
   - Integración con tópicos AOS IoT (`aos/devices/<device_id>/telemetry|status|events|commands|responses`) sin ejecución de Python por MQTT y con *watchdog* local para actuadores.
   - Desactivación automática y segura en perfil `strict` si las mediciones de RAM real muestran que TLS verificado compromete la estabilidad.
2. **Otras utilidades de prioridad media**:
   - Monitor de sensores (I2C/ADC con histórico en `<canvas>`).
   - Programador de tareas (cron ligero sobre `uasyncio` sincronizado con NTP).
   - Reglas de automatización locales (ej. *si ADC > X → activar GPIO / publicar MQTT*).

### Prioridad Futura (v0.3.0+ / Ecosistema AOS)
- Integración nativa con **AOS Control Center** y esquema API compatible con AOS.
- Repositorio remoto de paquetes `.mapp.json` firmados (SHA-256 + firma).
- Administración remota segura mediante túnel/broker MQTT-TLS (sin abrir puertos HTTP en el router).
- Evaluación de soporte **ESP32-P4** cuando MicroPython estabilice su port oficial.

---

## 2. Cambios con Potencial de Ruptura de Compatibilidad y Migración

| Cambio propuesto | Impacto en compatibilidad | Estrategia de migración segura |
| :--- | :--- | :--- |
| Autenticación obligatoria (`SEC-01`) | Los clientes sin cookie/token recibirán `401`. | El escritorio muestra modal de Login automáticamente; primer arranque permite definir contraseña. |
| Bloqueo de lectura de `/config/wifi.json` vía `fs.read` (`SEC-02`) | Ya no se podrá editar `wifi.json` como texto desde el Explorador. | La app **Ajustes Wi-Fi (`📶`)** y **PyOS Studio** gestionan la configuración sin exponer contraseñas. |
| Contraseña WPA2 en AP `PyOS-Setup` (`SEC-05`) | El AP dejará de ser abierto. | Mostrar contraseña generada en consola serie y pantalla OLED SSD1306 al arrancar. |
| Campos nuevos en `.mapp.json` v2 (`APP-02`) | Ninguna ruptura para apps v1. | Paquetes sin `schema_version` se cargan como v1 con aviso de compatibilidad. |

---

## 3. Secuencia Incremental de Implementación y Pruebas de Regresión

| Sprint / Etapa | Alcance | Pruebas de regresión obligatorias |
| :--- | :--- | :--- |
| **Etapa A — Seguridad Crítica Base** | `SEC-01`, `SEC-02`, `SEC-04`, `SEC-09`, `ARCH-02`, `APP-04`, `FE-01` | `test_security.py` (401, Origin, traversal, Slowloris, XSS) + `test_phase3.py` |
| **Etapa B — Estabilidad, Memoria y AppMgr** | `MEM-01..04`, `ARCH-01,03,04`, `SEC-05..08`, `APP-01..03`, `FE-02..03` | `test_hal.py`, `test_phase1.py`, `test_stress_c3.py` (en C3 y DevKit v1) |
| **Etapa C — Fase 10: MQTT sobre TLS + MQTT Center** | `sys/mqtt.py`, `config/mqtt.json`, `frontend_src/js/apps/mqtt.js`, `docs/mqtt-tls.md` | `test_mqtt.py` (18 pruebas TLS/QoS/reconexión + medición heap TLS) |
| **Etapa D — SDK JS y Validación 24/48/72 h** | `frontend_src/js/sdk.js`, pruebas prolongadas en hardware real | Burn-in 24/48/72 h en ESP32-CAM y ESP32-C3 |

---

## 4. Criterios de Aceptación para PyOS v0.2.0

1. Cero vulnerabilidades críticas abiertas (`SEC-01` a `SEC-04`, `APP-04`, `FE-01` resueltas y verificadas con pruebas).
2. Ninguna credencial (Wi-Fi, Auth, MQTT) legible desde el navegador, logs o respuestas HTTP/RPC.
3. Estabilidad verificada en hardware real en **ESP32-C3**, **ESP32 DevKit v1** y **ESP32-CAM + PSRAM** manteniendo > 50 KB libres en `strict` bajo estrés HTTP+WS.
4. Todas las apps nativas existentes y el ejemplo `termometro.mapp.json` operando sin regresiones.
5. Tamaño total del frontend `/www/*.gz` ≤ 50 KB (manteniendo tiempo de carga inicial < 1 s en ESP32-C3).
