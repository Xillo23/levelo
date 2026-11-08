"""Real-time engine: loopback capture -> Leveler -> per-app (session) volumes.

All COM objects live in the engine thread.
"""

from . import _bootstrap  # noqa: F401  (must come first)

import ctypes
import math
import os
import queue
import threading
import time
from collections import deque

import numpy as np
import soundcard as sc

from .dsp import BLOCK, BLOCK_S, HEADROOM, SR, Leveler, db_to_lin
from .storage import STATE_FILE, load_json, log, save_json

SESSION_MIN_DB = -60.0

# Voice-call apps (process names, lowercase). By default their volume is never
# changed and their sound does not drive the balancing: the voice of whoever
# is calling stays as it is while the movie is balanced.
CALL_APPS = {
    "discord.exe", "discordptb.exe", "discordcanary.exe", "teams.exe", "ms-teams.exe",
    "msteams.exe", "zoom.exe", "skype.exe", "skypeapp.exe", "slack.exe",
    "whatsapp.exe", "whatsapp.root.exe", "telegram.exe", "signal.exe", "viber.exe",
    "ts3client_win64.exe", "ts3client_win32.exe", "teamspeak.exe", "mumble.exe",
    "webex.exe", "ciscocollabhost.exe", "element.exe", "guilded.exe",
    "phoneexperiencehost.exe", "ringcentral.exe", "gotomeeting.exe",
    "ayugram.exe", "kotatogram.exe", "64gram.exe", "unigram.exe", "beeper.exe",
}
# An excluded app "speaks" when its peak is above -50 dBFS and not far below
# the balanced apps: then the measurement is frozen (see Leveler.process).
TALK_PEAK = 0.003
TALK_RATIO = 0.4
TALK_HOLD_S = 0.4
CALL_SHOW_S = 15.0       # the panel says "in a call" this long after the last word


def new_device_state():
    return {"apps": {}, "master_raised": 0.0, "active": False}


def load_restore():
    """restore.json: one state per output device. Migrates the 0.9.0 format."""
    root = load_json(STATE_FILE, {"devices": {}})
    if not isinstance(root.get("devices"), dict):
        root["devices"] = {}
    if "apps" in root:   # 0.9.0: a single state for "the" default device
        apps = root.pop("apps")
        root["legacy"] = {"apps": apps if isinstance(apps, dict) else {},
                          "master_raised": float(root.pop("master_raised", 0.0) or 0.0),
                          "active": bool(root.pop("active", False))}
    for key, st in list(root["devices"].items()):
        if not isinstance(st, dict):
            root["devices"][key] = st = new_device_state()
        if not isinstance(st.get("apps"), dict):
            st["apps"] = {}
    return root


_names = {}


def process_name(pid):
    if pid == 0:
        return "System"
    hit = _names.get(pid)
    if hit is not None:
        return hit
    try:
        import psutil
        name = psutil.Process(pid).name()
    except Exception:
        name = f"pid{pid}"
    if len(_names) > 500:
        _names.clear()
    _names[pid] = name
    return name


class Session:
    __slots__ = ("name", "sav", "meter")

    def __init__(self, name, sav, meter):
        self.name, self.sav, self.meter = name, sav, meter


