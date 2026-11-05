"""COM initialisation order.

`soundcard` must be imported before `comtypes`, and comtypes must use the
single-threaded apartment. Import this module before anything audio related.
"""

import ctypes
import sys
import warnings

sys.coinit_flags = 0  # STA

import soundcard  # noqa: E402,F401  (initialises COM)

try:
    import comtypes  # noqa: F401
except OSError:  # COM already initialised in another mode (frozen builds)
    ctypes.windll.ole32.CoUninitialize()
    ctypes.windll.ole32.CoInitializeEx(None, 0)
    import comtypes  # noqa: F401

warnings.filterwarnings("ignore", message="data discontinuity")
