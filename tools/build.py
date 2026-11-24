#!/usr/bin/env python3
"""
PyOS - Herramienta de Compilacion y Despliegue (Host)
1. Minifica de forma segura y comprime con gzip (nivel 9) /frontend_src -> /www/*.gz
2. Opcionalmente compila /sys/*.py a bytecode .mpy con mpy-cross
   (rv32imc para ESP32-C3, xtensawin para ESP32-S y ESP32-S3).
3. Opcionalmente despliega todo el sistema a la placa mediante mpremote.
"""

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend_src"
WWW_OUT = ROOT / "www"
SYS_SRC = ROOT / "sys"
MPY_OUT = ROOT / "build" / "mpy" / "sys"

BOARD_ARCH = {
    "esp32c3": "rv32imc",
    "esp32": "xtensawin",
    "esp32s3": "xtensawin",
}


def minify_css(text: str) -> str:
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([{}:;,>~])\s*", r"\1", text)
    text = text.replace(";}", "}")
    return text.strip()


def minify_html(text: str) -> str:
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r">\s+<", "><", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def minify_js_safe(text: str) -> str:
    """
    Minificador conservador de JS sin dependencias externas:
    elimina comentarios de bloque y lineas vacias/indentacion sin alterar literales.
    """
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        lines.append(s)
    return "\n".join(lines)


def build_frontend() -> None:
    if not FRONTEND_SRC.exists():
        raise FileNotFoundError(f"No existe {FRONTEND_SRC}")

    if WWW_OUT.exists():
        shutil.rmtree(WWW_OUT)
    WWW_OUT.mkdir(parents=True, exist_ok=True)

    total_raw = 0
    total_min = 0
    total_gz = 0

    print("=== [1/3] Minificando y comprimiendo frontend_src -> www/*.gz ===")
    for src_path in sorted(FRONTEND_SRC.rglob("*")):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(FRONTEND_SRC)
        dst_path = WWW_OUT / (str(rel) + ".gz")
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        raw_bytes = src_path.read_bytes()
        total_raw += len(raw_bytes)

        ext = src_path.suffix.lower()
        if ext in (".css", ".html", ".js"):
            txt = raw_bytes.decode("utf-8")
            if ext == ".css":
                txt = minify_css(txt)
            elif ext == ".html":
                txt = minify_html(txt)
            elif ext == ".js":
                txt = minify_js_safe(txt)
            min_bytes = txt.encode("utf-8")
        else:
            min_bytes = raw_bytes

        total_min += len(min_bytes)
        gz_bytes = gzip.compress(min_bytes, compresslevel=9, mtime=0)
        total_gz += len(gz_bytes)
        dst_path.write_bytes(gz_bytes)

        print(
            f"  {str(rel):28s} raw={len(raw_bytes):5d} B -> min={len(min_bytes):5d} B -> gz={len(gz_bytes):4d} B"
        )

    ratio = (1.0 - (total_gz / total_raw)) * 100.0 if total_raw else 0.0
    print(
        f"Total WWW: {total_raw/1024:.1f} KB -> {total_gz/1024:.1f} KB gzip ({ratio:.1f}% reduccion)\n"
    )


def build_mpy(board: str) -> bool:
    arch = BOARD_ARCH[board]
    mpy_cross = shutil.which("mpy-cross")
    if not mpy_cross:
        print(
            "[WARN] 'mpy-cross' no encontrado en PATH. "
            "Instala con 'pip install mpy-cross' para compilar /sys a .mpy.\n"
        )
        return False

    if MPY_OUT.exists():
        shutil.rmtree(MPY_OUT)
    MPY_OUT.mkdir(parents=True, exist_ok=True)

    print(f"=== [2/3] Compilando /sys/*.py -> .mpy (placa={board}, -march={arch}) ===")
    for py_file in sorted(SYS_SRC.glob("*.py")):
        out_file = MPY_OUT / (py_file.stem + ".mpy")
        cmd = [mpy_cross, f"-march={arch}", "-O2", str(py_file), "-o", str(out_file)]
        subprocess.run(cmd, check=True)
        print(f"  {py_file.name:14s} -> {out_file.name:14s} ({out_file.stat().st_size} B)")
    print()
    return True


