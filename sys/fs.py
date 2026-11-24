"""
PyOS - Sistema de Archivos Seguro (VFS) sobre LittleFS
Provee navegacion segura contra path traversal y lectura/escritura por bloques
(streaming) con minimo consumo de RAM para ESP32-C3 / ESP32-S / ESP32-S3.
"""

import os

try:
    import ujson as json
except ImportError:
    import json

try:
    from micropython import const
except ImportError:
    def const(x):
        return x

_DIR_TYPE = const(0x4000)
_DEFAULT_CHUNK = const(512)
_DEFAULT_MAX_TEXT = const(8192)

# Rutas protegidas que no pueden eliminarse sin autorizacion explicita
_PROTECTED_PATHS = frozenset(("/", "/sys", "/config", "/config/secrets", "/boot.py", "/main.py"))

# Rutas secretas inaccesibles para lectura/descarga desde APIs de usuario (SEC-02)
_SECRET_EXACT = frozenset(("/config/wifi.json", "/config/secrets"))


def is_secret_path(full_path):
    """Indica si una ruta normalizada contiene credenciales o secretos."""
    if full_path in _SECRET_EXACT:
        return True
    if full_path.startswith("/config/secrets/"):
        return True
    if full_path.endswith(".secret"):
        return True
    return False


def is_write_protected(full_path):
    """Indica si una ruta critica del sistema esta protegida contra escritura externa."""
    if full_path in ("/boot.py", "/main.py", "/sys", "/config/secrets"):
        return True
    if full_path.startswith("/sys/") or full_path.startswith("/config/secrets/"):
        return True
    return False


def _normalize_segments(raw_path):
    """
    Normaliza una ruta estilo Unix y resuelve '.' y '..'.
    Retorna tupla: (lista_segmentos, intento_escape_raiz).
    """
    if not isinstance(raw_path, str):
        raise ValueError("La ruta debe ser una cadena")
    if "\x00" in raw_path:
        raise ValueError("Caracter nulo no permitido en ruta")

    clean = raw_path.replace("\\", "/")
    parts = []
    escaped = False

    for seg in clean.split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            if parts:
                parts.pop()
            else:
                escaped = True
        else:
            parts.append(seg)

    return parts, escaped


def safe_path(path, base="/"):
    """
    Resuelve y valida 'path' confinandolo estrictamente dentro de 'base'.
    Lanza ValueError si detecta un intento de path traversal fuera de 'base'.
    """
    base_segs, base_esc = _normalize_segments(base)
    if base_esc:
        raise ValueError("Ruta base invalida")

    # Si path es relativo o absoluto respecto a base, normalizar con cuidado
    raw = (path or "").replace("\\", "/")
    if "\x00" in raw:
        raise ValueError("Caracter nulo no permitido en ruta")

    # Cuando base != '/', interpretar 'path' como ruta relativa a 'base'
    # o ya prefijada con 'base', bloqueando cualquier '..' que escape de base.
    base_prefix = "/" + "/".join(base_segs) if base_segs else "/"

    if base_segs:
        # Si el cliente envia '/www/index.html' con base='/www', quitar prefijo redundante
        if raw == base_prefix:
            rel = ""
        elif raw.startswith(base_prefix + "/"):
            rel = raw[len(base_prefix) + 1 :]
        else:
            rel = raw.lstrip("/")

        rel_segs, rel_esc = _normalize_segments(rel)
        if rel_esc:
            raise ValueError("Path traversal bloqueado: {}".format(path))
        full_segs = base_segs + rel_segs
    else:
        full_segs, root_esc = _normalize_segments(raw)
        if root_esc:
            raise ValueError("Path traversal bloqueado: {}".format(path))

    return "/" + "/".join(full_segs) if full_segs else "/"


