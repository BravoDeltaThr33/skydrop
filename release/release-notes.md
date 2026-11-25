The Now screen's **Next 12 hours** chart tracks the humidity too, in every color of the spectrum.

## Downloads

| System | File | Install |
|---|---|---|
| Debian, Ubuntu, Mint | `skydrop_3.16.0_all.deb` | `sudo apt install ./skydrop_3.16.0_all.deb` |
| Any Linux | `skydrop-3.16.0-linux.tar.gz` | `tar xzf skydrop-3.16.0-linux.tar.gz && cd skydrop-3.16.0 && ./install.sh` |
| macOS | `skydrop-3.16.0-macos.zip` | unzip, then `cd ~/Downloads/skydrop-3.16.0-macos && bash install.sh` |
| Windows 10 and 11 | `skydrop-3.16.0-windows.zip` | Extract All, then double-click `install.cmd` |
| Screenshots | `skydrop-3.15.0-screenshots.zip` | every screen, theme, CRT phosphor and drawing mode, taken with 3.15.0 |

`SHA256SUMS.txt` has the checksums. skydrop starts with the weather for Dallas,
TX (75201); press `z` to set your own ZIP code. No account or API key is needed.
Installing over 3.15.0 keeps your settings.

## What's new

* **A humidity line over the temperature** in the Now screen's Next 12 hours:
  the relative humidity hour by hour, 0% at the chart's foot and 100% at its
  top, colored along the whole spectrum: red for the driest air, then orange,
  yellow, green, cyan and blue, to violet for the most humid. A key under the
  chart shows the spectrum and the lowest and highest humidity in the next 12
  hours.
* **The version in the title bar**, beside skydrop's name.