def deploy_raw_repl(port: str, use_mpy: bool, baud: int = 115200) -> None:
    """
    Despliegue directo por Raw REPL usando pyserial con DTR=False, RTS=False.
    Resuelve el bloqueo de mpremote en placas ESP32-CAM-MB (CH340).
    """
    import base64
    import time
    import serial

    print(f"=== [3/3] Desplegando PyOS a {port} (modo --no-dtr / ESP32-CAM) ===")
    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0.2
    s.dtr = False
    s.rts = False
    s.open()
    s.dtr = False
    s.rts = False

    def read_until(marker: bytes, timeout_s: float = 5.0) -> bytes:
        buf = b""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            chunk = s.read(s.in_waiting or 1)
            if chunk:
                buf += chunk
                if marker in buf:
                    return buf
        raise TimeoutError(f"Timeout esperando {marker!r} (recibido: {buf[-120:]!r})")

    def raw_exec(code: str, timeout_s: float = 8.0) -> str:
        s.write(code.encode("utf-8") + b"\x04")
        resp = read_until(b"\x04>", timeout_s=timeout_s)
        # Formato raw REPL: OK<stdout>\x04<stderr>\x04>
        idx = resp.find(b"OK")
        if idx != -1:
            resp = resp[idx + 2 :]
        parts = resp.split(b"\x04")
        out = parts[0].decode("utf-8", "replace")
        err = parts[1].decode("utf-8", "replace") if len(parts) > 1 else ""
        if err.strip():
            raise RuntimeError(f"Error remoto:\n{err}")
        return out

    try:
        # Esperar arranque tras abrir el puerto, interrumpir y entrar en Raw REPL
        time.sleep(1.0)
        s.write(b"\r\x03\x03")
        try:
            read_until(b">>> ", timeout_s=3.0)
        except TimeoutError:
            s.write(b"\r\x03\x03")
            time.sleep(0.4)
        s.write(b"\x01")
        read_until(b"raw REPL; CTRL-B to exit\r\n>", timeout_s=4.0)

        # Crear directorios remotos
        raw_exec(
            "import os, binascii\n"
            "for d in ('/sys','/config','/apps','/www','/www/css','/www/js','/www/js/apps'):\n"
            "    try: os.mkdir(d)\n"
            "    except OSError: pass\n"
        )

        # Recolectar lista de archivos (local, remoto)
        files_to_send = [
            (ROOT / "boot.py", "/boot.py"),
            (ROOT / "main.py", "/main.py"),
            (ROOT / "config" / "boards.json", "/config/boards.json"),
            (ROOT / "config" / "system.json", "/config/system.json"),
        ]
        if (ROOT / "config" / "oled.json").exists():
            files_to_send.append((ROOT / "config" / "oled.json", "/config/oled.json"))
        if (ROOT / "config" / "mqtt.json").exists():
            files_to_send.append((ROOT / "config" / "mqtt.json", "/config/mqtt.json"))
        if (ROOT / "config" / "ca_bundle.pem").exists():
            files_to_send.append((ROOT / "config" / "ca_bundle.pem", "/config/ca_bundle.pem"))
        if (ROOT / "config" / "wifi.json").exists():
            files_to_send.append((ROOT / "config" / "wifi.json", "/config/wifi.json"))
        if (ROOT / "apps").exists():
            for f in sorted((ROOT / "apps").glob("*.py")):
                files_to_send.append((f, f"/apps/{f.name}"))

        sys_dir = MPY_OUT if (use_mpy and MPY_OUT.exists()) else SYS_SRC
        sys_glob = "*.mpy" if sys_dir == MPY_OUT else "*.py"
        for f in sorted(sys_dir.glob(sys_glob)):
            files_to_send.append((f, f"/sys/{f.name}"))

        for f in sorted(WWW_OUT.rglob("*.gz")):
            rel = f.relative_to(WWW_OUT).as_posix()
            files_to_send.append((f, f"/www/{rel}"))

        # Subir cada archivo en bloques base64 de 384 B (seguro y rapido)
        for local_p, remote_p in files_to_send:
            data = local_p.read_bytes()
            raw_exec(f"_f=open({remote_p!r},'wb')\n")
            for i in range(0, len(data), 384):
                b64 = base64.b64encode(data[i : i + 384]).decode("ascii")
                raw_exec(f"_f.write(binascii.a2b_base64({b64!r}))\n")
            raw_exec("_f.close()\n")
            print(f"  OK {remote_p:32s} ({len(data):5d} B)")

        # Salir de Raw REPL (Ctrl-B) y soft-reset (Ctrl-D) para arrancar PyOS
        s.write(b"\x02")
        time.sleep(0.1)
        s.write(b"\x04")
        time.sleep(1.5)
        boot_out = s.read(s.in_waiting or 1).decode("utf-8", "replace")
        if boot_out.strip():
            print("\n--- Salida de arranque ---")
            print(boot_out.strip())
        print("\nDespliegue completado con exito (modo --no-dtr).\n")
    finally:
        s.close()


