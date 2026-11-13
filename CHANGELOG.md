# Changelog

## 0.10.0 (beta)

**A new leveling engine, built and measured on real film soundtracks.**

- **Dialogue first.** Levelo now finds the voices (in films they sit in the centre of
  the mix, or in the centre channel of 5.1/7.1) with a small speech detector, and levels
  *the voices*: every line at the same comfortable level. Before, it levelled the
  overall loudness, so a scene with loud music and quiet speech was judged "loud" and
  the voices got even quieter.
- **Action never much louder than the voices.** Music, action and explosions are kept
  within a few dB of the dialogue (*Light* 7, *Normal* 4, *Night* 2 dB), relative to
  where the voices really end up. On a blockbuster-like test mix the action went from
  14.4 dB louder than the dialogue to 5.7 dB (*Normal*, was 7.9 with 0.9.8) and 4.4 dB
  (*Night*).
- **The same result with any source.** A quiet movie, a loud YouTube video, a streaming
  service that plays everything low, or the player volume at 30%: the voices end up at
  the same level (within 0.2 dB from +8 to -12 dB of source level in the test; 0.9.8
  differed by 4.4 dB). Also the silence threshold now follows the film, so very quiet
  sources are not mistaken for silence.
- **More room to raise quiet voices**: the Windows volume is raised by up to 15 dB
  (was 10) while the apps are lowered by the same amount, so nothing changes in
  loudness, but whispered scenes can be lifted further.
- **Windows volume at 100% no longer makes everything quieter.** Before, turning
  Levelo on with the volume at maximum lowered all audio by 10 dB. Now the voices keep
  their level and only the loud parts are lowered; the panel suggests turning Windows
  down a little for the full effect.
- **New apps are lowered at once.** Windows now tells Levelo the moment an app opens
  its audio: it is lowered in 30-60 ms instead of playing up to 1 s at the raised volume
  (measured).
- No more slight wobble of the volume on loud syllables; after an explosion the volume
  comes back as quickly with quiet and loud sources.
- 5.1 and 7.1 outputs: the centre channel is used to find the dialogue.
- `tools/film_bench.py`: reproducible benchmark on the open movie *Tears of Steel*
  (downloads the soundtrack and prints the numbers above).

## 0.9.8 (beta)

- **Fix: the panel and Settings no longer stop opening.** If one action of the window
  ran into an error, the loop that handles tray clicks stopped for good: the icon stayed
  in the notification area but clicking it, or "Settings" in its menu, did nothing until
  Levelo was restarted. Errors now only skip that one action and the loop keeps going.
- Errors of the window are written to `levelo.log` (in `%APPDATA%\Levelo`); if the
  window ever stops answering, Levelo writes where it got stuck there too. Please attach
  that file to bug reports.

## 0.9.7 (beta)

- **Settings: switches and buttons look right.** The theme draws every control on top
  of the window colour, so on the lighter cards each switch, button, option and the
  language box had a dark square (light theme: a grey one) around it. They are now
  drawn on the card colour, in both dark and light theme.
- No more focus frame staying around a switch or a button after clicking it (in
  Settings and in the panel), as in Windows 11, where it shows only with the keyboard.

## 0.9.6 (beta)

- **Important fix — please update.** On some PCs Windows keeps a trace record in kernel
  memory for each request an app makes to the audio service. Levelo 0.9.5 made hundreds
  to thousands of these requests per second (several for every app with sound open,
  every 10 ms), so with games and browsers open that hidden kernel memory could grow
  by gigabytes, slowing the PC and making shutdown hang. Task Manager didn't show it
  under any app. Levelo now makes 10–20 times fewer requests, and the number no longer
  grows with the number of open apps: only the apps that are playing follow the volume
  in real time, sound meters are read only during a call, and the audio sessions are
  re-read only when something changes.
- Uses about 500 MB less memory: numpy's math library no longer reserves memory for a
  thread per CPU core that Levelo never used.
- When an output device keeps failing (screen asleep, device being reset), Levelo waits
  longer and longer between retries (up to 30 s) instead of reopening it every second.

## 0.9.5 (beta)

