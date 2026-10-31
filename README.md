# 💧 skydrop

Retro TV weather for your Linux terminal. skydrop brings the look of the
1990s Weather Channel "Local on the 8s" to the command line, along with
serious weather tools: single-site NEXRAD radar, a
live warnings crawl, a NOAA Weather Radio style tornado alarm for your ZIP
code, a feed from every weather station around you, and forecasts read
aloud in a natural voice.

It draws real pictures in terminals that can show them (kitty, Ghostty,
WezTerm, Konsole, foot and other sixel terminals) and a block-character
version everywhere else (Tilix, GNOME Terminal, Xfce Terminal, tmux and the
rest).

**No account or API key is needed.** The National Weather Service and other
free, open data sources cover everything. If you have your own OpenWeather
key, you can add it in Settings as an extra source.

## Features

* **Nine screens**: current conditions, a 48-hour chart and table, a 10-day
  outlook, radar, a live station feed, alerts, air quality and UV, sky and
  almanac, and your NWS office's discussions and weather radio script.
* **Radar**: one NEXRAD radar at full resolution, decoded
  from its own Level 3 products: reflectivity, velocity, storm-relative
  velocity, correlation coefficient, differential reflectivity, specific
  differential phase, hydrometeor classification, echo tops, VIL, rain rate
  and rainfall totals, at six elevation angles. A toolbox of buttons and
  switches, 1, 2 or 4 linked panes, storm cell tracks with hail, mesocyclone
  and tornado vortex signatures, an inspect crosshair, GRLevelX color tables
  and placefiles, and the national NOAA MRMS mosaic one key away.
* **Warnings crawl**: watches, warnings, advisories and storm reports crawl
  along the bottom of every screen as they're issued, color-coded by
  severity.
* **Tornado alarm**: when a new Tornado Warning covers your ZIP code, skydrop
  sounds the NOAA Weather Radio alarm, puts up a red banner and reads the
  warning aloud. *Watch when closed* keeps watching even when skydrop isn't
  open.
* **Refreshes every 30 seconds** (alerts, radar scans and observations), and
  requests that haven't changed cost almost nothing.
* **Local on the 8s**: a full-screen, WeatherSTAR-style presentation mode
  whose pages advance by themselves, with optional narration.
* **Read aloud**: press `s` on any forecast screen. The voice comes from
  Piper and runs offline on your computer.
* **Five themes**, from royal-blue WeatherSTAR to a light 1970s magnetic
  weather board.
* **Keyboard and mouse**: every screen, button and setting works from the
  keyboard; click, drag and scroll work too.

## Requirements

* Linux with Python 3.8 or newer: Linux Mint, Ubuntu, Debian, Fedora and most
  other distributions.
* Any terminal. For pictures: kitty, Ghostty, WezTerm, Konsole, or a sixel
  terminal such as foot.
* On Debian-based systems (Mint, Ubuntu, Debian) the installer offers to add
  `python3-venv`, `espeak-ng`, `alsa-utils` and `libnotify-bin` with apt,
  asking for your password only if something is missing. Elsewhere, install
  them with your package manager (`skydrop` works without them, with fewer
  features).

## Install

Download `skydrop-3.1.1.tar.gz` from the Releases page to your Downloads
folder, open a terminal and run:

```bash
cd ~/Downloads && tar xzf skydrop-3.1.1.tar.gz && cd skydrop-3.1.1 && ./install.sh && skydrop --version
```

If you cloned this repository instead, run `./install.sh` from its folder.

Everything goes in your home folder; nothing needs root except the optional
system packages above. The installer sets up a private Python with Pillow
(pictures) and Piper (the offline voice, about 70 MB), downloads one voice,
adds the `skydrop` command and an applications-menu entry, and offers to
test the data sources. Installer options:

```
./install.sh --no-voice     skip the Piper voice (read-aloud uses espeak-ng)
./install.sh --voice NAME   use another Piper voice, e.g. en_US-lessac-medium
./install.sh --no-apt       never run sudo apt-get
./install.sh --yes          answer yes to every question
```

Running the installer again upgrades skydrop and keeps your settings.

## First run

Type `skydrop`. It starts with the weather for **Dallas, TX (75201)**. To use
your own location, press `z`, type your ZIP code (or a place such as
`Memphis, TN`) and press Enter. It's saved for next time.

Then look around: the number keys `1` to `9` switch screens, `0` opens
Settings, `?` shows every key, and `q` quits.

### API keys (all optional)

skydrop ships with no keys, and none are needed. To add your own, open
Settings (`0`):

* **OpenWeather > API key**: a free key from
  [openweathermap.org](https://openweathermap.org/api) adds OpenWeather's
  current conditions and air quality as extra sources (and minute-by-minute
  rain with a One Call subscription). *Daily call cap* keeps you within your
  plan.
* **National Weather Service > User-Agent**: the NWS asks every app to
  identify itself. skydrop sends a generic one by default; put your own here,
  such as `skydrop (you@example.com)`, so the NWS can reach you if your
  requests ever cause a problem.
* **National Weather Service > API key**: only if the NWS has given you one.
  The NWS API is free and doesn't require a key.

Keys are kept in `~/.config/skydrop/config.json`, which only you can read.

## Screens

Press the number key to jump to a screen, or Tab and the arrow keys to move
between them. Every screen fills the space it has: on a wide terminal the
right-hand sidebar adds radar, stations, almanac and more.

| Key | Screen | What's on it |
|-----|--------|--------------|
| 1 | Now | Current conditions with a big temperature and icon, the NWS forecast in words, the extended forecast strip, the nearest official station, model data, next-6-hour rain, and a sidebar with the local radar, nearby stations, almanac, outlooks and air quality |
| 2 | Hourly | 48-hour chart and table: temperature, feels-like, rain chance and amount, wind, gusts, humidity, dew point, clouds, UV, thunder chance, wet-bulb globe temperature, pressure, visibility; details for the selected hour, highlights, and the best times to be outside |
| 3 | 10-Day | Icons for each day, a table with temperature range bars and NWS HeatRisk, "the outlook at a glance", a highs-and-lows chart, and the selected day's full NWS text plus hour by hour |
| 4 | Radar | Single-site NEXRAD radar with every Level 3 product and tilt (or the national mosaic), the toolbox, linked panes, storm tracks, warning polygons, placefiles, and a nowcast that estimates when rain will reach you |
| 5 | Stations | Live readings from every station in and around your ZIP code: airport ASOS/AWOS, citizen CWOP stations, state road-weather sensors, co-op, river and soil networks, with a map, each station's last 24 hours and a log of the newest reports |
| 6 | Alerts | NWS watches, warnings and advisories with full text, SPC severe and WPC excessive-rain outlooks for your exact point, SPC watches, local storm reports, a regional radar with warning boxes, and the Hazardous Weather Outlook |
| 7 | Air & UV | US AQI, every pollutant, past and next 24 hours, daily peaks, the UV curve, sunburn time and a short guide to what each pollutant is |
| 8 | Sky & Almanac | Sunrise, sunset and twilight, today's moon (drawn), moon phases, normals and records from NOAA, this month against normal, the week ahead, the sun's path, daylight through the year, and the next solstice and clock change |
| 9 | NWS & Radio | Your NWS office's headlines and weather stories, the Area Forecast Discussion, the Hazardous Weather Outlook, and the NOAA Weather Radio broadcast script |
| 0 | Settings | Every setting, the data sources, connection tests and the Active errors list |
| L | Local on the 8s | Full-screen WeatherSTAR-style pages that advance by themselves. Press `n` to have every page read aloud |

## The radar

The Radar screen: you look at one radar at a
time, at its full resolution, and pick what it shows.

* **Map settings**: the radar site (`◀` `▶` steps through the nearest ones,
  *Auto* follows the radar closest to the middle of the map), site radar or
  the national mosaic, smoothing, and opacity.
* **Products** (`p` `P`): REF reflectivity, VEL velocity, SRM storm-relative
  velocity, CC correlation coefficient, ZDR differential reflectivity, KDP
  specific differential phase, HC hydrometeor classification, VIL, ET echo
  tops, ACC rainfall (1-hour and storm total) and rain rate, base or
  composite.
* **Elevation** (`e` `E`): 0.5°, 0.9°, 1.3°, 1.8°, 2.4° and 3.1°.
* **Timeline**: play, pause, step and jump (space `,` `.` `<` `>`), loop
  length and speed.
* **Layers**: storm tracks, warnings, cities, station temperatures, range
  rings, radar sites, your markers and placefiles, each a switch.
* **Panes and tools**: 1, 2 or 4 linked panes (pan and zoom together),
  inspect (`x`, or click the map), and the guide card (`i`), which explains
  every color, symbol and control.

Down the sidebar: what's under the mouse (the value, distance and direction
from the radar, and how high the beam is there), the storm cells the radar is
tracking (strength, motion, hail, mesocyclone and TVS flags, and when a cell
could reach you), warnings in view (click one to go there), and the nowcast.

