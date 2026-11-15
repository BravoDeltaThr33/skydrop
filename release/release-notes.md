skydrop now runs on **Linux, macOS and Windows**.

## Downloads

| System | File | Install |
|---|---|---|
| Debian, Ubuntu, Mint | `skydrop_3.14.0_all.deb` | `sudo apt install ./skydrop_3.14.0_all.deb` |
| Any Linux | `skydrop-3.14.0-linux.tar.gz` | `tar xzf skydrop-3.14.0-linux.tar.gz && cd skydrop-3.14.0 && ./install.sh` |
| macOS | `skydrop-3.14.0-macos.zip` | unzip, then `cd ~/Downloads/skydrop-3.14.0-macos && bash install.sh` |
| Windows 10 and 11 | `skydrop-3.14.0-windows.zip` | Extract All, then double-click `install.cmd` |
| Screenshots | `skydrop-3.14.0-screenshots.zip` | every screen, theme, CRT phosphor and drawing mode |

`SHA256SUMS.txt` has the checksums. skydrop starts with the weather for Dallas,
TX (75201); press `z` to set your own ZIP code. No account or API key is needed.

## What's new

* **macOS**: the same pictures as on Linux in kitty and Ghostty, sixel pictures in
  iTerm2 and WezTerm, a block-character look in Terminal. Read-aloud uses the
  macOS voice or Piper; the background tornado watch starts at login.
* **Windows**: a native console version. Windows Terminal 1.22 and newer shows
  every picture; the installer adds a Start menu shortcut and two Windows
  Terminal profiles, `skydrop` and `skydrop CRT`. Read-aloud uses Windows'
  voices or Piper, alerts appear as Windows notifications, and the background
  tornado watch runs without a window.
* A Debian/Ubuntu package alongside the installer archives.
* Labels in pictures use DejaVu Sans, which now comes with skydrop, so maps and
  charts look the same on every system.
