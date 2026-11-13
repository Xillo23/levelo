"""The engine must talk to the Windows audio service only a few dozen times a
second, however many apps have an audio session open: every call can leave a
trace record in the kernel, and a call per session many times a second made
the kernel pool grow by gigabytes on a tester's PC (0.9.5).

Fake COM objects count the calls; nothing is played or changed on the PC.

Run:  python -m unittest discover -s tests
"""

import math
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from levelo import engine  # noqa: E402
from levelo.dsp import BLOCK_S, HEADROOM  # noqa: E402

CALLS = {"n": 0}


def call():
    CALLS["n"] += 1


class FakeSession:
    """IAudioSessionControl2 + ISimpleAudioVolume + IAudioMeterInformation."""

    def __init__(self, i, playing):
        self.pid, self.key, self.playing = 100 + i, f"session{i}", playing
        self.v = 1.0

    def QueryInterface(self, itf):
        call()
        return self

    def GetState(self):
        call()
        return 1 if self.playing else 0

    def GetSessionInstanceIdentifier(self):
        call()
        return self.key

    def GetProcessId(self):
        call()
        return self.pid

    def GetMasterVolume(self):
        call()
        return self.v

    def SetMasterVolume(self, v, ctx):
        call()
        self.v = v

    def GetPeakValue(self):
        call()
        return 0.1


class FakeEnum:
    def __init__(self, ctls):
        self.ctls = ctls

    def GetCount(self):
        call()
        return len(self.ctls)

    def GetSession(self, i):
        call()
        return self.ctls[i]


class FakeManager:
    def __init__(self, ctls):
        self.ctls = ctls

    def GetSessionEnumerator(self):
        call()
        return FakeEnum(self.ctls)


class FakeStore:
    def save(self, dev_id, state):
        pass


NAMES = {}


def fake_process_info(pid):
    return NAMES.get(pid, f"app{pid}.exe"), ""


engine.process_info = fake_process_info


def fake_win(n_sessions, playing, excluded_playing=False):
    ctls = [FakeSession(i, i < playing) for i in range(n_sessions)]
    if excluded_playing:
        NAMES[ctls[0].pid] = "discord.exe"
    else:
        NAMES.pop(ctls[0].pid, None)
    w = engine.WinAudio.__new__(engine.WinAudio)
    w.mgr = FakeManager(ctls)
    w._itf = (None, None, None)
    w.store, w.dev_id = FakeStore(), "dev"
    w.restore = engine.new_device_state()
    w.sessions, w._ignored, w._count, w._last_full = {}, set(), -1, 0.0
    w.written, w.dirty, w.session_db, w.active = {}, False, 0.0, False
    w.my_pid, w.call_protect, w.rules = -1, True, {}
    w.post_volume, w.lo, w.hi = True, -96.0, 0.0
    w.master_db = lambda: (call(), 0.0)[1]
    w.set_master_db = lambda db: (call(), db)[1]
    return w


def run_movie(win, seconds):
    """The worker's loop without the capture: the gain moves all the time."""
    CALLS["n"] = 0
    talk_until = 0.0
    last_scan = last_write = 0.0
    ticks = int(seconds / BLOCK_S)
    t0 = time.monotonic()
    for tick in range(ticks):
        now = t0 + tick * BLOCK_S
        if now - last_scan >= engine.POLL_S:
            last_scan = now
            win.poll(now)
        if tick % engine.PEAKS_EVERY == 0 and win.excluded_playing():
            ex, _n, lev = win.peaks()
            if ex > engine.TALK_PEAK and ex > engine.TALK_RATIO * lev:
                talk_until = now + engine.TALK_HOLD_S
        if tick % engine.MASTER_EVERY == 0:
            win.master_db()
        # a busy soundtrack: the gain always moving as fast as the leveler
        # lets it (~20 dB/s), with a bang every 1.5 s
        gain = 6.0 * math.sin(tick * BLOCK_S * 2 * math.pi * 0.5)
        if tick % 150 < 30:
            gain -= 15.0
        target = gain - HEADROOM
        if engine.should_write(target - win.session_db, now - last_write):
            last_write = now
            win.apply_session_db(target)
    return CALLS["n"] / seconds, talk_until