class WinAudio:
    """Master (endpoint) volume + per-app session volumes of ONE output device."""

    def __init__(self, root, device_id):
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import (AudioUtilities, IAudioEndpointVolume,
                                 IAudioMeterInformation, IAudioSessionControl2,
                                 IAudioSessionManager2, ISimpleAudioVolume)
        self._itf = (IAudioSessionControl2, ISimpleAudioVolume, IAudioMeterInformation)
        dev = AudioUtilities.GetDeviceEnumerator().GetDevice(device_id)
        self.ep = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                               ).QueryInterface(IAudioEndpointVolume)
        self.mgr = dev.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None
                                ).QueryInterface(IAudioSessionManager2)
        lo, hi, _ = self.ep.GetVolumeRange()
        self.lo, self.hi = max(lo, -96.0), hi
        try:
            hw = int(self.ep.QueryHardwareSupport())
        except Exception:
            hw = 0
        # Software volume (almost always): the loopback is already attenuated
        self.post_volume = not (hw & 1)
        self.root = root                 # whole restore.json
        devs = root["devices"]
        if device_id not in devs:
            devs[device_id] = root.pop("legacy", None) or new_device_state()
        self.restore = devs[device_id]   # this device's state
        self.sessions = {}               # key -> Session
        self.written = {}                # key -> (last scalar we wrote, offset dB)
        self.dirty = False               # restore changed, save at next refresh
        self.session_db = 0.0
        self.active = False
        self.my_pid = os.getpid()
        self.call_protect = True
        self.rules = {}                  # lowercase app name -> balance it?

    def save(self):
        save_json(STATE_FILE, self.root)

    # --- which apps are balanced --------------------------------------------
    def is_excluded(self, name):
        n = name.lower()
        if n in self.rules:
            return not self.rules[n]
        return self.call_protect and n in CALL_APPS

    def set_rules(self, call_protect, rules):
        self.call_protect = bool(call_protect)
        self.rules = {str(k).lower(): bool(v) for k, v in (rules or {}).items()}
        if self.active:
            for key, s in self.sessions.items():
                self._set_session(key, s)

    def apps(self):
        """((name, excluded, is_call_app), ...) for the settings page."""
        seen = {}
        for s in self.sessions.values():
            if s.name not in seen:
                seen[s.name] = (s.name, self.is_excluded(s.name),
                                s.name.lower() in CALL_APPS)
        return tuple(sorted(seen.values(), key=lambda a: a[0].lower()))

    # --- master -------------------------------------------------------------
    def master_db(self):
        return float(self.ep.GetMasterVolumeLevel())

    def set_master_db(self, db):
        db = max(self.lo, min(self.hi, db))
        self.ep.SetMasterVolumeLevel(float(db), None)
        return db

    # --- sessions -----------------------------------------------------------
    def _scan(self):
        ctl2_i, sav_i, meter_i = self._itf
        found = {}
        en = self.mgr.GetSessionEnumerator()
        for i in range(en.GetCount()):
            try:
                ctl = en.GetSession(i).QueryInterface(ctl2_i)
                if ctl.GetState() == 2:          # expired
                    continue
                pid = ctl.GetProcessId()
                if pid == self.my_pid:
                    continue
                name = process_name(pid)
                key = ctl.GetSessionInstanceIdentifier() or f"{name}:{pid}"
                try:
                    meter = ctl.QueryInterface(meter_i)
                except Exception:
                    meter = None
                found[key] = Session(name, ctl.QueryInterface(sav_i), meter)
            except Exception:
                continue
        return found

    def _offset_db(self, name):
        if self.is_excluded(name):
            # exactly as loud as before: only the master raise is compensated
            return -float(self.restore.get("master_raised", 0.0))
        return self.session_db

    def _set_session(self, key, s):
        # Windows app volume is scalar^2 (0.5 = -12 dB)
        self._user_changed(key, s)
        orig = self.restore["apps"].get(s.name, 1.0)
        orig_db = 40.0 * math.log10(max(orig, 1e-4))
        off = self._offset_db(s.name)
        db = max(SESSION_MIN_DB, min(0.0, orig_db + off))
        v = float(10.0 ** (db / 40.0))
        w = self.written.get(key)
        if w is not None and w[0] == v:
            return
        try:
            s.sav.SetMasterVolume(v, None)
            self.written[key] = (v, off)
        except Exception:
            pass

    def _user_changed(self, key, s):
        """Someone else (volume mixer, EarTrumpet, Discord attenuation...) moved
        this app: that becomes its new original volume instead of being undone."""
        w = self.written.get(key)
        if w is None:
            return False
        try:
            cur = float(s.sav.GetMasterVolume())
        except Exception:
            return False
        if abs(cur - w[0]) < 0.01:
            return False
        orig_db = 40.0 * math.log10(max(cur, 1e-4)) - w[1]
        self.restore["apps"][s.name] = max(0.0, min(1.0, 10.0 ** (orig_db / 40.0)))
        self.written.pop(key, None)
        self.dirty = True
        return True

    def refresh(self):
        """New apps: remember their volume and apply the offset.
        Known apps: notice volumes changed by the user."""
        try:
            found = self._scan()
        except Exception:
            return
        changed = False
        apps = self.restore["apps"]
        left = self.restore.setdefault("left", {})
        # Apps closed while lowered: Windows keeps that lowered volume for their
        # next start. Remember it, to tell it apart from a volume set by the user.
        found_names = {s.name for s in found.values()}
        for key, s in self.sessions.items():
            if key not in found and s.name not in found_names and key in self.written:
                left[s.name] = self.written[key][0]
                changed = True
        for key, s in found.items():
            known = key in self.sessions
            if not known and s.name in left:
                was = left.pop(s.name)
                changed = True
                try:
                    if abs(float(s.sav.GetMasterVolume()) - was) > 0.02:
                        apps.pop(s.name, None)   # changed by the user meanwhile: keep it
                except Exception:
                    pass
            if self.active:
                if known:
                    if self._user_changed(key, s):
                        self._set_session(key, s)
                    continue
                if s.name not in self.restore["apps"]:
                    try:
                        self.restore["apps"][s.name] = float(s.sav.GetMasterVolume())
                    except Exception:
                        self.restore["apps"][s.name] = 1.0
                    changed = True
                self._set_session(key, s)
            elif not known and s.name in self.restore["apps"]:
                # left lowered by an abnormal exit: restore it
                try:
                    s.sav.SetMasterVolume(float(self.restore["apps"].pop(s.name)), None)
                    changed = True
                except Exception:
                    pass
        self.written = {k: v for k, v in self.written.items() if k in found}
        self.sessions = found
        if changed or self.dirty:
            self.dirty = False
            self.save()

    def apply_session_db(self, db):
        self.session_db = db
        for key, s in self.sessions.items():
            if not self.is_excluded(s.name):
                self._set_session(key, s)

    def peaks(self):
        """(loudest excluded app peak, its name, loudest balanced app peak).
        Session meters are measured before the app volume (verified)."""
        ex, ex_name, lev = 0.0, "", 0.0
        for s in self.sessions.values():
            if s.meter is None:
                continue
            try:
                p = float(s.meter.GetPeakValue())
            except Exception:
                continue
            if self.is_excluded(s.name):
                if p > ex:
                    ex, ex_name = p, s.name
            elif p > lev:
                lev = p
        return ex, ex_name, lev

    # --- on / off -----------------------------------------------------------
    def activate(self):
        if self.active:
            return
        cur = self.master_db()
        raise_db = max(0.0, min(HEADROOM, self.hi - cur))
        self.restore["master_raised"] = raise_db
        self.restore["active"] = True
        self.active = True
        self.sessions = {}
        self.written = {}
        self.save()
        # lower the apps first, then raise master: never a spike in between
        self.session_db = -HEADROOM
        self.refresh()
        if raise_db > 0:
            self.set_master_db(cur + raise_db)

    def deactivate(self):
        raised = float(self.restore.get("master_raised", 0.0))
        # lower master first, then raise the apps: never a spike in between
        master_ok = True
        if raised > 0:
            try:
                self.set_master_db(self.master_db() - raised)
            except Exception:
                master_ok = False   # device gone: undone by recover() when it is back
        try:
            found = self._scan()
        except Exception:
            found = self.sessions
        done = set()
        for s in found.values():
            if s.name in self.restore["apps"]:
                try:
                    s.sav.SetMasterVolume(float(self.restore["apps"][s.name]), None)
                    done.add(s.name)
                except Exception:
                    pass
        for name in done:
            self.restore["apps"].pop(name, None)
        if master_ok:
            self.restore["master_raised"] = 0.0
        self.restore["active"] = False
        self.active = False
        self.session_db = 0.0
        self.sessions = found
        self.written = {}
        self.save()

    def recover(self):
        """After an abnormal exit (or a device that vanished while active),
        put back what we can."""
        raised = float(self.restore.get("master_raised", 0.0))
        if raised > 0:
            try:
                self.set_master_db(self.master_db() - raised)
                self.restore["master_raised"] = 0.0
            except Exception:
                pass
        self.restore["active"] = False
        self.active = False
        self.refresh()
        self.save()


