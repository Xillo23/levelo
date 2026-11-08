"""Friendly name and icon of an app, read from its .exe (Win32 only, no COM)."""

import ctypes
from ctypes import wintypes

from PIL import Image

_ver = ctypes.WinDLL("version")
_user32 = ctypes.WinDLL("user32")
_gdi32 = ctypes.WinDLL("gdi32")

_ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.c_void_p]
_ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                ctypes.POINTER(ctypes.c_void_p),
                                ctypes.POINTER(wintypes.UINT)]
_user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.POINTER(wintypes.HICON),
                                         ctypes.POINTER(wintypes.UINT), wintypes.UINT,
                                         wintypes.UINT]
_user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HICON,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HBRUSH,
                               wintypes.UINT]
_user32.DestroyIcon.argtypes = [wintypes.HICON]
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                    wintypes.DWORD]
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wintypes.HDC]

_desc_cache = {}
_icon_cache = {}


def _query(buf, sub):
    ptr, size = ctypes.c_void_p(), wintypes.UINT()
    if not _ver.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(size)) or not size.value:
        return None, 0
    return ptr, size.value


def file_description(path):
    """'Google Chrome', 'Discord', ... or '' when the exe has no description."""
    if not path:
        return ""
    if path in _desc_cache:
        return _desc_cache[path]
    out = ""
    try:
        n = _ver.GetFileVersionInfoSizeW(path, None)
        if n:
            buf = ctypes.create_string_buffer(n)
            if _ver.GetFileVersionInfoW(path, 0, n, buf):
                langs = []
                ptr, size = _query(buf, "\\VarFileInfo\\Translation")
                if ptr:
                    arr = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD))
                    langs = [(arr[i], arr[i + 1]) for i in range(0, size // 2, 2)]
                langs += [(0x0409, 0x04B0), (0x0409, 0x04E4), (0x0000, 0x04B0)]
                for key in ("FileDescription", "ProductName"):
                    for lang, cp in langs:
                        ptr, size = _query(buf, f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{key}")
                        if ptr:
                            out = ctypes.wstring_at(ptr, size).strip("\x00 ").strip()
                            if out:
                                break
                    if out:
                        break
    except Exception:
        out = ""
    _desc_cache[path] = out
    return out


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def exe_icon(path, size):
    """The exe's icon as a PIL RGBA image of size x size, or None."""
    if not path:
        return None
    key = (path, size)
    if key in _icon_cache:
        return _icon_cache[key]
    img = None
    hicon = wintypes.HICON()
    try:
        got = _user32.PrivateExtractIconsW(path, 0, size, size, ctypes.byref(hicon),
                                           None, 1, 0)
        if got >= 1 and hicon.value:
            img = _hicon_to_image(hicon, size)
    except Exception:
        img = None
    finally:
        if hicon.value:
            _user32.DestroyIcon(hicon)
    _icon_cache[key] = img
    return img


def _hicon_to_image(hicon, size):
    hdc = _gdi32.CreateCompatibleDC(None)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth, bmi.biHeight = size, -size        # top-down
    bmi.biPlanes, bmi.biBitCount = 1, 32
    bits = ctypes.c_void_p()
    hbmp = _gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    try:
        old = _gdi32.SelectObject(hdc, hbmp)
        _user32.DrawIconEx(hdc, 0, 0, hicon, size, size, 0, None, 3)   # DI_NORMAL
        raw = ctypes.string_at(bits, size * size * 4)
        _gdi32.SelectObject(hdc, old)
    finally:
        _gdi32.DeleteObject(hbmp)
        _gdi32.DeleteDC(hdc)
    img = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1)
    if img.getchannel("A").getextrema()[1] == 0:     # old icon without alpha
        img.putalpha(255)
    return img.copy()
