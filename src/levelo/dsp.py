"""Loudness measurement and gain computation (pure numpy, no Windows APIs)."""

import math

import numpy as np

SR = 48000
BLOCK = 480                       # 10 ms
BLOCK_S = BLOCK / SR
FFT_N = 2048

SILENCE_DB = -58.0                # below this: silence, never boost
REF_DB = -24.0                    # "normal dialogue" level (K-weighted dB, pre-volume)
HEADROOM = 10.0                   # dB reserved to be able to raise quiet parts

# Intensity presets. ratio = how strongly the level is pulled to REF_DB.
PRESETS = {
    "light":  dict(max_boost=6.0,  max_cut=15.0, ratio=0.55, peak_margin=14.0),
    "normal": dict(max_boost=10.0, max_cut=24.0, ratio=0.75, peak_margin=10.0),
    "night":  dict(max_boost=10.0, max_cut=32.0, ratio=0.92, peak_margin=8.0),
}
DEFAULT_PRESET = "normal"


def db_to_lin(db):
    return 10.0 ** (db / 20.0)


def pow_to_db(p):
    return 10.0 * math.log10(p + 1e-20)


def tc(seconds):
    """Per-block smoothing coefficient for a time constant in seconds."""
    return 1.0 - math.exp(-BLOCK_S / max(seconds, 1e-4))


class LoudnessMeter:
    """Approximate K-weighting (ITU-R BS.1770) in the frequency domain."""

    def __init__(self):
        freqs = np.fft.rfftfreq(FFT_N, 1.0 / SR)
        f = np.maximum(freqs, 1.0)
        hp = f ** 4 / (f ** 4 + 60.0 ** 4)
        shelf = 1.0 + (10 ** 0.4 - 1.0) * f ** 2 / (f ** 2 + 1700.0 ** 2)
        self.weight = hp * shelf
        self.window = np.hanning(FFT_N)
        self.norm = 1.0 / (np.sum(self.window ** 2) * FFT_N / 2.0)
        self.buf = np.zeros(FFT_N)
        self.voice = (freqs >= 250) & (freqs <= 3500)

    def push(self, mono):
        """Returns (weighted power of last 43 ms, power of block, voice-band share)."""
        n = len(mono)
        self.buf = np.roll(self.buf, -n)
        self.buf[-n:] = mono
        spec = np.abs(np.fft.rfft(self.buf * self.window)) ** 2
        wspec = spec * self.weight
        tot = float(wspec.sum())
        p_win = tot * self.norm
        p_blk = float(np.mean(mono * mono))
        voice = float(wspec[self.voice].sum()) / (tot + 1e-20)
        return p_win, p_blk, voice


class Leveler:
    """Computes the gain (dB) to apply, from the ORIGINAL (un-attenuated) signal."""

    def __init__(self, preset=DEFAULT_PRESET):
        self.meter = LoudnessMeter()
        self.set_preset(preset)
        self.reset()

    def set_preset(self, name):
        if name not in PRESETS:
            name = DEFAULT_PRESET
        self.preset_name = name
        self.p = PRESETS[name]

    def reset(self):
        self.short_p = None
        self.long_p = None
        self.gain = 0.0
        self.hold = 0.0
        self.gap = 0.0
        self.voice = 0.0
        self.in_db = -100.0
        self.state = "silence"   # silence | loud | quiet | ok

    def process(self, mono, boost_cap=HEADROOM, freeze=False):
        """freeze=True: someone in a voice call is talking. Their voice must not
        move the movie's volume, so the measurement and the gain stay as they
        are; only a really loud hit (12 dB above the usual peak limit) is cut."""
        p_win, p_blk, voice = self.meter.push(mono)
        if freeze and self.short_p is not None:
            fast_db = pow_to_db(max(p_win, p_blk * 0.7))
            peak_limit = (REF_DB + self.p["peak_margin"]) - fast_db
            if peak_limit + 12.0 < min(self.gain, 0.0):
                self.gain += (max(-self.p["max_cut"] - 6.0, peak_limit) - self.gain) * tc(0.004)
                self.hold = 0.35
                self.state = "loud"
            elif self.hold > 0:
                self.hold -= BLOCK_S
            return self.gain
        if self.short_p is None:
            self.short_p = p_win
            self.long_p = p_win
        self.long_p += (p_win - self.long_p) * tc(3.0)
        # Relative gate: pauses between words (>12 dB below) do not pull the
        # measurement down. After ~0.6 s it is a scene change, so follow it.
        if p_win > self.short_p * 0.063 or self.gap > 0.6:
            self.short_p += (p_win - self.short_p) * tc(0.4)
            if p_win > self.short_p * 0.063:
                self.gap = 0.0
        else:
            self.gap += BLOCK_S
        self.voice += (voice - self.voice) * tc(1.0)

        short_db = pow_to_db(self.short_p)
        fast_db = pow_to_db(max(p_win, p_blk * 0.7))
        long_db = pow_to_db(self.long_p)
        self.in_db = short_db
        P = self.p

        if long_db < SILENCE_DB and short_db < SILENCE_DB:
            self.gain += (min(self.gain, 0.0) - self.gain) * tc(3.0)
            self.state = "silence"
            return self.gain

        diff = REF_DB - short_db
        desired = diff * P["ratio"]
        if diff > 0:  # a bit more help when there is speech
            desired *= 0.85 + 0.3 * min(1.0, self.voice / 0.7)
        if short_db < SILENCE_DB + 6:
            desired = min(desired, self.gain)
        desired = max(-P["max_cut"], min(P["max_boost"], boost_cap, desired))

        # Peak protection: sudden hits are cut immediately
        peak_limit = (REF_DB + P["peak_margin"]) - fast_db
        peak_hit = peak_limit < desired and peak_limit < self.gain
        if peak_hit:
            desired = max(-P["max_cut"] - 6.0, peak_limit)

        if desired < self.gain:
            a = tc(0.004) if peak_hit else tc(0.05)
            self.gain += (desired - self.gain) * a
            self.hold = 0.35 if peak_hit else max(self.hold, 0.15)
        elif self.hold > 0:
            self.hold -= BLOCK_S
        elif self.gain < 0:
            # loud part over: recover in ~1 s so the next line is audible
            step = (desired - self.gain) * tc(0.7)
            self.gain += min(step, 20.0 * BLOCK_S)
        else:
            # raising quiet voices: slowly, no "breathing"
            step = (desired - self.gain) * tc(1.5)
            self.gain += min(step, 6.0 * BLOCK_S)

        if peak_hit or self.gain < -3:
            self.state = "loud"
        elif self.gain > 2:
            self.state = "quiet"
        else:
            self.state = "ok"
        return self.gain
