"""
PyOS - Prueba de Estres de Memoria (ESP32-C3 / S / S3)
Valida estabilidad del stack completo:
  HAL + FS + Kernel + Wi-Fi AP + HTTP Streaming (/www/*.gz) + 20 RPCs WebSocket.
Ejecutar en placa: mpremote run tests/test_stress_c3.py
"""

import gc
import struct
import sys

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    import ujson as json
except ImportError:
    import json

if "/sys" not in sys.path:
    sys.path.append("/sys")

import kernel
import net
import rpc
import server


def _ws_masked(text):
    data = text.encode("utf-8")
    mask = b"\x37\xfa\x21\x3d"
    n = len(data)
    hdr = bytes((0x81, 0x80 | n)) if n < 126 else struct.pack("!BBH", 0x81, 0x80 | 126, n)
    out = bytearray(n)
    for i in range(n):
        out[i] = data[i] ^ mask[i & 3]
    return hdr + mask + bytes(out)


async def _ws_read(reader):
    hdr = await reader.readexactly(2)
    op = hdr[0] & 0x0F
    n = hdr[1] & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    return op, (await reader.readexactly(n) if n else b"")


async def run_stress():
    print("\n=== PyOS Fase 6: Prueba de Estres de Memoria ===")
    k = kernel.Kernel()
    nm = net.NetManager(kern=k)
    rt = rpc.RPCRouter(kern=k, net_mgr=nm)
    srv = server.MicroServer(host="127.0.0.1", port=8081, www_root="/www", kern=k, router=rt)

    k.register_task("net_manager", nm.run, critical=True)
    k.register_task("http_server", srv.run, critical=True)

    assets = [
        "/",
        "/css/desktop.css",
        "/js/rpc.js",
        "/js/wm.js",
        "/js/apps/monitor.js",
        "/js/apps/files.js",
        "/js/apps/terminal.js",
        "/js/apps/gpio.js",
        "/js/app.js",
    ]

    async def stress_client():
        await kernel._sleep_ms(1200)
        gc.collect()
        ram_start = gc.mem_free()
        ram_min = ram_start
        print("  RAM inicial (con Wi-Fi AP + HTTP activos): {} B".format(ram_start))

        # 1. Descargar 2 rondas completas de todo el escritorio SPA (.gz)
        total_http_bytes = 0
        for rnd in range(2):
            for path in assets:
                r, w = await asyncio.open_connection("127.0.0.1", 8081)
                req = "GET {} HTTP/1.1\r\nHost: pyos\r\nAccept-Encoding: gzip\r\n\r\n".format(path)
                w.write(req.encode("utf-8"))
                await w.drain()
                raw = await r.read()
                w.close()
                await w.wait_closed()
                head, body = raw.split(b"\r\n\r\n", 1)
                assert b"200 OK" in head, "Fallo HTTP en {}: {}".format(path, head[:60])
                assert b"Content-Encoding: gzip" in head, "Falta gzip en " + path
                total_http_bytes += len(body)
                ram_now = gc.mem_free()
                if ram_now < ram_min:
                    ram_min = ram_now

        print("  [1] OK 18 peticiones HTTP .gz ({} B totales) | RAM min: {} B".format(
            total_http_bytes, ram_min
        ))

        # 2. Sesion WebSocket con 20 llamadas JSON-RPC rapidas
        r, w = await asyncio.open_connection("127.0.0.1", 8081)
        w.write(
            b"GET /ws HTTP/1.1\r\nHost: pyos\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        await w.drain()
        hs = b""
        while b"\r\n\r\n" not in hs:
            hs += await r.read(128)
        assert b"101 Switching Protocols" in hs

        cmds = [
            ("sys", "stats", {}),
            ("gpio", "info", {}),
            ("fs", "ls", {"path": "/"}),
            ("term", "exec", {"code": "len(os.listdir('/sys'))"}),
            ("sys", "gc", {}),
        ]
        ok_count = 0
        for i in range(20):
            app, cmd, args = cmds[i % len(cmds)]
            req_id = i + 1
            w.write(_ws_masked(json.dumps({"id": req_id, "app": app, "cmd": cmd, "args": args})))
            await w.drain()

            # Leer hasta recibir nuestra respuesta (ignorando push events intermedios)
            while True:
                op, data = await _ws_read(r)
                assert op == 0x1
                msg = json.loads(data.decode("utf-8"))
                if msg.get("id") == req_id:
                    assert msg.get("ok") is True, msg
                    ok_count += 1
                    break

            ram_now = gc.mem_free()
            if ram_now < ram_min:
                ram_min = ram_now

        # Cerrar WS limpiamente
        w.write(bytes((0x88, 0x80, 0, 0, 0, 0)))
        await w.drain()
        w.close()
        await w.wait_closed()
        await kernel._sleep_ms(200)

        gc.collect()
        ram_end = gc.mem_free()
        delta = ram_start - ram_end
        print("  [2] OK {} RPCs WebSocket | RAM min: {} B | RAM final: {} B (delta: {} B)".format(
            ok_count, ram_min, ram_end, delta
        ))
        assert ram_min > 32768, "RAM minima cayo debajo de 32 KB: {}".format(ram_min)

        srv.close()
        nm.shutdown()
        k.stop()

    k.register_task("stress_client", stress_client, critical=False)
    await k.start()
    gc.collect()
    print("=== Estres C3 SUPERADO | RAM libre final: {} B ===".format(gc.mem_free()))


if __name__ == "__main__":
    asyncio.run(run_stress())
