# PyOS — Minisistema Operativo Web en MicroPython

Minisistema operativo web ultraligero para **ESP32-C3**, **ESP32-S (Clásico)** y **ESP32-S3**. El microcontrolador actúa como kernel cooperativo (`uasyncio`), gestor de hardware (HAL) y servidor HTTP/WebSocket por streaming; el navegador renderiza el escritorio completo en HTML5, CSS3 y Vanilla JS puro.

## Estructura del Proyecto

```text
PyOS/
├── boot.py             # Rutas de importación, GC temprano y autodetección de placa
├── main.py             # Arranque del Kernel uasyncio (net_manager + http_server)
├── config/
│   ├── boards.json     # Perfiles de pines seguros y límites (C3 / S / S3)
│   ├── system.json     # Hostname (pyos), versión e intervalo de telemetría
│   └── wifi.json       # Credenciales STA y configuración del AP de respaldo
├── sys/
│   ├── hal.py          # Capa de Abstracción de Hardware (GPIO, ADC1, PWM, telemetría)
│   ├── fs.py           # Utilidades seguras LittleFS (anti path-traversal, streaming)
│   ├── kernel.py       # Task Manager con supervisor, GC Watchdog y bus de eventos
│   ├── net.py          # Wi-Fi dual STA + AP (PyOS-Setup) con reconexión asíncrona
│   ├── rpc.py          # Enrutador JSON-RPC (sys, gpio, fs, net, term)
│   └── server.py       # Servidor HTTP (.gz en chunks) + WebSocket RFC 6455
├── frontend_src/       # Código fuente del escritorio SPA y 4 apps nativas
├── www/                # Salida comprimida (.gz) generada por tools/build.py
├── apps/               # Carpeta para scripts/apps de usuario
├── tools/
│   └── build.py        # Minificador + gzip + compilador mpy-cross + deploy mpremote
└── tests/              # Pruebas por fase y prueba de estrés para ESP32-C3
```

## Compilación y Despliegue

### 1. Generar `/www/*.gz` (minificado + gzip nivel 9)
```bash
python3 tools/build.py
```

### 2. Compilar `/sys/*.py` a bytecode `.mpy` (opcional, recomendado en producción)
Requiere `mpy-cross` (`pip install mpy-cross`):
```bash
# ESP32-C3 (RISC-V -> rv32imc)
python3 tools/build.py --board esp32c3 --mpy

# ESP32-S / ESP32-S3 (Xtensa -> xtensawin)
python3 tools/build.py --board esp32s3 --mpy
```

### 3. Desplegar directamente a la placa con `mpremote`
```bash
python3 tools/build.py --board esp32c3 --deploy --port /dev/ttyACM0
# Con bytecode .mpy:
python3 tools/build.py --board esp32c3 --mpy --deploy --port /dev/ttyACM0
```

### 4. Acceso al Escritorio Web
1. Reinicia la placa (`mpremote connect /dev/ttyACM0 soft-reset`).
2. **Sin Wi-Fi configurado**: conéctate a la red **`PyOS-Setup`** y abre **`http://192.168.4.1`**.
3. **Con Wi-Fi STA configurado** en `config/wifi.json`: abre **`http://pyos.local`** o la IP asignada por DHCP.

## Pruebas en Placa

```bash
mpremote connect /dev/ttyACM0 run tests/test_hal.py        # Fase 0: HAL y pines seguros
mpremote connect /dev/ttyACM0 run tests/test_phase1.py     # Fase 1: Kernel + FS
mpremote connect /dev/ttyACM0 run tests/test_phase2.py     # Fase 2: Wi-Fi STA/AP
mpremote connect /dev/ttyACM0 run tests/test_phase3.py     # Fase 3: HTTP + WS JSON-RPC
mpremote connect /dev/ttyACM0 run tests/test_stress_c3.py  # Fase 6: Estrés de memoria C3
```