class EngineCalls(unittest.TestCase):
    def test_few_calls_with_many_sessions(self):
        # a game + a browser + launchers: 30 sessions, 2 of them playing
        win = fake_win(30, playing=2)
        win.activate()
        rate, _ = run_movie(win, 30.0)
        # 0.9.5 made ~7000 calls/s here (every session, every 10 ms block)
        self.assertLess(rate, 150, f"{rate:.0f} calls/s")
        # the playing apps follow the gain closely
        for s in win.sessions.values():
            if s.playing:
                db = 40 * math.log10(s.sav.v)
                self.assertAlmostEqual(db, win.session_db, delta=0.3)

    def test_rate_does_not_grow_with_idle_sessions(self):
        few = fake_win(4, playing=2)
        many = fake_win(60, playing=2)
        few.activate()
        many.activate()
        r_few, _ = run_movie(few, 20.0)
        r_many, _ = run_movie(many, 20.0)
        # idle sessions cost about one call each per second, not one per block
        self.assertLess(r_many - r_few, 2.5 * (60 - 4))

    def test_app_that_starts_playing_gets_the_volume(self):
        win = fake_win(10, playing=1)
        win.activate()
        run_movie(win, 10.0)
        idle = next(s for s in win.sessions.values() if not s.playing)
        idle.ctl.playing = True                  # a browser tab starts a video
        win.poll(time.monotonic() + 0.5)         # the next poll (<= 1 s)
        self.assertAlmostEqual(40 * math.log10(idle.sav.v), win.session_db, places=3)

    def test_meters_only_during_a_call(self):
        win = fake_win(20, playing=3)
        win.activate()
        reads = {"n": 0}
        orig = win.peaks

        def counted():
            reads["n"] += 1
            return orig()
        win.peaks = counted
        run_movie(win, 5.0)
        self.assertEqual(reads["n"], 0)
        call_win = fake_win(20, playing=3, excluded_playing=True)
        call_win.activate()
        _, talk_until = run_movie(call_win, 5.0)
        self.assertGreater(talk_until, 0.0)       # the call is still noticed

    def test_learns_the_usual_voice_level(self):
        eng = engine.Engine(True, "normal")
        self.assertEqual(eng.target(), -24.0)
        for _ in range(int(120 / 0.01)):          # 2 minutes of loud videos
            eng.learn(-14.0, 0.01)
        self.assertGreater(eng.target(), -20.5)   # most of the way to the top (-18)
        self.assertLessEqual(eng.target(), -18.0)
        for _ in range(int(120 / 0.01)):          # then 2 minutes of a quiet film
            eng.learn(-32.0, 0.01)
        self.assertLess(eng.target(), -21.0)      # moves, but not all the way
        self.assertGreater(eng.target(), -30.0)
        old = engine.Engine(True, "normal", voice_ref=-27.0, voice_ref_s=20 * 3600)
        for _ in range(int(60 / 0.01)):           # after hours: one minute barely moves it
            old.learn(-15.0, 0.01)
        self.assertLess(old.target(), -26.5)

    def test_should_write(self):
        self.assertTrue(engine.should_write(-3.0, 0.0))     # a bang: at once
        self.assertFalse(engine.should_write(0.5, 0.02))    # too soon
        self.assertTrue(engine.should_write(0.5, 0.06))
        self.assertFalse(engine.should_write(0.1, 0.06))    # too small...
        self.assertTrue(engine.should_write(0.1, 0.6))      # ...until it settles
        self.assertFalse(engine.should_write(0.0, 5.0))


if __name__ == "__main__":
    unittest.main()
