"""
PyOS - Servidor HTTP (Streaming .gz) y Puente WebSocket (RFC 6455 / JSON-RPC)
Implementado directamente sobre uasyncio.start_server para minimo consumo de RAM
en ESP32-C3, ESP32-S y ESP32-S3.
"""

import binascii
import gc
import hashlib
import struct

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

try:
    import ujson as json
except ImportError:
    import json

try:
    from micropython import const
except ImportError:
    def const(x):
        return x

import auth
import fs
import kernel
import rpc

_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_WS_PAYLOAD = const(8192)
_MAX_PUSH_QUEUE = const(8)
_MAX_HEADERS = const(24)
_MAX_HDR_LINE = const(1024)
_HDR_TIMEOUT_S = const(5)
_MAX_UPLOAD_BYTES = const(262144)
_FLASH_RESERVE_BYTES = const(65536)


async def _read_line_limited(reader, max_len=_MAX_HDR_LINE, timeout_s=_HDR_TIMEOUT_S):
    """Lee una linea HTTP con timeout y limite de longitud (ARCH-02, SEC-09)."""
    line = await asyncio.wait_for(reader.readline(), timeout_s)
    if line and len(line) > max_len:
        raise OverflowError("Cabecera HTTP demasiado larga")
    return line


def _sanitize_filename(fname):
    """Elimina caracteres peligrosos de Content-Disposition (SEC-10)."""
    out = []
    for ch in fname:
        o = ord(ch)
        if 32 <= o <= 126 and ch not in ('"', "\\", ";"):
            out.append(ch)
    return "".join(out) or "download.bin"

_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".py": "text/plain; charset=utf-8",
}


def _guess_mime(path):
    p = path[:-3] if path.endswith(".gz") else path
    dot = p.rfind(".")
    if dot != -1:
        ext = p[dot:].lower()
        return _MIME_TYPES.get(ext, "application/octet-stream")
    return "application/octet-stream"


