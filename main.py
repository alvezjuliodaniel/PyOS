"""
MicroOS - main.py
Punto de entrada principal del Kernel cooperativo sobre uasyncio.
"""

import gc
import sys

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

if "/sys" not in sys.path:
    sys.path.append("/sys")

import appmgr
import kernel
import mqtt
import net
import server


def main():
    k = kernel.get_kernel()
    nm = net.get_net()
    mq = mqtt.get_mqtt(kern=k, net_mgr=nm)
    srv = server.get_server(host="0.0.0.0", port=80)
    srv.router.mqtt = mq
    am = appmgr.get_appmgr(kern=k, router=srv.router, net_mgr=nm)
    am.discover_all()

    # Registrar gestor de red como tarea critica supervisada
    k.register_task(
        "net_manager",
        nm.run,
        critical=True,
        restart_delay_ms=3000,
        max_restarts=0,
    )

    # Registrar servidor HTTP + WebSocket como tarea critica supervisada
    k.register_task(
        "http_server",
        srv.run,
        critical=True,
        restart_delay_ms=2000,
        max_restarts=0,
    )

    # Registrar cliente MQTT sobre TLS (Fase 10)
    k.register_task(
        "mqtt_client",
        mq.run,
        critical=False,
    )

    # Registrar app nativa OLED SSD1306 (se desactiva sola si no hay pantalla conectada)
    try:
        import oled_status

        oled = oled_status.get_oled_app()
        k.register_task(
            "oled_status",
            oled.run,
            critical=False,
        )
    except Exception as e:
        k.log("WARN", "BOOT", "OLED app no cargada: {}".format(e))

    try:
        asyncio.run(k.start())
    except KeyboardInterrupt:
        print("\n[MicroOS] Interrupcion por teclado (REPL activo).")
    finally:
        srv.close()
        k.stop()
        asyncio.new_event_loop()
        gc.collect()


if __name__ == "__main__":
    main()