def stat_info(path, base="/"):
    """
    Retorna diccionario con metadatos del archivo/directorio o None si no existe.
    """
    full = safe_path(path, base)
    try:
        st = os.stat(full)
        is_directory = (st[0] & _DIR_TYPE) != 0
        return {
            "path": full,
            "type": "dir" if is_directory else "file",
            "size": 0 if is_directory else st[6],
        }
    except OSError:
        return None


def exists(path, base="/"):
    """Indica si la ruta existe dentro de base."""
    return stat_info(path, base) is not None


def is_dir(path, base="/"):
    """Indica si la ruta corresponde a un directorio."""
    info = stat_info(path, base)
    return info is not None and info["type"] == "dir"


def list_dir(path="/", base="/"):
    """
    Lista entradas de un directorio usando os.ilistdir() para minimizar RAM.
    Retorna lista ordenada (directorios primero, luego archivos alfabeticamente).
    """
    full = safe_path(path, base)
    entries = []

    if hasattr(os, "ilistdir"):
        for item in os.ilistdir(full):
            name = item[0]
            itype = item[1]
            # item[3] es tamano en puertos modernos de MicroPython (LittleFS)
            size = item[3] if len(item) > 3 else 0
            is_d = (itype & _DIR_TYPE) != 0
            entries.append(
                {
                    "name": name,
                    "type": "dir" if is_d else "file",
                    "size": 0 if is_d else size,
                }
            )
    else:
        for name in os.listdir(full):
            child = (full.rstrip("/") + "/" + name) if full != "/" else ("/" + name)
            st = os.stat(child)
            is_d = (st[0] & _DIR_TYPE) != 0
            entries.append(
                {
                    "name": name,
                    "type": "dir" if is_d else "file",
                    "size": 0 if is_d else st[6],
                }
            )

    entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
    return entries


def read_chunks(path, chunk_size=_DEFAULT_CHUNK, base="/", buf=None, internal=False):
    """
    Generador de streaming: lee un archivo en bloques usando un unico bytearray
    y memoryview para evitar asignaciones repetidas en el heap del ESP32-C3.
    """
    full = safe_path(path, base)
    if not internal and is_secret_path(full):
        raise ValueError("Acceso denegado a archivo secreto: {}".format(full))
    if buf is None:
        buf = bytearray(chunk_size)
    mv = memoryview(buf)

    with open(full, "rb") as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            yield mv[:n]


def write_chunks(path, chunk_iter, base="/", internal=False):
    """
    Escribe un iterable de bloques (bytes/bytearray/memoryview) en disco
    sin cargar el archivo completo en RAM. Retorna total de bytes escritos.
    """
    full = safe_path(path, base)
    if not internal and is_write_protected(full):
        raise ValueError("Ruta del sistema protegida contra escritura: {}".format(full))
    total = 0
    with open(full, "wb") as f:
        for chunk in chunk_iter:
            if chunk:
                f.write(chunk)
                total += len(chunk)
    return total


async def async_write_stream(path, reader, content_length, chunk_size=_DEFAULT_CHUNK, base="/", internal=False, timeout_s=10):
    """
    Escribe un stream asincrono (StreamReader de uasyncio) a disco por bloques
    con timeout por bloque para evitar bloqueos por clientes colgados.
    """
    try:
        import uasyncio as asyncio
    except ImportError:
        import asyncio

    full = safe_path(path, base)
    if not internal and is_write_protected(full):
        raise ValueError("Ruta del sistema protegida contra escritura: {}".format(full))
    remaining = int(content_length)
    total = 0

    with open(full, "wb") as f:
        while remaining > 0:
            to_read = chunk_size if remaining > chunk_size else remaining
            data = await asyncio.wait_for(reader.read(to_read), timeout_s)
            if not data:
                break
            f.write(data)
            n = len(data)
            total += n
            remaining -= n

    return total