- **Much lighter**: CPU use down from ~12% to ~3–4% of one core (well under 1% of the
  whole processor). The audio capture no
  longer polls Windows hundreds of times a second, devices are re-read only when
  something changes, and the running-apps scan happens only while Settings is open.
- **Settings window is now instant**: pages are built once and kept, so switching page
  takes a few milliseconds and keeps the scroll position; scrolling is smooth; the app
  list is added in small steps and its names are prepared in the background; closing
  and reopening the window is immediate.
- A device appearing or disappearing (HDMI screen turned off, Bluetooth reconnecting)
  no longer shows "Audio problem": Levelo keeps working and retries.

## 0.9.4 (beta)

- The "Windows volume was almost at maximum" note no longer stays in the panel: it is
  shown once as a notification, and explained in Settings → General while it applies.
- Fixed: after many hours of running, quitting Levelo could take too long, and a Levelo
  started right after could close by itself. Quitting now always takes a couple of
  seconds and restores all volumes first.
- Updates: if an old version doesn't close in time, the installer closes it anyway (the
  new version restores any volume left lowered on its first start).
- Faster scan of the running apps.

## 0.9.3 (beta)

- Settings window rebuilt for smoothness: pages are built once and then only updated,
  so nothing flickers, jumps or loses the scroll position any more.
- **App list**: every app that ever played sound on this PC (from Windows' own audio
  history), in two stable groups, *In use now* and *Other apps*, alphabetical, with real
  names and icons. Apps no longer appear and disappear while they play.
- Text wraps cleanly at any window size; the scrollbar appears only when needed and no
  longer shifts the content.
- Light/dark mode changes in Windows are applied immediately, without restarting.
- The panel opens next to the taskbar on the right monitor, also with the taskbar at the
  top, on the left or on a second screen.
- Xbox Party Chat is recognised as a voice-call app.
- Releases now include a SHA256 checksum file.

## 0.9.2 (beta)

- New **Settings window**, like the Windows 11 Settings app: General, Devices, Apps,
  Calls, About. Open it from the ⚙ gear in the panel or from the tray menu.
- **Several devices at once**: balance speakers and headphones (or any outputs)
  together. *Automatic* still follows the Windows default device.
- **Complete app list** with real names and icons: apps playing now, apps open on any
  device, common media and call apps that are running, and apps seen before. Search,
  "Add an app…" to exclude one that isn't listed yet.
- Updates keep everything: settings, statistics and your "Start with Windows" choice
  (the installer no longer changes it on updates).
- Settings files keep a backup copy, used automatically if a file gets damaged.
- All new texts translated in the 11 languages; a test now checks every translation.

## 0.9.1 (beta)

- New **Settings** page (gear icon in the panel, or "Settings" in the tray menu).
- **Voice calls are left alone**: Discord, Teams, Zoom, WhatsApp, Telegram and similar
  apps keep their volume, and the voice of whoever is talking no longer lowers the movie.
  Can be turned off in Settings.
- **Choose the output device**: Automatic (follows the Windows default, as before) or a
  specific device. If it's disconnected, Levelo uses the default and switches back when
  it returns.
- **Exclude any app** from balancing with a switch.
- The panel shows which device is being balanced.
- Volumes you change in the Windows volume mixer (or EarTrumpet) are now respected and
  restored as you set them.
- Fixed: after switching between speakers and headphones, apps on the previous device
  could stay lowered; a disconnected Bluetooth device could come back too loud.
- Fixed: an app closed while lowered could get an old volume back when reopened.
- The audio engine recovers by itself after an error; errors are written to
  `%APPDATA%\Levelo\levelo.log`. "Report a problem" and "Open log folder" links in Settings.

## 0.9.0 (beta)

First public beta.

- Real-time automatic leveling: fast cut of loud parts, smooth lift of quiet dialogue.
- Uses per-app volumes, so the Windows master volume stays under your control.
- Three intensities: Light, Normal, Night.
- Notification-area app with a Windows 11 style panel and live graph.
- Light and dark theme following Windows.
- 11 languages, detected automatically.
- Installer (per-user, no admin rights) and portable zip.
- Restores all volumes on pause, quit, uninstall, device change and after crashes.
- Optional, non-intrusive support card, shown at most 3 times after long use.
- Update notifications (checks the GitHub releases twice a day).
