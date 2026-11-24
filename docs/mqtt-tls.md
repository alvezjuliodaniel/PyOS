# MicroOS — MQTT sobre TLS y Puente AOS IoT (Fase 10)

## 1. Arquitectura y Garantías de Seguridad TLS

El cliente MQTT de MicroOS ([sys/mqtt.py](../sys/mqtt.py)) implementa **MQTT 3.1.1** de forma cooperativa sobre `uasyncio` y sockets TCP + TLS **sin fallback inseguro**:

1. **Verificación obligatoria (`ssl.CERT_REQUIRED`)**: utiliza `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` con `verify_mode = ssl.CERT_REQUIRED`, cargando el certificado CA de confianza (`load_verify_locations(cadata=...)`) y validando SNI/hostname mediante `ctx.wrap_socket(raw_sock, server_hostname=host)`.
2. **Prohibido `CERT_NONE`**: si no existe un certificado CA PEM válido cargado, `request_connect()` rechaza la operación inmediatamente.
3. **Sincronización horaria NTP obligatoria**: antes del handshake TLS, verifica que el reloj RTC tenga año ≥ 2025 (invocando `ntptime.settime()` si es necesario) para que mbedTLS valide correctamente la expiración de los certificados X.509.
4. **Separación de secretos**:
   - Configuración no secreta: `/config/mqtt.json`.
   - Credenciales y certificados (`password`, `ca_pem`, `client_cert`, `client_key`): `/config/secrets/mqtt.secret`, bloqueado contra lectura/descarga en [sys/fs.py](../sys/fs.py) (`is_secret_path`).
   - Las contraseñas y claves privadas **nunca** se devuelven al navegador ni se escriben en logs o eventos WebSocket.
5. **Preparado para mTLS**: soporta `load_cert_chain(client_cert, client_key)` para autenticación futura mediante certificados de cliente.

---

## 2. Compatibilidad de Memoria por Perfil

| Perfil | Placas | Comportamiento MQTT TLS |
| :--- | :--- | :--- |
| **`strict`** | ESP32-C3, ESP32 DevKit v1 sin PSRAM | Verifica `gc.mem_free() >= 65536 B` antes del handshake mbedTLS; si no hay margen suficiente rechaza conectar de forma segura sin desactivar TLS. |
| **`standard`** | ESP32-CAM + PSRAM (`~4 MB` heap) | Totalmente operativo junto a HTTP + WebSocket. |
| **`extended`** | ESP32-S3 + PSRAM | Totalmente operativo. |

> **Firmwares verificados**: MicroPython `1.29.0` en ESP32-CAM (`ESP32_GENERIC-SPIRAM`) — verificado soporte real de `ssl.SSLContext`, `ssl.CERT_REQUIRED`, `load_verify_locations`, `load_cert_chain` y `ntptime`.

---

## 3. Esquema de Tópicos AOS IoT

Con `aos_enabled: true` (activo por defecto), MicroOS opera como nodo administrado usando su `client_id` único como `<device_id>`:

| Tópico | Dirección | Descripción |
| :--- | :---: | :--- |
| `aos/devices/<device_id>/telemetry` | Dispositivo → Broker | Publicación periódica (`aos_telemetry_s`) de RAM, Flash, CPU/núcleos, temperatura y Wi-Fi/RSSI. |
| `aos/devices/<device_id>/status` | Dispositivo → Broker | Estado `{"online": true}` al conectar y **Last Will & Testament** `{"online": false}` con `retain=True`. |
| `aos/devices/<device_id>/events` | Dispositivo → Broker | Eventos del sistema. |
| `aos/devices/<device_id>/commands` | Broker → Dispositivo | Comandos autorizados **en whitelist estricta**: `ping`, `stats`, `gpio_get`, `gpio_set` (pines seguros HAL). **Nunca ejecuta código Python por MQTT.** |
| `aos/devices/<device_id>/responses` | Dispositivo → Broker | Respuesta JSON correlacionada por `id`. |

---

## 4. Uso desde MicroOS MQTT Center (`📡`)

1. Abre **Inicio → 📡 MQTT Center** (o el icono del escritorio).
2. En **🔐 Conexión y TLS**: introduce el Host de tu broker AOS IoT, puerto (`8883`), usuario, contraseña y pega o carga el archivo **CA PEM** (`.pem` / `.crt`). Pulsa **Guardar Configuración** y luego **Conectar**.
3. En **📥 Explorador de Tópicos**: suscríbete a tópicos (soporta `+` y `#`), filtra, pausa o limpia el flujo en vivo.
4. En **📤 Publicador**: envía texto o JSON (con validador integrado) en **QoS 0** o **QoS 1** (con confirmación `PUBACK` y reintentos DUP) y opción `Retain`.
5. En **📈 Monitor**: observa mensajes enviados/recibidos, reconexiones, errores TLS, latencia RTT (`PINGREQ/PINGRESP`) y consumo de RAM del handshake TLS.