class Engine(threading.Thread):
    def __init__(self, enabled, preset, device=None, call_protect=True, app_rules=None):
        super().__init__(daemon=True, name="AudioEngine")
        self.cmds = queue.Queue()
        self.leveler = Leveler(preset)
        self.want_enabled = enabled
        self.want_device = device or None     # None = Windows default device
        self.call_protect = bool(call_protect)
        self.app_rules = dict(app_rules or {})
        self.enabled = False
        self.alive = True
        self.finished = threading.Event()
        # state read by the UI
        self.gain = 0.0
        self.state = "silence"
        self.history = deque([0.0] * 300, maxlen=300)   # 15 s at 20 Hz
        self.device = ""             # name of the device being balanced
        self.device_id = None
        self.devices = ()            # ((id, name), ...) connected outputs
        self.device_missing = False  # chosen device not connected: using default
        self.default_name = ""       # Windows default output (for "Automatic")
        self.apps = ()               # ((name, excluded, is_call_app), ...)
        self.call_app = ""           # call app heard in the last seconds
        self.error = None
        self.headroom_short = False
        # usage statistics (shown in the support card)
        self.active_s = 0.0      # seconds of audio actually balanced
        self.peaks = 0           # loud moments that were lowered

    # thread-safe API
    def set_enabled(self, on):
        self.cmds.put(("enabled", bool(on)))

    def set_preset(self, name):
        self.cmds.put(("preset", name))

    def set_device(self, dev_id):
        self.cmds.put(("device", dev_id or None))

    def set_app_rules(self, call_protect, rules):
        self.cmds.put(("apps", (bool(call_protect), dict(rules))))

    def shutdown(self, timeout=2.5):
        self.alive = False
        self.finished.wait(timeout)

    def _pick_speaker(self):
        speakers = sc.all_speakers()
        self.devices = tuple((s.id, s.name) for s in speakers)
        default = sc.default_speaker()
        self.default_name = default.name
        if self.want_device:
            for s in speakers:
                if s.id == self.want_device:
                    self.device_missing = False
                    return s
            self.device_missing = True
        else:
            self.device_missing = False
        return default

    def run(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        root = load_restore()
        try:
            while self.alive:
                self._run(root)      # returns only after an unexpected error
                if self.alive:
                    time.sleep(2.0)  # then start again instead of dying
        finally:
            self.finished.set()

    def _run(self, root):
        win = rec = rec_ctx = dev_id = None
        last_scan = last_dev = 0.0
        talk_until = call_until = 0.0
        call_name = ""
        tick = 0
        last_err = None
        try:
            while self.alive:
                if rec is None:
                    try:
                        spk = self._pick_speaker()
                        dev_id = spk.id
                        win = WinAudio(root, dev_id)
                        win.set_rules(self.call_protect, self.app_rules)
                        win.recover()
                        mic = sc.get_microphone(id=dev_id, include_loopback=True)
                        rec_ctx = mic.recorder(samplerate=SR, blocksize=BLOCK)
                        rec = rec_ctx.__enter__()
                        self.device, self.device_id = spk.name, dev_id
                        self.apps = win.apps()
                        self.leveler.reset()
                        self.enabled = False
                        self.error = None
                        last_dev = time.monotonic()
                    except Exception as exc:
                        self.error = str(exc)
                        if self.error != last_err:
                            last_err = self.error
                            log(f"open device: {type(exc).__name__}: {exc}")
                        self._close(win, rec_ctx)
                        win = rec = rec_ctx = None
                        time.sleep(1.0)
                        continue

                while True:
                    try:
                        cmd, val = self.cmds.get_nowait()
                    except queue.Empty:
                        break
                    if cmd == "enabled":
                        self.want_enabled = val
                    elif cmd == "preset":
                        self.leveler.set_preset(val)
                    elif cmd == "device":
                        self.want_device = val
                        last_dev = 0.0           # re-check right now
                    elif cmd == "apps":
                        self.call_protect, self.app_rules = val
                        win.set_rules(*val)
                        self.apps = win.apps()
                try:
                    if self.want_enabled and not self.enabled:
                        win.activate()
                        self.enabled = True
                    elif not self.want_enabled and self.enabled:
                        win.deactivate()
                        self.enabled = False
                except Exception as exc:
                    # device unplugged in the middle, COM error...: start over
                    log(f"on/off: {type(exc).__name__}: {exc}")
                    self._close(win, rec_ctx)
                    rec = None
                    time.sleep(0.5)
                    continue

                try:
                    data = rec.record(numframes=BLOCK)
                except Exception:
                    self._close(win, rec_ctx)
                    rec = None
                    continue
                data = np.asarray(data, dtype=np.float64)
                mono = data.mean(axis=1) if data.ndim > 1 else data

                now = time.monotonic()
                if now - last_scan > 1.0:
                    last_scan = now
                    win.refresh()
                    self.apps = win.apps()
                if now - last_dev > 2.0:
                    last_dev = now
                    try:
                        if self._pick_speaker().id != dev_id:
                            # headphones/speakers changed: restore and restart
                            self._close(win, rec_ctx)
                            rec = None
                            continue
                    except Exception:
                        pass

                # Someone in a call (or another excluded app) is talking:
                # do not let that voice move the movie's volume.
                if tick % 2 == 0:
                    ex, ex_name, lev = win.peaks()
                    if ex > TALK_PEAK and ex > TALK_RATIO * lev:
                        talk_until = now + TALK_HOLD_S
                        if ex_name.lower() in CALL_APPS:
                            call_name, call_until = ex_name, now + CALL_SHOW_S
                self.call_app = call_name if now < call_until else ""

                # original signal = captured / (master volume * app volume)
                try:
                    master = win.master_db() if win.post_volume else 0.0
                except Exception:
                    master = 0.0
                comp_db = -(master + win.session_db)
                orig = mono * db_to_lin(min(comp_db, 90.0))

                gain = self.leveler.process(orig, boost_cap=HEADROOM,
                                            freeze=now < talk_until)

                if self.enabled:
                    target = gain - HEADROOM
                    if abs(target - win.session_db) > 0.1:
                        win.apply_session_db(target)
                    self.headroom_short = (
                        float(win.restore.get("master_raised", 0.0)) < HEADROOM - 0.5)

                self.gain = gain if self.enabled else 0.0
                new_state = self.leveler.state
                if self.enabled and new_state != "silence":
                    self.active_s += BLOCK_S
                    if new_state == "loud" and self.state != "loud":
                        self.peaks += 1
                self.state = new_state
                tick += 1
                if tick % 5 == 0:
                    self.history.append(self.gain)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log(f"engine error: {self.error}")
        finally:
            self._close(win, rec_ctx)

    def _close(self, win, rec_ctx):
        if win is not None and win.active:
            try:
                win.deactivate()
            except Exception:
                pass
        self.enabled = False
        if rec_ctx is not None:
            try:
                rec_ctx.__exit__(None, None, None)
            except Exception:
                pass
