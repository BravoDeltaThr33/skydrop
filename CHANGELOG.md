# Changelog

## 3.17.0

skydrop comes as a Windows app: `skydrop-3.17.0-windows.exe`, skydrop in a window
of its own that draws it the way Ghostty draws it on Linux.

* **The Windows app** is one file: download it and double-click. It needs no
  Python and installs nothing; it shares its settings (`%APPDATA%\skydrop`) with
  the terminal version. From a Command Prompt or PowerShell it takes the same
  flags (`--now`, `--version`, ...). Its sounds, read-aloud and the background
  tornado watch (Watch when closed starts the .exe itself at login) work as in
  the terminal version.
* **skydrop's own window** is a small terminal of skydrop's: the app runs in it
  unchanged and sends it what it sends kitty or Ghostty. It works out its cell
  from the font the way Ghostty does and draws the box lines, blocks, sextants,
  the other legacy computing blocks and braille itself, to the pixel, as
  Ghostty does; kitty pictures and the CRT glass show as in Ghostty. It comes
  with Ghostty's default font, JetBrains Mono, and with DejaVu Sans Mono; the 💧
  in the title bar is Noto Color Emoji's.
* In the window: F11 or Alt+Enter for full screen, Ctrl+= and Ctrl+- for the
  font size (Ctrl+0 back to the size in Settings), drag to select text
  (Shift+drag while skydrop has the mouse), Ctrl+Shift+C or a right-click to
  copy, Ctrl+V to paste. It comes back at the size and place it was left. `?`
  lists these keys in the window.
* `skydrop --window` opens the same window on Linux and macOS (it needs tkinter
  and Pillow's ImageTk: `sudo apt install python3-tk python3-pil.imagetk` on
  Debian and Ubuntu, which the .deb suggests).
* Settings > Display: **Window font** (JetBrains Mono or DejaVu Sans Mono) and
  **Window font size**; the window follows them as soon as they're saved.
* `skydrop --licenses` prints the licenses of skydrop and of the fonts and code
  it includes (in the Windows app: Python, Tcl/Tk, Pillow, tzdata and
  PyInstaller's bootloader too).
* CI builds the Windows app with PyInstaller (pinned, hash-checked build tools)
  on a Windows machine and checks it there: it opens its window's terminal,
  finds its fonts, time zones and Tk, and draws the Now screen with live
  weather. It also runs the copy in release/.

## 3.16.0

* The Now screen's Next 12 hours chart has a humidity line over the
  temperature: the relative humidity hour by hour, on its own scale (0% at the
  chart's foot, 100% at its top), colored along the whole spectrum, from red
  for the driest air through orange, yellow, green, cyan and blue to violet
  for the most humid. In kitty, Ghostty and sixel terminals it's a smooth line
  with a dark edge, so it reads over the shaded temperature; elsewhere it's
  drawn in braille dots, and in plain characters in ASCII mode.
* A key under the chart, Humidity, shows the spectrum from 0% to 100% and the
  lowest and highest humidity in the next 12 hours, each in its color.
* The title bar shows skydrop's version beside its name.

## 3.15.0

* The Now screen's Next 12 hours is one chart instead of two, three rows
  shorter: the temperature along the top and, under it, a bar for each hour
  precipitation may fall, as tall as its chance, with the chance over it.
* The bars are colored by what falls, as on weather radar: rain in shades of
  green from light to heavy, thunderstorms gold, snow white, sleet pink,
  freezing rain magenta and a wintry mix violet. The key under the chart, now
  called Precipitation, names the kinds in the next 12 hours.
* A forecast of "Wintry Mix" gets the wintry mix icon.

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
