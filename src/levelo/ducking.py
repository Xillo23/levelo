"""Windows "communications activity" ducking.

When a call starts (Discord, Teams, WhatsApp...), Windows turns every other
sound down by 80% by default (Sound control panel > Communications). With
Levelo running that fights the balancing: the movie suddenly drops ~18 dB
while a friend talks. While Levelo is on it asks Windows not to do it, and
gives the listener's own choice back when it is paused or closed.

The user's choice is never written: it stays in the registry
(HKCU\\Software\\Microsoft\\Multimedia\\Audio\\UserDuckingPreference) and Windows
reloads it at the next start anyway. Writing the registry alone is NOT applied
live (measured 2026-11-16); the Sound control panel also calls the audio
policy object (CLSID 870AF99C..., interface E8478600..., method 18) with
L"Comm" and the ducking gain in dB. That is what is done here. It changes
new calls; a call already open keeps its ducking until it reopens.
"""

import ctypes
import winreg
from ctypes import wintypes

import comtypes
from comtypes import GUID, IUnknown

_KEY = r"Software\Microsoft\Multimedia\Audio"
_NAME = "UserDuckingPreference"
# control panel choice -> ducking gain (dB), as mmsys.cpl sends it
_GAIN = {0: -96.0, 1: -18.0, 2: -6.0, 3: 0.0}
_DEFAULT = 1                     # "reduce by 80%" when nothing was ever chosen

_CLSID_POLICY = GUID("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")


class _IPolicyDucking(IUnknown):
    # undocumented; QueryInterface fails on a Windows without it (nothing done)
    _iid_ = GUID("{E8478600-A74B-4B3A-A96B-1FC3E796FC46}")
    _methods_ = []


_SetDuckingGain = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p,
                                     wintypes.LPCWSTR, ctypes.c_float)


def user_choice():
    """The listener's choice in the Sound control panel (0..3)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY) as k:
            v = int(winreg.QueryValueEx(k, _NAME)[0])
        return v if v in _GAIN else _DEFAULT
    except (OSError, ValueError):
        return _DEFAULT


def set_gain(db):
    """Ducking of other sounds during calls, in dB (0 = none). Needs COM
    initialised on the calling thread. Raises on failure."""
    obj = comtypes.CoCreateInstance(_CLSID_POLICY, _IPolicyDucking, comtypes.CLSCTX_ALL)
    ptr = ctypes.cast(obj, ctypes.c_void_p).value
    vtbl = ctypes.cast(ctypes.c_void_p.from_address(ptr).value,
                       ctypes.POINTER(ctypes.c_void_p))
    _SetDuckingGain(vtbl[18])(ptr, "Comm", float(db))   # raises OSError on a bad HRESULT


class WindowsDucking:
    """Keeps Windows from ducking while wanted; idempotent, never raises."""

    def __init__(self, log=None):
        self.applied = False
        self.broken = False
        self._log = log or (lambda msg: None)

    def apply(self, want):
        if self.broken or bool(want) == self.applied:
            return
        try:
            if want:
                set_gain(0.0)
                self.applied = True
            else:
                set_gain(_GAIN[user_choice()])
                self.applied = False
        except Exception as exc:
            # not available on this Windows: leave it alone for good
            self.broken = True
            self._log(f"windows ducking: {type(exc).__name__}: {exc}")
