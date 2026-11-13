"""Loudness measurement and gain computation (pure numpy, no Windows APIs).

Dialogue-first leveling (1.0):
- the voice is found where films put it: the centre of the stereo mix (what is
  the same in left and right), or the centre channel of a 5.1/7.1 stream;
- a small speech detector (fitted on film soundtracks with known dialogue)
  tells when someone is talking;
- the VOICE level is brought to the reference, whatever the film, the
  streaming service or the volume of the player: every source sounds the same;
- music, action and explosions may be only a few dB louder than the voices
  (relative to where the voices really end up), and sudden hits are cut at once.
"""

import math

import numpy as np

SR = 48000
BLOCK = 480                       # 10 ms
BLOCK_S = BLOCK / SR
FFT_N = 2048

SILENCE_DB = -58.0                # below this: silence, never boost (adapts, see _silence)
SILENCE_MIN_DB = -72.0            # ...down to this for very quiet sources
REF_DB = -24.0                    # where the voices go (K-weighted dB, pre-volume)
HEADROOM = 15.0                   # dB of master raise wanted, to be able to raise voices
HEADROOM_MIN = 6.0                # less than this available: tell the user once
DLG_CAL = -1.0                    # centre-voice estimate -> dialogue loudness
LEAK = 0.12                       # share of loud music/effects seen as "centre voice"
DLG_RISE = 10 ** (8.0 * BLOCK_S / 10)   # voice level rises at most 8 dB/s
WARMUP = 150                      # blocks (1.5 s) before trusting the speech detector
ME_SUB = 0.8                      # share of the voice taken out for the ceiling

# Intensity presets.
#   allow:     how much louder than the voices music/action may be (dB)
#   busy:      extra allowance while someone talks over loud sound
#   peak:      sudden hits are cut above voices + this (43 ms measurement)
#   ratio, amb_boost: how sound without voices is raised (gently)
#   dlg:       how much of the voice correction is applied
PRESETS = {
    "light":  dict(max_boost=14.0, max_cut=24.0, allow=7.0, busy=3.0, peak=13.0,
                   ratio=0.5, amb_boost=4.0, dlg=1.0),
    "normal": dict(max_boost=14.0, max_cut=30.0, allow=4.0, busy=3.0, peak=11.0,
                   ratio=0.7, amb_boost=6.0, dlg=1.0),
    "night":  dict(max_boost=16.0, max_cut=36.0, allow=2.0, busy=2.0, peak=10.0,
                   ratio=0.85, amb_boost=7.0, dlg=1.0),
}
DEFAULT_PRESET = "normal"


def db_to_lin(db):
    return 10.0 ** (db / 20.0)


def pow_to_db(p):
    return 10.0 * math.log10(p + 1e-20)


def tc(seconds):
    """Per-block smoothing coefficient for a time constant in seconds."""
    return 1.0 - math.exp(-BLOCK_S / max(seconds, 1e-4))


def _sig(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))


