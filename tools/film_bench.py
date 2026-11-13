"""Film benchmark: how much does Levelo help on a real movie soundtrack?

Uses the open movie "Tears of Steel" (c) Blender Foundation, CC-BY 3.0,
https://mango.blender.org. Its 5.1 soundtrack keeps the dialogue in the
centre channel, so the dialogue and the music/effects can be measured apart.

From it, a "blockbuster" stereo mix is built (calm dialogue ~-27 LUFS,
battle scenes with voices under loud music, action peaks ~-9 LUFS: the
"turn it up for the voices, down for the explosions" kind of film) and played
through the leveler exactly like the app does (volume applied one 10 ms block
later, measurement compensated, write throttling of the engine). The same
film is also played 12 dB quieter and 8 dB louder, like a quiet streaming
service or the player volume at 30%.

Needs ffmpeg in PATH and ~400 MB of temporary disk space. Downloads ~41 MB.

    python tools/film_bench.py [--presets light,normal,night]

Printed per run (dB, relative to the calm dialogue as you hear it):
  dialogue  level of calm dialogue at the output (should not depend on the source)
  action98  98th percentile of loudness outside dialogue (how loud the action gets)
  busy      dialogue in busy scenes (voices over loud music/effects)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from levelo.dsp import BLOCK, HEADROOM, SR, Leveler  # noqa: E402
from levelo.engine import should_write  # noqa: E402

BASE = "https://download.blender.org/demo/movies/ToS/"
AC3 = "Surround-TOS_DVDSURROUND-Dolby%205.1.ac3"
SRT = "subtitles/TOS-en.srt"


def fetch(name, folder):
    path = os.path.join(folder, os.path.basename(name).replace("%20", "_"))
    if not os.path.exists(path):
        print("downloading", name, "...", flush=True)
        req = urllib.request.Request(BASE + name, headers={"User-Agent": "levelo-bench"})
        with urllib.request.urlopen(req) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)
    return path


def subtitles(path):
    t = lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    txt = open(path, encoding="utf-8", errors="ignore").read()
    return [(t(*g[:4]), t(*g[4:])) for g in
            re.findall(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", txt)]


def kweight(n):
    f = np.maximum(np.fft.rfftfreq(n, 1 / SR), 1.0)
    return (f ** 4 / (f ** 4 + 38.0 ** 4)) * (1 + (10 ** 0.4 - 1) * f ** 2 / (f ** 2 + 1500.0 ** 2))


def momentary(y):
    """K-weighted loudness of 400 ms windows every 100 ms (average of channels)."""
    W, H = int(0.4 * SR), int(0.1 * SR)
    w = kweight(W)[:, None]
    out = [float((np.abs(np.fft.rfft(y[s:s + W], axis=0)) ** 2 * w).sum()) * 2 / (W * W * y.shape[1])
           for s in range(0, len(y) - W, H)]
    return -0.691 + 10 * np.log10(np.array(out) + 1e-12)


def build_mix(ac3, subs, folder):
    raw = os.path.join(folder, "tos6.f32")
    if not os.path.exists(raw):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", ac3, "-f", "f32le", "-ar", str(SR),
                        "-ac", "6", raw], check=True)
    x = np.fromfile(raw, np.float32).reshape(-1, 6).astype(np.float64)
    C, LFE = x[:, 2], x[:, 3]
    dlg = np.stack([C, C], 1) * 0.7071
    me = np.stack([x[:, 0] + 0.7071 * x[:, 4] + 0.5 * LFE,
                   x[:, 1] + 0.7071 * x[:, 5] + 0.5 * LFE], 1)
    n = len(C)
    duck = np.zeros(n)
    for a, b in subs:                      # music ducked under calm dialogue...
        if a < 330:                        # ...but not in the battle (busy scenes)
            duck[int((a - 0.5) * SR):int((b + 0.5) * SR)] = 1
    k = SR // 2
    cs = np.concatenate([[0], np.cumsum(duck)])
    i = np.clip(np.arange(n) - k // 2, 0, n)
    j = np.clip(np.arange(n) + k // 2, 0, n)
    me *= (10 ** (-9 * (cs[j] - cs[i]) / k / 20))[:, None]
    Md, Mm = momentary(dlg), momentary(me)
    t = np.arange(len(Md)) * 0.1 + 0.2
    act = np.zeros(len(Md), bool)
    for a, b in subs:
        act |= (t >= a) & (t < b)
    act &= Md > -60
    dlg *= 10 ** ((-27 - np.median(Md[act])) / 20)
    me *= 10 ** ((-9 - np.percentile(Mm[Mm > -60], 99)) / 20)
    return dlg.astype(np.float32), me.astype(np.float32)


def play(x, preset):
    """Gains (dB per block) the app would apply, at the time the sound is heard."""
    if preset == "off":
        return np.zeros(len(x) // BLOCK)
    lev = Leveler(preset)
    n = len(x) // BLOCK
    g = np.zeros(n)
    applied, last = None, -1e9
    for i in range(n):
        gain = lev.process(x[i * BLOCK:(i + 1) * BLOCK], boost_cap=HEADROOM)
        now = i * BLOCK / SR
        if applied is None or should_write(gain - applied, now - last):
            applied, last = gain, now
        g[i] = applied
    return np.concatenate([[g[0]], g[:-1]])


def metrics(g, Md, Mm, subs):
    gl = 10 * np.log10(np.convolve(10 ** (g / 10), np.ones(40) / 40, "valid")[::10] + 1e-12)
    k = min(len(gl), len(Md))
    Md, Mm, gl = Md[:k], Mm[:k], gl[:k]
    t = np.arange(k) * 0.1 + 0.2
    dl = np.zeros(k, bool)
    for a, b in subs:
        dl |= (t >= a) & (t < b)
    act = dl & (Md > np.median(Md[dl]) - 18)
    mix = 10 * np.log10(10 ** (Md / 10) + 10 ** (Mm / 10))
    calm = act & (Mm < Md - 6)
    busy = act & (Mm > Md - 4)
    dc = np.median(Md[calm] + gl[calm])
    out = (mix + gl)[(~dl) & (mix > -70)]
    return dc, np.percentile(out, 98) - dc, np.median(Md[busy] + gl[busy]) - dc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", default="off,light,normal,night")
    ap.add_argument("--keep", default=os.path.join(tempfile.gettempdir(), "levelo_bench"))
    a = ap.parse_args()
    os.makedirs(a.keep, exist_ok=True)
    subs = subtitles(fetch(SRT, a.keep))
    dlg, me = build_mix(fetch(AC3, a.keep), subs, a.keep)
    Md0, Mm0 = momentary(dlg), momentary(me)
    print(f"{'preset':8s} {'source':>7s} {'dialogue':>9s} {'action98':>9s} {'busy':>6s}")
    for p in a.presets.split(","):
        for src_db in (0.0, -12.0, 8.0):
            k = np.float32(10 ** (src_db / 20))
            g = play((dlg + me) * k, p)
            dc, loud, busy = metrics(g, Md0 + src_db, Mm0 + src_db, subs)
            print(f"{p:8s} {src_db:+6.0f}  {dc:8.1f}  {loud:+8.1f}  {busy:+5.1f}", flush=True)


if __name__ == "__main__":
    main()
