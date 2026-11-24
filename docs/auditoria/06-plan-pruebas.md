# 06 — Plan de Pruebas y Validación de Estabilidad (MicroOS v0.1.0 → v0.2.0)

> **Principio rector**: Distinguir estrictamente entre pruebas **simuladas en host**, **automatizadas en placa** y **burn-in prolongado en hardware real**. Ninguna prueba prolongada (24/48/72 h) se declara superada hasta completarla físicamente y registrar su evidencia.

---

## 1. Inventario de Pruebas Existentes

| Script | Tipo | Estado real verificado | Cobertura |
| :--- | :--- | :--- | :--- |
| `tests/test_hal.py` | Hardware real | **Ejecutado y PASS** en ESP32-C3 | Autodetección, pines prohibidos, GPIO, PWM, ADC1, telemetría. |
| `tests/test_phase1.py` | Hardware real | **Ejecutado y PASS** en ESP32-C3 | Anti *path-traversal*, streaming por bloques, supervisor con 2 fallos y reinicio. |
| `tests/test_phase2.py` | Hardware real | **Ejecutado y PASS** en ESP32-C3 | AP de respaldo `MicroOS-Setup`, hostname `microos`, escaneo Wi-Fi. |
| `tests/test_phase3.py` | Hardware real (loopback) | **Ejecutado y PASS** en ESP32-C3 | HTTP `.gz`, upload/download streaming, WS RFC 6455, JSON-RPC, push `sys_stats`. |
| `tests/test_stress_c3.py` | Hardware real (estrés corto) | **Ejecutado y PASS** en ESP32-C3 | 18 peticiones HTTP `.gz` + 20 RPCs WS; `ram_min: 72,528 B`, `ram_end: 108,736 B`. |

### Brechas actuales de cobertura
- No existen pruebas automatizadas de seguridad (`401`, `Origin` inválido, bloqueo de `/config/wifi.json`, `manifest.ui` con `../`).
- No existe prueba de estrés ejecutada aún en **ESP32 DevKit v1 (112 KB heap)** ni en **ESP32-S3**.
- No existen pruebas de estabilidad continua de **24 h / 48 h / 72 h** ni pruebas automatizadas de frontend (apertura/cierre repetido de ventanas y fugas de *listeners*).
- No existe aún `tests/test_mqtt.py` (requerido para Fase 10).

---

## 2. Matriz de Pruebas de Estabilidad (12 Escenarios Obligatorios)

| # | Escenario | Tipo | Procedimiento | Criterio de aceptación |
| :-: | :--- | :---: | :--- | :--- |
| 1 | **Arranque repetido** | Hardware real | 20 ciclos de `soft-reset` y `hard-reset` automáticos. | 20/20 arranques completos sin `OSError` ni corrupción de LittleFS. |
| 2 | **Reconexión Wi-Fi** | Hardware real | Apagar router STA 60 s y volver a encenderlo. | Levanta AP de respaldo tras timeout y vuelve a STA automáticamente apagando AP. |
| 3 | **Corte en mitad de operación** | Hardware real | Cerrar socket TCP abruptamente a mitad de `POST /api/fs/upload` y en medio de un frame WS. | `_active_conns` vuelve a `0`, archivo temporal limpiado, sin crash ni fuga. |
| 4 | **Apertura/cierre repetido de ventanas** | Automatizada (navegador) | Script JS que abre y cierra 50 veces cada una de las 9 apps. | `client._listeners` y timers activos regresan al valor inicial; heap JS estable. |
| 5 | **Instalar/desinstalar paquetes** | Hardware real | 15 ciclos de `apps.install` → llamada RPC → `apps.uninstall` + 1 paquete con error de sintaxis. | Delta de RAM libre < 1 KB tras 15 ciclos; rollback íntegro ante error. |
| 6 | **Transferencias grandes** | Hardware real | Subir y descargar un archivo de 128 KB vía `/api/fs/upload` y `/download` en perfil `strict`. | SHA-256 idéntico; RAM libre nunca cae debajo de `ram_crit_bytes`. |
| 7 | **Scripts con errores** | Hardware real | Ejecutar `term.run_file` con `SyntaxError`, `ZeroDivisionError`, `MemoryError` simulado. | Error reportado limpiamente por JSON-RPC; Kernel y HTTP siguen operativos. |
| 8 | **Consumo prolongado de memoria** | Hardware real | Polling `sys_stats` cada 2 s + 1 petición HTTP cada 30 s durante 6 h. | Pendiente de RAM libre ≈ 0 B/hora (sin fuga progresiva). |
| 9 | **Clientes WebSocket simultáneos** | Hardware real | Conectar 3 clientes en `strict` (5 en `standard`) enviando RPCs concurrentes + un 4.º excedente. | Los permitidos responden OK; el excedente recibe `503` sin tumbar a los activos. |
| 10 | **Reinicio de servicios** | Hardware real | Invocar `sys.task_restart` sobre `http_server` y `net_manager` y forzar excepción en tarea crítica. | Supervisor reinicia la tarea y los clientes reconectan automáticamente. |
| 11 | **Recuperación tras errores HTTP/WS** | Automatizada | Enviar cabeceras de 4 KB sin `\n`, conexiones Slowloris sin datos, frames WS sin máscara y JSON roto. | Respuestas `431`/timeout/`1002`/`ok:false` sin afectar a clientes legítimos. |
| 12 | **Burn-in continuo 24 / 48 / 72 h** | Hardware real (pendiente) | Dispositivo alimentado en red doméstica con 1 cliente WS + MQTT TLS (Fase 10) registrando telemetría externa cada 5 min. | **Pendiente de ejecución física**: 0 reinicios no planeados (`uptime_s` continuo), RAM estable, respuesta HTTP < 500 ms al finalizar 72 h. |

---

## 3. Batería Específica de Fase 10 (MQTT sobre TLS)

Antes de dar por cerrada la Fase 10 se deberán ejecutar y documentar las **18 pruebas obligatorias** en `tests/test_mqtt.py` (conexión TLS válida, rechazo de CA no confiable, rechazo de hostname incorrecto, certificado caducado, credenciales inválidas, QoS 0, QoS 1 con PUBACK/reintento, suscripción/recepción, desconexión de broker, pérdida de Wi-Fi, reconexión backoff, reinicio, presión de RAM, payload excedido, múltiples suscripciones, operaciones sin permiso, convivencia HTTP+WS+MQTT y estabilidad prolongada), registrando el heap libre antes y después del handshake TLS en cada placa probada.
