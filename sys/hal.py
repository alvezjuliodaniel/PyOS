"""
MicroOS - Capa de Abstraccion de Hardware (HAL)
Compatible con ESP32-C3 (RISC-V), ESP32-S (Xtensa LX6) y ESP32-S3 (Xtensa LX7).
Disenado con minimo consumo de RAM y compatibilidad con mpy-cross.
"""

import gc
import os
import sys
import time

try:
    import ujson as json
except ImportError:
    import json

try:
    from micropython import const
except ImportError:
    def const(x):
        return x

import machine

try:
    import esp32
except ImportError:
    esp32 = None

# Constantes de configuracion (inlineables por mpy-cross)
_PSRAM_THRESHOLD_BYTES = const(524288)  # >512 KB de heap total implica PSRAM activa
_PWM_MAX_U16 = const(65535)
_ADC_MAX_U16 = const(65535)
_DEFAULT_BOARDS_PATH = "/config/boards.json"

# Perfil minimo de emergencia (ESP32-C3) si /config/boards.json falta o falla
_FALLBACK_PROFILE = {
    "name": "ESP32-C3 (Fallback)",
    "arch": "rv32imc",
    "cores": 1,
    "max_cpu_mhz": 160,
    "psram_capable": False,
    "pwm_max_channels": 6,
    "pins": {
        "safe_io": (0, 1, 3, 4, 5, 6, 7, 10),
        "safe_in_only": (),
        "safe_adc": (0, 1, 3, 4),
        "reserved": {
            "strapping": (2, 8, 9),
            "flash": (11, 12, 13, 14, 15, 16, 17),
            "usb": (18, 19),
            "uart0": (20, 21),
        },
    },
    "limits": {
        "profile": "strict",
        "ram_warn_bytes": 32768,
        "ram_crit_bytes": 16384,
        "max_sockets": 3,
        "http_chunk_bytes": 512,
        "gc_interval_ms": 5000,
    },
}


def detect_chip():
    """
    Autodetecta la familia ESP32 usando os.uname().machine y sys.implementation.
    Retorna tupla: (chip_id, machine_raw, exact_match).
    chip_id pertenece a: 'esp32c3', 'esp32s3', 'esp32'.
    """
    machine_raw = ""
    try:
        machine_raw = os.uname().machine
    except AttributeError:
        pass

    if not machine_raw:
        machine_raw = getattr(sys.implementation, "_machine", "") or ""

    m_upper = machine_raw.upper()

    # El orden importa: evaluar variantes especificas antes de 'ESP32' generico
    if "ESP32C3" in m_upper or "ESP32-C3" in m_upper:
        return ("esp32c3", machine_raw, True)
    if "ESP32S3" in m_upper or "ESP32-S3" in m_upper:
        return ("esp32s3", machine_raw, True)
    if "ESP32" in m_upper:
        return ("esp32", machine_raw, True)

    # Minimo comun denominador si la placa no coincide con firmas conocidas
    return ("esp32c3", machine_raw or "unknown", False)


def detect_psram(machine_raw=""):
    """
    Detecta si hay PSRAM habilitada en el heap de MicroPython.
    """
    total_heap = gc.mem_free() + gc.mem_alloc()
    if total_heap > _PSRAM_THRESHOLD_BYTES:
        return True
    m_upper = machine_raw.upper()
    return ("SPIRAM" in m_upper) or ("PSRAM" in m_upper)


