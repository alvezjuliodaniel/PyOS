"""
PyOS App: Monitor de Estado en Pantalla OLED I2C SSD1306
Muestra al iniciar y en tiempo real:
  - Modo de red (AP / STA / OFF)
  - Estado de conexion y SSID
  - Direccion IP asignada
  - Hostname / RSSI y telemetria rapida (RAM libre + Temperatura)

Driver SSD1306 I2C integrado (cero dependencias externas, usa framebuf nativo).
Compatible con ESP32-C3, ESP32-S y ESP32-S3 usando pines seguros de /config/oled.json.
"""

import framebuf
import gc

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

from machine import Pin, SoftI2C

import fs
import kernel
import net

_DEFAULT_OLED_CFG = "/config/oled.json"
_HAS_SLEEP_MS = hasattr(asyncio, "sleep_ms")


def _sleep_ms(ms):
    if _HAS_SLEEP_MS:
        return asyncio.sleep_ms(ms)
    return asyncio.sleep(ms / 1000.0)


class SSD1306_I2C(framebuf.FrameBuffer):
    """Driver minimalista SSD1306 por I2C (128x64 o 128x32) de bajo consumo de RAM."""

    def __init__(self, width, height, i2c, addr=0x3C):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.pages = height // 8
        # Buffer unico con prefijo 0x40 para cero asignaciones en show() (ARCH-04)
        self._raw = bytearray(1 + self.pages * width)
        self._raw[0] = 0x40
        self.buffer = memoryview(self._raw)[1:]
        self._cmd_buf = bytearray(2)
        self._cmd_buf[0] = 0x80  # Co=1, D/C#=0 (comando)
        super().__init__(self.buffer, width, height, framebuf.MONO_VLSB)
        self._init_display()

    def write_cmd(self, cmd):
        self._cmd_buf[1] = cmd
        self.i2c.writeto(self.addr, self._cmd_buf)

    def _init_display(self):
        cmds = (
            0xAE,               # Display OFF
            0x20, 0x00,         # Horizontal addressing mode
            0x40,               # Start line = 0
            0xA1,               # Segment remap
            0xA8, self.height - 1,  # Multiplex ratio
            0xC8,               # COM scan direction remapped
            0xD3, 0x00,         # Display offset = 0
            0xDA, 0x02 if self.height == 32 else 0x12,  # COM pins
            0xD5, 0x80,         # Clock divide
            0xD9, 0xF1,         # Pre-charge period
            0xDB, 0x30,         # VCOMH deselect level
            0x81, 0xCF,         # Contraste
            0xA4,               # Output follows RAM
            0xA6,               # Normal display (no invertido)
            0x8D, 0x14,         # Charge pump ON
            0xAF,               # Display ON
        )
        for c in cmds:
            self.write_cmd(c)
        self.fill(0)
        self.show()

    def show(self):
        self.write_cmd(0x21)  # Column addr
        self.write_cmd(0)
        self.write_cmd(self.width - 1)
        self.write_cmd(0x22)  # Page addr
        self.write_cmd(0)
        self.write_cmd(self.pages - 1)
        # Envio zero-copy usando el buffer preasignado con prefijo 0x40
        self.i2c.writeto(self.addr, self._raw)


