"""
MicroOS - Gestor Dinamico de Aplicaciones (AppManager)
Permite instalar, cargar en caliente, recargar y desinstalar aplicaciones
en /apps/<id>/ sin modificar el firmware MicroPython ni reiniciar la placa.

Estructura de una app en /apps/<id>/:
  - manifest.json : metadatos (id, name, version, icon, description, ui, backend, autostart)
  - backend.py    : opcional; puede definir rpc(cmd, args, ctx) y/o async def run(ctx)
  - ui.js         : opcional; script Vanilla JS que llama a MicroOS.wm.registerApp(...)
"""

import gc
import sys

import fs
import kernel

_APPS_ROOT = "/apps"
_RESERVED_IDS = frozenset(("sys", "gpio", "fs", "net", "term", "apps", "about", "monitor", "files", "terminal", "editor", "calc", "wifi", "appstore"))


def _valid_app_id(app_id):
    if not isinstance(app_id, str) or not (2 <= len(app_id) <= 24):
        return False
    if app_id in _RESERVED_IDS:
        return False
    for ch in app_id:
        if not (("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in ("_", "-")):
            return False
    return True


def _valid_app_filename(fname, default_name):
    """Valida que ui/backend en manifest.json sea un nombre simple sin '/' ni '..' (APP-04)."""
    name = (fname or default_name).strip()
    if not name or "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise ValueError("Nombre de archivo invalido en manifest: '{}'".format(fname))
    return name


class AppManager:
    """Administra el ciclo de vida en caliente de apps instalables en /apps/."""

    def __init__(self, kern=None, router=None, net_mgr=None):
        self.kernel = kern if kern is not None else kernel.get_kernel()
        self.hal = self.kernel.hal
        self.router = router
        self.net = net_mgr
        self._loaded = {}  # app_id -> {"manifest": dict, "ns": dict|None, "task_name": str|None}

        if self.router is not None:
            self.router.register_app("apps", self.handle_rpc)

    def _make_ctx(self, app_id, manifest):
        return {
            "id": app_id,
            "manifest": manifest,
            "hal": self.hal,
            "fs": fs,
            "kernel": self.kernel,
            "net": self.net,
        }

    def discover_all(self):
        """Escanea /apps/ al iniciar y carga todas las apps con manifest.json."""
        if not fs.exists(_APPS_ROOT, "/"):
            fs.mkdir(_APPS_ROOT, "/")
            return []

        loaded_ids = []
        for entry in fs.list_dir(_APPS_ROOT, "/"):
            if entry["type"] != "dir":
                continue
            app_id = entry["name"]
            m_path = "{}/{}/manifest.json".format(_APPS_ROOT, app_id)
            if fs.exists(m_path, "/"):
                try:
                    self.load_app(app_id)
                    loaded_ids.append(app_id)
                except Exception as e:
                    self.kernel.log("WARN", "APPS", "Error cargando '{}': {}".format(app_id, e))
        gc.collect()
        return loaded_ids

    def list_apps(self):
        """Retorna lista serializable de todas las apps instaladas en /apps/."""
        result = []
        if not fs.exists(_APPS_ROOT, "/"):
            return result

        for entry in fs.list_dir(_APPS_ROOT, "/"):
            if entry["type"] != "dir":
                continue
            app_id = entry["name"]
            base_dir = "{}/{}".format(_APPS_ROOT, app_id)
            m = fs.read_json(base_dir + "/manifest.json", default=None)
            if not isinstance(m, dict):
                continue
            ui_file = m.get("ui") or "ui.js"
            be_file = m.get("backend") or "backend.py"
            has_ui = fs.exists(base_dir + "/" + ui_file, "/") or fs.exists(base_dir + "/" + ui_file + ".gz", "/")
            has_be = fs.exists(base_dir + "/" + be_file, "/")
            loaded_info = self._loaded.get(app_id)
            result.append(
                {
                    "id": app_id,
                    "name": m.get("name", app_id),
                    "version": m.get("version", "1.0.0"),
                    "icon": m.get("icon", "🧩"),
                    "description": m.get("description", ""),
                    "autostart": bool(m.get("autostart", False)),
                    "has_ui": has_ui,
                    "has_backend": has_be,
                    "loaded": loaded_info is not None,
                    "ui_url": "/api/apps/{}/ui.js".format(app_id) if has_ui else None,
                }
            )
        return result

    def load_app(self, app_id):
        """Carga (o recarga) el backend Python de una app en caliente."""
        if app_id in self._loaded:
            self.unload_app(app_id)

        base_dir = "{}/{}".format(_APPS_ROOT, app_id)
        manifest = fs.read_json(base_dir + "/manifest.json", default=None)
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json invalido o inexistente en " + base_dir)

        be_file = _valid_app_filename(manifest.get("backend"), "backend.py")
        be_path = base_dir + "/" + be_file
        ns = None
        task_name = None

        if fs.exists(be_file, base_dir):
            code = fs.read_text(be_file, max_bytes=16384, base=base_dir)
            ctx = self._make_ctx(app_id, manifest)
            ns = {"__name__": "app_" + app_id, "ctx": ctx}
            exec(code, ns, ns)

            # Registrar manejador RPC si la app define rpc(cmd, args, ctx)
            rpc_fn = ns.get("rpc")
            if callable(rpc_fn) and self.router is not None:
                self.router.register_app(app_id, lambda cmd, args, _fn=rpc_fn, _ctx=ctx: _fn(cmd, args, _ctx))

            # Registrar tarea uasyncio si autostart=True y define async def run(ctx)
            run_fn = ns.get("run")
            if callable(run_fn) and manifest.get("autostart"):
                task_name = "app_" + app_id
                self.kernel.unregister_task(task_name)
                self.kernel.register_task(task_name, run_fn, args=(ctx,), critical=False)

        self._loaded[app_id] = {
            "manifest": manifest,
            "ns": ns,
            "task_name": task_name,
        }
        self.kernel.log("INFO", "APPS", "App '{}' v{} cargada".format(app_id, manifest.get("version", "1.0.0")))
        gc.collect()
        return True

    def unload_app(self, app_id):
        """Descarga en caliente el backend y tarea de una app liberando RAM (APP-03)."""
        info = self._loaded.pop(app_id, None)
        if info is None:
            return False

        if info["task_name"]:
            self.kernel.unregister_task(info["task_name"])

        if self.router is not None:
            self.router.unregister_app(app_id)

        ns = info["ns"]
        if ns is not None:
            on_unload = ns.get("on_unload")
            if callable(on_unload):
                try:
                    on_unload(ns.get("ctx"))
                except Exception:
                    pass
            ns.clear()
        del info
        gc.collect()
        return True

    def install_app(self, manifest, ui_code="", backend_code=""):
        """
        Instala o actualiza una app de forma atomica con staging y rollback (APP-01).
        """
        if not isinstance(manifest, dict):
            raise ValueError("Manifest debe ser un objeto JSON")

        app_id = (manifest.get("id") or "").strip().lower()
        if not _valid_app_id(app_id):
            raise ValueError("ID de app invalido o reservado: '{}'".format(app_id))

        manifest = dict(manifest)
        manifest["id"] = app_id
        manifest.setdefault("name", app_id)
        manifest.setdefault("version", "1.0.0")
        manifest.setdefault("icon", "🧩")
        manifest.setdefault("description", "")
        manifest["ui"] = _valid_app_filename(manifest.get("ui"), "ui.js")
        manifest["backend"] = _valid_app_filename(manifest.get("backend"), "backend.py")
        manifest.setdefault("autostart", False)

        # Pre-validar sintaxis Python antes de tocar Flash
        if backend_code and backend_code.strip():
            compile(backend_code, manifest["backend"], "exec")

        base_dir = "{}/{}".format(_APPS_ROOT, app_id)
        stg_dir = "{}/.stg_{}".format(_APPS_ROOT, app_id)
        bak_dir = "{}/.bak_{}".format(_APPS_ROOT, app_id)

        if fs.exists(stg_dir, "/"):
            fs.remove(stg_dir, "/", recursive=True)
        if fs.exists(bak_dir, "/"):
            fs.remove(bak_dir, "/", recursive=True)

        fs.mkdir(stg_dir, "/")
        fs.write_json("manifest.json", manifest, base=stg_dir)
        if ui_code and ui_code.strip():
            fs.write_text(manifest["ui"], ui_code, base=stg_dir)
        if backend_code and backend_code.strip():
            fs.write_text(manifest["backend"], backend_code, base=stg_dir)

        had_prev = fs.exists(base_dir, "/")
        if app_id in self._loaded:
            self.unload_app(app_id)
        if had_prev:
            fs.rename(base_dir, bak_dir, "/")

        try:
            fs.rename(stg_dir, base_dir, "/")
            self.load_app(app_id)
        except Exception:
            # Rollback automatico si falla la carga
            if fs.exists(base_dir, "/"):
                fs.remove(base_dir, "/", recursive=True)
            if had_prev and fs.exists(bak_dir, "/"):
                fs.rename(bak_dir, base_dir, "/")
                try:
                    self.load_app(app_id)
                except Exception:
                    pass
            raise
        finally:
            if fs.exists(stg_dir, "/"):
                fs.remove(stg_dir, "/", recursive=True)
            if fs.exists(bak_dir, "/"):
                fs.remove(bak_dir, "/", recursive=True)

        self.kernel.emit("apps_changed", {"action": "install", "id": app_id})
        gc.collect()
        return {"id": app_id, "installed": True}

    def uninstall_app(self, app_id):
        """Desinstala una app en caliente: detiene tarea, libera RAM y borra /apps/<id>/."""
        app_id = (app_id or "").strip().lower()
        if not _valid_app_id(app_id):
            raise ValueError("ID de app invalido: '{}'".format(app_id))

        self.unload_app(app_id)
        base_dir = "{}/{}".format(_APPS_ROOT, app_id)
        if fs.exists(base_dir, "/"):
            fs.remove(base_dir, "/", recursive=True)

        self.kernel.log("INFO", "APPS", "App '{}' desinstalada".format(app_id))
        self.kernel.emit("apps_changed", {"action": "uninstall", "id": app_id})
        gc.collect()
        return {"id": app_id, "uninstalled": True}

    def handle_rpc(self, cmd, args):
        """Manejador JSON-RPC bajo app='apps'."""
        if cmd == "list":
            return self.list_apps()
        if cmd == "install":
            return self.install_app(
                args.get("manifest"),
                ui_code=args.get("ui", ""),
                backend_code=args.get("backend", ""),
            )
        if cmd == "uninstall":
            return self.uninstall_app(args.get("id", ""))
        if cmd == "reload":
            app_id = (args.get("id") or "").strip().lower()
            self.load_app(app_id)
            self.kernel.emit("apps_changed", {"action": "reload", "id": app_id})
            return {"id": app_id, "reloaded": True}
        raise ValueError("Comando apps desconocido: {}".format(cmd))


_appmgr_instance = None


def get_appmgr(kern=None, router=None, net_mgr=None):
    global _appmgr_instance
    if _appmgr_instance is None:
        _appmgr_instance = AppManager(kern=kern, router=router, net_mgr=net_mgr)
    return _appmgr_instance
