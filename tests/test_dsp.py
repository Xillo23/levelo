"""Simulates a movie soundtrack through the leveler (with the volume applied
"after" the capture, like on a real PC) and checks the result.

Run:  python -m unittest discover -s tests
"""

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from levelo.dsp import BLOCK, HEADROOM, PRESETS, SR, Leveler, db_to_lin  # noqa: E402

rng = np.random.default_rng(1)


def bandpass(x, lo, hi):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def at_level(x, db):
    return x / np.sqrt(np.mean(x ** 2) + 1e-20) * 10 ** (db / 20)


def speech(sec, db):
    n = int(sec * SR)
    t = np.arange(n) / SR
    env = np.clip(np.sin(2 * np.pi * 4.5 * t) * 0.5 + 0.5
                  + 0.3 * np.sin(2 * np.pi * 1.1 * t), 0, None) ** 2
    env *= np.sin(2 * np.pi * 0.25 * t) > -0.6           # pauses between sentences
    return at_level(bandpass(rng.standard_normal(n), 250, 3500) * env, db)


def boom(sec, db):
    n = int(sec * SR)
    t = np.arange(n) / SR
    return at_level(bandpass(rng.standard_normal(n), 30, 400) * np.exp(-t * 2.5), db + 6)


def music(sec, db):
    n = int(sec * SR)
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * f * t) for f in (110, 220, 330, 440, 660, 880))
    return at_level(x + 0.3 * bandpass(rng.standard_normal(n), 2000, 8000), db)


SEGMENTS = [
    ("dialogue", speech(8, -26)),
    ("whisper", speech(8, -40)),
    ("explosion", np.clip(boom(3, -14) + speech(3, -40), -1, 1)),
    ("dialogue_after", speech(6, -28)),
    ("loud_music", music(8, -12)),
]


def level(a):
    return 20 * math.log10(np.sqrt(np.mean(a ** 2)) + 1e-12)


def run(preset, gain_db=0.0, cap=HEADROOM, segments=None):
    """Returns the output level of each segment (dB), skipping the first 100 ms.
    The volume is applied one block late and the measurement is compensated,
    like on a real PC. gain_db = how loud the source is (player volume...)."""
    segments = segments or SEGMENTS
    lev = Leveler(preset)
    x = np.concatenate([s for _, s in segments]) * db_to_lin(gain_db)
    out = np.zeros_like(x)
    hist = [-cap] * 3
    for i in range(0, len(x) - BLOCK + 1, BLOCK):
        played = x[i:i + BLOCK] * db_to_lin(hist[-2])     # volume applied with delay
        out[i:i + BLOCK] = played
        gain = lev.process(played * db_to_lin(-hist[-2]), boost_cap=cap)  # compensated
        hist.append(gain - cap)
        del hist[0]
    res, pos = {}, 0
    for name, s in segments:
        res[name] = level(out[pos + 4800:pos + len(s)]) + cap - gain_db
        pos += len(s)
    return res


class LevelerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = {p: run(p) for p in ("light", "normal", "night")}

    def test_whisper_is_raised(self):
        for p, r in self.r.items():
            self.assertGreater(r["whisper"], -40 + (4 if p == "light" else 7), p)

    def test_explosion_is_cut(self):
        for p, r in self.r.items():
            self.assertLess(r["explosion"], -10.7 - 9, p)
            # never much louder than the voices
            self.assertLess(r["explosion"], r["dialogue"] + PRESETS[p]["allow"] + 3, p)

    def test_music_is_cut(self):
        for p, r in self.r.items():
            self.assertLess(r["loud_music"], -12 - (3 if p == "light" else 4), p)
            self.assertLess(r["loud_music"], r["dialogue"] + PRESETS[p]["allow"] + 4.5, p)

    def test_voices_audible_after_explosion(self):
        for p, r in self.r.items():
            self.assertLess(abs(r["dialogue_after"] - r["dialogue"]), 3.0, p)

    def test_range_is_compressed(self):
        r = self.r["normal"]
        spread_out = max(r.values()) - min(r.values())
        self.assertLess(spread_out, 30 * 0.45)

    def test_night_stronger_than_light(self):
        spread = {p: max(r.values()) - min(r.values()) for p, r in self.r.items()}
        self.assertLess(spread["night"], spread["normal"])
        self.assertLess(spread["normal"], spread["light"])

    def test_same_result_at_any_source_level(self):
        """A quiet streaming service, a loud one, the player at 30%: the
        voices end up at the same level."""
        for p in ("normal", "night"):
            ref = self.r[p]
            for g in (-12.0, 6.0):
                r = run(p, gain_db=g)
                for seg in ("dialogue", "dialogue_after", "loud_music"):
                    # the first seconds are spent learning the film
                    self.assertAlmostEqual(r[seg] + g, ref[seg],
                                           delta=2.0 if seg == "dialogue" else 1.5,
                                           msg=f"{p} {g} {seg}")

    def test_no_headroom_never_louder(self):
        """Windows volume already at 100%: nothing can be raised, but the
        loud parts are still brought down to the voices."""
        r = run("normal", cap=0.0)
        for name, s in SEGMENTS:
            self.assertLess(r[name], level(s) + 0.6, name)
        self.assertLess(r["loud_music"], r["dialogue"] + PRESETS["normal"]["allow"] + 4.5)
        self.assertLess(r["explosion"], r["dialogue"] + PRESETS["normal"]["allow"] + 3)

    def test_silence_not_boosted(self):
        lev = Leveler("normal")
        noise = rng.standard_normal(SR * 5) * 10 ** (-80 / 20)
        for i in range(0, len(noise) - BLOCK + 1, BLOCK):
            g = lev.process(noise[i:i + BLOCK])
        self.assertLessEqual(g, 0.5)