def read_text(path, max_bytes=_DEFAULT_MAX_TEXT, base="/", internal=False):
    """
    Lee un archivo de texto acotando el tamano maximo para proteger la RAM.
    """
    full = safe_path(path, base)
    if not internal and is_secret_path(full):
        raise ValueError("Acceso denegado a archivo secreto: {}".format(full))
    info = stat_info(full, base)
    if info is None:
        raise OSError("Archivo no encontrado: {}".format(full))
    if info["type"] == "dir":
        raise OSError("La ruta es un directorio: {}".format(full))
    if info["size"] > max_bytes:
        raise ValueError(
            "Archivo demasiado grande para lectura completa ({} > {} B)".format(
                info["size"], max_bytes
            )
        )
    with open(full, "r") as f:
        return f.read()


def write_text(path, content, base="/", internal=False):
    """Escribe contenido de texto en un archivo validado."""
    full = safe_path(path, base)
    if not internal and is_write_protected(full):
        raise ValueError("Ruta del sistema protegida contra escritura: {}".format(full))
    with open(full, "w") as f:
        return f.write(content)


def read_json(path, default=None, base="/", internal=False):
    """Lee y parsea un archivo JSON pequeno; retorna 'default' si no existe o falla."""
    try:
        full = safe_path(path, base)
        if not internal and is_secret_path(full):
            return default
        with open(full, "r") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data, base="/", internal=False):
    """Serializa y guarda un objeto Python como JSON."""
    full = safe_path(path, base)
    if not internal and is_write_protected(full):
        raise ValueError("Ruta del sistema protegida contra escritura: {}".format(full))
    with open(full, "w") as f:
        json.dump(data, f)
    return True


def mkdir(path, base="/", parents=True):
    """Crea un directorio (y sus padres si parents=True) dentro de base."""
    full = safe_path(path, base)
    if full == "/":
        return "/"
    if not parents:
        os.mkdir(full)
        return full

    current = ""
    for seg in full.strip("/").split("/"):
        current += "/" + seg
        if not exists(current, "/"):
            os.mkdir(current)
    return full


def remove(path, base="/", recursive=False, force=False):
    """
    Elimina un archivo o directorio dentro de base.
    Bloquea la eliminacion de rutas criticas del sistema salvo force=True.
    """
    full = safe_path(path, base)
    if not force and full in _PROTECTED_PATHS:
        raise ValueError("Ruta critica protegida: {}".format(full))

    info = stat_info(full, "/")
    if info is None:
        raise OSError("No existe: {}".format(full))

    if info["type"] == "file":
        os.remove(full)
        return True

    # Directorio
    if recursive:
        for entry in list_dir(full, "/"):
            child = full.rstrip("/") + "/" + entry["name"]
            remove(child, "/", recursive=True, force=force)
    os.rmdir(full)
    return True


def rename(src, dst, base="/"):
    """Renombra o mueve un archivo/directorio dentro de base."""
    full_src = safe_path(src, base)
    full_dst = safe_path(dst, base)
    if full_src in _PROTECTED_PATHS:
        raise ValueError("No se puede mover ruta critica: {}".format(full_src))
    os.rename(full_src, full_dst)
    return full_dst


def copy_file(src, dst, chunk_size=_DEFAULT_CHUNK, base="/", internal=False):
    """Copia un archivo por bloques (streaming) sin cargarlo completo en RAM."""
    full_src = safe_path(src, base)
    full_dst = safe_path(dst, base)
    if not internal and is_secret_path(full_src):
        raise ValueError("Acceso denegado a archivo secreto: {}".format(full_src))
    if not internal and is_write_protected(full_dst):
        raise ValueError("Ruta del sistema protegida contra escritura: {}".format(full_dst))
    if is_dir(full_src, "/"):
        raise ValueError("Copia recursiva de carpetas no soportada; copia archivos individuales")
    total = 0
    buf = bytearray(chunk_size)
    with open(full_src, "rb") as fin, open(full_dst, "wb") as fout:
        while True:
            n = fin.readinto(buf)
            if not n:
                break
            fout.write(memoryview(buf)[:n])
            total += n
    return total
