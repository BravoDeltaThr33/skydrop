skydrop comes as a **Windows app**: one file that opens skydrop in a window of its own, drawn the way Ghostty draws it on Linux.

## Downloads

| System | File | Install |
|---|---|---|
| Debian, Ubuntu, Mint | `skydrop_3.17.0_all.deb` | `sudo apt install ./skydrop_3.17.0_all.deb` |
| Any Linux | `skydrop-3.17.0-linux.tar.gz` | `tar xzf skydrop-3.17.0-linux.tar.gz && cd skydrop-3.17.0 && ./install.sh` |
| macOS | `skydrop-3.17.0-macos.zip` | unzip, then `cd ~/Downloads/skydrop-3.17.0-macos && bash install.sh` |
| Windows 10 and 11: the app | `skydrop-3.17.0-windows.exe` | download it and double-click it; nothing to install |
| Windows 10 and 11: in a terminal | `skydrop-3.17.0-windows.zip` | Extract All, then double-click `install.cmd` |
| Screenshots | `skydrop-3.16.0-screenshots.zip` | every screen, theme, CRT phosphor and drawing mode, taken with 3.16.0 |

`SHA256SUMS.txt` has the checksums. skydrop starts with the weather for Dallas,
TX (75201); press `z` to set your own ZIP code. No account or API key is needed.
Installing over 3.16.0 keeps your settings, and the Windows app shares them with
the terminal version. The app isn't signed with a paid certificate, so the first
time Windows SmartScreen may say it protected your PC: choose **More info**, then
**Run anyway**.

## What's new

* **The Windows app**, `skydrop-3.17.0-windows.exe`: skydrop in a window of its
  own, with every picture, the CRT glass and the sounds. It needs no Python and
  installs nothing. From a Command Prompt or PowerShell it takes skydrop's
  usual flags (`--now`, `--version`, ...).
* **skydrop's own window** draws skydrop the way Ghostty does: Ghostty's cell
  sizes, its box lines, blocks and sextants drawn to the pixel, and its own
  font, JetBrains Mono, which comes with skydrop (DejaVu Sans Mono too).
  F11 or Alt+Enter goes full screen, Ctrl+= and Ctrl+- change the font size,
  and a drag selects text to copy.
* **`skydrop --window`** opens the same window on Linux and macOS.
* Settings > Display: **Window font** and **Window font size**.
* **`skydrop --licenses`** prints the licenses of skydrop and of everything it
  includes.