class OLEDStatusApp:
    """App del sistema que muestra modo AP/STA, estado Wi-Fi e IP en OLED SSD1306."""

    def __init__(self, config_path=_DEFAULT_OLED_CFG, kern=None, net_mgr=None):
        self.kernel = kern if kern is not None else kernel.get_kernel()
        self.hal = self.kernel.hal
        self.net = net_mgr if net_mgr is not None else net.get_net()
        self.config_path = config_path

        self.oled = None
        self.sda_pin = None
        self.scl_pin = None
        self.addr = 0x3C
        self.width = 128
        self.height = 64
        self.refresh_ms = 2000
        self.enabled = True
        self._dirty = True
        self._custom_msg = None

        self._load_config()

    def _load_config(self):
        cfg = fs.read_json(self.config_path, default={}) or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.width = int(cfg.get("width", 128))
        self.height = int(cfg.get("height", 64))
        self.addr = int(cfg.get("addr", 0x3C))
        self.freq = int(cfg.get("freq", 400000))
        self.refresh_ms = int(cfg.get("refresh_ms", 2000))

        # Pines seguros por defecto segun placa autodetectada
        defaults = {
            "esp32c3": {"sda": 6, "scl": 7},
            "esp32": {"sda": 21, "scl": 22},
            "esp32s3": {"sda": 8, "scl": 9},
        }
        pins_map = cfg.get("pins", {})
        board_pins = pins_map.get(self.hal.chip_id) or defaults.get(self.hal.chip_id, {"sda": 6, "scl": 7})
        self.sda_pin = int(board_pins.get("sda", 6))
        self.scl_pin = int(board_pins.get("scl", 7))

    def _init_hardware(self):
        if not self.enabled:
            return False

        # Validar con la HAL que los pines I2C son seguros en esta placa
        if not (self.hal.is_pin_safe(self.sda_pin, "out") and self.hal.is_pin_safe(self.scl_pin, "out")):
            self.kernel.log(
                "WARN",
                "OLED",
                "Pines I2C inseguros (SDA={}, SCL={}) en {}".format(
                    self.sda_pin, self.scl_pin, self.hal.board_name
                ),
            )
            return False

        try:
            i2c = SoftI2C(scl=Pin(self.scl_pin), sda=Pin(self.sda_pin), freq=self.freq)
            found = i2c.scan()
            target_addr = None
            if self.addr in found:
                target_addr = self.addr
            elif 0x3C in found:
                target_addr = 0x3C
            elif 0x3D in found:
                target_addr = 0x3D

            if target_addr is None:
                self.kernel.log(
                    "WARN",
                    "OLED",
                    "SSD1306 no detectada en SDA={} SCL={} (bus={})".format(
                        self.sda_pin, self.scl_pin, [hex(x) for x in found]
                    ),
                )
                return False

            self.addr = target_addr
            self.oled = SSD1306_I2C(self.width, self.height, i2c, addr=self.addr)
            self.kernel.log(
                "INFO",
                "OLED",
                "SSD1306 {}x{} lista en {} (SDA={}, SCL={})".format(
                    self.width, self.height, hex(self.addr), self.sda_pin, self.scl_pin
                ),
            )
            return True
        except Exception as e:
            self.kernel.log("WARN", "OLED", "Error inicializando OLED: {}".format(e))
            self.oled = None
            return False

    def _draw_boot_splash(self):
        if not self.oled:
            return
        d = self.oled
        d.fill(0)
        d.fill_rect(0, 0, self.width, 12, 1)
        d.text("PyOS", 36, 2, 0)
        d.text(self.hal.board_name[:16], 0, 18, 1)
        if self.height >= 64:
            d.text("Iniciando red...", 0, 34, 1)
            d.text("SDA:{} SCL:{}".format(self.sda_pin, self.scl_pin), 0, 50, 1)
        d.show()

    def _draw_status(self):
        if not self.oled:
            return
        d = self.oled
        st = self.net.get_status()
        mode = (st.get("mode") or "offline").upper()  # 'AP' | 'STA' | 'OFFLINE'
        connected = bool(st.get("connected"))
        ssid = st.get("ssid") or "---"
        ip = st.get("ip") or "0.0.0.0"
        rssi = st.get("rssi")

        d.fill(0)

        # Barra superior invertida: PyOS + [AP] / [STA] / [OFF]
        badge = "AP" if mode == "AP" else ("STA" if mode == "STA" else "OFF")
        d.fill_rect(0, 0, self.width, 11, 1)
        d.text("PyOS", 2, 2, 0)
        d.text("[{}]".format(badge), self.width - (len(badge) + 2) * 8 - 2, 2, 0)

        if self._custom_msg:
            d.text(self._custom_msg[:16], 0, 16, 1)
            d.text("IP:" + ip[:13], 0, 28, 1)
            d.show()
            return

        if self.height <= 32:
            # Modo compacto 128x32
            state_txt = "OK:" if connected else "NO:"
            d.text((state_txt + ssid)[:16], 0, 13, 1)
            d.text(ip[:16], 0, 23, 1)
            d.show()
            return

        # Modo completo 128x64
        if mode == "AP":
            conn_line = "AP Activo (OK)"
        elif connected:
            conn_line = "Wi-Fi Conectado"
        else:
            conn_line = "Sin conexion..."

        d.text(conn_line[:16], 0, 14, 1)
        d.text(("Red:" + ssid)[:16], 0, 25, 1)
        d.text(("IP:" + ip)[:16], 0, 36, 1)

        # Linea 5: RSSI en STA o hostname en AP
        if mode == "STA" and rssi is not None:
            d.text("RSSI:{} dBm".format(rssi)[:16], 0, 46, 1)
        else:
            d.text((st.get("hostname", "pyos") + ".local")[:16], 0, 46, 1)

        # Linea 6: Mini telemetria RAM + Temp
        ram_kb = gc.mem_free() // 1024
        temp = self.hal.get_temperature_c()
        temp_s = "{}C".format(int(temp)) if temp is not None else ""
        d.text("RAM:{}K {}".format(ram_kb, temp_s)[:16], 0, 56, 1)

        d.show()

    def _on_event(self, event_name, _data):
        if event_name == "net_status":
            self._dirty = True

    def set_message(self, msg=None):
        """Muestra un mensaje temporal (o None para volver al estado de red)."""
        self._custom_msg = msg
        self._dirty = True

    async def run(self):
        """Corrutina cooperativa administrada por el Kernel."""
        if not self.enabled:
            return

        if not self._init_hardware():
            # Si no hay pantalla conectada, salir limpiamente sin consumir recursos
            return

        self._draw_boot_splash()
        self.kernel.subscribe(self._on_event)

        try:
            await _sleep_ms(1200)
            while True:
                self._draw_status()
                self._dirty = False
                # Esperar refresh_ms en pasos cortos para reaccionar rapido a net_status
                waited = 0
                while waited < self.refresh_ms and not self._dirty:
                    await _sleep_ms(250)
                    waited += 250
        finally:
            self.kernel.unsubscribe(self._on_event)


_oled_app = None


def get_oled_app():
    global _oled_app
    if _oled_app is None:
        _oled_app = OLEDStatusApp()
    return _oled_app
