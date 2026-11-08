"""Real-time engine: loopback capture -> Leveler -> per-app (session) volumes.

All COM objects live in the engine thread.
"""

from . import _bootstrap  # noqa: F401  (must come first)

import ctypes
import json
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
    "ayugram.exe", "kotatogram.exe", "xboxpartychathost.exe", "64gram.exe", "unigram.exe", "beeper.exe",
}
# An excluded app "speaks" when its peak is above -50 dBFS and not far below
# the balanced apps: then the measurement is frozen (see Leveler.process).
TALK_PEAK = 0.003
TALK_RATIO = 0.4
TALK_HOLD_S = 0.4
CALL_SHOW_S = 15.0       # the panel says "in a call" this long after the last word

# Apps that usually play sound: listed in the settings as soon as they are
# running, even before they open an audio stream.
MEDIA_APPS = CALL_APPS | {
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe", "vivaldi.exe",
    "arc.exe", "spotify.exe", "vlc.exe", "mpc-hc64.exe", "mpc-hc.exe", "mpc-be64.exe",
    "potplayermini64.exe", "potplayermini.exe", "mpv.exe", "wmplayer.exe", "music.ui.exe",
    "video.ui.exe", "microsoft.media.player.exe", "plex.exe", "plex htpc.exe", "kodi.exe",
    "itunes.exe", "applemusic.exe", "appletv.exe", "deezer.exe", "tidal.exe",
    "amazon music.exe", "netflix.exe", "disneyplus.exe", "primevideo.exe", "steam.exe",
    "obs64.exe", "audacity.exe", "foobar2000.exe", "aimp.exe", "musicbee.exe",
}


def new_device_state():
    return {"apps": {}, "master_raised": 0.0, "active": False}


def load_restore():
    """restore.json: one state per output device. Migrates the 0.9.0 format."""
    root = load_json(STATE_FILE, {"devices": {}})
    if not isinstance(root.get("devices"), dict):
        root["devices"] = {}
    if not isinstance(root.get("legacy"), dict):
        root.pop("legacy", None)
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


_procs = {}


def process_info(pid):
    """(exe name, full path) of a process, cached."""
    if pid == 0:
        return "System", ""
    hit = _procs.get(pid)
    if hit is not None:
        return hit
    try:
        import psutil
        p = psutil.Process(pid)
        name = p.name()
        try:
            path = p.exe()
        except Exception:
            path = ""
    except Exception:
        name, path = f"pid{pid}", ""
    if len(_procs) > 500:
        _procs.clear()
    _procs[pid] = (name, path)
    return name, path


class Session:
    __slots__ = ("name", "sav", "meter", "path", "playing")

    def __init__(self, name, sav, meter, path="", playing=False):
        self.name, self.sav, self.meter = name, sav, meter
        self.path, self.playing = path, playing


class WinAudio:
    """Master (endpoint) volume + per-app session volumes of ONE output device."""

    def __init__(self, store, device_id):
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
        self.store, self.dev_id = store, device_id
        self.restore = store.take(device_id)   # this device's state (own copy)
        self.sessions = {}               # key -> Session
        self.written = {}                # key -> (last scalar we wrote, offset dB)
        self.dirty = False               # restore changed, save at next refresh
        self.session_db = 0.0
        self.active = False
        self.my_pid = os.getpid()
        self.call_protect = True
        self.rules = {}                  # lowercase app name -> balance it?

    def save(self):
        self.store.save(self.dev_id, self.restore)

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
        """((name, excluded, is_call_app, playing, exe path), ...) for the settings."""
        seen = {}
        for s in self.sessions.values():
            old = seen.get(s.name)
            if old is None or (s.playing and not old[3]):
                seen[s.name] = (s.name, self.is_excluded(s.name),
                                s.name.lower() in CALL_APPS, s.playing, s.path)
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
                state = ctl.GetState()
                if state == 2:                   # expired
                    continue
                pid = ctl.GetProcessId()
                if pid == self.my_pid:
                    continue
                name, path = process_info(pid)
                key = ctl.GetSessionInstanceIdentifier() or f"{name}:{pid}"
                try:
                    meter = ctl.QueryInterface(meter_i)
                except Exception:
                    meter = None
                found[key] = Session(name, ctl.QueryInterface(sav_i), meter, path,
                                     state == 1)
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


