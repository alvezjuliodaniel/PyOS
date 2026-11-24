"""
PyOS - Gestor de Red Wi-Fi Resiliente (STA + AP de respaldo)
100% cooperativo sobre uasyncio (sin time.sleep bloqueante).
Soporta reconexion automatica en segundo plano y hostname local (pyos).
"""

import gc

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import network

import fs
import kernel

_DEFAULT_WIFI_PATH = "/config/wifi.json"
_HAS_SLEEP_MS = hasattr(asyncio, "sleep_ms")


def _sleep_ms(ms):
    if _HAS_SLEEP_MS:
        return asyncio.sleep_ms(ms)
    return asyncio.sleep(ms / 1000.0)


def _format_mac(raw_mac):
    if not raw_mac:
        return ""
    return ":".join("{:02x}".format(b) for b in raw_mac)


class NetManager:
    """
    Administra la conectividad Wi-Fi en modo dual:
    - STA principal usando credenciales de /config/wifi.json
    - AP de respaldo ('PyOS-Setup') si no hay credenciales o falla STA
    - Reconexion automatica en segundo plano sin bloquear el Kernel
    """

    def __init__(self, config_path=_DEFAULT_WIFI_PATH, kern=None):
        self.config_path = config_path
        self.kernel = kern if kern is not None else kernel.get_kernel()
        self.hostname = self.kernel.hostname or "pyos"

        self.sta = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)

        self.mode = "offline"   # 'sta' | 'ap' | 'offline'
        self._cfg = {}
        self._force_reconnect = False
        self._load_config()

    def _load_config(self):
        default_cfg = {
            "sta": {
                "ssid": "",
                "password": "",
                "timeout_s": 15,
                "reconnect_interval_s": 30,
            },
            "ap": {
                "ssid": "PyOS-Setup",
                "password": "",
                "ip": "192.168.4.1",
                "channel": 6,
                "txpower": 20.0,
                "hidden": False,
            },
        }
        loaded = fs.read_json(self.config_path, default=None, internal=True)
        if isinstance(loaded, dict):
            self._cfg = {
                "sta": dict(default_cfg["sta"], **(loaded.get("sta") or {})),
                "ap": dict(default_cfg["ap"], **(loaded.get("ap") or {})),
            }
        else:
            self._cfg = default_cfg

    def _apply_hostname(self):
        """Configura el hostname de red para DHCP y mDNS local."""
        try:
            if hasattr(network, "hostname"):
                network.hostname(self.hostname)
                return
        except Exception:
            pass
        try:
            self.sta.config(dhcp_hostname=self.hostname)
        except Exception:
            pass

    def _stop_ap(self):
        try:
            if self.ap.active():
                self.ap.active(False)
        except Exception:
            pass

    def _apply_radio_tuning(self, iface, txpower=20.0):
        """Desactiva ahorro de energia Wi-Fi y ajusta potencia TX (dBm)."""
        try:
            pm_none = getattr(iface, "PM_NONE", 0)
            iface.config(pm=pm_none)
        except Exception:
            pass
        if txpower is not None:
            try:
                iface.config(txpower=float(txpower))
            except Exception:
                pass

    def _start_ap(self):
        """Activa el Access Point de respaldo (PyOS-Setup)."""
        ap_cfg = self._cfg.get("ap", {})
        ssid = ap_cfg.get("ssid") or "PyOS-Setup"
        password = ap_cfg.get("password") or ""
        ip = ap_cfg.get("ip") or "192.168.4.1"
        channel = int(ap_cfg.get("channel") or 6)
        txpower = ap_cfg.get("txpower", 20.0)
        hidden = bool(ap_cfg.get("hidden", False))

        try:
            # En ESP32-C3 desactivar STA ayuda a evitar interferencia de radio al emitir AP
            try:
                if self.sta.active() and not self.sta.isconnected():
                    self.sta.active(False)
            except Exception:
                pass

            self.ap.active(True)
            self._apply_hostname()

            # Configurar IP estatica del AP
            try:
                self.ap.ifconfig((ip, "255.255.255.0", ip, ip))
            except Exception:
                pass

            # Auth abierto si no hay password o es menor a 8 chars (requisito WPA2)
            if len(password) >= 8:
                auth = getattr(network, "AUTH_WPA_WPA2_PSK", 4)
                try:
                    self.ap.config(ssid=ssid, password=password, authmode=auth, channel=channel, hidden=hidden)
                except ValueError:
                    self.ap.config(essid=ssid, password=password, authmode=auth, channel=channel)
            else:
                auth = getattr(network, "AUTH_OPEN", 0)
                try:
                    self.ap.config(ssid=ssid, authmode=auth, channel=channel, hidden=hidden)
                except ValueError:
                    self.ap.config(essid=ssid, authmode=auth, channel=channel)

            self._apply_radio_tuning(self.ap, txpower=txpower)

            self.mode = "ap"
            ap_ip = self.ap.ifconfig()[0]
            try:
                actual_tx = self.ap.config("txpower")
            except Exception:
                actual_tx = txpower
            self.kernel.log(
                "INFO",
                "NET",
                "AP de respaldo activo: '{}' en http://{} (ch={}, tx={} dBm)".format(
                    ssid, ap_ip, channel, actual_tx
                ),
            )
            self.kernel.emit("net_status", self.get_status())
            gc.collect()
            return True
        except Exception as e:
            self.kernel.log("ERR", "NET", "Error iniciando AP: {}".format(e))
            return False

    async def _connect_sta(self):
        """Intenta conectar en modo STA de forma no bloqueante."""
        sta_cfg = self._cfg.get("sta", {})
        ssid = (sta_cfg.get("ssid") or "").strip()
        password = sta_cfg.get("password") or ""
        timeout_ms = int(sta_cfg.get("timeout_s") or 15) * 1000

        if not ssid:
            return False

        self.kernel.log("INFO", "NET", "Conectando STA a '{}'...".format(ssid))
        try:
            if not self.sta.active():
                self.sta.active(True)
            self._apply_hostname()
            self._apply_radio_tuning(self.sta, txpower=sta_cfg.get("txpower", 20.0))

            if self.sta.isconnected():
                self.sta.disconnect()
                await _sleep_ms(200)

            self.sta.connect(ssid, password)
        except Exception as e:
            self.kernel.log("ERR", "NET", "Fallo al iniciar STA: {}".format(e))
            return False

        elapsed = 0
        step = 250
        while elapsed < timeout_ms:
            if self.sta.isconnected():
                self._stop_ap()
                self.mode = "sta"
                ip = self.sta.ifconfig()[0]
                self.kernel.log(
                    "INFO",
                    "NET",
                    "STA conectado a '{}' | IP: {} | host: {}.local".format(
                        ssid, ip, self.hostname
                    ),
                )
                self.kernel.emit("net_status", self.get_status())
                gc.collect()
                return True
            await _sleep_ms(step)
            elapsed += step

        self.kernel.log("WARN", "NET", "Timeout conectando a '{}'".format(ssid))
        try:
            self.sta.disconnect()
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # API Publica (Estado, Escaneo, Guardar Credenciales)
    # ------------------------------------------------------------------

    def get_status(self):
        """Retorna estado actual de red serializable a JSON (para Monitor/UI)."""
        if self.mode == "sta" and self.sta.isconnected():
            cfg = self.sta.ifconfig()
            try:
                rssi = self.sta.status("rssi")
            except Exception:
                rssi = None
            return {
                "mode": "sta",
                "connected": True,
                "ssid": self._cfg.get("sta", {}).get("ssid", ""),
                "ip": cfg[0],
                "netmask": cfg[1],
                "gateway": cfg[2],
                "rssi": rssi,
                "hostname": self.hostname,
                "mac": _format_mac(self.sta.config("mac")),
            }

        if self.ap.active():
            cfg = self.ap.ifconfig()
            return {
                "mode": "ap",
                "connected": True,
                "ssid": self._cfg.get("ap", {}).get("ssid", "PyOS-Setup"),
                "ip": cfg[0],
                "netmask": cfg[1],
                "gateway": cfg[2],
                "rssi": None,
                "hostname": self.hostname,
                "mac": _format_mac(self.ap.config("mac")),
            }

        return {
            "mode": "offline",
            "connected": False,
            "ssid": "",
            "ip": "0.0.0.0",
            "netmask": "0.0.0.0",
            "gateway": "0.0.0.0",
            "rssi": None,
            "hostname": self.hostname,
            "mac": "",
        }

    def scan(self):
        """Escanea redes Wi-Fi cercanas, deduplicadas por SSID y ordenadas por RSSI."""
        was_active = self.sta.active()
        if not was_active:
            self.sta.active(True)
        try:
            raw = self.sta.scan()
        finally:
            if not was_active and self.mode != "sta":
                self.sta.active(False)

        best = {}
        for item in raw:
            ssid = item[0].decode("utf-8", "ignore") if isinstance(item[0], bytes) else str(item[0])
            if not ssid:
                continue
            rssi = item[3]
            auth = item[4]
            if ssid not in best or rssi > best[ssid]["rssi"]:
                best[ssid] = {"ssid": ssid, "rssi": rssi, "auth": auth}

        result = list(best.values())
        result.sort(key=lambda x: x["rssi"], reverse=True)
        return result

    def save_credentials(self, ssid, password):
        """Actualiza credenciales STA en /config/wifi.json y solicita reconexion."""
        self._cfg.setdefault("sta", {})["ssid"] = ssid
        self._cfg["sta"]["password"] = password
        fs.write_json(self.config_path, self._cfg, internal=True)
        self._force_reconnect = True
        self.kernel.log("INFO", "NET", "Credenciales guardadas para '{}'".format(ssid))
        return True

    def shutdown(self):
        """Desactiva interfaces Wi-Fi limpiamente."""
        self._stop_ap()
        try:
            if self.sta.active():
                self.sta.disconnect()
                self.sta.active(False)
        except Exception:
            pass
        self.mode = "offline"

    # ------------------------------------------------------------------
    # Corrutina Principal (Resiliencia y Reconexion en Segundo Plano)
    # ------------------------------------------------------------------

    async def run(self):
        """
        Tarea principal de red supervisada por el Kernel:
        1. Intenta conectar STA si hay SSID configurado.
        2. Si falla o no hay configuracion, levanta el AP de respaldo.
        3. Vigila continuamente la conexion y reconecta sin bloquear el sistema.
        """
        self._apply_hostname()

        has_ssid = bool((self._cfg.get("sta", {}).get("ssid") or "").strip())
        if has_ssid:
            ok = await self._connect_sta()
            if not ok:
                self._start_ap()
        else:
            self.kernel.log("INFO", "NET", "Sin SSID configurado; iniciando AP de respaldo.")
            self._start_ap()

        reconnect_ms = int(self._cfg.get("sta", {}).get("reconnect_interval_s") or 30) * 1000
        if reconnect_ms < 5000:
            reconnect_ms = 5000

        check_step = 2000
        retry_elapsed = 0

        while True:
            await _sleep_ms(check_step)

            if self._force_reconnect:
                self._force_reconnect = False
                retry_elapsed = 0
                ok = await self._connect_sta()
                if not ok and not self.ap.active():
                    self._start_ap()
                continue

            has_ssid = bool((self._cfg.get("sta", {}).get("ssid") or "").strip())

            # Caso 1: estabamos en STA y se cayo la conexion
            if self.mode == "sta" and not self.sta.isconnected():
                self.kernel.log("WARN", "NET", "Conexion STA perdida; intentando reconectar...")
                self.mode = "offline"
                self.kernel.emit("net_status", self.get_status())

                ok = await self._connect_sta()
                if not ok:
                    self._start_ap()
                    retry_elapsed = 0
                continue

            # Caso 2: estamos en AP de respaldo pero hay SSID configurado -> reintentar STA periodicamente
            if self.mode == "ap" and has_ssid:
                retry_elapsed += check_step
                if retry_elapsed >= reconnect_ms:
                    retry_elapsed = 0
                    await self._connect_sta()


_net_instance = None


def get_net(config_path=_DEFAULT_WIFI_PATH):
    """Obtiene o crea la instancia unica de NetManager."""
    global _net_instance
    if _net_instance is None:
        _net_instance = NetManager(config_path=config_path)
    return _net_instance