def wide(x):
    """Stereo sound that is different in left and right (music, effects)."""
    d = int(0.011 * SR)
    return np.stack([x, np.concatenate([x[d:], x[:d]]) * 0.9], 1)


class StereoSurroundTest(unittest.TestCase):
    def feed(self, lev, x):
        g = 0.0
        talk = 0
        for i in range(0, len(x) - BLOCK + 1, BLOCK):
            g = lev.process(x[i:i + BLOCK])
            talk += lev.talking
        return g, talk * BLOCK / SR

    def test_voice_found_in_the_centre_of_a_stereo_mix(self):
        n = SR * 10
        mus = music(10, -30)
        v = speech(10, -30)
        x = wide(mus) + np.stack([v, v], 1)
        lev = Leveler("normal")
        g, talk = self.feed(lev, x)
        self.assertGreater(talk, 4.0)          # seconds recognised as speech
        lev2 = Leveler("normal")
        _, talk2 = self.feed(lev2, wide(music(10, -27)))
        self.assertLess(talk2, 1.0)            # music alone is not a voice
        self.assertEqual(len(x), n)

    def test_surround_centre_channel(self):
        """5.1: the voice is in the centre channel; same result as stereo."""
        v = speech(10, -30)
        mus = music(10, -34)
        st = wide(mus) + np.stack([v, v], 1) * 0.7071
        sur = np.zeros((len(v), 6))
        sur[:, 2] = v
        sur[:, 0], sur[:, 1] = wide(mus)[:, 0], wide(mus)[:, 1]
        g1, t1 = self.feed(Leveler("normal"), st)
        g2, t2 = self.feed(Leveler("normal"), sur)
        self.assertGreater(t2, 4.0)
        self.assertAlmostEqual(g1, g2, delta=2.5)


class CallFreezeTest(unittest.TestCase):
    def feed(self, lev, x, **kw):
        g = 0.0
        for i in range(0, len(x) - BLOCK + 1, BLOCK):
            g = lev.process(x[i:i + BLOCK], **kw)
        return g

    def test_call_voice_does_not_move_gain(self):
        lev = Leveler("normal")
        g0 = self.feed(lev, speech(4, -34))            # quiet movie: raised
        self.assertGreater(g0, 3)
        g1 = self.feed(lev, speech(3, -18), freeze=True)  # friend talking loudly
        self.assertAlmostEqual(g1, g0, delta=0.5)

    def test_big_hit_still_cut_during_call(self):
        lev = Leveler("normal")
        self.feed(lev, speech(4, -34))
        g = self.feed(lev, at_level(rng.standard_normal(SR // 2), 0), freeze=True)
        self.assertLess(g, -10)


class TranslationsTest(unittest.TestCase):
    def test_all_languages_complete(self):
        from levelo.i18n import LANGUAGES, STRINGS
        keys = set(STRINGS["en"])
        for code in LANGUAGES:
            self.assertEqual(set(STRINGS[code]), keys, code)

    def test_placeholders_match(self):
        """Every translation uses the same {placeholders} as English, and none is empty."""
        import re
        from levelo.i18n import LANGUAGES, STRINGS
        ph = re.compile(r"{(\w*)}")
        for key, en in STRINGS["en"].items():
            for code in LANGUAGES:
                text = STRINGS[code][key]
                self.assertTrue(text.strip(), f"{code}.{key} is empty")
                self.assertEqual(sorted(ph.findall(text)), sorted(ph.findall(en)),
                                 f"{code}.{key}")

    def test_every_used_key_exists(self):
        """Every t("key") in the code has a translation (no English-key fallback)."""
        import glob
        import re
        from levelo.i18n import STRINGS
        src = os.path.join(os.path.dirname(__file__), "..", "src", "levelo", "*.py")
        used = set()
        for path in glob.glob(src):
            with open(path, encoding="utf-8") as f:
                used |= set(re.findall(r'\bt\("([a-z_]+)"', f.read()))
        self.assertTrue(used)
        self.assertEqual(sorted(used - set(STRINGS["en"])), [])


if __name__ == "__main__":
    unittest.main()