class LoudnessMeter:
    """K-weighting (ITU-R BS.1770, in the frequency domain) of the last 43 ms,
    plus the voice: the part of the voice band (200-4000 Hz) that is common to
    left and right, or the centre channel of a surround stream."""

    def __init__(self):
        freqs = np.fft.rfftfreq(FFT_N, 1.0 / SR)
        f = np.maximum(freqs, 1.0)
        hp = f ** 4 / (f ** 4 + 38.0 ** 4)
        shelf = 1.0 + (10 ** 0.4 - 1.0) * f ** 2 / (f ** 2 + 1500.0 ** 2)
        self.weight = hp * shelf
        self.window = np.hanning(FFT_N)
        self.norm = 1.0 / (np.sum(self.window ** 2) * FFT_N / 2.0)
        self.voice = (freqs >= 200) & (freqs <= 4000)
        self.vweight = self.weight[self.voice]
        self.reset()

    def reset(self):
        self.buf = np.zeros((FFT_N, 2))
        nv = int(self.voice.sum())
        self.cross = np.zeros(nv)
        self.mcur = np.full(nv, 1e9)       # per-band minimum, current 0.75 s
        self.mprev = np.full(nv, 1e9)      # and the previous 0.75 s
        self.mcount = 0
        self.channels = 2

    def push(self, block):
        """block: (n,) mono or (n, channels), FL FR FC LFE SL SR [BL BR].
        Returns (weighted power of last 43 ms, power of the block,
        centre voice-band power, the part of it that moves like a voice)."""
        a = np.asarray(block, dtype=np.float64)
        if a.ndim == 1:
            a = a[:, None]
        ch = a.shape[1]
        if ch >= 3:
            # centre channel = dialogue; the rest folded like a stereo downmix
            rest = a[:, 0] + a[:, 1]
            if ch >= 6:
                rest = rest + 0.7071 * (a[:, 4] + a[:, 5])
            if ch >= 8:
                rest = rest + 0.7071 * (a[:, 6] + a[:, 7])
            pair = np.stack([a[:, 2], rest * 0.5], 1)
        elif ch == 2:
            pair = a
        else:
            pair = np.repeat(a, 2, axis=1)
        if ch != self.channels:
            self.channels = ch
            self.cross[:] = 0.0
            self.mcur[:] = 1e9
            self.mprev[:] = 1e9
        n = len(pair)
        self.buf = np.roll(self.buf, -n, axis=0)
        self.buf[-n:] = pair
        spec = np.fft.rfft(self.buf * self.window[:, None], axis=0)
        p = spec.real ** 2 + spec.imag ** 2
        if ch >= 3:
            tot = float(((0.5 * p[:, 0] + 2.0 * p[:, 1]) * self.weight).sum()) * self.norm
            x = 0.5 * p[self.voice, 0]
        else:
            # average power of the two channels (= mono power for mono sound)
            tot = float(((p[:, 0] + p[:, 1]) * self.weight).sum()) * self.norm * 0.5
            L, R = spec[self.voice, 0], spec[self.voice, 1]
            x = L.real * R.real + L.imag * R.imag      # Re(L R*): what is common
        self.cross += (x - self.cross) * 0.12          # ~80 ms
        c = np.maximum(self.cross, 0.0)
        cen = float((c * self.vweight).sum()) * self.norm
        # Music and effects in the centre are steady in each band; a voice
        # moves from band to band and stops between syllables. The lowest
        # level of each band over the last ~1-1.5 s is "not voice".
        np.minimum(self.mcur, c, out=self.mcur)
        self.mcount += 1
        if self.mcount >= 75:
            self.mcount = 0
            self.mprev = self.mcur
            self.mcur = c.copy()
        floor = np.minimum(self.mcur, self.mprev)
        cen_v = float((np.maximum(c - 2.0 * floor, 0.0) * self.vweight).sum()) * self.norm
        blk = pair[:, 0] * 0.7071 + pair[:, 1] if ch >= 3 else pair.mean(axis=1)
        p_blk = float(np.mean(blk * blk))
        return tot, p_blk, cen, cen_v


