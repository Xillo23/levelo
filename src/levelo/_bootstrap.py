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


def _calmer_capture():
    """soundcard's recorder waits for audio by sleeping ~0.75 ms and asking the
    device period (a COM call + allocations) every time: ~700 calls/s, most of
    Levelo's CPU. The period never changes: ask once, and poll every ~5 ms
    (audio arrives in 10 ms packets, so nothing is lost or delayed)."""
    try:
        from soundcard import mediafoundation as mf
        base = mf._AudioClient.deviceperiod.fget

        def period(self):
            p = self.__dict__.get("_levelo_period")
            if p is None:
                default, minimum = base(self)
                p = (default, max(minimum, default) * 2)
                self.__dict__["_levelo_period"] = p
            return p
        mf._Recorder.deviceperiod = property(period)
    except Exception:
        pass


_calmer_capture()