class HAL:
    """
    Capa de Abstraccion de Hardware con validacion estricta de pines y
    gestion de recursos (Pin, ADC, PWM) para evitar fugas y bloqueos.
    """

    def __init__(self, config_path=_DEFAULT_BOARDS_PATH):
        self._last_ticks = time.ticks_ms()
        self._uptime_ms = 0

        self.chip_id, self.machine_raw, self.exact_match = detect_chip()
        self.has_psram = detect_psram(self.machine_raw)

        # Registros de hardware activo para evitar fragmentacion y fugas LEDC
        self._pins = {}      # pin_num -> (machine.Pin, mode_str)
        self._pwms = {}      # pin_num -> (machine.PWM, freq, duty_pct)
        self._adcs = {}      # pin_num -> machine.ADC

        self._load_profile(config_path)
        gc.collect()

    def _load_profile(self, config_path):
        """
        Carga unicamente el perfil de la placa detectada desde boards.json
        y descarta el resto para minimizar el uso de RAM en ESP32-C3.
        """
        profile = None
        try:
            with open(config_path, "r") as f:
                all_boards = json.load(f)
            profile = all_boards.get(self.chip_id)
            del all_boards
        except OSError:
            profile = None
        except ValueError:
            profile = None

        if not profile:
            profile = _FALLBACK_PROFILE

        self.board_name = profile.get("name", self.chip_id)
        self.arch = profile.get("arch", "rv32imc")
        self.cores = profile.get("cores", 1)
        self.max_cpu_mhz = profile.get("max_cpu_mhz", 160)
        self.pwm_max_channels = profile.get("pwm_max_channels", 6)
        self.limits = dict(profile.get("limits", _FALLBACK_PROFILE["limits"]))

        # ESP32 clasico sin PSRAM tiene heap Python reducido (~112 KB):
        # aplicar perfil estricto de bajo consumo automaticamente.
        if self.chip_id == "esp32" and not self.has_psram:
            self.limits["profile"] = "strict"
            self.limits["http_chunk_bytes"] = 512
            self.limits["max_sockets"] = 3
            self.limits["gc_interval_ms"] = 5000
            self.limits["ram_warn_bytes"] = 24576
            self.limits["ram_crit_bytes"] = 12288

        pins_cfg = profile.get("pins", {})
        safe_io = list(pins_cfg.get("safe_io", ()))
        safe_in_only = tuple(pins_cfg.get("safe_in_only", ()))
        safe_adc = tuple(pins_cfg.get("safe_adc", ()))
        reserved = pins_cfg.get("reserved", {})

        # En ESP32 clasico con PSRAM, GPIO 16 y 17 estan ocupados por la PSRAM
        if self.has_psram and "psram_pins" in pins_cfg:
            psram_pins = pins_cfg["psram_pins"]
            safe_io = [p for p in safe_io if p not in psram_pins]
            reserved = dict(reserved)
            reserved["psram_active"] = tuple(psram_pins)

        # Usar estructuras inmutables para validacion rapida O(1) y baja RAM
        self._safe_io_tuple = tuple(safe_io)
        self._safe_in_only_tuple = safe_in_only
        self._safe_adc_tuple = safe_adc

        self._safe_io_set = frozenset(self._safe_io_tuple)
        self._safe_in_set = frozenset(self._safe_io_tuple + self._safe_in_only_tuple)
        self._safe_adc_set = frozenset(self._safe_adc_tuple)

        # Mapa inverso pin -> motivo de reserva para mensajes de error claros
        self._reserved_reasons = {}
        for reason, pin_list in reserved.items():
            for p in pin_list:
                self._reserved_reasons[p] = reason

    # ------------------------------------------------------------------
    # Informacion de placa y validacion de pines
    # ------------------------------------------------------------------

    def get_board_info(self):
        """Retorna metadatos de la placa detectada y su mapa de pines seguros."""
        v = sys.implementation.version
        mpy_ver = "{}.{}.{}".format(v[0], v[1], v[2])
        return {
            "chip_id": self.chip_id,
            "name": self.board_name,
            "arch": self.arch,
            "cores": self.cores,
            "psram": self.has_psram,
            "exact_match": self.exact_match,
            "machine": self.machine_raw,
            "mpy_version": mpy_ver,
            "pwm_max_channels": self.pwm_max_channels,
            "pins": {
                "safe_io": list(self._safe_io_tuple),
                "safe_in_only": list(self._safe_in_only_tuple),
                "safe_adc": list(self._safe_adc_tuple),
            },
            "limits": self.limits,
        }

    def is_pin_safe(self, pin, mode="in"):
        """Verifica si un pin es seguro para el modo indicado ('in', 'out', 'pwm', 'adc')."""
        if not isinstance(pin, int):
            return False
        if mode == "in":
            return pin in self._safe_in_set
        if mode in ("out", "pwm"):
            return pin in self._safe_io_set
        if mode == "adc":
            return pin in self._safe_adc_set
        return False

    def _validate_pin(self, pin, mode):
        """Valida un pin y lanza ValueError descriptivo si esta restringido."""
        if not isinstance(pin, int):
            raise ValueError("Pin invalido: debe ser entero")

        if pin in self._reserved_reasons:
            raise ValueError(
                "GPIO {} reservado ({}) en {}".format(
                    pin, self._reserved_reasons[pin], self.board_name
                )
            )

        if mode == "in":
            if pin not in self._safe_in_set:
                raise ValueError(
                    "GPIO {} no habilitado como entrada en {}".format(pin, self.board_name)
                )
        elif mode in ("out", "pwm"):
            if pin in self._safe_in_only_tuple:
                raise ValueError(
                    "GPIO {} es solo entrada en {}".format(pin, self.board_name)
                )
            if pin not in self._safe_io_set:
                raise ValueError(
                    "GPIO {} no habilitado como salida/PWM en {}".format(pin, self.board_name)
                )
        elif mode == "adc":
            if pin not in self._safe_adc_set:
                raise ValueError(
                    "GPIO {} no es un pin ADC1 seguro en {}".format(pin, self.board_name)
                )
        else:
            raise ValueError("Modo desconocido: {}".format(mode))

    # ------------------------------------------------------------------
    # API GPIO Digital
    # ------------------------------------------------------------------

    def _release_conflicts(self, pin, keep=None):
        """Libera recursos previos (PWM/ADC/Pin) en el mismo GPIO antes de reconfigurarlo."""
        if keep != "pwm" and pin in self._pwms:
            pwm_obj = self._pwms.pop(pin)[0]
            try:
                pwm_obj.deinit()
            except Exception:
                pass
        if keep != "adc" and pin in self._adcs:
            self._adcs.pop(pin, None)
        if keep != "pin" and pin in self._pins:
            self._pins.pop(pin, None)

    def gpio_config(self, pin, mode="in", pull=None, value=None):
        """
        Configura un GPIO digital.
        mode: 'in' o 'out'
        pull: None, 'up', 'down'
        value: 0 o 1 (opcional cuando mode == 'out')
        """
        self._validate_pin(pin, mode)
        self._release_conflicts(pin, keep=None)

        pin_mode = machine.Pin.OUT if mode == "out" else machine.Pin.IN
        pin_pull = None
        if pull == "up":
            pin_pull = machine.Pin.PULL_UP
        elif pull == "down":
            pin_pull = machine.Pin.PULL_DOWN

        if mode == "out" and value is not None:
            p = machine.Pin(pin, pin_mode, value=1 if value else 0)
        elif pin_pull is not None:
            p = machine.Pin(pin, pin_mode, pin_pull)
        else:
            p = machine.Pin(pin, pin_mode)

        self._pins[pin] = (p, mode)
        return {"pin": pin, "mode": mode, "val": p.value()}

    def gpio_read(self, pin):
        """Lee el estado logico (0 o 1) de un GPIO seguro."""
        if pin in self._pins:
            return self._pins[pin][0].value()
        self._validate_pin(pin, "in")
        self._release_conflicts(pin, keep="pin")
        p = machine.Pin(pin, machine.Pin.IN)
        self._pins[pin] = (p, "in")
        return p.value()

    def gpio_write(self, pin, val):
        """Escribe un valor logico (0 o 1) en un GPIO de salida seguro."""
        self._validate_pin(pin, "out")
        bit = 1 if val else 0
        entry = self._pins.get(pin)
        if entry and entry[1] == "out":
            entry[0].value(bit)
        else:
            self._release_conflicts(pin, keep=None)
            p = machine.Pin(pin, machine.Pin.OUT, value=bit)
            self._pins[pin] = (p, "out")
        return bit

    def gpio_toggle(self, pin):
        """Invierte el estado actual de un GPIO de salida seguro."""
        self._validate_pin(pin, "out")
        current = self.gpio_read(pin)
        return self.gpio_write(pin, 0 if current else 1)

    def gpio_release(self, pin):
        """Libera cualquier recurso (Pin, PWM o ADC) asociado a un GPIO."""
        existed = (pin in self._pins) or (pin in self._pwms) or (pin in self._adcs)
        self._release_conflicts(pin, keep=None)
        return existed

    def gpio_states(self):
        """Retorna el estado de todos los pines actualmente activos en la HAL."""
        states = {}
        for pin, (p_obj, mode) in self._pins.items():
            states[pin] = {"mode": mode, "val": p_obj.value()}
        for pin, (_, freq, duty_pct) in self._pwms.items():
            states[pin] = {"mode": "pwm", "freq": freq, "duty_pct": duty_pct}
        for pin in self._adcs:
            states[pin] = {"mode": "adc"}
        return states

    # ------------------------------------------------------------------
    # API ADC (Analogico a Digital - Solo ADC1 seguro con Wi-Fi)
    # ------------------------------------------------------------------

    def adc_read(self, pin, atten=None):
        """
        Lee un canal ADC1 seguro.
        Por defecto usa atenuacion de 11 dB (rango aprox. 0 - 3.3 V).
        Retorna dict con raw_u16 (0..65535), mv (milivoltios) y pct (0..100).
        """
        self._validate_pin(pin, "adc")
        adc = self._adcs.get(pin)
        if adc is None:
            self._release_conflicts(pin, keep="adc")
            adc = machine.ADC(machine.Pin(pin))
            # Configurar atenuacion 11dB para rango completo 0..3.3V
            target_atten = atten if atten is not None else getattr(machine.ADC, "ATTN_11DB", 3)
            try:
                adc.atten(target_atten)
            except Exception:
                pass
            self._adcs[pin] = adc

        raw = adc.read_u16()
        if hasattr(adc, "read_uv"):
            mv = adc.read_uv() // 1000
        else:
            mv = (raw * 3300) // _ADC_MAX_U16

        pct = round((raw * 100.0) / _ADC_MAX_U16, 1)
        return {"pin": pin, "raw_u16": raw, "mv": mv, "pct": pct}

    # ------------------------------------------------------------------
    # API PWM (Modulacion por Ancho de Pulso - LEDC)
    # ------------------------------------------------------------------

    def pwm_set(self, pin, freq=1000, duty_pct=50.0):
        """
        Configura o actualiza salida PWM en un GPIO seguro.
        freq: frecuencia en Hz (1..40000000 segun resolucion)
        duty_pct: ciclo de trabajo en porcentaje (0.0 .. 100.0)
        """
        self._validate_pin(pin, "pwm")
        if freq < 1:
            raise ValueError("Frecuencia PWM debe ser >= 1 Hz")
        if duty_pct < 0.0:
            duty_pct = 0.0
        elif duty_pct > 100.0:
            duty_pct = 100.0

        duty_u16 = int((duty_pct / 100.0) * _PWM_MAX_U16)

        if pin in self._pwms:
            pwm_obj = self._pwms[pin][0]
            pwm_obj.freq(int(freq))
            pwm_obj.duty_u16(duty_u16)
        else:
            if len(self._pwms) >= self.pwm_max_channels:
                raise RuntimeError(
                    "Limite de canales PWM alcanzado ({}) en {}".format(
                        self.pwm_max_channels, self.board_name
                    )
                )
            self._release_conflicts(pin, keep="pwm")
            pwm_obj = machine.PWM(machine.Pin(pin), freq=int(freq), duty_u16=duty_u16)

        self._pwms[pin] = (pwm_obj, int(freq), round(duty_pct, 2))
        return {
            "pin": pin,
            "freq": int(freq),
            "duty_pct": round(duty_pct, 2),
            "duty_u16": duty_u16,
        }

    def pwm_stop(self, pin):
        """Detiene el PWM en el pin indicado y libera el canal LEDC."""
        if pin in self._pwms:
            pwm_obj = self._pwms.pop(pin)[0]
            try:
                pwm_obj.deinit()
            except Exception:
                pass
            return True
        return False

    # ------------------------------------------------------------------
    # Telemetria del Sistema
    # ------------------------------------------------------------------

    def get_uptime_ms(self):
        """
        Retorna el tiempo de actividad en milisegundos de forma segura frente
        al desbordamiento de ticks_ms() (~12.4 dias en 30 bits).
        """
        now = time.ticks_ms()
        delta = time.ticks_diff(now, self._last_ticks)
        if delta > 0:
            self._uptime_ms += delta
            self._last_ticks = now
        return self._uptime_ms

    def get_cpu_freq_mhz(self):
        """Retorna la frecuencia actual de la CPU en MHz."""
        try:
            return machine.freq() // 1000000
        except Exception:
            return 0

    def get_temperature_c(self):
        """
        Retorna la temperatura interna del MCU en grados Celsius, o None si
        el chip/build de MicroPython no la soporta.
        """
        if esp32 is None:
            return None
        # ESP32-C3 y ESP32-S3 modernos (MicroPython >= 1.20)
        if hasattr(esp32, "mcu_temperature"):
            try:
                return round(float(esp32.mcu_temperature()), 1)
            except Exception:
                pass
        # Fallback para builds antiguos de ESP32 clasico (Fahrenheit -> Celsius)
        if hasattr(esp32, "raw_temperature"):
            try:
                tf = esp32.raw_temperature()
                return round((tf - 32.0) * 5.0 / 9.0, 1)
            except Exception:
                pass
        return None

    def get_memory_stats(self):
        """Retorna estadisticas de RAM del heap y banderas de alerta."""
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        total = free + alloc
        used_pct = round((alloc * 100.0) / total, 1) if total > 0 else 0.0
        warn_lim = self.limits.get("ram_warn_bytes", 32768)
        crit_lim = self.limits.get("ram_crit_bytes", 16384)
        return {
            "free": free,
            "alloc": alloc,
            "total": total,
            "used_pct": used_pct,
            "warn": free < warn_lim,
            "critical": free < crit_lim,
        }

    def get_flash_stats(self, path="/"):
        """Retorna espacio total, libre y usado del sistema de archivos (LittleFS)."""
        try:
            st = os.statvfs(path)
            bsize = st[0]
            total = bsize * st[2]
            free = bsize * st[3]
            used = total - free
            used_pct = round((used * 100.0) / total, 1) if total > 0 else 0.0
            return {
                "total": total,
                "free": free,
                "used": used,
                "used_pct": used_pct,
            }
        except OSError:
            return {"total": 0, "free": 0, "used": 0, "used_pct": 0.0}

    def get_telemetry(self):
        """Retorna un snapshot completo de telemetria del microcontrolador."""
        freq = self.get_cpu_freq_mhz()
        temp = self.get_temperature_c()
        return {
            "chip_id": self.chip_id,
            "board": self.board_name,
            "uptime_s": self.get_uptime_ms() // 1000,
            "cpu_mhz": freq,
            "temp_c": temp,
            "cpu": {
                "arch": self.arch,
                "cores": self.cores,
                "freq_mhz": freq,
                "max_mhz": self.max_cpu_mhz,
                "temp_c": temp,
            },
            "mem": self.get_memory_stats(),
            "flash": self.get_flash_stats("/"),
        }


# Singleton ligero para compartir la instancia entre modulos del kernel
_hal_instance = None


def get_hal(config_path=_DEFAULT_BOARDS_PATH):
    """Obtiene o inicializa la instancia unica de HAL."""
    global _hal_instance
    if _hal_instance is None:
        _hal_instance = HAL(config_path=config_path)
    return _hal_instance
