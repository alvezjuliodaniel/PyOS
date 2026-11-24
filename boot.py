"""
PyOS - boot.py
Configuracion inicial minima: rutas de importacion, GC temprano y deteccion de placa.
"""

import gc
import sys

# Asegurar que /sys y /apps esten al final de sys.path sin sombrear el modulo nativo 'sys'
for _p in ("/sys", "/apps"):
    if _p not in sys.path:
        sys.path.append(_p)
del _p

# Activar recoleccion de basura automatica y umbral preventivo contra fragmentacion (MEM-03)
gc.enable()
gc.collect()
try:
    # Dispara GC cada ~1/4 de RAM libre, acotado a 128 KB en PSRAM para evitar pausas largas
    gc.threshold(min(gc.mem_free() // 4, 131072))
except Exception:
    pass

# Deteccion temprana de hardware (HAL)
try:
    import hal

    _hal = hal.get_hal()
    _mem = _hal.get_memory_stats()
    print(
        "[PyOS] Boot OK | {} ({}) | PSRAM: {} | RAM libre: {} KB".format(
            _hal.board_name,
            _hal.arch,
            "SI" if _hal.has_psram else "NO",
            _mem["free"] // 1024,
        )
    )
    del _mem
except Exception as _e:
    print("[PyOS] Error inicializando HAL en boot:", _e)

gc.collect()