def _parse_query(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[rpc._url_decode(k)] = rpc._url_decode(v)
        elif pair:
            params[rpc._url_decode(pair)] = ""
    return params


def _ws_build_header(opcode, n):
    """Construye solo la cabecera WebSocket servidor->cliente sin copiar el payload (MEM-02)."""
    first = 0x80 | (opcode & 0x0F)
    if n < 126:
        return bytes((first, n))
    if n < 65536:
        return struct.pack("!BBH", first, 126, n)
    return struct.pack("!BBQ", first, 127, n)


async def _ws_read_frame(reader):
    """
    Lee un frame WebSocket RFC 6455 con soporte de bit FIN y validacion de mascara (SEC-07).
    Retorna (fin, opcode, bytearray_payload) o (True, None, None) si el socket se cerro.
    """
    hdr = await reader.readexactly(2)
    if not hdr or len(hdr) < 2:
        return True, None, None

    b0, b1 = hdr[0], hdr[1]
    fin = (b0 & 0x80) != 0
    opcode = b0 & 0x0F
    masked = (b1 & 0x80) != 0
    length = b1 & 0x7F

    if not masked:
        raise PermissionError("RFC6455: frame cliente sin mascara")

    if length == 126:
        ext = await reader.readexactly(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = await reader.readexactly(8)
        length = struct.unpack("!Q", ext)[0]

    if length > _MAX_WS_PAYLOAD:
        raise ValueError("Frame WS excede limite de RAM ({} B)".format(length))

    mask_key = await reader.readexactly(4)

    if length > 0:
        data = bytearray(await reader.readexactly(length))
        m0, m1, m2, m3 = mask_key[0], mask_key[1], mask_key[2], mask_key[3]
        for i in range(length):
            r = i & 3
            if r == 0:
                data[i] ^= m0
            elif r == 1:
                data[i] ^= m1
            elif r == 2:
                data[i] ^= m2
            else:
                data[i] ^= m3
        return fin, opcode, data

    return fin, opcode, bytearray()


class _WSClient:
    """Representa un cliente WebSocket conectado con cola acotada de envio."""

    __slots__ = ("writer", "queue", "event", "alive")

    def __init__(self, writer):
        self.writer = writer
        self.queue = []
        self.event = asyncio.Event()
        self.alive = True

    def enqueue(self, text_msg, drop_oldest=True):
        if not self.alive:
            return
        if len(self.queue) >= _MAX_PUSH_QUEUE:
            if drop_oldest:
                self.queue.pop(0)
            else:
                return
        self.queue.append(text_msg)
        self.event.set()


class MicroServer:
    """
    Servidor HTTP (streaming .gz + upload/download) y WebSocket JSON-RPC.
    """

    def __init__(self, host="0.0.0.0", port=80, www_root="/www", kern=None, router=None, auth_mgr=None):
        self.host = host
        self.port = port
        self.www_root = www_root
        self.kernel = kern if kern is not None else kernel.get_kernel()
        self.hal = self.kernel.hal
        self.router = router if router is not None else rpc.RPCRouter(kern=self.kernel)
        self.auth = auth_mgr if auth_mgr is not None else auth.get_auth()

        self.chunk_size = self.hal.limits.get("http_chunk_bytes", 512)
        self.max_sockets = self.hal.limits.get("max_sockets", 3)

        self._active_conns = 0
        self._ws_clients = []
        self._server = None
        self._subscribed = False
        # Pool preasignado de buffers HTTP para reducir fragmentacion (MEM-01)
        self._buf_pool = [bytearray(self.chunk_size) for _ in range(self.max_sockets)]

        # Liberar caches / GC ante presion critica de RAM
        self.kernel.register_low_memory_hook(self._on_low_memory)

    def _acquire_buf(self):
        return self._buf_pool.pop() if self._buf_pool else bytearray(self.chunk_size)

    def _release_buf(self, buf):
        if len(self._buf_pool) < self.max_sockets and len(buf) == self.chunk_size:
            self._buf_pool.append(buf)

    def _on_low_memory(self):
        # Vaciar colas de eventos push pendientes para recuperar RAM de inmediato
        for cli in self._ws_clients:
            cli.queue.clear()

    def _update_kernel_subscription(self):
        """Solo se suscribe al bus del kernel si hay al menos un cliente WS activo."""
        has_clients = len(self._ws_clients) > 0
        if has_clients and not self._subscribed:
            self.kernel.subscribe(self._on_kernel_event)
            self._subscribed = True
        elif not has_clients and self._subscribed:
            self.kernel.unsubscribe(self._on_kernel_event)
            self._subscribed = False

    def _on_kernel_event(self, event_name, data):
        """Difunde eventos push del kernel a los clientes WebSocket conectados."""
        if not self._ws_clients:
            return
        try:
            payload = json.dumps({"event": event_name, "data": data})
        except Exception:
            return
        for cli in list(self._ws_clients):
            cli.enqueue(payload, drop_oldest=True)

    # ------------------------------------------------------------------
    # Respuestas HTTP auxiliares
    # ------------------------------------------------------------------

    async def _send_status(self, writer, status_line, body="", content_type="text/plain; charset=utf-8", extra_headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        lines = [
            "HTTP/1.1 " + status_line,
            "Content-Type: " + content_type,
            "Content-Length: " + str(len(data)),
            "Cache-Control: no-cache",
            "Connection: close",
        ]
        if extra_headers:
            lines.extend(extra_headers)
        hdr = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        writer.write(hdr)
        if data:
            writer.write(data)
        await writer.drain()

    async def _send_json(self, writer, status_line, obj, extra_headers=None):
        body = json.dumps(obj)
        await self._send_status(writer, status_line, body, "application/json; charset=utf-8", extra_headers=extra_headers)

    def _get_session(self, headers):
        tok = self.auth.extract_cookie_token(headers)
        return self.auth.validate_token(tok)

    async def _read_json_body(self, reader, headers, max_len=1024):
        try:
            clen = int(headers.get("content-length", "0"))
        except ValueError:
            clen = 0
        if clen <= 0 or clen > max_len:
            return {}
        raw = await asyncio.wait_for(reader.readexactly(clen), _HDR_TIMEOUT_S)
        return json.loads(raw.decode("utf-8"))

    async def _handle_auth(self, method, path, reader, writer, headers):
        if method == "GET" and path == "/api/auth/status":
            sess = self._get_session(headers)
            await self._send_json(writer, "200 OK", {
                "needs_setup": self.auth.needs_setup(),
                "authenticated": sess is not None,
                "user": sess["user"] if sess else None,
                "role": sess["role"] if sess else None,
            })
            return

        if method == "POST" and path == "/api/auth/setup":
            if not self.auth.needs_setup():
                await self._send_json(writer, "403 Forbidden", {"ok": False, "error": "Ya configurado"})
                return
            try:
                body = await self._read_json_body(reader, headers)
                tok = self.auth.setup_admin(body.get("password", ""), body.get("username", "admin") or "admin")
                cookie = "Set-Cookie: mos_sid={}; Path=/; HttpOnly; SameSite=Strict".format(tok)
                self.kernel.log("INFO", "AUTH", "Administrador inicial configurado")
                await self._send_json(writer, "200 OK", {"ok": True, "user": "admin", "role": "admin"}, [cookie])
            except Exception as e:
                await self._send_json(writer, "400 Bad Request", {"ok": False, "error": str(e)})
            return

        if method == "POST" and path == "/api/auth/login":
            try:
                body = await self._read_json_body(reader, headers)
                user = (body.get("username") or "admin").strip()
                pwd = body.get("password") or ""
                tok = self.auth.login(user, pwd)
                if not tok:
                    self.kernel.log("WARN", "AUTH", "Intento de login fallido para '{}'".format(user))
                    await self._send_json(writer, "401 Unauthorized", {"ok": False, "error": "Credenciales invalidas"})
                    return
                cookie = "Set-Cookie: mos_sid={}; Path=/; HttpOnly; SameSite=Strict".format(tok)
                self.kernel.log("INFO", "AUTH", "Sesion iniciada: '{}'".format(user))
                await self._send_json(writer, "200 OK", {"ok": True, "user": user, "role": "admin"}, [cookie])
            except Exception as e:
                await self._send_json(writer, "400 Bad Request", {"ok": False, "error": str(e)})
            return

        if method == "POST" and path == "/api/auth/logout":
            tok = self.auth.extract_cookie_token(headers)
            if tok:
                self.auth.logout(tok)
            cookie = "Set-Cookie: mos_sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
            await self._send_json(writer, "200 OK", {"ok": True}, [cookie])
            return

        await self._send_status(writer, "404 Not Found", "Endpoint auth no encontrado")

    # ------------------------------------------------------------------
    # HTTP: Archivos Estaticos (/www con deteccion automatica .gz y streaming)
    # ------------------------------------------------------------------

    async def _serve_static(self, writer, req_path, headers):
        if req_path == "/" or not req_path:
            req_path = "/index.html"

        try:
            target = fs.safe_path(req_path, self.www_root)
        except ValueError:
            await self._send_status(writer, "403 Forbidden", "Acceso denegado")
            return

        accept_enc = headers.get("accept-encoding", "")
        wants_gzip = "gzip" in accept_enc

        file_path = None
        is_gzip = False

        # Preferir version .gz si el navegador acepta gzip (o si solo existe .gz)
        gz_path = target + ".gz" if not target.endswith(".gz") else target
        if wants_gzip and fs.exists(gz_path, self.www_root):
            file_path = gz_path
            is_gzip = True
        elif fs.exists(target, self.www_root) and not fs.is_dir(target, self.www_root):
            file_path = target
            is_gzip = target.endswith(".gz")
        elif fs.exists(gz_path, self.www_root):
            file_path = gz_path
            is_gzip = True

        if file_path is None:
            await self._send_status(writer, "404 Not Found", "Archivo no encontrado")
            return

        info = fs.stat_info(file_path, self.www_root)
        mime = _guess_mime(file_path)

        cache_hdr = "Cache-Control: no-cache" if mime.startswith("text/html") else "Cache-Control: public, max-age=3600"
        hdr_lines = [
            "HTTP/1.1 200 OK",
            "Content-Type: " + mime,
            "Content-Length: " + str(info["size"]),
            cache_hdr,
            "Connection: close",
        ]
        if is_gzip:
            hdr_lines.append("Content-Encoding: gzip")
        hdr_raw = ("\r\n".join(hdr_lines) + "\r\n\r\n").encode("utf-8")

        writer.write(hdr_raw)
        await writer.drain()

        # Streaming en bloques reutilizando buffer del pool (MEM-01)
        buf = self._acquire_buf()
        try:
            for mv in fs.read_chunks(file_path, chunk_size=self.chunk_size, base=self.www_root, buf=buf):
                writer.write(mv)
                await writer.drain()
        finally:
            self._release_buf(buf)

    async def _handle_app_asset(self, writer, req_path, headers):
        """Sirve /api/apps/<app_id>/ui.js (o .gz) por streaming desde /apps/<app_id>/."""
        rel = req_path[len("/api/apps/"):]
        try:
            target = fs.safe_path(rel, "/apps")
        except ValueError:
            await self._send_status(writer, "403 Forbidden", "Acceso denegado")
            return

        wants_gzip = "gzip" in headers.get("accept-encoding", "")
        gz_path = target + ".gz" if not target.endswith(".gz") else target
        if wants_gzip and fs.exists(gz_path, "/apps"):
            file_path, is_gzip = gz_path, True
        elif fs.exists(target, "/apps") and not fs.is_dir(target, "/apps"):
            file_path, is_gzip = target, target.endswith(".gz")
        elif fs.exists(gz_path, "/apps"):
            file_path, is_gzip = gz_path, True
        else:
            await self._send_status(writer, "404 Not Found", "Recurso de app no encontrado")
            return

        info = fs.stat_info(file_path, "/apps")
        hdr_lines = [
            "HTTP/1.1 200 OK",
            "Content-Type: " + _guess_mime(file_path),
            "Content-Length: " + str(info["size"]),
            "Cache-Control: no-cache",
            "Connection: close",
        ]
        if is_gzip:
            hdr_lines.append("Content-Encoding: gzip")
        writer.write(("\r\n".join(hdr_lines) + "\r\n\r\n").encode("utf-8"))
        await writer.drain()

        buf = bytearray(self.chunk_size)
        for mv in fs.read_chunks(file_path, chunk_size=self.chunk_size, base="/apps", buf=buf):
            writer.write(mv)
            await writer.drain()

    # ------------------------------------------------------------------
    # HTTP: Descarga y Subida de archivos por streaming (/api/fs/...)
    # ------------------------------------------------------------------

    async def _handle_download(self, writer, params):
        raw_path = params.get("path", "")
        if not raw_path:
            await self._send_status(writer, "400 Bad Request", "Falta parametro 'path'")
            return
        try:
            full = fs.safe_path(raw_path, "/")
        except ValueError as e:
            await self._send_status(writer, "403 Forbidden", str(e))
            return

        if fs.is_secret_path(full):
            await self._send_status(writer, "403 Forbidden", "Archivo secreto protegido")
            return

        info = fs.stat_info(full, "/")
        if not info or info["type"] != "file":
            await self._send_status(writer, "404 Not Found", "Archivo no encontrado")
            return

        fname = _sanitize_filename(full.rsplit("/", 1)[-1])
        hdr = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/octet-stream\r\n"
            "Content-Disposition: attachment; filename=\"{}\"\r\n"
            "Content-Length: {}\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: close\r\n\r\n"
        ).format(fname, info["size"])
        writer.write(hdr.encode("utf-8"))
        await writer.drain()

        buf = bytearray(self.chunk_size)
        for mv in fs.read_chunks(full, chunk_size=self.chunk_size, base="/", buf=buf):
            writer.write(mv)
            await writer.drain()

    async def _handle_upload(self, reader, writer, params, headers):
        raw_path = params.get("path", "")
        if not raw_path:
            await self._send_json(writer, "400 Bad Request", {"ok": False, "error": "Falta parametro 'path'"})
            return

        try:
            content_len = int(headers.get("content-length", "0"))
        except ValueError:
            content_len = 0

        if content_len < 0 or content_len > _MAX_UPLOAD_BYTES:
            await self._send_json(writer, "413 Payload Too Large", {"ok": False, "error": "Tamano excede limite permitido"})
            return

        flash_free = self.hal.get_flash_stats("/").get("free", 0)
        if content_len > 0 and (flash_free - content_len) < _FLASH_RESERVE_BYTES:
            await self._send_json(writer, "413 Payload Too Large", {"ok": False, "error": "Espacio insuficiente en Flash"})
            return

        try:
            full = fs.safe_path(raw_path, "/")
            written = await fs.async_write_stream(
                full, reader, content_len, chunk_size=self.chunk_size, base="/"
            )
            gc.collect()
            await self._send_json(
                writer,
                "200 OK",
                {"ok": True, "path": full, "written": written},
            )
        except ValueError as e:
            await self._send_json(writer, "403 Forbidden", {"ok": False, "error": str(e)})
        except Exception as e:
            await self._send_json(writer, "500 Internal Server Error", {"ok": False, "error": str(e)})

    # ------------------------------------------------------------------
    # WebSocket RFC 6455 (/ws) + JSON-RPC Bidireccional
    # ------------------------------------------------------------------

    async def _ws_writer_loop(self, client):
        """Envia mensajes serializados sin concatenacion extra de payload (MEM-02)."""
        try:
            while client.alive:
                if not client.queue:
                    client.event.clear()
                    await client.event.wait()
                    continue
                msg = client.queue.pop(0)
                raw = msg.encode("utf-8") if isinstance(msg, str) else msg
                client.writer.write(_ws_build_header(0x1, len(raw)))
                if raw:
                    client.writer.write(raw)
                await client.writer.drain()
        except Exception:
            client.alive = False

    async def _handle_websocket(self, reader, writer, headers, sess):
        # Validar Origin para prevenir Cross-Site WebSocket Hijacking (SEC-04)
        origin = headers.get("origin", "").strip()
        host_hdr = headers.get("host", "").strip()
        if origin and host_hdr:
            allowed = ("http://" + host_hdr, "https://" + host_hdr)
            if origin not in allowed:
                self.kernel.log("WARN", "WS", "Origin rechazado: {}".format(origin))
                await self._send_status(writer, "403 Forbidden", "Origin no permitido")
                return

        ws_key = headers.get("sec-websocket-key")
        if not ws_key:
            await self._send_status(writer, "400 Bad Request", "Falta Sec-WebSocket-Key")
            return

        # Handshake RFC 6455
        digest = hashlib.sha1(ws_key.strip().encode("utf-8") + _WS_GUID).digest()
        accept = binascii.b2a_base64(digest).strip().decode("utf-8")

        resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: {}\r\n\r\n"
        ).format(accept)
        writer.write(resp.encode("utf-8"))
        await writer.drain()

        client = _WSClient(writer)
        self._ws_clients.append(client)
        self._update_kernel_subscription()
        self.kernel.log("INFO", "WS", "Cliente conectado (total: {})".format(len(self._ws_clients)))

        writer_task = asyncio.create_task(self._ws_writer_loop(client))
        frag_buf = None
        try:
            while client.alive:
                try:
                    fin, opcode, payload = await _ws_read_frame(reader)
                except EOFError:
                    break
                except OSError:
                    break
                except PermissionError:
                    # RFC 6455: cerrar con codigo 1002 (protocol error)
                    try:
                        writer.write(_ws_build_header(0x8, 2))
                        writer.write(b"\x03\xea")
                        await writer.drain()
                    except Exception:
                        pass
                    break
                except ValueError as e:
                    client.enqueue(json.dumps({"id": None, "ok": False, "error": str(e)}), drop_oldest=False)
                    break

                if opcode is None or opcode == 0x8:  # Close
                    try:
                        writer.write(_ws_build_header(0x8, 0))
                        await writer.drain()
                    except Exception:
                        pass
                    break

                if opcode == 0x9:  # Ping -> Pong
                    try:
                        writer.write(_ws_build_header(0xA, len(payload)))
                        if payload:
                            writer.write(payload)
                        await writer.drain()
                    except Exception:
                        break
                    continue

                if opcode == 0xA:  # Pong
                    continue

                # Ensamblaje de mensajes fragmentados RFC 6455 (SEC-07)
                if opcode == 0x1:
                    if not fin:
                        frag_buf = payload
                        continue
                    full_payload = payload
                elif opcode == 0x0 and frag_buf is not None:
                    if len(frag_buf) + len(payload) > _MAX_WS_PAYLOAD:
                        client.enqueue(json.dumps({"id": None, "ok": False, "error": "Mensaje fragmentado excede limite"}), drop_oldest=False)
                        frag_buf = None
                        break
                    frag_buf.extend(payload)
                    if not fin:
                        continue
                    full_payload = frag_buf
                    frag_buf = None
                else:
                    continue

                try:
                    req = json.loads(full_payload.decode("utf-8"))
                except Exception:
                    client.enqueue(
                        json.dumps({"id": None, "ok": False, "error": "JSON invalido"}),
                        drop_oldest=False,
                    )
                    continue

                if not isinstance(req, dict):
                    client.enqueue(
                        json.dumps({"id": None, "ok": False, "error": "Peticion debe ser objeto JSON"}),
                        drop_oldest=False,
                    )
                    continue

                res = await self.router.dispatch_async(req, role=sess["role"])
                client.enqueue(json.dumps(res), drop_oldest=False)
        finally:
            client.alive = False
            client.event.set()
            try:
                writer_task.cancel()
            except Exception:
                pass
            if client in self._ws_clients:
                self._ws_clients.remove(client)
            self._update_kernel_subscription()
            self.kernel.log("INFO", "WS", "Cliente desconectado (total: {})".format(len(self._ws_clients)))
            gc.collect()

    # ------------------------------------------------------------------
    # Manejador de Conexiones Entrantes
    # ------------------------------------------------------------------

    async def _handle_client(self, reader, writer):
        if self._active_conns >= self.max_sockets:
            try:
                await self._send_status(writer, "503 Service Unavailable", "Servidor ocupado")
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            return

        self._active_conns += 1
        try:
            try:
                req_line = await _read_line_limited(reader)
            except OverflowError:
                await self._send_status(writer, "431 Request Header Fields Too Large", "Linea demasiado larga")
                return
            except Exception:
                return
            if not req_line:
                return
            parts = req_line.decode("utf-8", "ignore").strip().split()
            if len(parts) < 2:
                return
            method, raw_target = parts[0].upper(), parts[1]

            # Leer cabeceras HTTP con tope de seguridad y timeout (ARCH-02, SEC-09)
            headers = {}
            for _ in range(_MAX_HEADERS):
                try:
                    line = await _read_line_limited(reader)
                except OverflowError:
                    await self._send_status(writer, "431 Request Header Fields Too Large", "Cabecera demasiado larga")
                    return
                if not line or line in (b"\r\n", b"\n"):
                    break
                if b":" in line:
                    k, v = line.decode("utf-8", "ignore").split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            # Separar ruta y query string
            if "?" in raw_target:
                path, qs = raw_target.split("?", 1)
                params = _parse_query(qs)
            else:
                path, params = raw_target, {}
            path = rpc._url_decode(path)

            # Endpoints publicos de autenticacion
            if path.startswith("/api/auth/"):
                await self._handle_auth(method, path, reader, writer, headers)
                return

            # Rutas protegidas que requieren sesion autenticada (SEC-01)
            if path == "/ws" or path.startswith("/api/"):
                sess = self._get_session(headers)
                if sess is None:
                    await self._send_json(writer, "401 Unauthorized", {"ok": False, "error": "Autenticacion requerida"})
                    return

                if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                    await self._handle_websocket(reader, writer, headers, sess)
                elif method == "GET" and path.startswith("/api/apps/"):
                    await self._handle_app_asset(writer, path, headers)
                elif method == "GET" and path == "/api/fs/download":
                    await self._handle_download(writer, params)
                elif method == "POST" and path == "/api/fs/upload":
                    await self._handle_upload(reader, writer, params, headers)
                else:
                    await self._send_status(writer, "404 Not Found", "API no encontrada")
                return

            if method == "GET":
                await self._serve_static(writer, path, headers)
            else:
                await self._send_status(writer, "405 Method Not Allowed", "Metodo no soportado")
        except Exception as e:
            # Ignorar cierres abruptos de socket del navegador
            if "ECONNRESET" not in str(e) and "EPIPE" not in str(e):
                self.kernel.log("WARN", "HTTP", "Error conexion: {}".format(e))
        finally:
            self._active_conns -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Corrutina Principal del Servidor (Supervisada por el Kernel)
    # ------------------------------------------------------------------

    async def run(self):
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        self.kernel.log(
            "INFO",
            "HTTP",
            "Servidor escuchando en {}:{} (chunk={} B, max_sock={})".format(
                self.host, self.port, self.chunk_size, self.max_sockets
            ),
        )
        gc.collect()
        # Mantener la corrutina viva mientras el servidor atiende conexiones
        try:
            while True:
                if hasattr(asyncio, "sleep_ms"):
                    await asyncio.sleep_ms(5000)
                else:
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            self.close()
            raise

    def close(self):
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        for cli in list(self._ws_clients):
            cli.alive = False
            cli.event.set()
        self._ws_clients.clear()
        self._update_kernel_subscription()


_server_instance = None


def get_server(host="0.0.0.0", port=80):
    global _server_instance
    if _server_instance is None:
        _server_instance = MicroServer(host=host, port=port)
    return _server_instance