class Leveler:
    """Computes the gain (dB) to apply, from the ORIGINAL (un-attenuated) signal.
    gain 0 = as loud as the source; boost_cap = how much it may be raised."""

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
        self.meter.reset()
        self.short_p = None       # loudness ~0.4 s, gated (pauses ignored)
        self.long_p = None        # ~3 s, for silence
        self.me_s = None          # music/effects without the voice, ~0.4 s
        self.mom_p = None         # ... ~0.1 s: how a bang feels
        self.dlg_p = None         # voice level (power), held between lines
        self.dv_s = None          # voice level over the last ~0.6 s
        self.film_p = None        # voice level of the whole film (~10 s)
        self.dgap = 0.0           # speech much below the voice level for this long
        self.blocks = 0           # blocks since reset (warm-up)
        self.f_share = -10.0      # speech detector state (see _speech)
        self.f_vr = -2.0
        self.env_v = self.env_t = None
        self.mod_v = self.mod_t = 0.0
        self.speech = 0.0         # 0..1 how sure we are someone is talking
        self.since_speech = 99.0  # seconds since the last confident speech
        self.gain = 0.0
        self.hold = 0.0
        self.recovering = False   # coming back up after a cut: quickly
        self.gap = 0.0
        self.in_db = -100.0
        self.dlg_db = -100.0
        self.ref = REF_DB
        self.state = "silence"    # silence | loud | quiet | ok

    @property
    def talking(self):
        return self.speech > 0.5

    def _speech(self, tot, cen, cen_v):
        """Probability that someone is talking, from four clues (weights fitted
        on film soundtracks with known dialogue): how much of the sound is a
        moving voice-band centre, how much of the centre moves, and the
        syllable-rate fluctuations of the voice band vs the whole sound.
        Only level differences are used: it works at any volume."""
        t = max(pow_to_db(tot), -110.0)
        c = max(pow_to_db(cen), -110.0)
        v = max(pow_to_db(cen_v), -110.0)
        if self.env_v is None:
            self.env_v, self.env_t, self.f_share, self.f_vr = v, t, v - t, v - c
        self.f_share += (v - t - self.f_share) * 0.033      # ~0.3 s
        self.f_vr += (v - c - self.f_vr) * 0.033
        self.env_v += (v - self.env_v) * 0.064               # ~150 ms
        self.env_t += (t - self.env_t) * 0.064
        self.mod_v += ((v - self.env_v) ** 2 - self.mod_v) * 0.02   # ~0.5 s
        self.mod_t += ((t - self.env_t) ** 2 - self.mod_t) * 0.02
        z = (0.278 * self.f_share + 0.398 * self.f_vr + 0.987 * math.sqrt(self.mod_v)
             - 0.259 * math.sqrt(self.mod_t) - 1.616)
        return _sig(z)

    def _silence(self):
        """Silence threshold. A quiet film (or a low volume in the player)
        has its voices and ambience lower: the threshold follows the voice
        level, so nothing is mistaken for silence and noise is never boosted."""
        if self.dlg_p is None:
            return SILENCE_DB
        return max(SILENCE_MIN_DB, min(SILENCE_DB, pow_to_db(self.dlg_p) - 30.0))

    def process(self, block, boost_cap=HEADROOM, freeze=False):
        """block: mono or multichannel samples (original level).
        freeze=True: someone in a voice call is talking. Their voice must not
        move the movie's volume, so the measurement and the gain stay as they
        are; only a really loud hit is cut."""
        return self.step(*self.meter.push(block), boost_cap=boost_cap, freeze=freeze)

    def step(self, p_win, p_blk, cen, cen_v, boost_cap=HEADROOM, freeze=False):
        P = self.p
        boost_cap = max(0.0, boost_cap)
        # Where the voices will really end up: the reference, or lower when
        # they cannot be raised that much (very quiet film, Windows volume
        # already at 100%). Loud parts are limited relative to THAT, so they
        # are never much louder than the voices, whatever the source.
        ref = REF_DB
        if self.film_p is not None:
            ref = min(REF_DB, pow_to_db(self.film_p) + DLG_CAL + min(P["max_boost"], boost_cap))
        self.ref = ref
        ceiling = ref + P["allow"]
        fast_db = pow_to_db(max(p_win, p_blk * 0.7))
        peak_limit = (ref + P["peak"]) - fast_db

        if freeze and self.short_p is not None:
            if peak_limit + 12.0 < min(self.gain, 0.0):
                self.gain += (max(-P["max_cut"] - 6.0, peak_limit) - self.gain) * tc(0.004)
                self.hold = 0.35
                self.state = "loud"
            elif self.hold > 0:
                self.hold -= BLOCK_S
            return self.gain

        if self.short_p is None:
            self.short_p = self.long_p = p_win
        self.long_p += (p_win - self.long_p) * tc(3.0)
        # Relative gate: pauses between words (>12 dB below) do not pull the
        # measurement down. After ~0.6 s it is a scene change, so follow it.
        if p_win > self.short_p * 0.063 or self.gap > 0.6:
            self.short_p += (p_win - self.short_p) * tc(0.4)
            if p_win > self.short_p * 0.063:
                self.gap = 0.0
        else:
            self.gap += BLOCK_S

        sp = self._speech(p_win, cen, cen_v)
        if self.blocks < WARMUP:
            self.blocks += 1      # the band minimums need ~1.5 s to settle
            sp = 0.0
        self.speech += (sp - self.speech) * (tc(0.25) if sp > self.speech else tc(1.5))
        # voice level over the last ~0.6 s (syllables and the gaps between
        # them), minus the share that leaks in from loud music/effects
        dv = max(cen_v - LEAK * max(p_win - cen_v, 0.0), 0.1 * cen_v)
        self.dv_s = dv if self.dv_s is None else self.dv_s + (dv - self.dv_s) * tc(0.6)
        # music/effects alone (most of the voice taken out), for the ceiling:
        # the voice itself must not be squashed by it
        p_me = max(p_win - ME_SUB * dv, 0.15 * p_win)
        if self.me_s is None:
            self.me_s = self.mom_p = p_me
        self.me_s += (p_me - self.me_s) * tc(0.4)
        self.mom_p += (p_me - self.mom_p) * tc(0.1)
        sil = self._silence()
        if sp > 0.5 and pow_to_db(cen) > sil:
            self.since_speech = 0.0
            if self.dlg_p is None:
                self.dlg_p = self.film_p = self.dv_s
            elif self.dv_s > self.dlg_p * 0.1 or self.dgap > 1.0:
                # gate: pauses do not pull it down; but a voice that stays
                # much lower for 1 s is the new level (after a bang taken for
                # a voice, or a whispered scene)
                if self.dv_s > self.dlg_p * 0.1:
                    self.dgap = 0.0
                new = self.dlg_p + (self.dv_s - self.dlg_p) * tc(1.0)
                self.dlg_p = min(new, self.dlg_p * DLG_RISE)   # a bang is not a voice
                self.film_p += (self.dlg_p - self.film_p) * tc(10.0)
            else:
                self.dgap += BLOCK_S
        else:
            self.since_speech += BLOCK_S

        short_db = pow_to_db(self.short_p)
        long_db = pow_to_db(self.long_p)
        self.in_db = short_db

        if long_db < sil and short_db < sil:
            self.gain += (min(self.gain, 0.0) - self.gain) * tc(3.0)
            self.state = "silence"
            return self.gain

        # Without voices: the film keeps the correction its voices need (a
        # quiet film stays raised, a loud one lowered); quieter sound is
        # raised only a little more (ambience must not be blown up), louder
        # sound is handled by the ceiling below.
        base = 0.0
        if self.film_p is not None:
            base = (REF_DB - (pow_to_db(self.film_p) + DLG_CAL)) * P["dlg"]
        amb = base + max(0.0, min((REF_DB - base - short_db) * P["ratio"], P["amb_boost"]))
        if self.dlg_p is not None and self.since_speech < 12.0:
            self.dlg_db = pow_to_db(self.dlg_p) + DLG_CAL
            dlg = (REF_DB - self.dlg_db) * P["dlg"]
            # trust the voice level fully for 4 s after the last line, then
            # fade to the no-voice behaviour
            w = 1.0 if self.since_speech < 4.0 else max(0.0, 1.0 - (self.since_speech - 4.0) / 8.0)
            desired = w * dlg + (1.0 - w) * amb
        else:
            desired = amb
        # Ceiling: music/action only `allow` dB louder than the voices (a bit
        # more while someone talks over it, so the words are not pushed down)
        loud_db = max(pow_to_db(self.me_s), pow_to_db(self.mom_p) - 1.0)
        desired = min(desired, ceiling + P["busy"] * min(1.0, self.speech) - loud_db)
        if short_db < sil + 6:
            desired = min(desired, self.gain)
        desired = max(-P["max_cut"], min(P["max_boost"], boost_cap, desired))

        # Peak protection: sudden hits are cut immediately
        peak_hit = peak_limit < desired and peak_limit < self.gain
        if peak_hit:
            desired = max(-P["max_cut"] - 6.0, peak_limit)

        if desired < self.gain:
            a = tc(0.004) if peak_hit else tc(0.05)
            self.gain += (desired - self.gain) * a
            self.hold = 0.35 if peak_hit else max(self.hold, 0.15)
            self.recovering = True
        elif self.hold > 0:
            self.hold -= BLOCK_S
        elif self.recovering or self.since_speech < 4.0:
            # loud part over / dialogue scene: follow quickly so words are heard
            step = (desired - self.gain) * tc(0.6)
            self.gain += min(step, 20.0 * BLOCK_S)
            if desired - self.gain < 0.5:
                self.recovering = False
        else:
            # raising quiet sound without voices: slowly, no "breathing"
            step = (desired - self.gain) * tc(1.5)
            self.gain += min(step, 6.0 * BLOCK_S)

        if peak_hit or self.gain < -3:
            self.state = "loud"
        elif self.gain > 2:
            self.state = "quiet"
        else:
            self.state = "ok"
        return self.gain
