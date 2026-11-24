"""
PyOS - Script de prueba de Fase 1 (Kernel uasyncio + VFS seguro)
Ejecutar en la placa con: mpremote run tests/test_phase1.py
"""

import gc
import sys

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

if "/sys" not in sys.path:
    sys.path.append("/sys")

import fs as vfs
import kernel


def test_vfs():
    print("\n=== [1] Pruebas de VFS y Path Traversal ===")
    # 1. Path traversal bloqueado
    attacks = [
        ("../../config/wifi.json", "/www"),
        ("/www/../config/boards.json", "/www"),
        ("..\\..\\boot.py", "/www"),
        ("/../etc/passwd", "/"),
        ("/apps/test\x00.py", "/"),
    ]
    for raw, base in attacks:
        try:
            res = vfs.safe_path(raw, base)
            raise AssertionError("Traversal NO bloqueado: {} -> {}".format(raw, res))
        except ValueError as e:
            print("  OK bloqueado '{}' (base={}): {}".format(raw, base, e))

    # 2. Rutas validas
    assert vfs.safe_path("index.html", "/www") == "/www/index.html"
    assert vfs.safe_path("/www/js/../css/app.css", "/www") == "/www/css/app.css"
    print("  OK normalizacion de rutas validas.")

    # 3. Proteccion contra borrado de rutas criticas
    for protected in ("/", "/sys", "/boot.py"):
        try:
            vfs.remove(protected)
            raise AssertionError("Permitió borrar ruta critica: " + protected)
        except ValueError:
            print("  OK proteccion borrado:", protected)

    # 4. Escritura y lectura por bloques (streaming)
    tmp_dir = "/tmp_test_vfs"
    vfs.mkdir(tmp_dir)
    test_file = tmp_dir + "/stream.bin"
    payload = [b"A" * 300, b"B" * 300, b"C" * 150]
    written = vfs.write_chunks(test_file, payload)
    assert written == 750

    read_total = 0
    chunks_count = 0
    for mv in vfs.read_chunks(test_file, chunk_size=256):
        read_total += len(mv)
        chunks_count += 1
    assert read_total == 750 and chunks_count == 3
    print("  OK streaming: {} B escritos y leidos en {} chunks.".format(read_total, chunks_count))

    # 5. Listado y limpieza
    items = vfs.list_dir(tmp_dir)
    assert len(items) == 1 and items[0]["name"] == "stream.bin"
    vfs.remove(tmp_dir, recursive=True)
    assert not vfs.exists(tmp_dir)
    print("  OK list_dir y borrado recursivo.")


async def test_kernel_async():
    print("\n=== [2] Pruebas del Kernel uasyncio (Task Manager + GC Watchdog) ===")
    k = kernel.Kernel()
    events = []
    k.subscribe(lambda ev, data: events.append((ev, data)))

    crash_counter = [0]

    async def flaky_critical_task():
        crash_counter[0] += 1
        if crash_counter[0] <= 2:
            raise RuntimeError("Fallo simulado #{}".format(crash_counter[0]))
        # En el 3er intento funciona correctamente
        while True:
            await kernel._sleep_ms(200)

    k.register_task(
        "flaky_srv",
        flaky_critical_task,
        critical=True,
        restart_delay_ms=150,
        max_restarts=5,
    )

    async def stopper():
        # Esperar suficiente para que flaky_srv falle 2 veces y se recupere
        await kernel._sleep_ms(900)
        tasks = k.list_tasks()
        print("  Estado de tareas:", tasks)
        flaky = [t for t in tasks if t["name"] == "flaky_srv"][0]
        assert flaky["state"] == "running", "flaky_srv deberia haberse recuperado"
        assert flaky["restarts"] == 2, "flaky_srv deberia tener 2 reinicios"
        print("  OK supervisor reinicio tarea critica tras 2 fallos.")
        k.stop()

    k.register_task("stopper", stopper, critical=False)
    await k.start()

    assert len(k.get_logs()) > 0
    print("  OK logs registrados:", len(k.get_logs()), "| eventos capturados:", len(events))


def run_tests():
    test_vfs()
    asyncio.run(test_kernel_async())
    gc.collect()
    print("\n=== Pruebas Fase 1 completadas | RAM libre: {} B ===".format(gc.mem_free()))


if __name__ == "__main__":
    run_tests()