def deploy_board(port: str, use_mpy: bool) -> None:
    mpremote = shutil.which("mpremote")
    if not mpremote:
        raise RuntimeError("'mpremote' no encontrado en PATH.")

    print(f"=== [3/3] Desplegando PyOS a {port} ({'bytecode .mpy' if use_mpy else 'fuente .py'}) ===")

    def run_mp(*args):
        cmd = [mpremote, "connect", port, *args]
        subprocess.run(cmd, check=True)

    # Crear arbol de directorios remoto (ignorar si ya existen)
    for d in (":/sys", ":/config", ":/apps", ":/www", ":/www/css", ":/www/js", ":/www/js/apps"):
        subprocess.run([mpremote, "connect", port, "fs", "mkdir", d], stderr=subprocess.DEVNULL)

    ops = [
        "fs", "cp", str(ROOT / "boot.py"), ":/boot.py",
        "+", "fs", "cp", str(ROOT / "main.py"), ":/main.py",
        "+", "fs", "cp", str(ROOT / "config" / "boards.json"), ":/config/boards.json",
        "+", "fs", "cp", str(ROOT / "config" / "system.json"), ":/config/system.json",
    ]

    oled_local = ROOT / "config" / "oled.json"
    if oled_local.exists():
        ops += ["+", "fs", "cp", str(oled_local), ":/config/oled.json"]
    mqtt_local = ROOT / "config" / "mqtt.json"
    if mqtt_local.exists():
        ops += ["+", "fs", "cp", str(mqtt_local), ":/config/mqtt.json"]
    ca_local = ROOT / "config" / "ca_bundle.pem"
    if ca_local.exists():
        ops += ["+", "fs", "cp", str(ca_local), ":/config/ca_bundle.pem"]

    apps_dir = ROOT / "apps"
    if apps_dir.exists():
        for f in sorted(apps_dir.glob("*.py")):
            ops += ["+", "fs", "cp", str(f), f":/apps/{f.name}"]

    # No sobreescribir wifi.json si ya existe en la placa? Copiar solo si se pasa --wifi o no existe
    wifi_local = ROOT / "config" / "wifi.json"
    if wifi_local.exists():
        ops += ["+", "fs", "cp", str(wifi_local), ":/config/wifi.json"]

    sys_dir = MPY_OUT if (use_mpy and MPY_OUT.exists()) else SYS_SRC
    sys_glob = "*.mpy" if sys_dir == MPY_OUT else "*.py"
    for f in sorted(sys_dir.glob(sys_glob)):
        ops += ["+", "fs", "cp", str(f), f":/sys/{f.name}"]

    for f in sorted(WWW_OUT.rglob("*.gz")):
        rel = f.relative_to(WWW_OUT).as_posix()
        ops += ["+", "fs", "cp", str(f), f":/www/{rel}"]

    run_mp(*ops)
    print("Despliegue completado con exito.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="PyOS Build & Deploy Tool")
    parser.add_argument(
        "--board",
        choices=list(BOARD_ARCH.keys()),
        default="esp32c3",
        help="Placa objetivo para mpy-cross (default: esp32c3)",
    )
    parser.add_argument("--mpy", action="store_true", help="Compilar /sys/*.py a .mpy")
    parser.add_argument("--deploy", action="store_true", help="Desplegar a la placa con mpremote")
    parser.add_argument("--no-dtr", action="store_true", help="Usar Raw REPL sin DTR/RTS (ideal para ESP32-CAM-MB)")
    parser.add_argument("--port", default="/dev/ttyACM0", help="Puerto serie para despliegue")
    args = parser.parse_args()

    build_frontend()
    mpy_ok = False
    if args.mpy:
        mpy_ok = build_mpy(args.board)
    if args.deploy:
        if args.no_dtr:
            deploy_raw_repl(args.port, use_mpy=mpy_ok)
        else:
            deploy_board(args.port, use_mpy=mpy_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
