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

from levelo.dsp import BLOCK, HEADROOM, SR, Leveler, db_to_lin  # noqa: E402

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


def run(preset):
    """Returns the output level of each segment (dB), skipping the first 100 ms."""
    lev = Leveler(preset)
    x = np.concatenate([s for _, s in SEGMENTS])
    out = np.zeros_like(x)
    applied = -HEADROOM
    hist = [applied] * 3
    for i in range(0, len(x) - BLOCK + 1, BLOCK):
        played = x[i:i + BLOCK] * db_to_lin(hist[-2])     # volume applied with delay
        out[i:i + BLOCK] = played
        gain = lev.process(played * db_to_lin(-hist[-2]))  # compensated measurement
        hist.append(gain - HEADROOM)
        del hist[0]
    res, pos = {}, 0
    for name, s in SEGMENTS:
        res[name] = level(out[pos + 4800:pos + len(s)]) + HEADROOM
        pos += len(s)
    return res


class LevelerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = {p: run(p) for p in ("light", "normal", "night")}

    def test_whisper_is_raised(self):
        for p, r in self.r.items():
            self.assertGreater(r["whisper"], -40 + 3, p)

    def test_explosion_is_cut(self):
        for p, r in self.r.items():
            self.assertLess(r["explosion"], -10.7 - 9, p)

    def test_music_is_cut(self):
        for p, r in self.r.items():
            self.assertLess(r["loud_music"], -12 - 6, p)

    def test_voices_audible_after_explosion(self):
        r = self.r["normal"]
        self.assertLess(abs(r["dialogue_after"] - r["dialogue"]), 4.0)

    def test_range_is_compressed(self):
        r = self.r["normal"]
        spread_out = max(r.values()) - min(r.values())
        self.assertLess(spread_out, 30 * 0.6)

    def test_night_stronger_than_light(self):
        spread = {p: max(r.values()) - min(r.values()) for p, r in self.r.items()}
        self.assertLess(spread["night"], spread["light"])

    def test_silence_not_boosted(self):
        lev = Leveler("normal")
        noise = rng.standard_normal(SR * 5) * 10 ** (-80 / 20)
        for i in range(0, len(noise) - BLOCK + 1, BLOCK):
            g = lev.process(noise[i:i + BLOCK])
        self.assertLessEqual(g, 0.5)


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
