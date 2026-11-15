# Changelog

## 3.14.0

skydrop now runs on macOS and Windows as well as Linux.

* **macOS**: the same pictures as on Linux in kitty and Ghostty; sixel pictures in
  iTerm2 and WezTerm; a block-character look in Terminal (24-bit color on macOS 26).
  Read-aloud uses the macOS voice (`say`) or Piper, sound plays through `afplay`,
  and the background tornado watch starts at login as a launch agent. CRT fonts
  can be set in kitty's own config from Settings, as on Linux.
* **Windows 10 and 11**: a native console version. Windows Terminal 1.22 and later
  shows every picture (sixel); the older console window gets the block-character
  look. Read-aloud uses Windows' own voices or Piper, sound plays through Windows
  itself, notifications are Windows toasts, and the background tornado watch runs
  without a window and starts at login. The installer adds a Start menu shortcut
  and two Windows Terminal profiles, `skydrop` and `skydrop CRT` (with Windows
  Terminal's scanline effect).
* **Downloads for each system**: a Debian/Ubuntu package (`.deb`), a Linux
  installer archive, a macOS archive and a Windows archive, each with its own
  installer; see the README.
* The labels in pictures use the DejaVu Sans fonts that now come with skydrop,
  so maps and charts look the same everywhere.
* Settings shows only the speech engines and audio players the system has.
* The Now screen's measured-and-modeled table cuts a long sky report short
  (…) instead of running it into the model's column.
* The default User-Agent no longer names Linux.

## 3.13.0

* The big temperature (Now) and air quality number (Air & UV) are drawn in a
  rounded, squared display face.
* The AQI and PM2.5 charts on Air & UV are outlined solid columns.
* The Alerts screen's regional radar became a TV-style warnings map: every county
  in your state colored by the watch, warning or advisory it is under.

## Earlier

Versions up to 3.13.0 were Linux-only and shipped as `skydrop-VERSION.tar.gz`.
