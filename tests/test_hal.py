"""
PyOS - Script de prueba de Fase 0 (HAL)
Ejecutar en la placa con: mpremote run tests/test_hal.py
"""

import gc
import sys

if "/sys" not in sys.path:
    sys.path.append("/sys")

import hal


def run_tests():
    print("=== PyOS Fase 0: Pruebas de HAL ===")
    h = hal.get_hal()
    info = h.get_board_info()

    print("\n[1] Deteccion de placa:")
    print("  chip_id    :", info["chip_id"])
    print("  name       :", info["name"])
    print("  arch       :", info["arch"])
    print("  cores      :", info["cores"])
    print("  psram      :", info["psram"])
    print("  exact_match:", info["exact_match"])
    print("  mpy_version:", info["mpy_version"])
    print("  safe_io    :", info["pins"]["safe_io"])
    print("  safe_in    :", info["pins"]["safe_in_only"])
    print("  safe_adc   :", info["pins"]["safe_adc"])

    print("\n[2] Telemetria:")
    tel = h.get_telemetry()
    print("  CPU (MHz)  :", tel["cpu_mhz"])
    print("  Temp (C)   :", tel["temp_c"])
    print("  Uptime (s) :", tel["uptime_s"])
    print(
        "  RAM        : {} / {} B ({}% usado)".format(
            tel["mem"]["alloc"], tel["mem"]["total"], tel["mem"]["used_pct"]
        )
    )
    print(
        "  Flash      : {} / {} B ({}% usado)".format(
            tel["flash"]["used"], tel["flash"]["total"], tel["flash"]["used_pct"]
        )
    )

    print("\n[3] Validacion de pines prohibidos (debe rechazar):")
    forbidden_by_chip = {
        "esp32c3": [2, 8, 9, 12, 18, 19],
        "esp32": [0, 6, 7, 11, 12],
        "esp32s3": [0, 3, 26, 33, 45, 46],
    }
    for pin in forbidden_by_chip.get(info["chip_id"], [99]):
        try:
            h.gpio_write(pin, 1)
            print("  ERROR: GPIO {} no fue bloqueado!".format(pin))
        except ValueError as e:
            print("  OK bloqueado GPIO {}: {}".format(pin, e))

    # Verificar que pines input-only en ESP32 clasico rechazan salida
    for pin in info["pins"]["safe_in_only"]:
        try:
            h.gpio_write(pin, 1)
            print("  ERROR: GPIO {} input-only permitio escritura!".format(pin))
        except ValueError as e:
            print("  OK input-only GPIO {}: {}".format(pin, e))
        break

    print("\n[4] Prueba funcional en primer pin seguro:")
    safe_io = info["pins"]["safe_io"]
    if safe_io:
        test_pin = safe_io[0]
        print("  Usando GPIO {} para GPIO/PWM...".format(test_pin))
        val = h.gpio_write(test_pin, 1)
        print("  gpio_write({}, 1) -> {}".format(test_pin, val))
        val = h.gpio_toggle(test_pin)
        print("  gpio_toggle({}) -> {}".format(test_pin, val))
        pwm_res = h.pwm_set(test_pin, freq=1000, duty_pct=25.0)
        print("  pwm_set ->", pwm_res)
        h.pwm_stop(test_pin)
        h.gpio_release(test_pin)
        print("  PWM detenido y pin liberado.")

    safe_adc = info["pins"]["safe_adc"]
    if safe_adc:
        adc_pin = safe_adc[0]
        adc_res = h.adc_read(adc_pin)
        print("  adc_read({}) -> {}".format(adc_pin, adc_res))
        h.gpio_release(adc_pin)

    gc.collect()
    print("\n=== Pruebas Fase 0 completadas | RAM libre: {} B ===".format(gc.mem_free()))


if __name__ == "__main__":
    run_tests()
