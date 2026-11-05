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
from .storage import STATE_FILE, load_json, save_json

SESSION_MIN_DB = -60.0


class WinAudio:
    """Master (endpoint) volume + per-app session volumes of the default device."""

    def __init__(self, restore):
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        self._AU = AudioUtilities
        dev = AudioUtilities.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            self.ep = dev.EndpointVolume
        else:
            itf = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self.ep = itf.QueryInterface(IAudioEndpointVolume)
        lo, hi, _ = self.ep.GetVolumeRange()
        self.lo, self.hi = max(lo, -96.0), hi
        try:
            hw = int(self.ep.QueryHardwareSupport())
        except Exception:
            hw = 0
        # Software volume (almost always): the loopback is already attenuated
        self.post_volume = not (hw & 1)
        self.restore = restore           # persisted in restore.json
        self.sessions = {}               # key -> (app name, ISimpleAudioVolume)
        self.session_db = 0.0
        self.active = False
        self.my_pid = os.getpid()

    # --- master -------------------------------------------------------------
    def master_db(self):
        return float(self.ep.GetMasterVolumeLevel())

    def set_master_db(self, db):
        db = max(self.lo, min(self.hi, db))
        self.ep.SetMasterVolumeLevel(float(db), None)
        return db

    # --- sessions -----------------------------------------------------------
    def _scan(self):
        found = {}
        for s in self._AU.GetAllSessions():
            try:
                pid = s.ProcessId
                if pid == self.my_pid:
                    continue
                name = s.Process.name() if s.Process else "System"
                key = getattr(s, "InstanceIdentifier", None) or f"{name}:{pid}"
                found[key] = (name, s.SimpleAudioVolume)
            except Exception:
                continue
        return found

    def _set_session(self, sav, name):
        # Windows app volume is scalar^2 (0.5 = -12 dB)
        orig = self.restore["apps"].get(name, 1.0)
        orig_db = 40.0 * math.log10(max(orig, 1e-4))
        db = max(SESSION_MIN_DB, min(0.0, orig_db + self.session_db))
        try:
            sav.SetMasterVolume(float(10.0 ** (db / 40.0)), None)
        except Exception:
            pass

    def refresh(self):
        """Handle new apps: remember their volume, apply the offset."""
        try:
            found = self._scan()
        except Exception:
            return
        changed = False
        for key, (name, sav) in found.items():
            if key in self.sessions:
                continue
            if self.active:
                if name not in self.restore["apps"]:
                    try:
                        self.restore["apps"][name] = float(sav.GetMasterVolume())
                    except Exception:
                        self.restore["apps"][name] = 1.0
                    changed = True
                self._set_session(sav, name)
            elif name in self.restore["apps"]:
                # left lowered by an abnormal exit: restore it
                try:
                    sav.SetMasterVolume(float(self.restore["apps"].pop(name)), None)
                    changed = True
                except Exception:
                    pass
        self.sessions = found
        if changed:
            save_json(STATE_FILE, self.restore)

    def apply_session_db(self, db):
        self.session_db = db
        for name, sav in self.sessions.values():
            self._set_session(sav, name)

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
        save_json(STATE_FILE, self.restore)
        # lower the apps first, then raise master: never a spike in between
        self.session_db = -HEADROOM
        self.refresh()
        if raise_db > 0:
            self.set_master_db(cur + raise_db)

    def deactivate(self):
        raised = float(self.restore.get("master_raised", 0.0))
        # lower master first, then raise the apps: never a spike in between
        if raised > 0:
            try:
                self.set_master_db(self.master_db() - raised)
            except Exception:
                pass
        try:
            found = self._scan()
        except Exception:
            found = self.sessions
        for name, sav in found.values():
            if name in self.restore["apps"]:
                try:
                    sav.SetMasterVolume(float(self.restore["apps"][name]), None)
                except Exception:
                    pass
        for name, _ in found.values():
            self.restore["apps"].pop(name, None)
        self.restore["master_raised"] = 0.0
        self.restore["active"] = False
        self.active = False
        self.session_db = 0.0
        self.sessions = found
        save_json(STATE_FILE, self.restore)

    def recover(self):
        """After an abnormal exit, put back what we can."""
        raised = float(self.restore.get("master_raised", 0.0))
        if raised > 0:
            try:
                self.set_master_db(self.master_db() - raised)
            except Exception:
                pass
            self.restore["master_raised"] = 0.0
        self.restore["active"] = False
        self.active = False
        self.refresh()
        save_json(STATE_FILE, self.restore)


class Engine(threading.Thread):
    def __init__(self, enabled, preset):
        super().__init__(daemon=True, name="AudioEngine")
        self.cmds = queue.Queue()
        self.leveler = Leveler(preset)
        self.want_enabled = enabled
        self.enabled = False
        self.alive = True
        self.finished = threading.Event()
        # state read by the UI
        self.gain = 0.0
        self.state = "silence"
        self.history = deque([0.0] * 300, maxlen=300)   # 15 s at 20 Hz
        self.device = ""
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

    def shutdown(self, timeout=2.5):
        self.alive = False
        self.finished.wait(timeout)

    def run(self):
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        restore = load_json(STATE_FILE, {"apps": {}, "master_raised": 0.0, "active": False})
        win = rec = rec_ctx = dev_id = None
        last_scan = last_dev = 0.0
        tick = 0
        try:
            while self.alive:
                if rec is None:
                    try:
                        spk = sc.default_speaker()
                        dev_id = spk.id
                        self.device = spk.name
                        win = WinAudio(restore)
                        win.recover()
                        mic = sc.get_microphone(id=spk.id, include_loopback=True)
                        rec_ctx = mic.recorder(samplerate=SR, blocksize=BLOCK)
                        rec = rec_ctx.__enter__()
                        self.leveler.reset()
                        self.enabled = False
                        self.error = None
                    except Exception as exc:
                        self.error = str(exc)
                        rec = None
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
                if self.want_enabled and not self.enabled:
                    win.activate()
                    self.enabled = True
                elif not self.want_enabled and self.enabled:
                    win.deactivate()
                    self.enabled = False

                try:
                    data = rec.record(numframes=BLOCK)
                except Exception:
                    self._close(win, rec_ctx)
                    rec = None
                    continue
                mono = np.asarray(data, dtype=np.float64)
                if mono.ndim > 1:
                    mono = mono.mean(axis=1)

                now = time.monotonic()
                if now - last_scan > 1.0:
                    last_scan = now
                    win.refresh()
                if now - last_dev > 2.0:
                    last_dev = now
                    try:
                        if sc.default_speaker().id != dev_id:
                            # headphones/speakers changed: restore and restart
                            self._close(win, rec_ctx)
                            rec = None
                            continue
                    except Exception:
                        pass

                # original signal = captured / (master volume * app volume)
                try:
                    master = win.master_db() if win.post_volume else 0.0
                except Exception:
                    master = 0.0
                comp_db = -(master + win.session_db)
                orig = mono * db_to_lin(min(comp_db, 90.0))

                gain = self.leveler.process(orig, boost_cap=HEADROOM)

                if self.enabled:
                    target = gain - HEADROOM
                    if abs(target - win.session_db) > 0.1:
                        win.apply_session_db(target)
                    self.headroom_short = (
                        float(restore.get("master_raised", 0.0)) < HEADROOM - 0.5)

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
        finally:
            self._close(win, rec_ctx)
            self.finished.set()

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
