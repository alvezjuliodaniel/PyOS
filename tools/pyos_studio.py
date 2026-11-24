#!/usr/bin/env python3
"""
PyOS Studio - Herramienta grafica (Tkinter) para Linux
Permite gestionar la placa ESP32-C3 / ESP32-S / ESP32-S3 por USB:
  - Detectar puerto serie y placa conectada
  - Compilar frontend (.gz) y bytecode (.mpy opcional)
  - Instalacion completa o actualizacion rapida (solo /www o solo /sys)
  - Subir archivos individuales y explorar LittleFS
  - Configurar Wi-Fi (STA + AP + txpower para antena roja) y escanear redes
  - Ejecutar pruebas y comandos Python en la placa
"""

import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = ROOT / "tools" / "build.py"
WIFI_JSON_PATH = ROOT / "config" / "wifi.json"
WWW_DIR = ROOT / "www"
SYS_DIR = ROOT / "sys"
MPY_DIR = ROOT / "build" / "mpy" / "sys"

BOARDS = [
    ("ESP32-C3 (RISC-V / rv32imc)", "esp32c3"),
    ("ESP32-S Clasico (Xtensa / xtensawin)", "esp32"),
    ("ESP32-S3 (Xtensa / xtensawin)", "esp32s3"),
]

TX_PRESETS = ["8.5", "11.0", "15.0", "19.5", "20.0"]


class PyOSStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PyOS Studio — Gestor USB para ESP32")
        self.geometry("920x680")
        self.minsize(820, 600)

        self.log_queue = queue.Queue()
        self.busy = False

        # Variables de estado
        self.var_port = tk.StringVar(value="")
        self.var_board = tk.StringVar(value=BOARDS[0][0])
        self.var_use_mpy = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="Listo")

        # Variables Wi-Fi
        self.var_sta_ssid = tk.StringVar()
        self.var_sta_pass = tk.StringVar()
        self.var_sta_timeout = tk.StringVar(value="15")
        self.var_sta_reconn = tk.StringVar(value="30")
        self.var_sta_tx = tk.StringVar(value="8.5")
        self.var_ap_ssid = tk.StringVar(value="PyOS-Setup")
        self.var_ap_pass = tk.StringVar(value="")
        self.var_ap_ip = tk.StringVar(value="192.168.4.1")
        self.var_ap_ch = tk.StringVar(value="6")
        self.var_ap_tx = tk.StringVar(value="8.5")
        self.var_show_pass = tk.BooleanVar(value=False)

        # Variables subida individual y REPL
        self.var_custom_local = tk.StringVar()
        self.var_custom_remote = tk.StringVar(value="/apps/")
        self.var_repl_cmd = tk.StringVar(value="import hal; print(hal.get_hal().get_telemetry())")

        self._setup_theme()
        self._build_ui()
        self._refresh_ports()
        self._load_local_wifi()
        self.after(100, self._poll_logs)

    # ------------------------------------------------------------------
    # Tema oscuro limpio (ttk clam)
    # ------------------------------------------------------------------

    def _setup_theme(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        bg = "#0f172a"
        panel = "#1e293b"
        fg = "#e2e8f0"
        muted = "#94a3b8"
        accent = "#38bdf8"

        self.configure(bg=bg)
        style.configure(".", background=bg, foreground=fg, font=("Sans", 10))
        style.configure("TFrame", background=bg)
        style.configure("Card.TLabelframe", background=panel, foreground=accent, bordercolor="#334155")
        style.configure("Card.TLabelframe.Label", background=panel, foreground=accent, font=("Sans", 10, "bold"))
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Panel.TLabel", background=panel, foreground=fg)
        style.configure("Muted.TLabel", background=panel, foreground=muted, font=("Sans", 9))
        style.configure("TButton", background="#334155", foreground=fg, padding=(10, 5), borderwidth=1)
        style.map("TButton", background=[("active", "#475569"), ("disabled", "#1e293b")])
        style.configure("Accent.TButton", background="#0284c7", foreground="#ffffff", padding=(10, 5))
        style.map("Accent.TButton", background=[("active", "#0369a1")])
        style.configure("Ok.TButton", background="#15803d", foreground="#ffffff", padding=(10, 5))
        style.map("Ok.TButton", background=[("active", "#166534")])
        style.configure("TEntry", fieldbackground="#020617", foreground=fg, insertcolor=fg)
        style.configure("TCombobox", fieldbackground="#020617", foreground=fg, background="#334155")
        style.configure("TCheckbutton", background=bg, foreground=fg)
        style.configure("Panel.TCheckbutton", background=panel, foreground=fg)
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel, foreground=muted, padding=(14, 7))
        style.map("TNotebook.Tab", background=[("selected", "#0284c7")], foreground=[("selected", "#ffffff")])

    # ------------------------------------------------------------------
    # Construccion de la Interfaz
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Barra superior: Puerto USB + Placa + Detectar
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill="x")

        ttk.Label(top, text="Puerto USB:").pack(side="left")
        self.cb_port = ttk.Combobox(top, textvariable=self.var_port, width=18, state="readonly")
        self.cb_port.pack(side="left", padx=(6, 4))
        ttk.Button(top, text="↻", width=3, command=self._refresh_ports).pack(side="left", padx=(0, 14))

        ttk.Label(top, text="Placa:").pack(side="left")
        self.cb_board = ttk.Combobox(
            top,
            textvariable=self.var_board,
            values=[b[0] for b in BOARDS],
            width=30,
            state="readonly",
        )
        self.cb_board.pack(side="left", padx=(6, 10))

        has_mpy = shutil.which("mpy-cross") is not None
        self.chk_mpy = ttk.Checkbutton(
            top,
            text="Compilar .mpy" + ("" if has_mpy else " (sin mpy-cross)"),
            variable=self.var_use_mpy,
            state="normal" if has_mpy else "disabled",
        )
        self.chk_mpy.pack(side="left", padx=(0, 10))

        ttk.Button(top, text="🔍 Detectar Placa", style="Accent.TButton", command=self._detect_board).pack(side="right")

        # Notebook principal
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(4, 6))

        self.tab_deploy = ttk.Frame(nb, padding=10)
        self.tab_wifi = ttk.Frame(nb, padding=10)
        self.tab_tools = ttk.Frame(nb, padding=10)

        nb.add(self.tab_deploy, text="  🚀 Despliegue y Actualización  ")
        nb.add(self.tab_wifi, text="  📶 Configuración Wi-Fi  ")
        nb.add(self.tab_tools, text="  🛠️ Pruebas y REPL USB  ")

        self._build_deploy_tab()
        self._build_wifi_tab()
        self._build_tools_tab()

        # Consola de salida (compartida abajo)
        log_frame = ttk.LabelFrame(self, text="Salida / Logs USB", style="Card.TLabelframe", padding=6)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        self.txt_log = scrolledtext.ScrolledText(
            log_frame,
            height=11,
            bg="#020617",
            fg="#cbd5e1",
            insertbackground="#38bdf8",
            font=("Monospace", 9),
            relief="flat",
            state="disabled",
        )
        self.txt_log.pack(fill="both", expand=True)

        # Barra inferior de estado y progreso
        bottom = ttk.Frame(self, padding=(12, 4, 12, 8))
        bottom.pack(fill="x")

        self.lbl_status = ttk.Label(bottom, textvariable=self.var_status)
        self.lbl_status.pack(side="left")

        ttk.Button(bottom, text="Limpiar consola", command=self._clear_log).pack(side="right", padx=(8, 0))
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=180)
        self.progress.pack(side="right")

    def _build_deploy_tab(self):
        # Acciones principales
        box1 = ttk.LabelFrame(self.tab_deploy, text="Compilación y Despliegue de PyOS", style="Card.TLabelframe", padding=10)
        box1.pack(fill="x", pady=(0, 10))

        r1 = ttk.Frame(box1)
        r1.pack(fill="x", pady=4)
        ttk.Button(
            r1,
            text="⚡ Instalación Completa (Build + Subir Todo + Reset)",
            style="Ok.TButton",
            command=self._deploy_full,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r1,
            text="🌐 Actualizar solo Frontend (/www/*.gz)",
            style="Accent.TButton",
            command=self._deploy_www_only,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            r1,
            text="🔧 Actualizar solo Kernel (/sys)",
            command=self._deploy_sys_only,
        ).pack(side="left")

        r2 = ttk.Frame(box1)
        r2.pack(fill="x", pady=(6, 2))
        ttk.Button(r2, text="📦 Solo Compilar Frontend (/www/*.gz)", command=self._build_only).pack(side="left", padx=(0, 8))
        ttk.Button(r2, text="🔄 Reiniciar ESP32 (Reset)", command=self._reset_board).pack(side="left", padx=(0, 8))
        ttk.Button(r2, text="📂 Listar archivos en ESP32", command=self._list_remote_fs).pack(side="left")

        ttk.Label(
            box1,
            text="Nota: 'Actualizar solo Frontend' y 'Actualizar solo Kernel' conservan tu /config/wifi.json intacto en la placa.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        # Subida de archivo individual
        box2 = ttk.LabelFrame(self.tab_deploy, text="Subir Archivo Individual a la ESP32", style="Card.TLabelframe", padding=10)
        box2.pack(fill="x")

        grid = ttk.Frame(box2)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Archivo local:", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.var_custom_local).grid(row=0, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(grid, text="Examinar...", command=self._browse_local_file).grid(row=0, column=2, pady=4)

        ttk.Label(grid, text="Ruta en ESP32:", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(grid, textvariable=self.var_custom_remote).grid(row=1, column=1, sticky="ew", padx=8, pady=4)
        ttk.Button(grid, text="⬆ Subir Archivo", style="Accent.TButton", command=self._upload_single_file).grid(row=1, column=2, pady=4)

    def _build_wifi_tab(self):
        top_grid = ttk.Frame(self.tab_wifi)
        top_grid.pack(fill="x")
        top_grid.columnconfigure(0, weight=1)
        top_grid.columnconfigure(1, weight=1)

        # Modo Estacion (STA)
        f_sta = ttk.LabelFrame(top_grid, text="Wi-Fi Doméstico (Modo STA)", style="Card.TLabelframe", padding=10)
        f_sta.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        f_sta.columnconfigure(1, weight=1)

        ttk.Label(f_sta, text="SSID (2.4 GHz):", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(f_sta, textvariable=self.var_sta_ssid).grid(row=0, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(f_sta, text="Contraseña:", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        self.ent_sta_pass = ttk.Entry(f_sta, textvariable=self.var_sta_pass, show="*")
        self.ent_sta_pass.grid(row=1, column=1, sticky="ew", padx=6, pady=3)

        ttk.Checkbutton(
            f_sta,
            text="Mostrar contraseñas",
            variable=self.var_show_pass,
            style="Panel.TCheckbutton",
            command=self._toggle_pass_visibility,
        ).grid(row=2, column=1, sticky="w", padx=6, pady=2)

        ttk.Label(f_sta, text="Potencia TX (dBm):", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(f_sta, textvariable=self.var_sta_tx, values=TX_PRESETS, width=10).grid(row=3, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(f_sta, text="Timeout (s):", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(f_sta, textvariable=self.var_sta_timeout, width=10).grid(row=4, column=1, sticky="w", padx=6, pady=3)

        # Modo AP de respaldo
        f_ap = ttk.LabelFrame(top_grid, text="Access Point de Respaldo (Modo AP)", style="Card.TLabelframe", padding=10)
        f_ap.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        f_ap.columnconfigure(1, weight=1)

        ttk.Label(f_ap, text="SSID AP:", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(f_ap, textvariable=self.var_ap_ssid).grid(row=0, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(f_ap, text="Password AP:", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        self.ent_ap_pass = ttk.Entry(f_ap, textvariable=self.var_ap_pass, show="*")
        self.ent_ap_pass.grid(row=1, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(f_ap, text="IP AP:", style="Panel.TLabel").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(f_ap, textvariable=self.var_ap_ip).grid(row=2, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(f_ap, text="Potencia TX (dBm):", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=3)
        ttk.Combobox(f_ap, textvariable=self.var_ap_tx, values=TX_PRESETS, width=10).grid(row=3, column=1, sticky="w", padx=6, pady=3)

        ttk.Label(f_ap, text="Canal Wi-Fi:", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(f_ap, textvariable=self.var_ap_ch, width=10).grid(row=4, column=1, sticky="w", padx=6, pady=3)

        # Botones Wi-Fi
        actions = ttk.Frame(self.tab_wifi)
        actions.pack(fill="x", pady=(10, 6))
        ttk.Button(actions, text="💾 Guardar Local + Subir a ESP32 y Reiniciar", style="Ok.TButton", command=self._save_and_push_wifi).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="📡 Escanear Redes desde ESP32", style="Accent.TButton", command=self._scan_wifi_usb).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="⬇ Leer wifi.json de ESP32", command=self._pull_wifi_from_board).pack(side="left")

        ttk.Label(
            self.tab_wifi,
            text="Tip ESP32-C3 SuperMini (antena roja): usa 8.5 dBm para máxima estabilidad de señal.",
            style="Muted.TLabel",
        ).pack(anchor="w")

        # Lista de redes escaneadas
        scan_box = ttk.LabelFrame(self.tab_wifi, text="Redes 2.4 GHz Detectadas (doble clic para seleccionar)", style="Card.TLabelframe", padding=6)
        scan_box.pack(fill="both", expand=True, pady=(6, 0))

        self.lst_wifi = tk.Listbox(scan_box, height=5, bg="#020617", fg="#e2e8f0", selectbackground="#0284c7", font=("Monospace", 9), relief="flat")
        self.lst_wifi.pack(fill="both", expand=True)
        self.lst_wifi.bind("<Double-Button-1>", self._on_wifi_select)

    def _build_tools_tab(self):
        # Pruebas automaticas en hardware
        tbox = ttk.LabelFrame(self.tab_tools, text="Batería de Pruebas en Hardware Real", style="Card.TLabelframe", padding=10)
        tbox.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(tbox)
        row.pack(fill="x")
        tests = [
            ("Fase 0: HAL", "tests/test_hal.py"),
            ("Fase 1: Kernel/FS", "tests/test_phase1.py"),
            ("Fase 2: Wi-Fi", "tests/test_phase2.py"),
            ("Fase 3: HTTP/WS", "tests/test_phase3.py"),
            ("Fase 6: Estrés C3", "tests/test_stress_c3.py"),
        ]
        for label, rel in tests:
            ttk.Button(row, text=label, command=lambda p=rel: self._run_test_script(p)).pack(side="left", padx=(0, 6))

        # Consola REPL rapida por USB
        rbox = ttk.LabelFrame(self.tab_tools, text="Ejecutar Expresión / Script Python en la Placa (REPL USB)", style="Card.TLabelframe", padding=10)
        rbox.pack(fill="x")

        rrow = ttk.Frame(rbox)
        rrow.pack(fill="x")
        ent = ttk.Entry(rrow, textvariable=self.var_repl_cmd)
        ent.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ent.bind("<Return>", lambda _e: self._run_repl_cmd())
        ttk.Button(rrow, text="▶ Ejecutar", style="Accent.TButton", command=self._run_repl_cmd).pack(side="left", padx=(0, 6))
        ttk.Button(rrow, text="🌐 Ver IP Actual", command=self._check_ip).pack(side="left")

    # ------------------------------------------------------------------
    # Utilidades de Logging y Ejecucion en Hilo
    # ------------------------------------------------------------------

    def _log(self, msg: str):
        self.log_queue.put(msg)

    def _clear_log(self):
        self.txt_log.configure(state="normal")
        self.txt_log.delete("1.0", "end")
        self.txt_log.configure(state="disabled")

    def _poll_logs(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.txt_log.configure(state="normal")
                self.txt_log.insert("end", line + "\n")
                self.txt_log.see("end")
                self.txt_log.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(80, self._poll_logs)

    def _set_busy(self, busy: bool, status: str):
        self.busy = busy
        self.var_status.set(status)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _run_bg(self, title: str, fn):
        if self.busy:
            messagebox.showwarning("Ocupado", "Ya hay una operación en curso. Espera a que termine.")
            return

        self._set_busy(True, title)

        def worker():
            try:
                fn()
                self.after(0, lambda: self._set_busy(False, "Listo"))
            except Exception as exc:
                self._log(f"[ERROR] {exc}")
                self.after(0, lambda: self._set_busy(False, f"Error: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _run_cmd(self, cmd: list, check=True) -> str:
        self._log("$ " + " ".join(str(c) for c in cmd))
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        out_lines = []
        assert proc.stdout is not None
        for line in proc.stdout:
            clean = line.rstrip("\r\n")
            if clean:
                out_lines.append(clean)
                # Filtrar barras de progreso repetitivas de mpremote
                if not clean.strip().startswith("... copying"):
                    self._log(clean)
        rc = proc.wait()
        if check and rc != 0:
            raise RuntimeError(f"Comando finalizó con código {rc}")
        return "\n".join(out_lines)

    def _get_port(self) -> str:
        p = self.var_port.get().strip()
        if not p:
            raise RuntimeError("No hay ningún puerto USB seleccionado. Conecta la placa y pulsa ↻.")
        return p

    def _get_board_id(self) -> str:
        lbl = self.var_board.get()
        for name, bid in BOARDS:
            if name == lbl:
                return bid
        return "esp32c3"

    # ------------------------------------------------------------------
    # Acciones de Hardware y Despliegue
    # ------------------------------------------------------------------

    def _refresh_ports(self):
        ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
        self.cb_port["values"] = ports
        if ports:
            if self.var_port.get() not in ports:
                self.var_port.set(ports[0])
        else:
            self.var_port.set("")
        self._log(f"[USB] Puertos detectados: {ports if ports else 'ninguno'}")

    def _detect_board(self):
        def task():
            port = self._get_port()
            code = (
                "import os, gc, sys; "
                "sys.path.append('/sys'); "
                "print('MACHINE:', os.uname().machine); "
                "print('MPY:', sys.implementation.version); "
                "print('RAM_FREE:', gc.mem_free()); "
                "st = os.statvfs('/'); "
                "print('FLASH_KB:', (st[0]*st[2])//1024, 'FREE_KB:', (st[0]*st[3])//1024)"
            )
            out = self._run_cmd(["mpremote", "connect", port, "exec", code])
            upper = out.upper()
            if "ESP32C3" in upper or "ESP32-C3" in upper:
                self.after(0, lambda: self.var_board.set(BOARDS[0][0]))
            elif "ESP32S3" in upper or "ESP32-S3" in upper:
                self.after(0, lambda: self.var_board.set(BOARDS[2][0]))
            elif "ESP32" in upper:
                self.after(0, lambda: self.var_board.set(BOARDS[1][0]))

        self._run_bg("Detectando placa por USB...", task)

    def _build_only(self):
        def task():
            cmd = [sys.executable, str(BUILD_SCRIPT), "--board", self._get_board_id()]
            if self.var_use_mpy.get():
                cmd.append("--mpy")
            self._run_cmd(cmd)
            self._log("[OK] Compilación finalizada.")

        self._run_bg("Compilando frontend...", task)

    def _deploy_full(self):
        def task():
            port = self._get_port()
            cmd = [
                sys.executable,
                str(BUILD_SCRIPT),
                "--board",
                self._get_board_id(),
                "--deploy",
                "--port",
                port,
            ]
            if self.var_use_mpy.get():
                cmd.append("--mpy")
            self._run_cmd(cmd)
            self._run_cmd(["mpremote", "connect", port, "reset"])
            self._log("[OK] Instalación completa finalizada y placa reiniciada.")

        self._run_bg("Instalación completa en ESP32...", task)

    def _deploy_www_only(self):
        def task():
            port = self._get_port()
            self._run_cmd([sys.executable, str(BUILD_SCRIPT), "--board", self._get_board_id()])
            for d in (":/www", ":/www/css", ":/www/js", ":/www/js/apps"):
                subprocess.run(["mpremote", "connect", port, "fs", "mkdir", d], stderr=subprocess.DEVNULL)
            ops = []
            for f in sorted(WWW_DIR.rglob("*.gz")):
                rel = f.relative_to(WWW_DIR).as_posix()
                if ops:
                    ops.append("+")
                ops += ["fs", "cp", str(f), f":/www/{rel}"]
            ops += ["+", "reset"]
            self._run_cmd(["mpremote", "connect", port, *ops])
            self._log("[OK] Frontend /www actualizado y placa reiniciada.")

        self._run_bg("Actualizando /www/*.gz...", task)

    def _deploy_sys_only(self):
        def task():
            port = self._get_port()
            use_mpy = self.var_use_mpy.get()
            if use_mpy:
                self._run_cmd([sys.executable, str(BUILD_SCRIPT), "--board", self._get_board_id(), "--mpy"])
            src_dir = MPY_DIR if (use_mpy and MPY_DIR.exists()) else SYS_DIR
            pattern = "*.mpy" if src_dir == MPY_DIR else "*.py"
            ops = [
                "fs", "cp", str(ROOT / "boot.py"), ":/boot.py",
                "+", "fs", "cp", str(ROOT / "main.py"), ":/main.py",
                "+", "fs", "cp", str(ROOT / "config" / "boards.json"), ":/config/boards.json",
                "+", "fs", "cp", str(ROOT / "config" / "system.json"), ":/config/system.json",
            ]
            for f in sorted(src_dir.glob(pattern)):
                ops += ["+", "fs", "cp", str(f), f":/sys/{f.name}"]
            ops += ["+", "reset"]
            self._run_cmd(["mpremote", "connect", port, *ops])
            self._log("[OK] Núcleo /sys actualizado y placa reiniciada.")

        self._run_bg("Actualizando /sys en ESP32...", task)

    def _reset_board(self):
        self._run_bg("Reiniciando placa...", lambda: self._run_cmd(["mpremote", "connect", self._get_port(), "reset"]))

    def _list_remote_fs(self):
        def task():
            port = self._get_port()
            code = (
                "import os\n"
                "def walk(p, ind=''):\n"
                "  for it in sorted(os.ilistdir(p)):\n"
                "    n, t = it[0], it[1]\n"
                "    full = (p.rstrip('/') + '/' + n) if p != '/' else ('/' + n)\n"
                "    if t & 0x4000:\n"
                "      print(ind + '[DIR] ' + n + '/')\n"
                "      walk(full, ind + '  ')\n"
                "    else:\n"
                "      sz = it[3] if len(it) > 3 else 0\n"
                "      print(ind + n + ' (' + str(sz) + ' B)')\n"
                "walk('/')\n"
            )
            self._run_cmd(["mpremote", "connect", port, "exec", code])

        self._run_bg("Listando sistema de archivos...", task)

    def _browse_local_file(self):
        path = filedialog.askopenfilename(initialdir=str(ROOT), title="Seleccionar archivo a subir")
        if path:
            self.var_custom_local.set(path)
            fname = os.path.basename(path)
            cur = self.var_custom_remote.get().strip() or "/apps/"
            if cur.endswith("/"):
                self.var_custom_remote.set(cur + fname)

    def _upload_single_file(self):
        local = self.var_custom_local.get().strip()
        remote = self.var_custom_remote.get().strip()
        if not local or not os.path.isfile(local):
            messagebox.showerror("Error", "Selecciona un archivo local válido.")
            return
        if not remote.startswith("/"):
            remote = "/" + remote

        def task():
            port = self._get_port()
            self._run_cmd(["mpremote", "connect", port, "fs", "cp", local, f":{remote}"])
            self._log(f"[OK] Archivo subido a :{remote}")

        self._run_bg(f"Subiendo a {remote}...", task)

    # ------------------------------------------------------------------
    # Configuracion Wi-Fi
    # ------------------------------------------------------------------

    def _toggle_pass_visibility(self):
        ch = "" if self.var_show_pass.get() else "*"
        self.ent_sta_pass.configure(show=ch)
        self.ent_ap_pass.configure(show=ch)

    def _apply_wifi_dict(self, cfg: dict):
        sta = cfg.get("sta", {})
        ap = cfg.get("ap", {})
        self.var_sta_ssid.set(sta.get("ssid", ""))
        self.var_sta_pass.set(sta.get("password", ""))
        self.var_sta_timeout.set(str(sta.get("timeout_s", 15)))
        self.var_sta_reconn.set(str(sta.get("reconnect_interval_s", 30)))
        self.var_sta_tx.set(str(sta.get("txpower", 8.5)))

        self.var_ap_ssid.set(ap.get("ssid", "PyOS-Setup"))
        self.var_ap_pass.set(ap.get("password", ""))
        self.var_ap_ip.set(ap.get("ip", "192.168.4.1"))
        self.var_ap_ch.set(str(ap.get("channel", 6)))
        self.var_ap_tx.set(str(ap.get("txpower", 8.5)))

    def _load_local_wifi(self):
        if WIFI_JSON_PATH.exists():
            try:
                cfg = json.loads(WIFI_JSON_PATH.read_text(encoding="utf-8"))
                self._apply_wifi_dict(cfg)
            except Exception as e:
                self._log(f"[WARN] No se pudo leer config/wifi.json: {e}")

    def _build_wifi_dict(self) -> dict:
        return {
            "sta": {
                "ssid": self.var_sta_ssid.get().strip(),
                "password": self.var_sta_pass.get(),
                "timeout_s": int(self.var_sta_timeout.get() or 15),
                "reconnect_interval_s": int(self.var_sta_reconn.get() or 30),
                "txpower": float(self.var_sta_tx.get() or 8.5),
            },
            "ap": {
                "ssid": self.var_ap_ssid.get().strip() or "PyOS-Setup",
                "password": self.var_ap_pass.get(),
                "ip": self.var_ap_ip.get().strip() or "192.168.4.1",
                "channel": int(self.var_ap_ch.get() or 6),
                "txpower": float(self.var_ap_tx.get() or 8.5),
                "hidden": False,
            },
        }

    def _save_and_push_wifi(self):
        try:
            cfg = self._build_wifi_dict()
        except ValueError as e:
            messagebox.showerror("Valor inválido", f"Revisa los campos numéricos: {e}")
            return

        WIFI_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        WIFI_JSON_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        self._log("[OK] Guardado en config/wifi.json local.")

        def task():
            port = self._get_port()
            self._run_cmd([
                "mpremote", "connect", port,
                "fs", "cp", str(WIFI_JSON_PATH), ":/config/wifi.json",
                "+", "reset",
            ])
            self._log("[OK] wifi.json subido y placa reiniciada.")

        self._run_bg("Subiendo configuración Wi-Fi...", task)

    def _pull_wifi_from_board(self):
        def task():
            port = self._get_port()
            out = self._run_cmd(["mpremote", "connect", port, "fs", "cat", ":/config/wifi.json"])
            cfg = json.loads(out)
            self.after(0, lambda: self._apply_wifi_dict(cfg))
            self._log("[OK] Configuración Wi-Fi cargada desde la ESP32.")

        self._run_bg("Leyendo wifi.json de la placa...", task)

    def _scan_wifi_usb(self):
        def task():
            port = self._get_port()
            code = (
                "import network, json; "
                "sta = network.WLAN(network.STA_IF); sta.active(True); "
                "nets = {}; "
                "[nets.update({x[0].decode('utf-8','ignore'): (x[3], x[4])}) for x in sta.scan() if x[0]]; "
                "out = sorted([{'ssid': k, 'rssi': v[0], 'auth': v[1]} for k, v in nets.items()], key=lambda a: a['rssi'], reverse=True); "
                "print('SCAN_JSON:' + json.dumps(out))"
            )
            out = self._run_cmd(["mpremote", "connect", port, "exec", code])
            for line in out.splitlines():
                if line.startswith("SCAN_JSON:"):
                    nets = json.loads(line[len("SCAN_JSON:"):])

                    def fill():
                        self.lst_wifi.delete(0, "end")
                        for n in nets:
                            lock = "🔒" if n["auth"] else "🔓"
                            self.lst_wifi.insert("end", f"{lock} {n['ssid']}  ({n['rssi']} dBm)")

                    self.after(0, fill)
                    break

        self._run_bg("Escaneando redes 2.4 GHz desde ESP32...", task)

    def _on_wifi_select(self, _event):
        sel = self.lst_wifi.curselection()
        if not sel:
            return
        text = self.lst_wifi.get(sel[0])
        # Formato: "🔒 SSID  (-60 dBm)"
        ssid = text[2:].rsplit("  (", 1)[0].strip()
        self.var_sta_ssid.set(ssid)
        self.ent_sta_pass.focus_set()

    # ------------------------------------------------------------------
    # Pruebas y REPL USB
    # ------------------------------------------------------------------

    def _run_test_script(self, rel_path: str):
        full = ROOT / rel_path
        if not full.exists():
            messagebox.showerror("Error", f"No existe {full}")
            return
        self._run_bg(
            f"Ejecutando {rel_path}...",
            lambda: self._run_cmd(["mpremote", "connect", self._get_port(), "run", str(full)]),
        )

    def _run_repl_cmd(self):
        cmd_str = self.var_repl_cmd.get().strip()
        if not cmd_str:
            return
        self._run_bg(
            "Ejecutando en REPL...",
            lambda: self._run_cmd(["mpremote", "connect", self._get_port(), "exec", cmd_str]),
        )

    def _check_ip(self):
        code = (
            "import network; "
            "sta = network.WLAN(network.STA_IF); ap = network.WLAN(network.AP_IF); "
            "print('STA active:', sta.active(), 'connected:', sta.isconnected(), 'cfg:', sta.ifconfig()); "
            "print('AP  active:', ap.active(), 'cfg:', ap.ifconfig())"
        )
        self._run_bg(
            "Consultando IP de la placa...",
            lambda: self._run_cmd(["mpremote", "connect", self._get_port(), "exec", code]),
        )


def main():
    app = PyOSStudio()
    app.mainloop()


if __name__ == "__main__":
    main()