The colors are GRLevelX color tables (`.pal`). To use your own, put `ref.pal`, `vel.pal`, `cc.pal`, `zdr.pal`,
`kdp.pal`, `hc.pal`, `et.pal`, `vil.pal`, `rate.pal` or `precip.pal` in
`~/.config/skydrop/palettes/`.

### Placefiles

Placefiles are the overlay files GRLevel3, RadarScope read:
spotter positions, lightning, storm attributes, station plots and more. Add
their addresses in Settings > Radar > Placefiles, separated by spaces or
commas (local files such as `~/wx/mine.txt` work too). skydrop draws text,
places, icons (rotated, from icon sheets), lines, polygons, triangles and
images, follows each file's refresh time and zoom thresholds, shows
time-ranged items in the matching loop frames, and shows an item's details
when the mouse is over it. A few public ones from the Iowa Environmental
Mesonet to try:

```
https://mesonet.agron.iastate.edu/request/grx/l3attr.txt
https://mesonet.agron.iastate.edu/request/grx/time_mot_loc.txt
```

Your own pins go in Settings > Radar > Map markers, for example
`Work: 32.78, -96.80; Home: 32.84, -96.78`.

## Warnings crawl and tornado alarm

The crawl along the bottom shows watches, warnings and storm reports as they
come in. Settings > Warnings crawl decides what it covers: your ZIP code,
your county, everything within 25 to 200 miles, or your state, with or
without advisories and storm reports, and how fast it moves.

