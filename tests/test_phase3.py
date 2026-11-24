"""
MicroOS - Script de prueba de Fase 3 (HTTP Streaming .gz + WebSocket JSON-RPC)
Prueba loopback completa en la propia placa sin requerir Wi-Fi externo.
Ejecutar en la placa con: mpremote run tests/test_phase3.py
"""

import binascii
import gc
import hashlib
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

import fs
import kernel
import net
import rpc
import server


def _ws_make_masked_text(text):
    data = text.encode("utf-8")
    mask = b"\x12\x34\x56\x78"
    n = len(data)
    if n < 126:
        hdr = bytes((0x81, 0x80 | n))
    else:
        hdr = struct.pack("!BBH", 0x81, 0x80 | 126, n)
    masked = bytearray(n)
    for i in range(n):
        masked[i] = data[i] ^ mask[i & 3]
    return hdr + mask + bytes(masked)


async def _ws_recv_server_frame(reader):
    hdr = await reader.readexactly(2)
    opcode = hdr[0] & 0x0F
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    payload = await reader.readexactly(length) if length else b""
    return opcode, payload


async def test_server_loopback():
    print("\n=== MicroOS Fase 3: Pruebas HTTP Streaming + WebSocket JSON-RPC ===")

    # Preparar archivo .gz de prueba en /www (gzip valido de "Hello MicroOS")
    gz_sample = binascii.unhexlify(
        "1f8b08000000000002fff348cdc9c957f0cd4c2ef20f0600c534785b0d000000"
    )
    fs.mkdir("/www")
    fs.write_chunks("/www/_test.txt.gz", [gz_sample])

    k = kernel.Kernel()
    k._telemetry_ms = 600  # Acelerar push sys_stats para la prueba
    nm = net.NetManager(kern=k)
    rt = rpc.RPCRouter(kern=k, net_mgr=nm)
    srv = server.MicroServer(host="127.0.0.1", port=8080, www_root="/www", kern=k, router=rt)

    k.register_task("http_server", srv.run, critical=True)

    async def client_tests():
        await kernel._sleep_ms(300)

        # [1] HTTP Static .gz streaming
        r, w = await asyncio.open_connection("127.0.0.1", 8080)
        w.write(b"GET /_test.txt HTTP/1.1\r\nHost: localhost\r\nAccept-Encoding: gzip\r\n\r\n")
        await w.drain()
        raw = await r.read()
        w.close()
        await w.wait_closed()
        head, body = raw.split(b"\r\n\r\n", 1)
        assert b"200 OK" in head, head
        assert b"Content-Encoding: gzip" in head, head
        assert body == gz_sample, "Cuerpo .gz no coincide"
        print("  [1] OK HTTP static .gz streaming ({} B)".format(len(body)))

        # [2] HTTP Upload + Download streaming
        upload_payload = b"MicroOS-Stream-Test-" * 40  # 800 B (> 1 chunk en C3)
        r, w = await asyncio.open_connection("127.0.0.1", 8080)
        req = (
            "POST /api/fs/upload?path=/_up_test.bin HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Content-Length: {}\r\n\r\n"
        ).format(len(upload_payload)).encode("utf-8")
        w.write(req + upload_payload)
        await w.drain()
        raw = await r.read()
        w.close()
        await w.wait_closed()
        assert b'"ok": true' in raw or b'"ok":true' in raw, raw
        print("  [2a] OK HTTP upload streaming ({} B)".format(len(upload_payload)))

        r, w = await asyncio.open_connection("127.0.0.1", 8080)
        w.write(b"GET /api/fs/download?path=/_up_test.bin HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await w.drain()
        raw = await r.read()
        w.close()
        await w.wait_closed()
        head, body = raw.split(b"\r\n\r\n", 1)
        assert b"200 OK" in head and body == upload_payload
        print("  [2b] OK HTTP download streaming ({} B)".format(len(body)))

        # [3] WebSocket Handshake + JSON-RPC + Push Event
        r, w = await asyncio.open_connection("127.0.0.1", 8080)
        ws_key = "dGhlIHNhbXBsZSBub25jZQ=="
        w.write(
            (
                "GET /ws HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: {}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).format(ws_key).encode("utf-8")
        )
        await w.drain()
        hs = b""
        while b"\r\n\r\n" not in hs:
            hs += await r.read(128)
        assert b"101 Switching Protocols" in hs, hs
        assert b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in hs, hs
        print("  [3] OK WebSocket RFC 6455 handshake (101)")

        # Enviar comandos JSON-RPC
        tests_rpc = [
            {"id": 1, "app": "sys", "cmd": "info"},
            {"id": 2, "app": "gpio", "cmd": "set", "args": {"pin": 0, "val": 1}},
            {"id": 3, "app": "gpio", "cmd": "set", "args": {"pin": 18, "val": 1}},
            {"id": 4, "app": "term", "cmd": "exec", "args": {"code": "1 + 2 * 3"}},
        ]
        for req_obj in tests_rpc:
            w.write(_ws_make_masked_text(json.dumps(req_obj)))
        await w.drain()

        got_ids = {}
        got_push = False
        for _ in range(12):
            op, data = await _ws_recv_server_frame(r)
            assert op == 0x1
            msg = json.loads(data.decode("utf-8"))
            if "id" in msg:
                got_ids[msg["id"]] = msg
            elif msg.get("event") == "sys_stats":
                got_push = True
            if len(got_ids) == 4 and got_push:
                break

        assert got_ids[1]["ok"] is True and got_ids[1]["data"]["chip_id"] == k.hal.chip_id
        assert got_ids[2]["ok"] is True and got_ids[2]["data"]["val"] == 1
        assert got_ids[3]["ok"] is False  # GPIO 18 USB bloqueado
        assert got_ids[4]["ok"] is True and got_ids[4]["data"]["result"] == "7"
        assert got_push is True, "No se recibio evento push sys_stats"
        print("  [4] OK JSON-RPC (sys.info, gpio.set OK/bloqueado, term.exec=7)")
        print("  [5] OK Evento push 'sys_stats' recibido por WebSocket")

        # Cerrar WS y limpiar
        w.write(bytes((0x88, 0x80, 0, 0, 0, 0)))
        await w.drain()
        w.close()
        await w.wait_closed()

        k.hal.gpio_release(0)
        fs.remove("/www/_test.txt.gz")
        fs.remove("/_up_test.bin")
        gc.collect()
        print("  RAM libre con HTTP+WS+Kernel+HAL: {} B".format(gc.mem_free()))

        srv.close()
        k.stop()

    k.register_task("client_tests", client_tests, critical=False)
    await k.start()


def run_tests():
    asyncio.run(test_server_loopback())
    gc.collect()
    print("=== Pruebas Fase 3 completadas | RAM libre final: {} B ===".format(gc.mem_free()))


if __name__ == "__main__":
    run_tests()