class RestoreStore:
    """restore.json shared by the device threads. Each thread works on its own
    copy of its device state and hands a snapshot here to be written."""

    def __init__(self):
        self.lock = threading.Lock()
        root = load_restore()
        self.legacy = root.get("legacy")
        self.snap = dict(root["devices"])

    def take(self, dev_id):
        with self.lock:
            st = self.snap.get(dev_id)
            if st is None:
                st = self.legacy or new_device_state()
                self.legacy = None
                self.snap[dev_id] = st
            return json.loads(json.dumps(st))

    def peek(self, dev_id):
        with self.lock:
            return self.snap.get(dev_id)

    def save(self, dev_id, state):
        with self.lock:
            self.snap[dev_id] = json.loads(json.dumps(state))
            data = {"devices": self.snap}
            if self.legacy:
                data["legacy"] = self.legacy
            save_json(STATE_FILE, data)


class DeviceWorker(threading.Thread):
    """Capture + balancing of ONE output device (its own thread and COM objects)."""

    def __init__(self, engine, dev_id, name):
        super().__init__(daemon=True, name=f"Device {name}")
        self.engine, self.dev_id, self.device_name = engine, dev_id, name
        self.cmds = queue.Queue()
        self.leveler = Leveler(engine.preset)
        self.alive = True
        self.finished = threading.Event()
        self.enabled = False
        self.gain = 0.0
        self.state = "silence"
        self.history = deque([0.0] * 300, maxlen=300)   # 15 s at 20 Hz
        self.last_sound = 0.0     # monotonic time of the last non-silent block
        self.apps = ()
        self.call_app = ""
        self.error = None
        self.headroom_short = False
        self.active_s = 0.0
        self.peaks = 0

    def stop(self):
        self.alive = False

    def run(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        try:
            while self.alive:
                self._run()          # returns only after an unexpected error
                if self.alive:
                    time.sleep(2.0)  # then start again instead of dying
        finally:
            self.enabled = False
            self.finished.set()

    def _run(self):
        eng = self.engine
        win = rec = rec_ctx = None
        last_scan = 0.0
        talk_until = call_until = 0.0
        call_name = ""
        tick = 0
        last_err = None
        try:
            while self.alive:
                if rec is None:
                    try:
                        win = WinAudio(eng.store, self.dev_id)
                        win.set_rules(eng.call_protect, eng.app_rules)
                        win.recover()
                        mic = sc.get_microphone(id=self.dev_id, include_loopback=True)
                        rec_ctx = mic.recorder(samplerate=SR, blocksize=BLOCK)
                        rec = rec_ctx.__enter__()
                        self.apps = win.apps()
                        self.leveler.reset()
                        self.enabled = False
                        self.error = None
                    except Exception as exc:
                        self.error = str(exc)
                        if self.error != last_err:
                            last_err = self.error
                            log(f"open {self.device_name}: {type(exc).__name__}: {exc}")
                        self._close(win, rec_ctx)
                        win = rec = rec_ctx = None
                        time.sleep(1.0)
                        continue

                while True:
                    try:
                        cmd, val = self.cmds.get_nowait()
                    except queue.Empty:
                        break
                    if cmd == "preset":
                        self.leveler.set_preset(val)
                    elif cmd == "apps":
                        win.set_rules(*val)
                        self.apps = win.apps()
                try:
                    if eng.want_enabled and not self.enabled:
                        win.activate()
                        self.enabled = True
                    elif not eng.want_enabled and self.enabled:
                        win.deactivate()
                        self.enabled = False
                except Exception as exc:
                    # device unplugged in the middle, COM error...: start over
                    log(f"on/off {self.device_name}: {type(exc).__name__}: {exc}")
                    self._close(win, rec_ctx)
                    rec = None
                    time.sleep(0.5)
                    continue

                try:
                    data = rec.record(numframes=BLOCK)
                except Exception:
                    self._close(win, rec_ctx)
                    rec = None
                    time.sleep(0.5)
                    continue
                data = np.asarray(data, dtype=np.float64)
                mono = data.mean(axis=1) if data.ndim > 1 else data

                now = time.monotonic()
                if now - last_scan > 1.0:
                    last_scan = now
                    win.refresh()
                    self.apps = win.apps()

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
                if new_state != "silence":
                    self.last_sound = now
                    if self.enabled:
                        self.active_s += BLOCK_S
                        if new_state == "loud" and self.state != "loud":
                            self.peaks += 1
                self.state = new_state
                tick += 1
                if tick % 5 == 0:
                    self.history.append(self.gain)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            log(f"{self.device_name}: {self.error}")
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


FORM_FACTOR_KEY = "{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0"


class Engine(threading.Thread):
    """Starts one DeviceWorker per output device to balance: the Windows default
    device (devices=None) or the devices chosen in the settings."""

    def __init__(self, enabled, preset, devices=None, call_protect=True, app_rules=None):
        super().__init__(daemon=True, name="AudioEngine")
        self.cmds = queue.Queue()
        self.want_enabled = bool(enabled)
        self.preset = preset
        self.want_devices = list(devices) if devices is not None else None
        self.call_protect = bool(call_protect)
        self.app_rules = dict(app_rules or {})
        self.alive = True
        self.finished = threading.Event()
        self.store = None
        self.workers = {}            # device id -> DeviceWorker
        # state read by the UI
        self.devices = ()            # ((id, name, form factor), ...) connected outputs
        self.default_id = None
        self.default_name = ""
        self.missing = ()            # chosen devices that are not connected
        self.error = None
        self._done_s = 0.0           # stats of workers already stopped
        self._done_peaks = 0
        self._idle = deque([0.0] * 300, maxlen=300)
        self.other_apps = ()         # ((name, path, playing), ...) not on balanced devices
        self._last_apps = 0.0

    # ---- thread-safe API ----------------------------------------------------
    def set_enabled(self, on):
        self.want_enabled = bool(on)

    def set_preset(self, name):
        self.preset = name
        for w in list(self.workers.values()):
            w.cmds.put(("preset", name))

    def set_devices(self, ids):
        """ids: list of device ids, or None for the Windows default device."""
        self.cmds.put(("devices", list(ids) if ids is not None else None))

    def set_app_rules(self, call_protect, rules):
        self.call_protect, self.app_rules = bool(call_protect), dict(rules)
        for w in list(self.workers.values()):
            w.cmds.put(("apps", (self.call_protect, dict(self.app_rules))))

    def shutdown(self, timeout=6.0):
        """Stop and put every volume back (waits for the device threads)."""
        self.alive = False
        self.finished.wait(timeout)

    # ---- aggregated state for the UI ----------------------------------------
    def _primary(self):
        ws = [w for w in list(self.workers.values()) if w.error is None]
        if not ws:
            return None
        return max(ws, key=lambda w: w.last_sound)

    @property
    def gain(self):
        w = self._primary()
        return w.gain if w else 0.0

    @property
    def state(self):
        w = self._primary()
        return w.state if w else "silence"

    @property
    def history(self):
        w = self._primary()
        return w.history if w else self._idle

    @property
    def enabled(self):
        return any(w.enabled for w in list(self.workers.values()))

    @property
    def headroom_short(self):
        return any(w.enabled and w.headroom_short for w in list(self.workers.values()))

    @property
    def call_app(self):
        return next((w.call_app for w in list(self.workers.values()) if w.call_app), "")

    @property
    def device(self):
        return " + ".join(w.device_name for w in list(self.workers.values()))

    @property
    def device_ids(self):
        return tuple(self.workers)

    @property
    def apps(self):
        out = {}
        for w in list(self.workers.values()):
            for a in w.apps:
                old = out.get(a[0])
                # (name, excluded, is_call, playing, path): playing on any device
                out[a[0]] = a if old is None else old[:3] + (old[3] or a[3],) + old[4:]
        return tuple(sorted(out.values(), key=lambda a: a[0].lower()))

    @property
    def device_missing(self):
        return bool(self.missing)

    @property
    def worker_error(self):
        ws = list(self.workers.values())
        if self.error:
            return self.error
        if ws and all(w.error for w in ws):
            return ws[0].error
        return None

    @property
    def active_s(self):
        return self._done_s + sum(w.active_s for w in list(self.workers.values()))

    @property
    def peaks(self):
        return self._done_peaks + sum(w.peaks for w in list(self.workers.values()))

    # ---- coordinator --------------------------------------------------------
    def _scan_other_apps(self):
        """Apps with sound on the other devices + known media apps that are
        running: so the settings list is complete, not only what plays now."""
        from pycaw.pycaw import AudioUtilities, IAudioSessionControl2
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioSessionManager2
        out = {}
        enum = AudioUtilities.GetDeviceEnumerator()
        for dev_id, _n, _f in self.devices:
            if dev_id in self.workers:
                continue
            try:
                mgr = enum.GetDevice(dev_id).Activate(
                    IAudioSessionManager2._iid_, CLSCTX_ALL, None
                ).QueryInterface(IAudioSessionManager2)
                en = mgr.GetSessionEnumerator()
                for i in range(en.GetCount()):
                    ctl = en.GetSession(i).QueryInterface(IAudioSessionControl2)
                    state = ctl.GetState()
                    pid = ctl.GetProcessId()
                    if state == 2 or pid in (0, os.getpid()):
                        continue
                    name, path = process_info(pid)
                    old = out.get(name)
                    out[name] = (name, path, (state == 1) or (old is not None and old[2]))
            except Exception:
                continue
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                if not self.alive:
                    break
                name = p.info.get("name") or ""
                if name.lower() in MEDIA_APPS and name not in out:
                    try:
                        exe = p.exe()           # only for the few that match: fast
                    except Exception:
                        exe = ""
                    out[name] = (name, exe, False)
        except Exception:
            pass
        self.other_apps = tuple(out.values())

    def _scan_devices(self):
        from pycaw.constants import DEVICE_STATE, EDataFlow
        from pycaw.pycaw import AudioUtilities
        found = []
        for d in AudioUtilities.GetAllDevices(EDataFlow.eRender.value,
                                              DEVICE_STATE.ACTIVE.value):
            try:
                ff = int(d.properties.get(FORM_FACTOR_KEY) or 0)
            except Exception:
                ff = 0
            found.append((d.id, d.FriendlyName or "?", ff))
        default = AudioUtilities.GetSpeakers()
        self.default_id = default.id if default is not None else None
        self.default_name = next((n for i, n, _f in found if i == self.default_id), "")
        self.devices = tuple(found)

    def _targets(self):
        connected = {i: n for i, n, _f in self.devices}
        if self.want_devices is None:
            self.missing = ()
            ids = [self.default_id] if self.default_id in connected else []
        else:
            ids = [i for i in self.want_devices if i in connected]
            self.missing = tuple(i for i in self.want_devices if i not in connected)
            if not ids and self.default_id in connected:
                ids = [self.default_id]   # nothing chosen is connected: use the default
        return {i: connected[i] for i in ids}

    def _stop_worker(self, dev_id):
        w = self.workers.pop(dev_id)
        w.stop()
        w.finished.wait(3.0)
        self._done_s += w.active_s
        self._done_peaks += w.peaks

    def _recover_idle(self, targets, recovered):
        """Devices left raised by a crash but not balanced now: put them back."""
        for dev_id, _name, _ff in self.devices:
            if dev_id in targets or dev_id in recovered:
                continue
            st = self.store.peek(dev_id)
            if st and (float(st.get("master_raised", 0.0)) > 0 or st.get("active")):
                try:
                    WinAudio(self.store, dev_id).recover()
                except Exception as exc:
                    log(f"recover idle device: {exc}")
            recovered.add(dev_id)

    def run(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        self.store = RestoreStore()
        last = 0.0
        recovered = set()
        try:
            while self.alive:
                force = False
                while True:
                    try:
                        cmd, val = self.cmds.get_nowait()
                    except queue.Empty:
                        break
                    if cmd == "devices":
                        self.want_devices = val
                        force = True
                now = time.monotonic()
                if force or now - last > 2.0:
                    last = now
                    try:
                        self._scan_devices()
                        targets = self._targets()
                        self.error = None if self.devices else "no output device"
                        for dev_id in [d for d in self.workers if d not in targets]:
                            self._stop_worker(dev_id)
                        for dev_id, name in targets.items():
                            if dev_id not in self.workers:
                                w = DeviceWorker(self, dev_id, name)
                                self.workers[dev_id] = w
                                w.start()
                        self._recover_idle(targets, recovered)
                        if now - self._last_apps > 5.0:
                            self._last_apps = now
                            self._scan_other_apps()
                    except Exception as exc:
                        self.error = f"{type(exc).__name__}: {exc}"
                        log(f"devices: {self.error}")
                time.sleep(0.1)
        finally:
            workers = list(self.workers.values())
            for w in workers:          # all devices restore their volumes in parallel
                w.stop()
            deadline = time.monotonic() + 5.0
            for w in workers:
                w.finished.wait(max(0.0, deadline - time.monotonic()))
            self.finished.set()
