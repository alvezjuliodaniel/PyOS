"""
PyOS - Script de prueba de Fase 2 (Conectividad Wi-Fi STA + AP de respaldo)
Ejecutar en la placa con: mpremote run tests/test_phase2.py
"""

import gc
import sys
import network

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

if "/sys" not in sys.path:
    sys.path.append("/sys")

import kernel
import net


async def test_net_async():
    print("\n=== PyOS Fase 2: Pruebas de Red (STA / AP / Hostname) ===")
    k = kernel.Kernel()
    nm = net.NetManager(kern=k)

    k.register_task("net_manager", nm.run, critical=True, restart_delay_ms=2000)

    async def verifier():
        # Dar tiempo a que levante STA o AP de respaldo
        await kernel._sleep_ms(1500)
        st = nm.get_status()
        print("  Estado de red:", st)

        if hasattr(network, "hostname"):
            hn = network.hostname()
            print("  Hostname activo:", hn)
            assert hn == "pyos", "Hostname inesperado: {}".format(hn)

        assert st["connected"] is True, "Deberia estar conectado en modo STA o AP"
        assert st["mode"] in ("sta", "ap"), "Modo invalido: {}".format(st["mode"])
        if st["mode"] == "ap":
            assert st["ssid"] == "PyOS-Setup"
            assert st["ip"] == "192.168.4.1"
            print("  OK AP de respaldo 'PyOS-Setup' activo en 192.168.4.1")
        else:
            print("  OK STA conectado en IP:", st["ip"], "RSSI:", st["rssi"])

        # Verificar que el escaneo de redes funciona sin bloquear/fallar
        nets = nm.scan()
        print("  Redes Wi-Fi detectadas ({}): {}".format(
            len(nets), [n["ssid"] for n in nets[:5]]
        ))

        gc.collect()
        print("  RAM libre con Wi-Fi activo: {} B".format(gc.mem_free()))

        nm.shutdown()
        k.stop()

    k.register_task("verifier", verifier, critical=False)
    await k.start()


def run_tests():
    asyncio.run(test_net_async())
    gc.collect()
    print("=== Pruebas Fase 2 completadas | RAM libre final: {} B ===".format(gc.mem_free()))


if __name__ == "__main__":
    run_tests()
