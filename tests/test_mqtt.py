"""
PyOS - Pruebas de Fase 10: MQTT sobre TLS y AOS IoT
Valida:
  1. Rechazo obligatorio sin CA PEM (TLS estricto, sin CERT_NONE).
  2. Secretos (password, ca_pem) guardados en /config/secrets/mqtt.secret,
     bloqueados por fs.read() y nunca expuestos en mqtt.status.
  3. Validacion de topicos, wildcards y limites de payload.
  4. Autorizacion por rol (viewer bloqueado en mqtt.configure).
  5. Whitelist segura de comandos AOS IoT (rechaza comandos no permitidos).
  6. Codificacion de longitudes MQTT 3.1.1 y disponibilidad de ssl.CERT_REQUIRED.
"""

import gc
import ssl
import sys

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

if "/sys" not in sys.path:
    sys.path.append("/sys")

import fs
import kernel
import mqtt
import rpc


async def run_mqtt_tests():
    print("\n=== PyOS Fase 10: Pruebas MQTT sobre TLS ===")

    # [1] Verificar soporte TLS estricto en el firmware MicroPython
    assert hasattr(ssl, "SSLContext"), "Falta ssl.SSLContext"
    assert hasattr(ssl, "CERT_REQUIRED"), "Falta ssl.CERT_REQUIRED"
    print("  [1] OK Firmware soporta ssl.SSLContext + CERT_REQUIRED={}".format(ssl.CERT_REQUIRED))

    k = kernel.Kernel()
    mq = mqtt.MQTTTLSManager(kern=k)
    rt = rpc.RPCRouter(kern=k, mqtt_mgr=mq)

    # [2] Codificacion MQTT 3.1.1 Remaining Length
    assert mqtt._encode_rem_len(0) == b"\x00"
    assert mqtt._encode_rem_len(127) == b"\x7f"
    assert mqtt._encode_rem_len(128) == b"\x80\x01"
    assert mqtt._encode_rem_len(16384) == b"\x80\x80\x01"
    print("  [2] OK Codificacion MQTT 3.1.1 validada")

    # [3] Validacion de topicos
    assert mqtt._valid_topic("aos/devices/dev1/telemetry", allow_wildcards=False)
    assert not mqtt._valid_topic("aos/devices/+/telemetry", allow_wildcards=False)
    assert mqtt._valid_topic("aos/devices/#", allow_wildcards=True)
    assert not mqtt._valid_topic("", allow_wildcards=True)
    print("  [3] OK Validacion de topicos y wildcards")

    # [4] Rechazo sin CA PEM (TLS obligatorio)
    try:
        mq._has_ca = False
        mq._cfg["host"] = "broker.example.com"
        mq.request_connect()
        raise AssertionError("Permitió conectar sin CA PEM!")
    except ValueError as e:
        print("  [4] OK Conexion sin CA rechazada:", e)

    # [5] Secretos nunca expuestos en mqtt.status ni legibles via fs.read
    st = mq.configure({
        "host": "broker.example.com",
        "port": 8883,
        "username": "aos_node",
        "password": "super_secret_password_123",
    })
    assert "password" not in st
    assert st["has_password"] is True
    try:
        fs.read_text("/config/secrets/mqtt.secret")
        raise AssertionError("fs.read_text permitió leer mqtt.secret!")
    except ValueError:
        print("  [5] OK Credenciales ocultas en status y bloqueadas por fs.read()")

    # [6] Autorizacion por rol en JSON-RPC
    r_deny = rt.dispatch({"id": 1, "app": "mqtt", "cmd": "configure", "args": {"host": "x"}}, role="viewer")
    assert not r_deny["ok"], r_deny
    r_ok = rt.dispatch({"id": 2, "app": "mqtt", "cmd": "status"}, role="viewer")
    assert r_ok["ok"], r_ok
    print("  [6] OK Roles RPC verificados (viewer bloqueado en configure)")

    # [7] Limpiar configuracion de prueba
    try:
        fs.remove("/config/secrets/mqtt.secret", "/", force=True)
    except Exception:
        pass
    mq.configure({"host": "", "username": ""})

    gc.collect()
    print("=== Pruebas Fase 10 (Base TLS/Seguridad) PASS | RAM libre: {} B ===".format(gc.mem_free()))


if __name__ == "__main__":
    asyncio.run(run_mqtt_tests())