When a **new Tornado Warning covers your ZIP code** (the warning polygon
overlaps your ZIP code's outline, or the warning lists your county), skydrop:

1. plays the alarm: three SAME data bursts carrying the warning's own codes,
   then the 1050 Hz tone, the way NOAA Weather Radio sounds it (or the tone
   alone, or the two-tone broadcast attention signal, as you choose);
2. puts up a flashing red banner with the threat (radar indicated or
   observed), where the storm is and where it's heading, and what to do;
3. reads the warning aloud, then plays the end-of-message bursts.

Press any key to acknowledge it; a red bar stays at the top until the warning
ends. The alarm sounds again for an upgrade to a Tornado Emergency. You can
have it repeat every few minutes until you acknowledge it, and pick a chime
or tone for other warnings that cover your ZIP code.

**Watch when closed** (Settings > Tornado alarm) starts a small background
watch that keeps checking while skydrop is closed and from the moment you
log in. It sounds the same alarm and shows a desktop notification. While a
skydrop window is open, the window handles the alarm; either way a warning
sounds once.

Test it any time: Settings > Tornado alarm > *Test tornado alarm* (or press
`A` on the Settings screen), or `skydrop --test-alarm`. The test uses the
Required Weekly Test code, not a tornado warning.

The alarm plays only on your computer. Please don't record it and broadcast
or stream it: FCC rules (47 CFR 11.45) forbid transmitting EAS codes or the
attention signal, or a simulation of them, except in a real emergency or an
authorized test. skydrop is not a replacement for a NOAA Weather Radio, a
weather radio app or local sirens; use more than one way to get warnings.

## Pictures in every terminal

skydrop asks the terminal what it can do when it starts and picks the best
of four ways to draw:

| Mode | Terminals | What you get |
|------|-----------|--------------|
| kitty | kitty, Ghostty, WezTerm, Konsole | true pixels: the radar map, weather icons, charts and the moon |
| sixel | foot, mlterm, xterm (`xterm -ti vt340`), Contour, Windows Terminal, recent VTE builds | the same pictures, sent as sixel images |
| blocks | Tilix, GNOME Terminal, Xfce Terminal, Terminator, tmux, everything else with Unicode | half-block pixel art for radar and icons, line-art weather icons, braille and block charts |
| ascii | basic consoles and fonts without block characters | plain characters only |

Colors use 24-bit truecolor when the terminal supports it, otherwise 256 or
16 colors. To see what skydrop detected, run `skydrop --probe`. To force a
mode, change Graphics and Colors in Settings, or try one for a single run:
`skydrop --graphics blocks`. Inside tmux, pictures are switched off because
tmux doesn't pass them through.

## Themes

Pick one in Settings > Display > Theme, or try one for a single run with
`skydrop --theme NAME`. Charts, icons, the radar map and every color follow
the theme.

| Theme | Looks like |
|-------|------------|
| weatherstar | royal blue, gold and white: the classic Local on the 8s (the default) |
| midnight | near-black with soft colors, easy on the eyes at night |
| retro8s | steel and sky-blue panels, gold headings, a navy-to-orange title bar |
| star4000 | WeatherSTAR 4000 blue-violet with red-orange bars and yellow numbers |
| magnetic | light: the pale blue 1970s TV weather board with yellow and black magnets |

## Read aloud

Press `s` on any screen with a forecast (Now, Hourly, 10-Day, Radar,
Stations, Alerts, Air, Sky, NWS & Radio, and every page of Local on the 8s).
The sentence being spoken is shown and highlighted at the bottom of the
screen; `[` and `]` go back and forward a sentence, and `s` or Esc stops.

The voice comes from Piper, which runs offline on your computer. Pick a
different voice and speed in Settings (Voice and sound), then use Download
voice. If Piper isn't installed, skydrop uses pico2wave, espeak-ng, festival
or speech-dispatcher, whichever it finds. Sound plays through pw-play
(PipeWire), paplay (PulseAudio), aplay (ALSA) or ffplay.

From the command line: `skydrop --speak` reads current conditions,
`skydrop --speak daily` the forecast.

## Keys

| Keys | What they do |
|------|--------------|
| 1-9, 0 | jump to a screen (0 is Settings) |
| Tab, ← → | next or previous screen (arrows pan the map on Radar) |
| ↑ ↓, j k | move the selection or scroll |
| PgUp PgDn, g G | page, top, bottom |
| J K | scroll the detail pane or sidebar |
| s | read this screen aloud (again to stop) |
| [ ] | previous / next sentence while reading |
| L | Local on the 8s (← → change pages, space pauses, n turns narration on or off) |
| z | switch to another ZIP code or place (it's saved; `skydrop --zip` tries one without saving) |
| r | refresh now |
| u | switch between °F and °C |
| ? | help |
| q | quit |
| any key | acknowledges a tornado alarm |
| Radar | `p` `P` product, `e` `E` elevation, `n` `N` radar site, `m` site radar or mosaic, `v` panes, `+` `-` zoom, arrows pan, `c` center on home, space `,` `.` `<` `>` loop, `x` inspect, `a` storm tracks, `o` smoothing, `l` `t` `w` cities, temperatures, warnings, `d` range rings, `D` radar sites, `M` markers, `F` placefiles, `y` mosaic layer, `b` map background, `i` the radar guide |
| Stations | `f` filter, `o` sort, `+` `-` radius, Enter uses the selected station for current conditions |
| Settings | ↑ ↓ move, PgUp PgDn section, Shift+← → or `<` `>` column, Home End first and last, `/` find, ← → change a choice, switch or number, Enter edits or toggles, `S` or Ctrl-S save, `T` test connections, `D` `D` discard changes, `R` restore deleted sources, `A` test the tornado alarm, `x` deletes a data source, `e` edits its address, `v` shows keys |

The mouse works too: click a tab to switch screens, click a row on Hourly,
10-Day or Stations for its details, and scroll with the wheel. On Radar,
click the toolbox buttons, click the map to inspect a point, drag to pan, use
the wheel to zoom, right-click to recenter, and click a warning in the
sidebar to go to it.

## Data sources

Every source skydrop contacts is listed on the Settings screen under Data
sources. Press Enter to turn one off (it is never contacted while off), `x`
to delete it from the list, or `e` to change its address if a provider moves.
"Restore deleted sources" brings them all back. All of them are free, and
none needs an account except OpenWeather, which is optional and uses your
own key.

| Source | Used for | Terms |
|--------|----------|-------|
| National Weather Service API (api.weather.gov) | forecasts, hourly and gridpoint data, observations, alerts, office products, NOAA Weather Radio scripts, station and radar lists | U.S. Government, public domain |
| NEXRAD Level 3 on NOAA Open Data (AWS) | single-site radar: every product and tilt, with history for loops | NOAA Open Data Dissemination, public domain |
| NWS tgftp radar products | the latest storm tracks, hail, mesocyclone and TVS detections; backup for site radar | U.S. Government, public domain |
| NOAA MRMS radar (opengeo.ncep.noaa.gov) | the national radar mosaic | U.S. Government, public domain |
| Open-Meteo forecast | 16-day and 15-minute model forecasts, UV index | open source (AGPL-3.0), data CC BY 4.0 |
| Open-Meteo air quality | US AQI and pollutants from CAMS | open source (AGPL-3.0), data CC BY 4.0 |
| Iowa Environmental Mesonet | live station networks, SPC/WPC outlooks for your point, storm reports, warning polygons, storm attributes, radar backup | Iowa State University open data, open-source code |
| NOAA RCC ACIS | normals, records, month and year precipitation | NOAA Regional Climate Centers, public |
| U.S. Census TIGERweb | radar map lines, city names, ZIP code outlines | U.S. Government, public domain |
| Zippopotam.us | ZIP code lookup without a key | open source (MIT), GeoNames data CC BY 4.0 |
| RainViewer | radar backup outside the U.S. | free for personal use |
| NASA GIBS | GOES infrared satellite layer | NASA open data |
| USGS The National Map | imagery and topo map backgrounds | U.S. Government, public domain |
| OpenStreetMap tiles | street-map background (off unless you turn it on) | © OpenStreetMap contributors, ODbL |
| Piper voices on Hugging Face | downloading voices | MIT code, per-voice licenses |
| OpenWeather (optional, your own key) | extra current conditions, air quality and, with a One Call subscription, minute-by-minute rain | commercial service with a free tier |

Placefiles you add are fetched from the addresses you give.

## Command line

```
skydrop                    the full-screen app
skydrop --local8s          start in Local on the 8s
skydrop --zip 90210        another place for this run only ("City, ST" works too)
skydrop --now              print current conditions and exit
                            (also --hourly --daily --radar --stations --alerts --air --sky --office)
skydrop --json             the merged data as JSON
skydrop --speak [screen]   read a forecast aloud
skydrop --test-alarm       play the tornado alarm as it will sound
skydrop --watch            watch for tornado warnings without a window (Settings starts this for you)
skydrop --watch-stop       stop the background watch
skydrop --get-voice NAME   download a Piper voice and use it
skydrop --check            test every data source
skydrop --probe            show what your terminal supports
skydrop --graphics MODE    auto, kitty, sixel, blocks or ascii for this run
skydrop --theme NAME       weatherstar, midnight, retro8s, star4000 or magnetic for this run
skydrop --units metric     units for this run
skydrop --set KEY=VALUE    change a setting without opening the app
```

## Files

| Path | What |
|------|------|
| `~/.config/skydrop/config.json` | your settings and keys (readable only by you); edit them on the Settings screen |
| `~/.config/skydrop/palettes/` | your own radar color tables (optional) |
| `~/.config/autostart/skydrop-watch.desktop` | starts the background tornado watch when you log in (only with Watch when closed on) |
| `~/.local/share/skydrop/` | the program, its private Python, voices, this README and `uninstall.sh` |
| `~/.cache/skydrop/` | cached data, map tiles, radar scans, speech audio, `skydrop.log` and `watch.log` |
| `~/.local/bin/skydrop` | the command |

## Troubleshooting

* **`skydrop: command not found`**: open a new terminal window (the installer
  added `~/.local/bin` to your PATH), or run `~/.local/bin/skydrop`.
* **No alarm sound**: run `skydrop --test-alarm`. It names the audio player it
  uses; if it finds none, install `alsa-utils`. Check your volume, and that
  Tornado alarm is on in Settings > Tornado alarm.
* **Did the background watch see a warning?** Look in
  `~/.cache/skydrop/watch.log`. `skydrop --watch` in a terminal shows what
  it's doing as it happens.
* **No pictures in kitty or Ghostty**: check `skydrop --probe`; inside tmux
  pictures are off on purpose. Set Graphics to kitty in Settings to force it.
* **The radar says it has no data**: a radar can be down for maintenance. Press
  `n` for the next nearest radar, or `m` for the national mosaic.
* **Boxes or question marks instead of icons**: your font lacks some
  characters; choose a font like DejaVu Sans Mono or set Graphics to ascii.
  The 💧 in the title bar needs an emoji font (most desktops have one).
* **No sound when reading aloud**: Settings, Voice and sound, Test voice. The
  Settings sidebar shows which speech engine and audio player were found.
* **OpenWeather says "Invalid API key"**: new keys take a few hours to start
  working. Some OpenWeather endpoints need a paid plan; skydrop skips those
  and keeps using the rest.
* **Something in Active errors**: Settings (`0`) lists every error since
  skydrop started, with the source and the message. Most clear by
  themselves on the next refresh; one that stays tells you which source to
  turn off or check.
* **A source keeps failing**: it is skipped automatically and the others fill
  in. Turn it off in Settings if you don't want it tried. Open-Meteo's free
  tier allows 10,000 requests a day per network address.
* **Something else**: details go to `~/.cache/skydrop/skydrop.log`.

## Uninstall

```bash
~/.local/share/skydrop/uninstall.sh
```

This also stops the background tornado watch and removes its login entry.
Your settings stay in `~/.config/skydrop` unless you add `--purge`.

## Credits

Weather data from the U.S. National Weather Service, NOAA, the Iowa
Environmental Mesonet, Open-Meteo, NASA, the U.S. Census Bureau, USGS,
RainViewer, OpenStreetMap contributors and GeoNames, and OpenWeather with
your own key. The radar screen is modeled on Supercell Wx by Dan Paulat, and
the color tables and placefiles follow the GRLevelX formats by Gibson Ridge
Software. Voices by the Piper project (Open Home Foundation). The look is a
tribute to The Weather Channel's WeatherSTAR 4000; skydrop isn't affiliated
with The Weather Channel, Supercell Wx, Gibson Ridge or any data provider.
