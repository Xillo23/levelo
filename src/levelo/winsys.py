"""Windows integration: autostart, single instance, theme, work area, DWM."""

import ctypes
import os
import sys
import winreg

from . import APP_ID

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
APP_KEY = rf"Software\{APP_ID}"


def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" -m levelo --background'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_ID)
            return True
    except OSError:
        return False


def set_autostart(on):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_ID)
                except OSError:
                    pass
    except OSError:
        pass


def installer_language():
    """Language chosen in the setup wizard, if any."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APP_KEY) as k:
            return str(winreg.QueryValueEx(k, "Language")[0])
    except OSError:
        return None


def theme_flag(name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, THEME_KEY) as k:
            return winreg.QueryValueEx(k, name)[0] == 1
    except OSError:
        return False


def apps_dark():
    return not theme_flag("AppsUseLightTheme")


def taskbar_light():
    return theme_flag("SystemUsesLightTheme")


class SingleInstance:
    """Instance mutex + named events to show / quit the running instance."""

    WAIT_OBJECT_0 = 0

    def __init__(self):
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateEventW.restype = ctypes.c_void_p
        k32.OpenMutexW.restype = ctypes.c_void_p
        self.k32 = k32
        self.mutex = k32.CreateMutexW(None, False, f"Local\\{APP_ID}.Instance")
        self.already = k32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        self.ev_show = k32.CreateEventW(None, False, False, f"Local\\{APP_ID}.Show")
        self.ev_quit = k32.CreateEventW(None, True, False, f"Local\\{APP_ID}.Quit")

    def signal_show(self):
        self.k32.SetEvent(ctypes.c_void_p(self.ev_show))

    def signal_quit(self, wait_s=6.0):
        """Ask the running instance to exit cleanly and wait for it."""
        self.k32.SetEvent(ctypes.c_void_p(self.ev_quit))
        self.k32.CloseHandle(ctypes.c_void_p(self.mutex))
        import time
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait_s:
            h = self.k32.OpenMutexW(0x00100000, False, f"Local\\{APP_ID}.Instance")
            if not h:
                return True
            self.k32.CloseHandle(ctypes.c_void_p(h))
            time.sleep(0.1)
        return False

    def wait_loop(self, on_show, on_quit):
        handles = (ctypes.c_void_p * 2)(self.ev_show, self.ev_quit)
        while True:
            r = self.k32.WaitForMultipleObjects(2, handles, False, 0xFFFFFFFF)
            if r == 0:
                on_show()
            elif r == 1:
                on_quit()
                return


def work_area():
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rc), 0)
    return rc.l, rc.t, rc.r, rc.b


def dwm_style(hwnd, dark):
    dwm = ctypes.windll.dwmapi
    for attr, val in ((20, int(dark)),   # dark border
                      (33, 2)):          # rounded corners (Windows 11)
        v = ctypes.c_int(val)
        try:
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr,
                                      ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def set_app_user_model_id():
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def open_url(url):
    if url:
        os.startfile(url)
