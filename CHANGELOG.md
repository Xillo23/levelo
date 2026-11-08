# Changelog

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
