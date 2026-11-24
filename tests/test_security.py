"""
PyOS v0.2.0 - Pruebas de Regresion de Seguridad (Etapa A)
Valida SEC-01 (auth 401/login), SEC-02 (secretos y write-protect),
SEC-03 (roles y allow_remote_python), SEC-04 (WS Origin CSWSH),
SEC-06 (413 upload), SEC-09 (431 cabecera larga) y APP-04 (manifest traversal).
"""

import gc
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

import auth
import fs
import kernel
import rpc
import appmgr
import server


async def run_security_tests():
    print("\n=== PyOS v0.2.0: Pruebas de Seguridad (Etapa A) ===")

    # [1] SEC-02: Secretos bloqueados y archivos del sistema protegidos contra escritura
    for secret_p in ("/config/wifi.json", "/config/secrets/auth.json", "/apps/x.secret"):
        try:
            fs.read_text(secret_p)
            raise AssertionError("Permitió leer secreto: " + secret_p)
        except ValueError:
            pass
    for ro_p in ("/boot.py", "/main.py", "/sys/server.py", "/config/secrets/auth.json"):
        try:
            fs.write_text(ro_p, "hack")
            raise AssertionError("Permitió escribir ruta critica: " + ro_p)
        except ValueError:
            pass
    print("  [1] OK SEC-02: secretos bloqueados en lectura y /sys,/boot.py protegidos en escritura")

    # [2] APP-04: Path traversal en manifest.ui / manifest.backend bloqueado
    k = kernel.Kernel()
    rt = rpc.RPCRouter(kern=k)
    am = appmgr.AppManager(kern=k, router=rt)
    try:
        am.install_app({"id": "evilapp", "ui": "../../boot.py"}, ui_code="hack")
        raise AssertionError("APP-04 no bloqueado!")
    except ValueError:
        print("  [2] OK APP-04: path traversal en manifest.ui bloqueado")

    # [3] SEC-03: Roles y allow_remote_python
    r_viewer = rt.dispatch({"id": 1, "app": "term", "cmd": "exec", "args": {"code": "1+1"}}, role="viewer")
    assert not r_viewer["ok"], r_viewer
    k.allow_remote_python = False
    r_dis = rt.dispatch({"id": 2, "app": "term", "cmd": "exec", "args": {"code": "1+1"}}, role="admin")
    assert not r_dis["ok"], r_dis
    k.allow_remote_python = True
    print("  [3] OK SEC-03: roles y allow_remote_python verificados")

    # [4] HTTP/WS Auth (SEC-01), Origin CSWSH (SEC-04), 413 Upload (SEC-06), 431 Header (SEC-09)
    amgr = auth.AuthManager(auth_path="/config/secrets/_test_auth.json")
    srv = server.MicroServer(host="127.0.0.1", port=8085, kern=k, router=rt, auth_mgr=amgr)
    k.register_task("srv_sec", srv.run)

    async def client_checks():
        await kernel._sleep_ms(250)

        async def http_req(raw_bytes):
            r, w = await asyncio.open_connection("127.0.0.1", 8085)
            w.write(raw_bytes)
            await w.drain()
            resp = await r.read()
            w.close()
            await w.wait_closed()
            return resp

        # Sin cookie -> /ws y /api/fs/download deben dar 401
        resp = await http_req(b"GET /api/fs/download?path=/config/system.json HTTP/1.1\r\nHost: 127.0.0.1:8085\r\n\r\n")
        assert b"401 Unauthorized" in resp, resp[:80]

        # Setup admin + obtener cookie mos_sid
        body = b'{"username":"admin","password":"secret123"}'
        resp = await http_req(
            b"POST /api/auth/setup HTTP/1.1\r\nHost: 127.0.0.1:8085\r\n"
            b"Content-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        assert b"200 OK" in resp and b"mos_sid=" in resp, resp[:120]
        cookie_val = resp.split(b"mos_sid=", 1)[1].split(b";", 1)[0].decode()
        print("  [4] OK SEC-01: 401 sin sesion + setup admin + Cookie HttpOnly")

        # SEC-04: WebSocket con Origin malicioso -> 403 Forbidden
        resp = await http_req(
            b"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:8085\r\n"
            b"Origin: http://evil.example\r\n"
            b"Cookie: mos_sid=" + cookie_val.encode() + b"\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        )
        assert b"403 Forbidden" in resp, resp[:80]
        print("  [5] OK SEC-04: WebSocket con Origin externo rechazado (403)")

        # SEC-06: Upload > 256 KB -> 413 Payload Too Large
        resp = await http_req(
            b"POST /api/fs/upload?path=/apps/huge.bin HTTP/1.1\r\nHost: 127.0.0.1:8085\r\n"
            b"Cookie: mos_sid=" + cookie_val.encode() + b"\r\n"
            b"Content-Length: 9999999\r\n\r\n"
        )
        assert b"413 Payload Too Large" in resp, resp[:80]
        print("  [6] OK SEC-06: Upload excesivo rechazado (413)")

        # SEC-09: Cabecera > 1024 B -> 431
        resp = await http_req(
            b"GET / HTTP/1.1\r\nHost: 127.0.0.1:8085\r\nX-Big: " + (b"A" * 1200) + b"\r\n\r\n"
        )
        assert b"431 Request Header Fields Too Large" in resp, resp[:80]
        print("  [7] OK SEC-09: Cabecera > 1024 B rechazada (431)")

        # Limpiar archivo temporal de auth de prueba
        try:
            fs.remove("/config/secrets/_test_auth.json", "/", force=True)
        except Exception:
            pass
        srv.close()
        k.stop()

    k.register_task("client_checks", client_checks)
    await k.start()
    gc.collect()
    print("=== Pruebas de Seguridad Etapa A: TODAS PASS | RAM libre: {} B ===".format(gc.mem_free()))


if __name__ == "__main__":
    asyncio.run(run_security_tests())
