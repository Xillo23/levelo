<div align="center">

<img src="assets/icon.png" width="96" alt="Levelo logo">

# Levelo

**Stop riding the volume during movies.**
Explosions get tamed, whispers get lifted, automatically, for every app on your Windows PC.

[Download](https://github.com/Xillo23/levelo/releases/latest) · [Italiano](README.it.md) · [How it works](#how-it-works) · [FAQ](#faq)


[![Support Levelo on Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Levelo-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/xillo23)

<img src="docs/screenshot.png" width="372" alt="Levelo panel">

</div>

> [!NOTE]
> **Levelo is in public beta.** It works and is used every day, but it hasn't been tested on
> many PCs yet. If something doesn't work on yours, please [open an issue](https://github.com/Xillo23/levelo/issues).
> Version 1.0 is planned for February.

---

You know the routine: you turn the volume up to understand the dialogue, then the
next action scene blows your ears off. **Levelo** fixes that. It sits in the
notification area and keeps everything at a comfortable level in real time:

- 💥 **Loud parts are lowered in ~10–30 ms**: explosions, gunshots, sudden music.
- 🗣️ **Quiet parts are raised smoothly**: whispers and soft dialogue become intelligible.
- 🎚️ **You keep using the normal Windows volume** (keys, taskbar, speakers). There's no extra slider.
- 🌙 **Three intensities**: *Light*, *Normal* and *Night* (everything at almost the same level, great for late-night watching).
- 🪶 **Lightweight and private**: no account, no tracking, no drivers to install. Audio never leaves your PC.
- 🌍 **11 languages**: English, Italiano, Español, Deutsch, Français, Português, Русский, Polski, Türkçe, 简体中文, 日本語.

Works with any app that plays sound: browsers (Netflix, YouTube, Prime Video…), VLC,
MPC-HC, Plex, games, and more.

## Download and install

1. Download **`Levelo-x.y.z-Setup.exe`** from the [latest release](https://github.com/Xillo23/levelo/releases/latest).
2. Run it. No administrator rights are needed: it installs for your user only.
3. Done. Levelo starts in the notification area, next to the clock.

A **portable zip** is also available: extract it anywhere and run `Levelo.exe`.

Requirements: Windows 10 or 11 (64-bit).

## How to use it

There's nothing to do: set the volume as you always do. Click the tray icon to open the panel:

| Control | What it does |
|---|---|
| Switch | Turns balancing on or off. Volumes are restored instantly. |
| Live graph | What it's doing right now: ▲ raising, ▼ lowering. |
| Intensity | **Light**: keeps the movie's dynamics · **Normal**: recommended · **Night**: maximum leveling |
| Start with Windows | Starts hidden in the notification area at login. |

Right-click the icon for the menu: intensity, language, start with Windows, quit.

> **Tip:** to raise quiet voices, Levelo needs a bit of room. If your Windows
> volume is at 100%, turn it down a little and turn up your speakers/TV/monitor instead.
> The panel tells you when this happens.

## How it works

1. **Listens** to the PC's audio output (WASAPI loopback) in 10 ms blocks.
2. **Measures perceived loudness** with K-weighting (the ITU-R BS.1770 broadcast standard),
   ignoring pauses between words so it doesn't pump up background noise.
3. **Computes a correction** toward a reference level. It cuts fast when sound gets loud,
   recovers in about 1 s after a loud scene, and raises quiet parts slowly. It never boosts
   silence.
4. **Applies it to the per-app volumes** (the Windows volume mixer), not to the master
   volume, so your volume slider stays yours. To be able to *raise* audio it keeps apps
   10 dB lower and raises the master by the same amount, so overall loudness doesn't change.
5. **Restores everything** when you pause, quit, uninstall, switch audio device, or even
   after a crash (on the next start).

Because the captured audio is already attenuated by the volumes the app sets, the
measurement subtracts them to see the *original* signal. That avoids the feedback loop
that makes simpler auto-volume tools oscillate.

**Limitation:** the very first 10–30 ms of a sudden hit get through before the cut, a
short "tick" rather than a full blast. Removing that would need a virtual audio driver,
which is on the roadmap as an optional mode.

## FAQ

**Windows SmartScreen says "Windows protected your PC".**
New apps without a paid code-signing certificate show this warning. Click *More info* →
*Run anyway*. The full source code is in this repository and every release is built
automatically by GitHub Actions from it.

**My antivirus flags it.**
Apps packaged with PyInstaller sometimes trigger false positives. Please report it to your
antivirus vendor, and open an issue so we can track it.

**Does it work with headphones / Bluetooth / HDMI?**
Yes. It always follows the current default output device and switches with it.

**Can I exclude an app?**
Not yet. It's on the roadmap.

**Does it connect to the internet?**
Only to check for updates: twice a day it downloads two public files from this GitHub
repository (the latest release info and `links.json`). Nothing about you or your PC is sent.

**Does it slow down my PC?**
No. It uses about 1% of one CPU core.

## Build from source

```powershell
git clone https://github.com/Xillo23/levelo.git
cd levelo
pip install -r requirements.txt -r requirements-dev.txt
python -m unittest discover -s tests      # tests
$env:PYTHONPATH="src"; python -m levelo   # run
python build.py                           # dist/ : installer + portable zip
```

The installer requires [Inno Setup 6+](https://jrsoftware.org/isdl.php).

Project layout:

```
src/levelo/   dsp.py (leveling algorithm) · engine.py (Windows audio) · ui.py (panel + tray)
                    i18n.py (translations) · winsys.py (autostart, single instance, theme)
packaging/          Inno Setup script + PyInstaller entry point
tests/              simulated movie soundtrack through the leveler
```

## Contributing

- 🌍 **Translations**: add a block to `src/levelo/i18n.py` and open a pull request.
  A test checks that every language has all the strings.
- 🐞 **Bugs and ideas**: [open an issue](https://github.com/Xillo23/levelo/issues).

## Support the project

Levelo is free and open source, with no ads, no tracking and no locked features.
If it saved your ears (or your neighbours' sleep), consider supporting its development. ❤️

**[☕ Buy me a coffee on Ko-fi](https://ko-fi.com/xillo23)**

No nagging: after many hours of balanced audio, the app shows a small card in its panel
**at most three times**, and stays quiet for a year once you click "donate" or
"I already donated". The usage counters it
shows (hours balanced, loud moments tamed) are stored only on your PC.

## License

[GPL-3.0](LICENSE). You may use, study, share and modify it. Derivative works must stay open source.
