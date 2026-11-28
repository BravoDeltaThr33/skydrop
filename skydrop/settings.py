"""Settings screen (0)."""

import os
import re
import threading
import time

from . import crt as C
from . import sources as S
from . import ui
from .config import DEFAULT_NWS_BASE, DEFAULT_OWM_BASE, DEFAULTS, save_config
from .errlog import LOG
from .net import Http
from .paths import CONFIG_PATH, pretty_path
from .speech import (ENGINE_CHOICES, PLAYER_CHOICES, VOICE_CHOICES, engine_status, engines_help, find_player,
                     no_player_hint, voice_path)
from .store import FALLBACK_COORDS, ONECALL_MSG, check_connections, geocode
from .term import (KEY_BACKSPACE, KEY_BTAB, KEY_DC, KEY_DOWN, KEY_END, KEY_ENTER, KEY_HOME, KEY_LEFT, KEY_NPAGE,
                   KEY_PPAGE, KEY_RIGHT, KEY_SLEFT, KEY_SRIGHT, KEY_UP, Paste)
from .text import clip, labeled_wrap, padto, wrap
from .theme import BACKGROUND_LABEL, BACKGROUNDS, background_of, hex_rgb
from .util import num

ONOFF = "choice:on,off"

# ("sec", title) | ("fld", key, label, type, help) | ("btn", action, label, None, help) | ("src", id)
FIELDS = [
    ("sec", "Location"),
    ("fld", "zip", "ZIP code", "zip",
     "A U.S. ZIP code, or a place such as Memphis, TN. Save (or press z on any screen) to pull its weather."),
    ("fld", "location_name", "Display name", "text",
     "Shown in the header. Leave blank to use the city NWS reports for the location."),
    ("fld", "lat", "Latitude", "lat",
     "Optional. When latitude and longitude are both set they're used instead of the ZIP code."),
    ("fld", "lon", "Longitude", "lon",
     "Optional. When latitude and longitude are both set they're used instead of the ZIP code."),
    ("fld", "country", "Country code", "country",
     "Two-letter code used for ZIP and place lookups. NWS, radar and station data cover the U.S."),
    ("fld", "nws_station", "NWS station", "station",
     "Observation station ID used for current conditions, such as KDAL. Leave blank for the nearest airport. "
     "Tip: on the Stations screen (5), press Enter on any station to use it."),
    ("sec", "Display"),
    ("fld", "units", "Units", "choice:imperial,metric", "Enter switches. The u key does the same on any screen."),
    ("fld", "theme", "Theme", "choice:weatherstar,midnight,retro8s,star4000,magnetic",
     "weatherstar: royal blue and gold, the classic Local on the 8s. midnight: near-black, easy on the eyes "
     "at night. retro8s: steel and sky-blue panels, gold headings, a navy-to-orange title bar. star4000: "
     "WeatherSTAR 4000 blue-violet with red-orange bars and yellow numbers. magnetic: light, the pale blue "
     "1970s TV weather board with yellow and black magnets."),
    ("fld", "background", "Background", "color",
     "The page's color under the theme: the theme's own, black, deep blue, blue black, ink, near black, dark "
     "slate or charcoal - Enter types any #RRGGBB - or the terminal's own, which lets your terminal's "
     "background (and transparency) show through. The light theme (magnetic) keeps its own: its lettering is "
     "dark. In CRT mode a tube paints its own raised black."),
    ("fld", "rounded_borders", "Rounded corners", ONOFF,
     "╭╮ rounded box corners, or ┌┐ square ones for fonts without them."),
    ("fld", "graphics", "Graphics", "choice:auto,kitty,sixel,blocks,ascii",
     "auto picks the best your terminal supports: kitty (kitty, Ghostty, WezTerm) and sixel draw real "
     "pixels; blocks uses colored half-block characters (Tilix, GNOME Terminal); ascii is plain text. The CRT "
     "glass effects need kitty pictures in kitty or Ghostty; blocks and ascii keep the CRT's text effects."),
    ("fld", "color_mode", "Colors", "choice:auto,truecolor,256,16",
     "auto detects 24-bit color. Pick 256 or 16 if colors look wrong."),
    ("fld", "window_font", "Window font", "choice:jetbrains,dejavu",
     "The font of skydrop's own window (skydrop.exe on Windows, or skydrop --window anywhere), which draws "
     "skydrop the way Ghostty does: jetbrains is JetBrains Mono, Ghostty's own font; dejavu is DejaVu Sans Mono. "
     "Both come with skydrop. In a terminal, skydrop shows in the terminal's font."),
    ("fld", "window_font_size", "Window font size", "int:6:48",
     "The window's font size in points (6 to 48). In the window Ctrl + and Ctrl - make it bigger or smaller for a "
     "while, and Ctrl 0 puts this size back."),
    ("fld", "ticker", "Bottom ticker", "choice:paged,crawl,off",
     "The WeatherSTAR-style line along the bottom: paged shows one item at a time, crawl scrolls."),
    ("fld", "clock_seconds", "Clock seconds", ONOFF, "Show seconds on the header clock."),
    ("fld", "mouse", "Mouse", ONOFF, "Click tabs and rows, scroll with the wheel. Hold Shift to select text "
                                     "while the mouse is on."),
    ("fld", "refresh_seconds", "Refresh every", "int:10:3600",
     "Seconds between checks for new data: alerts, warnings, radar scans and observations (10 to 3600). "
     "Forecasts and other slow products still refresh on their own schedule, so 30 costs very little."),
    ("fld", "hourly_hours", "Hourly hours", "int:12:96", "How many hours the Hourly screen shows."),
    ("fld", "daily_days", "Daily days", "int:5:16", "How many days the 10-Day screen shows (NWS covers 7; "
                                                   "Open-Meteo extends to 16)."),
    ("sec", "Tornado alarm"),
    ("fld", "tornado_alarm", "Tornado alarm", ONOFF,
     "Sound an alarm when a new Tornado Warning covers your ZIP code (or upgrades to a Tornado Emergency). "
     "A red banner stays up until you press any key."),
    ("fld", "tornado_alarm_sound", "Alarm sound", "choice:noaa,nwr,eas",
     "noaa: the SAME data bursts then the 1050 Hz tone, the way NOAA Weather Radio sounds; nwr: the tone alone; "
     "eas: the two-tone broadcast attention signal."),
    ("fld", "tornado_alarm_repeat", "Repeat every", "int:0:30",
     "Minutes between repeats until you press a key; 0 plays it once."),
    ("fld", "tornado_alarm_voice", "Read it aloud", ONOFF, "Read the warning aloud after the alarm tone."),
    ("fld", "warning_alarm", "Other warnings", "choice:chime,nwr,off",
     "Sound for other new warnings that cover your ZIP code (severe thunderstorm, flash flood, and so on)."),
    ("fld", "auto_read_warnings", "Read warnings", ONOFF, "Also read those other warnings aloud."),
    ("fld", "background_watch", "Watch when closed", ONOFF,
     "Keep watching for tornado warnings after skydrop closes, and from the moment you log in: a small "
     "background watch sounds the same alarm and shows a desktop notification. Starts when you save."),
    ("btn", "test_alarm", "Test tornado alarm", None, "Play the tornado alarm exactly as it will sound, "
                                                       "with a test message instead of a warning. Key: A."),
    ("sec", "Warnings crawl"),
    ("fld", "alert_ticker", "Warnings crawl", ONOFF,
     "A crawl of watches, warnings and storm reports along the bottom, updated at every refresh. One that "
     "covers your county carries the county's name, lit up, right after its own."),
    ("fld", "ticker_area", "Crawl covers", "choice:zip,county,25,50,100,200,state",
     "Which alerts the crawl shows: your ZIP code; your county; warnings and watches within 25 to 200 miles "
     "(plus everything for your county); or everything issued for your state."),
    ("fld", "ticker_advisories", "Advisories too", ONOFF,
     "Include advisories and statements in the crawl, not just watches and warnings."),
    ("fld", "ticker_reports", "Storm reports", ONOFF,
     "Include local storm reports (tornado, hail, wind damage) in the crawl as they come in."),
    ("fld", "ticker_speed", "Crawl speed", "choice:slow,normal,fast",
     "How fast a warning's counties go by in the crawl when they don't all fit: slow, normal (an easy reading "
     "pace) or fast. Each warning's name, times and distance stay put while its counties scroll; with several "
     "warnings they take turns."),
    ("sec", "Radar"),
    ("fld", "radar_source", "Radar source", "choice:site,mosaic",
     "site shows one NEXRAD radar at full resolution with all its Level 3 products, like Supercell Wx; mosaic "
     "is the national NOAA MRMS composite."),
    ("fld", "radar_site", "Radar site", "radarsite",
     "auto follows the radar nearest the map center; type an ID such as KFWS to stay on one radar."),
    ("fld", "radar_product", "Product", "choice:ref,vel,srv,cc,zdr,kdp,hc,hhc,et,vil,rate,p1h,pst,cr",
     "ref reflectivity, vel velocity, srv storm-relative velocity, cc correlation coefficient, zdr differential "
     "reflectivity, kdp specific differential phase, hc / hhc hydrometeor class, et echo tops, vil liquid water, "
     "rate rain rate, p1h / pst rainfall totals, cr composite reflectivity. Press i on the Radar screen for more."),
    ("fld", "radar_panes", "Panes", "choice:1,2,4", "One map, or two or four linked maps that pan and zoom together."),
    ("fld", "radar_pane_products", "Pane products", "products",
     "Products for the extra panes, in order, separated by commas (for example ref,vel,cc,zdr)."),
    ("fld", "radar_smooth", "Smoothing", ONOFF, "Blend neighbouring radar bins for a smoother picture."),
    ("fld", "radar_cells", "Storm tracks", ONOFF,
     "Storm cell tracks and forecast positions, hail, mesocyclones and tornado vortex signatures."),
    ("fld", "radar_min_dbz", "Hide below", "choice:-30,-10,0,5,10,15,20",
     "Hide reflectivity weaker than this many dBZ (5 hides most clear-air clutter)."),
    ("fld", "radar_opacity", "Opacity", "choice:30,40,50,60,70,80,90,100", "How solid the radar colors are, in percent."),
    ("fld", "radar_hover", "Hover readout", ONOFF, "Show the value under the mouse pointer in the radar sidebar."),
    ("fld", "placefiles", "Placefiles", "text",
     "GRLevelX placefile addresses (spotters, lightning, storm attributes, station plots), separated by spaces "
     "or commas. Local files work too, such as ~/wx/mine.txt. Toggle them with Placefiles on the Radar screen."),
    ("fld", "map_markers", "Map markers", "text", "Your own pins, such as Work: 32.78, -96.80; Home: 32.84, -96.78"),
    ("fld", "radar_span", "Map width", "choice:15,30,60,120,240,480,960", "Miles across the radar map; + and - "
                                                                          "change it on the Radar screen."),
    ("fld", "radar_frames", "Loop frames", "int:1:20", "Scans in the radar loop."),
    ("fld", "radar_step", "Frame spacing", "choice:2,5,10,15,20,30", "Minutes between loop frames."),
    ("fld", "radar_speed", "Loop speed", "int:1:10", "Frames per second."),
    ("fld", "radar_layer", "Mosaic layer", "choice:reflectivity,type,satellite,radar+sat",
     "For the national mosaic: reflectivity is classic radar; type colors rain, snow and ice; satellite adds "
     "GOES infrared clouds."),
    ("fld", "radar_basemap", "Background", "choice:classic,imagery,topo,osm,plain",
     "classic draws Census map lines on blue; imagery and topo use USGS; osm needs the OpenStreetMap source "
     "turned on below."),
    ("fld", "radar_labels", "City names", ONOFF, "Label towns on the map."),
    ("fld", "radar_stations", "Station temps", ONOFF, "Show live station temperatures on the map."),
    ("fld", "radar_warnings", "Warning boxes", ONOFF, "Draw storm-based warning polygons and watches."),
    ("sec", "Station feed"),
    ("fld", "station_radius", "Radius", "int:1:100", "Miles around the ZIP code to gather stations from."),
    ("fld", "station_max", "Max stations", "int:5:120", "Most NWS stations to poll for live observations."),
    ("sec", "Voice and sound"),
    ("fld", "voice_engine", "Speech engine", "choice:" + ",".join(ENGINE_CHOICES), engines_help()),
    ("fld", "voice", "Piper voice", "choice:" + ",".join(VOICE_CHOICES),
     "Natural voices from the Piper project (about 60 MB each). Use Download voice after changing it."),
    ("fld", "voice_rate", "Speaking rate", "int:50:200", "Percent of normal speed."),
    ("fld", "audio_player", "Audio player", "choice:" + ",".join(PLAYER_CHOICES), "Program used to play sound."),
    ("fld", "narrate", "Narrate Local 8s", ONOFF, "Read each page aloud in Local on the 8s mode (L)."),
    ("fld", "local8s_seconds", "Page seconds", "int:5:60", "Seconds per page in Local on the 8s mode."),
    ("btn", "voice_dl", "Download voice", None, "Download the selected Piper voice from Hugging Face."),
    ("btn", "voice_test", "Test voice", None, "Speak a short sample with the current settings."),
    ("sec", "OpenWeather (optional)"),
    ("fld", "owm_api_key", "API key", "secret",
     "Optional: your own free key from openweathermap.org (My API keys). NWS and Open-Meteo cover "
     "forecasts without it. New keys can take a few hours to start working."),
    ("fld", "owm_shared_secret", "Shared secret", "secret", "Saved for later. OpenWeather doesn't use one today."),
    ("fld", "owm_onecall_version", "One Call version", "choice:auto,4.0,3.0,off",
     "auto tries 4.0, then 3.0, then the free endpoints; off uses only the free endpoints."),
    ("fld", "owm_daily_cap", "Daily call cap", "int:0:100000",
     "Stop billed One Call requests after this many in a UTC day. 1,000 a day are free; 0 turns One Call off."),
    ("fld", "owm_base_url", "API address", "url:" + DEFAULT_OWM_BASE,
     "Change only if OpenWeather moves its API. Clear the field to restore the default."),
    ("sec", "National Weather Service"),
    ("fld", "nws_user_agent", "User-Agent", "text",
     "Optional. Sent with every NWS request; the NWS asks every app to identify itself. Left empty, skydrop "
     "sends a generic one. Put your own, such as skydrop (you@example.com), so the NWS can reach you."),
    ("fld", "nws_api_key", "API key", "secret", "Sent as the API-Key header. The NWS API stays free."),
    ("fld", "nws_shared_secret", "Shared secret", "secret", "Saved for later. NWS doesn't use one today."),
    ("fld", "nws_feature_flags", "Feature flags", "flags",
     "Sent as the Feature-Flags header, comma-separated."),
    ("fld", "nws_base_url", "API address", "url:" + DEFAULT_NWS_BASE,
     "Change only if NWS moves its API. Clear the field to restore the default."),
    ("sec", "Data sources"),
] + [("src", s.id) for s in S.SOURCES] + [
    ("btn", "restore", "Restore deleted sources", None,
     "Bring back every data source you deleted. Key: R, from anywhere on this screen."),
    ("btn", "save", "Save and refresh", None,
     "Save these values and pull fresh data with them. Key: S or Ctrl-S, from anywhere on this screen."),
    ("btn", "test", "Test connections", None,
     "Check every enabled source with your current values. Key: T, from anywhere on this screen."),
    ("btn", "revert", "Discard changes", None,
     "Put back the saved values. Key: D twice, from anywhere on this screen."),
]
# CRT rows apply (and save) as they change; the effects themselves are on the CRT effects card
CRT_ROWS = [
    ("crt", "meter_style", "Meters", "choice",
     "How every meter in skydrop is drawn - the 10-day temperature bars, the column charts (the next 12 hours, "
     "Air & UV, a station's last day, the month's highs) and the CRT effects card: LED segments, "
     "like an old stereo's level meter, a tall lit segment and a dark gap; LED dots, a row of small square lamps; "
     "bricks, blocks with a sliver of gap after and above each; raised blocks, each with a darker edge down its "
     "right side and along its foot; one solid bar; or shaded blocks, a 1980s terminal's full blocks shading off "
     "at the tip █▓▒░. Only the lit part of a meter is ever drawn. ← → shows each at once; the CRT effects card "
     "(c) has it too."),
    ("crt", "meter_blocks", "Meter blocks", "choice",
     "LED segments, LED dots, bricks and raised blocks are drawn with characters from Unicode's Symbols for "
     "Legacy Computing, which kitty, Ghostty, WezTerm, foot and the VTE terminals (GNOME Terminal, Tilix) draw "
     "themselves; anywhere else they need a font that has them. auto uses them in those terminals and plainer "
     "blocks (▌ ▖ ▊ ▕) everywhere else; legacy blocks uses them anyway; plain blocks never does. Solid bar "
     "and shaded blocks look the same either way."),
    ("crt", "crt", "CRT effect", "switch",
     "An old phosphor screen. In any color terminal: the phosphor's colors (CRT phosphor) on raised blacks, a "
     "power-on sweep and a self test (POST) before the first screen, text typed out as down a slow line, trails "
     "where text moves away, burn-in of the borders shown longest, a row now and then slipping out of sync, alerts "
     "that blink and numbers that flash as fresh data changes them. Where the terminal shows pictures (kitty, "
     "Ghostty) the glass too: scanlines, the aperture grille, a glow round bright text with red and blue fringes, "
     "the vignette, grain, flicker, a slow hum bar, the tube's curve, a glare on its glass and a bezel round it. A "
     "bitmap-style font finishes it: the CRT effects card (c) looks for one and copies the line that sets it in the "
     "terminal."),
    ("crt", "crt_phosphor", "CRT phosphor", "choice",
     "The tube's phosphor in CRT mode: green (P1, the classic terminal), amber (P3), white (P4) - everything in "
     "that one color, brighter or dimmer - or color, the theme's colors as they are, on the raised black of a lit "
     "tube."),
    ("crt", "crt_font", "CRT font", "font",
     C.FONT_HELP + " The CRT effects card (c) has it too, and says there whether the font is installed and what "
     "the terminal is set to."),
    ("crt", "crt_font_size", "CRT font size", "fontsize", C.FONT_SIZE_HELP),
    ("crt", "crt_card", "CRT effects", "card",
     "Every CRT effect on one card, each from 0 (off) to 10 - 5 is the standard: raised blacks, the power-on "
     "sweep, persistence, burn-in, sync slips, the typewriter, the POST screen, blinking alerts and the update "
     "flash in any color terminal; scanlines, the aperture grille, bloom, soft focus, color fringes, the vignette, "
     "grain, flicker, the hum bar, curvature, the glare and the bezel on the glass (kitty, Ghostty). At the top: "
     "CRT mode itself, the phosphor, a bitmap-style font for the terminal and the meters' look. Changes show at "
     "once and are saved as you go. enter or c opens the card (c from anywhere on this screen) · R every effect "
     "back to the standard."),
]
_at = next(i for i, it in enumerate(FIELDS) if it[0] == "fld" and it[1] == "color_mode") + 1
FIELDS[_at:_at] = CRT_ROWS

FIELD_BY_KEY = {it[1]: it for it in FIELDS if it[0] == "fld"}
# Save, Test and Discard sit under every section, always in view
GLOBAL_BUTTONS = ("save", "test", "revert")
# each row's section (the section titles and those buttons have none)
SECTION_OF = {}
for _it in FIELDS:
    if _it[0] == "sec":
        _sec = _it[1]
    elif not (_it[0] == "btn" and _it[1] in GLOBAL_BUTTONS):
        SECTION_OF[_it] = _sec
SECTION_HELP = {
    "Location": "Where the weather is for: the ZIP code or a place, a display name, coordinates, the "
                "country and the observation station.",
    "Display": "How skydrop looks: units, theme, background, corners, graphics and colors; the meters, the CRT "
               "effect, its phosphor, font and font size; the font of skydrop's own window; the ticker, clock, "
               "mouse, refresh, and how much the Hourly and 10-Day screens show.",
    "Tornado alarm": "The alarm when a tornado warning covers your ZIP code, its sound and repeats, the sounds "
                     "for other warnings, and the watch that keeps listening after skydrop closes.",
    "Warnings crawl": "The crawl of watches, warnings and storm reports along the bottom: what it covers and "
                      "how fast it moves.",
    "Radar": "The radar's source, site, product and panes; smoothing, storm tracks, placefiles and your map "
             "markers; the map's width, background and labels; and the loop.",
    "Station feed": "How far around the ZIP code live stations are gathered from, and how many.",
    "Voice and sound": "The speech engine, Piper voice and speaking rate, the audio player, and narration for "
                       "Local on the 8s.",
    "OpenWeather (optional)": "Your own OpenWeather key, the One Call version, a daily cap on billed calls and "
                              "the API address.",
    "National Weather Service": "The User-Agent and key sent with NWS requests, feature flags and the API "
                                "address.",
    "Data sources": "Every data source skydrop reads, each on or off, with its address; deleted ones can be "
                    "restored here.",
}
LOCATION_KEYS = {"zip", "lat", "lon", "nws_station", "country"}
CREDENTIAL_KEYS = {"owm_api_key", "nws_user_agent", "nws_api_key", "owm_daily_cap", "owm_onecall_version",
                   "owm_base_url", "nws_base_url", "nws_feature_flags"}
DISPLAY_KEYS = {"theme", "graphics", "color_mode", "mouse", "background", "rounded_borders"}
RADAR_KEYS = {"radar_source", "radar_site", "radar_product", "radar_panes", "radar_smooth", "radar_cells",
              "radar_labels", "radar_stations", "radar_warnings", "radar_layer", "radar_span"}
PLACE_RE = re.compile(r"[^\W\d_][\w .'-]*(,\s*[^\W\d_][\w .'-]*){0,2}")  # any alphabet: Zürich, Montréal
BUTTON_ROWS = [("test_alarm",), ("voice_dl", "voice_test"), ("restore",), ("save", "test", "revert")]
# keyboard shortcuts for the buttons (shown on them), and the keys that also work
BUTTON_KEYS = {"save": "S", "test": "T", "revert": "D", "restore": "R", "test_alarm": "A"}
SHORTCUTS = {"S": "save", "\x13": "save", "T": "test", "t": "test", "D": "revert", "R": "restore",
             "A": "test_alarm"}
KEY_LEGEND = [("↑↓", "move"), ("Enter", "open · edit"), ("←→", "change"), ("PgUp PgDn", "sections"),
              ("+ -", "open or close all"), ("/", "find"), ("S", "save"), ("T", "test"), ("D", "discard"),
              ("R", "restore sources"), ("A", "test alarm"), ("v", "show keys"), ("x", "delete source"),
              ("e", "edit address"), ("Esc", "close · cancel")]


def nice_steps(lo, hi):
    """Round values between lo and hi for stepping a number with the arrow keys."""
    vals = {lo, hi}
    for e in range(0, 7):
        for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 7.5, 8):
            v = m * 10 ** e
            if v == int(v) and lo <= v <= hi:
                vals.add(int(v))
    return sorted(vals)


def active_errors_panel(F, width):
    """Every problem since skydrop started that hasn't cleared yet."""
    items = LOG.active()
    out = [ui.section("Active errors", width, f"{len(items)} now" if items else "")]
    if not items:
        out.append([("  ✓ ", "good"), ("None right now.", "good")])
    for it in items:
        out.append(clip([("  ✗ ", "bad"), (it["label"], "bad")], width))
        out += wrap(it["msg"], width, "warn", 4)
        when = f"since {F.time(it['first'])}"
        if it["count"] > 1:
            when += f" · {it['count']} times · last {F.time(it['last'])}"
        out.append(clip([("    " + when, "dim")], width))
    note = f"skydrop started at {F.time(LOG.started)}"
    if LOG.cleared:
        note += f"; {LOG.cleared} error{'s' if LOG.cleared != 1 else ''} cleared since then"
    out += wrap(note + ". An error leaves this list as soon as that part works again.", width, "faint", 2)
    return out


def parse_field(item, raw):
    key, label, typ = item[1], item[2], item[3]
    raw = (raw or "").strip()
    if typ in ("text", "secret"):
        return raw, None
    if typ == "zip":
        mt = re.fullmatch(r"(\d{5})(?:-?\d{4})?", raw)
        if mt:
            return mt.group(1), None
        if not raw:
            return "", None
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]{1,9}", raw) and any(c.isdigit() for c in raw):
            return raw.upper(), None  # postal codes outside the U.S. (K1A 0B1, SW1A 1AA, 2000)
        if len(raw) <= 60 and PLACE_RE.fullmatch(raw):
            return ", ".join(p.strip() for p in raw.split(",")), None
        return None, "Enter a ZIP or postal code, or a place such as Memphis, TN."
    if typ == "country":
        val = raw.upper() or "US"
        if not re.fullmatch(r"[A-Z]{2}", val):
            return None, "Use a two-letter country code, such as US."
        return val, None
    if typ == "flags":
        flags = [f.strip() for f in raw.split(",") if f.strip()]
        if any(not re.fullmatch(r"[A-Za-z0-9_]+", f) for f in flags):
            return None, "Feature flags are words made of letters, digits, and underscores, separated by commas."
        return ",".join(flags), None
    if typ.startswith("url:"):
        default = typ.split(":", 1)[1]
        if not raw:
            return default, None
        val = raw.rstrip("/")
        if not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", val):
            return None, f"{label} must start with https:// (or clear it to use {default})."
        return val, None
    if typ == "station":
        val = raw.upper()
        if val and not re.fullmatch(r"[A-Z0-9]{3,9}", val):
            return None, "Station IDs are 3 to 9 letters or digits, like KDAL."
        return val, None
    if typ in ("lat", "lon"):
        if not raw:
            return "", None
        lim = 90 if typ == "lat" else 180
        val = num(raw)
        if val is None or abs(val) > lim:
            return None, f"{label} must be a number from -{lim} to {lim}."
        return val, None
    if typ.startswith("int:"):
        _, lo, hi = typ.split(":")
        lo, hi = int(lo), int(hi)
        val = int(raw) if re.fullmatch(r"\d+", raw) else None
        if val is None or not lo <= val <= hi:
            return None, f"{label} must be a whole number from {lo} to {hi}."
        return val, None
    if typ == "color":
        val = background_of(raw)
        if val is None:
            return None, "Type a color as #RRGGBB (or theme, or terminal)."
        return val, None
    if typ.startswith("choice:"):
        choices = typ.split(":", 1)[1].split(",")
        val = raw if raw in choices else choices[0]
        if key in ("radar_span", "radar_step", "radar_panes", "radar_min_dbz", "radar_opacity"):
            return int(val), None
        return val, None
    if typ == "radarsite":
        val = raw.upper() or "AUTO"
        if val == "AUTO":
            return "auto", None
        if not re.fullmatch(r"[KTPR][A-Z0-9]{3}", val):
            return None, "Radar IDs are 4 characters, such as KFWS (or type auto)."
        return val, None
    if typ == "products":
        from .nexrad import PRODUCT_BY_KEY
        keys = [k.strip().lower() for k in re.split(r"[,\s]+", raw) if k.strip()]
        bad = [k for k in keys if k not in PRODUCT_BY_KEY]
        if bad:
            return None, f"Unknown product {bad[0]}; use: {', '.join(PRODUCT_BY_KEY)}."
        return ",".join(keys) or "ref,vel,cc,zdr", None
    return raw, None


UNITS = {"radar_opacity": "%", "radar_min_dbz": " dBZ", "radar_span": " mi across", "radar_step": " min apart",
         "radar_frames": " scans", "radar_speed": " frames/s", "hourly_hours": " hours", "daily_days": " days",
         "station_radius": " mi", "station_max": " stations", "local8s_seconds": " s a page",
         "ticker_area": " mi"}


def with_unit(key, val):
    """A setting's value with its unit, for display ('90%', '120 mi across')."""
    text = str(val)
    unit = UNITS.get(key)
    if unit and text.lstrip("-").isdigit():
        return text + unit
    return text


def control_segs(val, focused, ascii_only=False):
    """An on/off setting drawn as a switch: ■ on (bright) or □ off (dim); highlighted when focused."""
    is_on = str(val).lower() == "on"
    if focused:
        return [((("[x] " if ascii_only else "■ ") if is_on else ("[ ] " if ascii_only else "□ ")) + str(val) + " ",
                 "fieldon")]
    if is_on:
        return [("[x]" if ascii_only else "■", "tgonmk"), (" on", "tgon")]
    return [("[ ]" if ascii_only else "□", "tgoffmk"), (" off", "tgoff")]


def as_text(val):
    return "" if val is None else str(val)


def mask(val):
    if not val:
        return ""
    if len(val) <= 8:
        return "•" * len(val)
    return val[:4] + "•" * min(len(val) - 8, 16) + val[-4:]


class Settings:
    def __init__(self, app):
        self.app = app
        self.focus = 0
        self.editing = False
        self.quick = False
        self.edit_key_name = None
        self.buf = ""
        self.pos = 0
        self.show = False
        self.message = ("", "text")
        self.results = []
        self.testing = False
        self.version = 0
        self.draft = {}
        self.items = []           # the rows ↑ ↓ move through, section titles among them
        self.sec_of = []          # section number of each item (None: the buttons at the foot)
        self.sections = []
        self.open = set()         # the sections shown open; they all start closed
        self.find_opened = set()  # sections "/" opened to show a match
        self.quick_opened = None  # the section z opened for a quick ZIP code
        self.hits = []            # (line, x0, x1, item index) as last drawn, for clicks
        self.pos_of = {}          # item index -> (column, row) as last drawn, for ← → between columns
        self.finding = False      # "/" find a setting by name
        self.find_buf = ""
        self.find_from = None
        self.armed = None         # (action, time): a second press confirms
        self.card = None          # the CRT effects card, while it's open
        self.crt_prev = {}        # effect -> its level before Enter turned it off
        self.sync()

    # -- model -------------------------------------------------------------------
    def build_items(self, keep=None):
        """The rows ↑ ↓ move through: every section's title, the settings of the open
        sections under theirs, then Save, Test and Discard. The focus stays on the row it
        was on (or `keep`), or goes to the title of the section that row closed into."""
        if keep is None and self.items:
            keep = self.items[max(0, min(self.focus, len(self.items) - 1))]
        removed = set(self.draft.get("__removed__", "").split(",")) - {""}
        self.items, self.sec_of, self.sections = [], [], []
        tail = []
        for it in FIELDS:
            if it[0] == "sec":
                self.sections.append(it[1])
                self.items.append(it)
                self.sec_of.append(len(self.sections) - 1)
            elif it[0] == "btn" and it[1] in GLOBAL_BUTTONS:
                tail.append(it)
            elif self.sections and self.sections[-1] in self.open and not (it[0] == "src" and it[1] in removed):
                self.items.append(it)
                self.sec_of.append(len(self.sections) - 1)
        for it in tail:
            self.items.append(it)
            self.sec_of.append(None)
        if keep is not None:
            if keep in self.items:
                self.focus = self.items.index(keep)
            elif SECTION_OF.get(keep) and ("sec", SECTION_OF[keep]) in self.items:
                self.focus = self.items.index(("sec", SECTION_OF[keep]))
        self.focus = max(0, min(self.focus, len(self.items) - 1))

    def toggle_section(self, title, want=None):
        """Open or close a section (want: True, False, or None to flip it); the focus goes to
        its title when its rows close. True when that changed anything."""
        is_open = title in self.open
        want = (not is_open) if want is None else want
        if want == is_open:
            return False
        if want:
            self.open.add(title)
        else:
            self.open.discard(title)
        here = self.items[self.focus] if self.items else None
        self.build_items(keep=here if want or SECTION_OF.get(here) != title else ("sec", title))
        return True

    def set_all(self, want):
        """Open every section, or close them all."""
        here = self.items[self.focus] if self.items else None
        self.open = set(self.sections) if want else set()
        self.build_items(keep=here if want or SECTION_OF.get(here) is None else ("sec", SECTION_OF[here]))

    def reveal(self, it):
        """Open the section `it` is in (if it's closed) and put the focus on it."""
        sec = SECTION_OF.get(it)
        if sec and sec not in self.open:
            self.open.add(sec)
        self.build_items(keep=it)
        return it in self.items

    def bump(self):
        self.version += 1

    def say(self, text, style="text"):
        self.message = (text, style)
        self.bump()

    def sync(self):
        cfg = self.app.cfg
        self.draft = {it[1]: as_text(cfg.get(it[1], DEFAULTS.get(it[1]))) for it in FIELDS if it[0] == "fld"}
        for s in S.SOURCES:
            self.draft[f"src:{s.id}:on"] = "on" if S.enabled_flag(cfg, s.id) else "off"
            self.draft[f"src:{s.id}:url"] = S.url(cfg, s.id)
        self.draft["__removed__"] = ",".join(cfg.get("sources_removed") or [])
        self.build_items()

    def saved_view(self):
        cfg = self.app.cfg
        d = {it[1]: as_text(cfg.get(it[1], DEFAULTS.get(it[1]))) for it in FIELDS if it[0] == "fld"}
        for s in S.SOURCES:
            d[f"src:{s.id}:on"] = "on" if S.enabled_flag(cfg, s.id) else "off"
            d[f"src:{s.id}:url"] = S.url(cfg, s.id)
        d["__removed__"] = ",".join(cfg.get("sources_removed") or [])
        return d

    def adopt(self, *keys):
        """A setting was changed outside this screen (a hotkey): show the new value
        without throwing away other unsaved edits."""
        cfg = self.app.cfg
        for k in keys:
            self.draft[k] = as_text(cfg.get(k, DEFAULTS.get(k)))
        self.bump()

    def dirty(self):
        saved = self.saved_view()
        return {k for k, val in self.draft.items() if val != saved.get(k)}

    def on_show(self):
        if not self.dirty() and not self.editing:
            self.sync()

    def focus_key(self, key):
        """Put the focus on a setting, opening its section to show it."""
        it = next((x for x in FIELDS if x[0] != "sec" and x[1] == key), None)
        if it is None or not self.reveal(it):
            return None
        return it

    def begin_edit(self, key=None, quick=False, url_of=None):
        if key:
            sec = SECTION_OF.get(FIELD_BY_KEY.get(key))
            self.quick_opened = sec if quick and sec and sec not in self.open else None
            self.focus_key(key)
        it = self.items[self.focus]
        self.editing = True
        self.quick = quick
        if url_of:
            self.edit_key_name = f"src:{url_of}:url"
        else:
            self.edit_key_name = it[1]
        self.buf = "" if quick else self.draft.get(self.edit_key_name, "")
        self.pos = len(self.buf)
        if quick:
            self.say("Type a ZIP code (or a place such as Memphis, TN) and press Enter. Esc cancels.", "accent")
        self.bump()

    def close_quick(self):
        """Close the section a quick ZIP code (z) opened, as it was before."""
        if self.quick_opened:
            self.toggle_section(self.quick_opened, False)
        self.quick_opened = None

    # -- keys ---------------------------------------------------------------------
    @property
    def capturing(self):
        """Typing goes to this screen (a field being edited, or the find prompt)."""
        return self.editing or self.finding

    def label_of(self, it):
        if it[0] == "sec":
            return it[1]
        return S.SOURCE_BY_ID[it[1]].name if it[0] == "src" else it[2]

    def section_step(self, step):
        """PgDn: the next section's title; PgUp: this section's title, then the one before."""
        titles = [i for i, it in enumerate(self.items) if it[0] == "sec"]
        if step > 0:
            self.focus = next((i for i in titles if i > self.focus), len(self.items) - 1)
        else:
            self.focus = next((i for i in reversed(titles) if i < self.focus), 0)

    def column_step(self, step):
        """Move to the setting beside this one in the next column over (as drawn)."""
        here = self.pos_of.get(self.focus)
        if not here:
            return
        col, row = here
        best = None
        for i, (c, r) in self.pos_of.items():
            if c == col + step:
                d = (abs(r - row), r)
                if best is None or d < best[0]:
                    best = (d, i)
        if best:
            self.focus = best[1]

    def step_number(self, it, step):
        lo, hi = (int(x) for x in it[3].split(":")[1:3])
        cur = num(self.draft.get(it[1]))
        cur = int(cur) if cur is not None else lo
        vals = nice_steps(lo, hi)
        if step > 0:
            nxt = next((v for v in vals if v > cur), hi)
        else:
            nxt = next((v for v in reversed(vals) if v < cur), lo)
        self.draft[it[1]] = str(nxt)

    def shortcut(self, action):
        if action == "revert":
            n = len(self.dirty())
            if not n:
                self.say("No unsaved changes to discard.", "dim")
                return
            if not (self.armed and self.armed[0] == "revert" and time.time() - self.armed[1] < 5):
                self.armed = ("revert", time.time())
                self.say(f"Press D again to discard {n} unsaved change{'s' if n != 1 else ''}.", "warn")
                return
        self.armed = None
        if action == "restore" and not self.draft.get("__removed__"):
            self.say("No deleted sources to restore.", "dim")
            return
        {"save": self.save, "test": self.start_test, "revert": self.revert, "restore": self.restore,
         "test_alarm": self.test_alarm}[action]()

    def start_find(self):
        self.finding, self.find_buf = True, ""
        self.find_from = self.items[self.focus] if self.items else None
        self.find_opened = set()
        self.show_find()

    def show_find(self, note=""):
        self.say(f"Find a setting: {self.find_buf}▏   Enter stays here, Esc goes back, ↓ ↑ next and previous "
                 f"match{note}", "accent")

    def find_matches(self):
        """Every row whose name has the text in it (closed sections too), then the rows
        whose key or section does."""
        q = self.find_buf.strip().lower()
        if not q:
            return []
        removed = set(self.draft.get("__removed__", "").split(",")) - {""}
        pool = [it for it in FIELDS if not (it[0] == "src" and it[1] in removed)]
        hits = [it for it in pool if q in self.label_of(it).lower()]
        more = [it for it in pool if it not in hits and it[0] != "sec" and
                (q in it[1].lower() or q in (SECTION_OF.get(it) or "").lower())]
        return hits + more

    def find_show(self, it):
        """Focus a match, opening its section for it; a section opened for an earlier match
        closes again."""
        sec = SECTION_OF.get(it)
        for other in list(self.find_opened):
            if other != sec:
                self.open.discard(other)
                self.find_opened.discard(other)
        if sec and sec not in self.open:
            self.open.add(sec)
            self.find_opened.add(sec)
        self.build_items(keep=it)

    def find_key(self, k):
        if k == KEY_ENTER:
            self.finding = False
            self.find_opened = set()
            self.say(f"{self.label_of(self.items[self.focus])}.", "dim")
        elif k == "\x1b":
            self.finding = False
            self.open -= self.find_opened
            self.find_opened = set()
            self.build_items(keep=self.find_from)
            self.say("", "text")
        elif k in (KEY_DOWN, KEY_UP, "\t", KEY_BTAB):
            hits = self.find_matches()
            if hits:
                here = self.items[self.focus] if self.items else None
                at = hits.index(here) if here in hits else -1
                self.find_show(hits[(at + (1 if k in (KEY_DOWN, "\t") else -1)) % len(hits)])
            self.show_find(f"   ({len(hits)} found)" if hits else "")
            return
        else:
            if k == KEY_BACKSPACE:
                self.find_buf = self.find_buf[:-1]
            elif isinstance(k, Paste):
                self.find_buf += "".join(ch for ch in k.text if ch.isprintable())
            elif isinstance(k, str) and len(k) == 1 and k.isprintable():
                self.find_buf += k
            hits = self.find_matches()
            if hits:
                self.find_show(hits[0])
            self.show_find(f"   ({len(hits)} found)" if hits else "   (no match)" if self.find_buf.strip() else "")
        self.bump()

    def click(self, line, x):
        """A click on the list: a section's title opens or closes it, a button presses it,
        any other row takes the focus. False: nothing there."""
        for row, x0, x1, i in self.hits:
            if row == line and x0 <= x < x1 and i < len(self.items):
                self.focus = i
                it = self.items[i]
                if it[0] == "sec":
                    self.toggle_section(it[1])
                elif it[0] == "btn":
                    self.activate()
                self.bump()
                return True
        return False

    def nav_key(self, k):
        it = self.items[self.focus]
        if it[0] == "sec" and k in (KEY_ENTER, " ", KEY_LEFT, KEY_RIGHT, "h", "l"):
            # a section's title: Enter opens or closes it, → opens it, ← closes it
            want = None if k in (KEY_ENTER, " ") else k in (KEY_RIGHT, "l")
            if not self.toggle_section(it[1], want) and want and self.focus + 1 < len(self.items) and \
                    SECTION_OF.get(self.items[self.focus + 1]) == it[1]:
                self.focus += 1                    # → on an open section: into it
        elif k in ("+", "="):
            self.set_all(True)
        elif k in ("-", "_"):
            self.set_all(False)
        elif k == "\x1b":
            sec = SECTION_OF.get(it) or (it[1] if it[0] == "sec" else None)
            if not (sec and self.toggle_section(sec, False)):
                return False
        elif k in (KEY_DOWN, "j"):
            self.focus = min(len(self.items) - 1, self.focus + 1)
        elif k in (KEY_UP, "k"):
            self.focus = max(0, self.focus - 1)
        elif k == KEY_NPAGE:
            self.section_step(1)
        elif k == KEY_PPAGE:
            self.section_step(-1)
        elif k in (KEY_HOME, "g"):
            self.focus = 0
        elif k in (KEY_END, "G"):
            self.focus = len(self.items) - 1
        elif k == "/":
            self.start_find()
        elif k in (KEY_SLEFT, KEY_SRIGHT, "<", ">"):
            self.column_step(1 if k in (KEY_SRIGHT, ">") else -1)
        elif it[0] == "crt" and k in (KEY_ENTER, " ", KEY_LEFT, KEY_RIGHT, "h", "l"):
            self.crt_adjust(it, k)
        elif k in (KEY_ENTER, " "):
            self.activate()
        elif k == "v":
            self.show = not self.show
        elif k in SHORTCUTS:
            self.shortcut(SHORTCUTS[k])
        elif it[0] == "src" and k in ("x", KEY_DC):
            removed = [x for x in self.draft["__removed__"].split(",") if x]
            removed.append(it[1])
            self.draft["__removed__"] = ",".join(removed)
            nxt = self.items[self.focus + 1] if self.focus + 1 < len(self.items) else self.items[self.focus - 1]
            self.build_items(keep=nxt)
            self.say(f"{S.SOURCE_BY_ID[it[1]].name} will be deleted when you save (Restore deleted sources "
                     "brings it back).", "warn")
        elif it[0] == "src" and k == "e":
            self.begin_edit(url_of=it[1])
        elif k in (KEY_LEFT, KEY_RIGHT, "h", "l"):
            # ← → change what can be changed; anywhere else they move between columns
            # (they never leave the Settings screen: use Tab or the number keys for that)
            step = 1 if k in (KEY_RIGHT, "l") else -1
            if it[0] == "src":
                key = f"src:{it[1]}:on"
                self.draft[key] = "off" if self.draft.get(key) == "on" else "on"
            elif it[0] == "fld" and it[3].startswith("choice:"):
                self.cycle(it, step)
            elif it[0] == "fld" and it[3] == "color":
                self.cycle_color(it, step)
            elif it[0] == "fld" and it[3].startswith("int:"):
                self.step_number(it, step)
            elif it[0] == "btn" and 0 <= self.focus + step < len(self.items) and \
                    self.items[self.focus + step][0] == "btn" and \
                    self.pos_of.get(self.focus + step) == self.pos_of.get(self.focus):
                self.focus += step                 # along a row of buttons
            else:
                self.column_step(step)
        else:
            return False
        self.bump()
        return True

    # -- CRT rows: they apply (and save) as they change -------------------------------------
    def crt_row(self, it, on, lw, width=200):
        app = self.app
        cfg = app.cfg
        key, kind = it[1], it[3]
        prefix = "  " + ("▸ " if on else "  ") + it[2].ljust(lw - 1) + "  "
        dim, val = ("fieldon", "fieldon") if on else ("dim", "value")

        def chips(text):
            return [("◂ ", dim), (text, val), (" ▸", dim)]

        if kind == "switch":
            segs = control_segs("on" if C.CRT_ON(cfg) else "off", on, app.ascii)
        elif key == "crt_phosphor":
            segs = chips(C.PHOSPHOR_LABEL[C.phosphor_of(cfg)])
        elif key == "meter_style":
            segs = chips(C.METER_LABEL[C.meter_style(cfg)]) + [("  ", "text")] + C.meter_block(5, "accent")
        elif key == "meter_blocks":
            pick = C.meter_blocks_of(cfg)
            name = C.METER_BLOCKS_LABEL[pick]
            if pick == "auto":
                name += ": legacy" if C.METER["blocks"] else ": plain"
            segs = chips(name) + [("  ", "text")] + C.meter_block(5, "accent")
        elif kind == "font":
            f = C.font_of(cfg)
            segs = chips(C.font_label(f))
            word = C.font_word(f)
            if word and len(prefix) + len(C.font_label(f)) + 4 + 3 + len(word) <= width:
                segs.append(("   " + word, "good" if word in ("in use", "installed") else "dim"))
        elif kind == "fontsize":
            f = C.font_of(cfg)
            size = C.font_size_of(cfg)
            segs = chips(C.size_label(size))
            if C.size_sharp(f, size):
                segs.append(("   sharp", "good"))
        else:                                               # the CRT effects row: a summary, enter opens the card
            text, changed = C.summary_changed(cfg)
            segs = [(text, "accent" if changed and not on else val)]
            hint = "   enter or c opens the card"
            if len(prefix) + len(text) + len(hint) <= width:
                segs.append((hint, "dim"))
        return [(prefix, "value" if on else "label")] + segs

    def crt_change(self, key, value, note=None):
        """A CRT setting changes: used at once and saved. True when it was saved."""
        cfg = self.app.cfg
        cfg[key] = value
        (cfg.get("_runonly") or {}).pop(key, None)        # a change made here is kept, --crt or not
        saved = True
        try:
            save_config(cfg)
        except OSError as e:
            self.say(f"Couldn't save: {e}", "bad")
            saved = False
        self.app.apply_crt()
        if note:
            self.say(note, "dim")
        self.bump()
        return saved

    def crt_set_level(self, fx, n):
        """Set one effect's level (0-10)."""
        levels = C.levels_of(self.app.cfg)
        n = max(0, min(C.LEVEL_TOP, int(n)))
        if n != levels[fx]:
            levels[fx] = n
            self.crt_change("crt_levels", C.levels_saved(levels))
        return n

    def crt_all_standard(self):
        self.crt_change("crt_levels", C.levels_saved({}), "CRT effects: every one back to the standard.")

    def crt_toggle(self):
        turn = not C.CRT_ON(self.app.cfg)
        if turn and self.app.color_mode not in ("truecolor", "256", "16"):
            self.say("CRT mode needs color: skydrop was started with --no-color.", "warn")
            return
        self.crt_change("crt", turn, "CRT effect on (any key skips the power-on)." if turn else "CRT effect off.")

    def crt_cycle(self, key, step, quiet=False):
        cfg = self.app.cfg
        if key == "crt_phosphor":
            names = list(C.ORDER)
            new = names[(names.index(C.phosphor_of(cfg)) + step) % len(names)]
            self.crt_change(key, new, None if quiet else f"CRT phosphor: {C.PHOSPHOR_LABEL[new]}.")
        elif key == "meter_style":
            names = list(C.METER_ORDER)
            new = names[(names.index(C.meter_style(cfg)) + step) % len(names)]
            self.crt_change(key, new, None if quiet else f"Meters: {C.METER_LABEL[new]}.")
        elif key == "crt_font":
            names = C.FONT_KEYS
            new = names[(names.index(C.font_of(cfg)) + step) % len(names)]
            self.crt_change(key, new, None if quiet else f"CRT font: {C.font_label(new)}.")
            done = self.apply_font()
            if done and not quiet:
                self.say(done, "accent" if not done.startswith(("⚠", "skydrop can't")) else "warn")
            return done
        elif key == "meter_blocks":
            names = list(C.METER_BLOCKS)
            new = names[(names.index(C.meter_blocks_of(cfg)) + step) % len(names)]
            self.crt_change(key, new, None if quiet else f"Meter blocks: {C.METER_BLOCKS_LABEL[new]}.")

    def crt_adjust(self, it, k):
        key, kind = it[1], it[3]
        step = -1 if k in (KEY_LEFT, "h") else 1
        enter = k in (KEY_ENTER, " ")
        if kind == "switch":
            self.crt_toggle()
        elif kind == "card":
            if enter:
                self.open_card()
        elif kind == "font" and enter:
            self.font_enter()
        elif kind == "fontsize":
            msg = self.reset_font_size() if enter else self.step_font_size(step)
            if msg:
                self.say(msg, "warn" if msg.startswith(("⚠", "skydrop can't")) else "accent")
        else:
            self.crt_cycle(key, step)

    # -- the CRT font in the terminal itself (kitty: skydrop writes kitty.conf) ---------------
    def apply_font(self):
        """In kitty: the CRT font and its size into kitty.conf, and kitty reloads. What
        happened, for the status line (None: not kitty, nothing to say)."""
        from .termfont import KITTY
        cfg = self.app.cfg
        key = C.font_of(cfg)
        size = C.font_size_of(cfg)
        if C.terminal_kind() != "kitty":
            return None
        if key != "own" and C.font_installed(key, "kitty") is False:
            return (f"⚠ {C.font_label(key)} isn't installed, so skydrop left kitty's font alone "
                    "(kitty would only fall back to another font).")
        family = None if key == "own" else (C.font_installed(key, "kitty") or C.font_family(key, "kitty")[0])
        done, info = KITTY.apply(family, size)
        if not done:
            return f"skydrop can't set kitty's font: {info}." if info else None
        what = f"{family}, {C.size_label(size)}" if family else (
            "its own font" + (f", {C.size_label(size)}" if size else " and size"))
        return f"kitty: {what}, set in {info}."

    def font_enter(self):
        """Enter on the CRT font row: in kitty set the font again, elsewhere copy its lines."""
        if C.kitty_direct()[0]:
            msg = self.apply_font()
            if msg:
                self.say(msg, "accent" if not msg.startswith("⚠") else "warn")
            return msg
        return self.copy_font_line(C.font_of(self.app.cfg))

    def set_font_size(self, size):
        cfg = self.app.cfg
        key = C.font_of(cfg)
        self.crt_change("crt_font_sizes", C.font_sizes_saved(cfg, key, size))
        return self.apply_font() or f"CRT font size: {C.size_label(size)}."

    def step_font_size(self, step):
        """The CRT font a size up or down (the terminal's own steps from what kitty.conf sets)."""
        cfg = self.app.cfg
        key = C.font_of(cfg)
        size = C.font_size_of(cfg)
        start = 11.0
        if size is None and C.terminal_kind() == "kitty":
            from .termfont import kitty_conf_path, kitty_process, own_size
            start = own_size(kitty_conf_path(kitty_process()[0]))
        return self.set_font_size(C.step_size(key, size, step, start))

    def reset_font_size(self):
        """The size the font starts at (the terminal's own size, for its own font)."""
        return self.set_font_size(C.FONT_SIZES[C.font_of(self.app.cfg)][0])

    def open_card(self):
        from .crtcard import CrtCard
        self.card = CrtCard(self)
        self.bump()

    def copy_font_line(self, font):
        """The CRT font row's enter outside kitty: the lines that set the font (and its size)
        onto the clipboard (OSC 52). Returns what happened."""
        kind = C.terminal_kind()
        lines = C.font_lines(font, C.font_size_of(self.app.cfg, font), kind)
        if not lines:
            msg = "The terminal's own font: nothing to copy."
            self.say(msg, "dim")
            return msg
        try:
            self.app.term.write(C.osc52("\n".join(lines)))
            self.app.term.flush()
        except Exception:
            msg = "⚠ The terminal took no clipboard text."
            self.say(msg, "warn")
            return msg
        msg = (f"Copied: paste it into the terminal's config.   {'   '.join(lines)}" if kind else
               f"Copied the font's name: {lines[0]}")
        self.say(msg, "accent")
        return msg

    def font_status(self):
        """The CRT font row's help ends with what the card says about the font here."""
        cfg = self.app.cfg
        return C.font_advice(C.font_of(cfg), size=C.font_size_of(cfg))

    def crt_key(self, k):
        """CRT keys on the Settings list: c opens the CRT effects card from anywhere on this screen,
        R on the CRT effects row sets every effect back to 5. False: not ours, let the app have it."""
        if self.capturing or not self.items or not isinstance(k, str) or len(k) != 1:
            return False
        if k in ("c", "C"):
            self.open_card()
            return True
        it = self.items[self.focus]
        if it[0] == "crt" and it[3] == "card" and k in ("R", "r"):
            self.crt_all_standard()
            return True
        return False

    def cycle(self, it, step=1):
        choices = it[3].split(":", 1)[1].split(",")
        cur = self.draft.get(it[1])
        idx = choices.index(cur) if cur in choices else -1
        self.draft[it[1]] = choices[(idx + step) % len(choices)]

    def color_segs(self, val, on):
        """Background as the row shows it: ‹ its name › and a swatch of it."""
        dim, main = ("fieldon", "fieldon") if on else ("dim", "value")
        name = BACKGROUND_LABEL.get(val, val)
        segs = [("‹ ", dim), (name, main), (" ›", dim)]
        app = self.app
        theme = app.theme
        if val == "terminal":
            note = "  (the light theme keeps its own)" if theme.light else ""
            return segs + ([(note, "dim")] if note else [])
        rgb = theme.rgb("bg") if val == "theme" else hex_rgb(val)
        if theme.light and val != "theme":
            return segs + [("  (the light theme keeps its own)", "dim")]
        if app.ascii:
            return segs
        return segs + [("  ", "text"), ("    ", ("rgb", theme.rgb("text"), tuple(rgb), ""))]

    def cycle_color(self, it, step=1):
        """Background: the next color of the list (a color typed in goes on to the list's first or last)."""
        keys = [key for key, _label in BACKGROUNDS]
        cur = background_of(self.draft.get(it[1]))
        if cur in keys:
            new = keys[(keys.index(cur) + step) % len(keys)]
        else:
            new = keys[0 if step > 0 else -1]
        self.draft[it[1]] = new

    def activate(self):
        it = self.items[self.focus]
        if it[0] == "sec":
            self.toggle_section(it[1])
            return
        if it[0] == "btn":
            {"save": self.save, "test": self.start_test, "revert": self.revert, "restore": self.restore,
             "voice_dl": self.voice_dl, "voice_test": self.voice_test, "test_alarm": self.test_alarm}[it[1]]()
            return
        if it[0] == "src":
            key = f"src:{it[1]}:on"
            self.draft[key] = "off" if self.draft.get(key) == "on" else "on"
            return
        if it[3].startswith("choice:"):
            self.cycle(it, 1)
            return
        self.begin_edit()

    def edit_key(self, k):
        if self.finding:
            return self.find_key(k)
        if isinstance(k, Paste):
            text = "".join(ch for ch in k.text if ch.isprintable())
            self.buf = self.buf[:self.pos] + text + self.buf[self.pos:]
            self.pos += len(text)
        elif k == KEY_ENTER:
            self.commit()
        elif k == "\x1b":
            self.editing = False
            if self.quick:
                self.quick = False
                self.close_quick()
                self.app.back()
            self.say("Edit canceled.", "dim")
        elif k == KEY_BACKSPACE:
            if self.pos > 0:
                self.buf = self.buf[:self.pos - 1] + self.buf[self.pos:]
                self.pos -= 1
        elif k == KEY_DC:
            self.buf = self.buf[:self.pos] + self.buf[self.pos + 1:]
        elif k in (KEY_LEFT, KEY_SLEFT):
            self.pos = max(0, self.pos - 1)
        elif k in (KEY_RIGHT, KEY_SRIGHT):
            self.pos = min(len(self.buf), self.pos + 1)
        elif k in (KEY_HOME, "\x01"):
            self.pos = 0
        elif k in (KEY_END, "\x05"):
            self.pos = len(self.buf)
        elif k == "\x15":
            self.buf, self.pos = "", 0
        elif isinstance(k, str) and len(k) == 1 and k.isprintable():
            self.buf = self.buf[:self.pos] + k + self.buf[self.pos:]
            self.pos += 1
        self.bump()

    def commit(self):
        key = self.edit_key_name
        if key and key.startswith("src:"):
            val = self.buf.strip().rstrip("/")
            sid = key.split(":")[1]
            if val and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", val):
                self.say("Addresses must start with https:// (clear the field for the default).", "bad")
                return
            self.draft[key] = val or S.SOURCE_BY_ID[sid].url
            self.editing = False
            self.say(f"{S.SOURCE_BY_ID[sid].name} address changed. Choose Save and refresh to use it.", "accent")
            return
        it = FIELD_BY_KEY[key]
        val, err = parse_field(it, self.buf)
        if err:
            self.say(err, "bad")
            return
        self.draft[key] = as_text(val)
        self.editing = False
        if self.quick:
            self.quick = False
            if not val:
                self.say("Enter a ZIP code or a place.", "bad")
                return
            if self.save(only={"zip"}):
                self.close_quick()
                self.app.back()
            return
        self.say(f"{it[2]} changed. Choose Save and refresh to use it.", "accent")

    # -- actions ----------------------------------------------------------------------
    def save(self, only=None):
        app, cfg, store = self.app, self.app.cfg, self.app.store
        parsed = {}
        for it in FIELDS:
            if it[0] != "fld":
                continue
            if only is not None and it[1] not in only:
                parsed[it[1]] = cfg.get(it[1], DEFAULTS.get(it[1]))
                continue
            val, err = parse_field(it, self.draft.get(it[1], ""))
            if err:
                self.focus_key(it[1])
                self.say(err, "bad")
                return False
            parsed[it[1]] = val
        changed = {k for k, val in parsed.items() if val != cfg.get(k, DEFAULTS.get(k))}
        if "zip" in changed:
            for k, blank in (("lat", ""), ("lon", ""), ("nws_station", ""), ("location_name", "")):
                if k not in changed or only is not None:
                    parsed[k] = blank
            changed |= {"lat", "lon"}
        if parsed.get("nws_station") and "country" in changed and parsed.get("country") != "US":
            parsed["nws_station"] = ""
        if (parsed["lat"] == "") != (parsed["lon"] == ""):
            self.say("Enter both latitude and longitude, or leave both blank.", "bad")
            return False
        if parsed["lat"] == "" and not parsed["zip"]:
            self.focus_key("zip")
            self.say("Enter a ZIP code or place (or both coordinates).", "bad")
            return False
        src_changed = False
        with store.cfg_lock:
            cfg.update(parsed)
            for k in changed:  # a setting changed here is no longer a run-only override
                (cfg.get("_runonly") or {}).pop(k, None)
            if only is None:
                for s in S.SOURCES:
                    on = self.draft.get(f"src:{s.id}:on") == "on"
                    if on != S.enabled_flag(cfg, s.id):
                        S.set_enabled(cfg, s.id, on)
                        src_changed = True
                    url = self.draft.get(f"src:{s.id}:url") or s.url
                    if url != S.url(cfg, s.id):
                        S.set_url(cfg, s.id, url)
                        src_changed = True
                removed = [x for x in self.draft.get("__removed__", "").split(",") if x]
                if sorted(removed) != sorted(cfg.get("sources_removed") or []):
                    cfg["sources_removed"] = removed
                    src_changed = True
            if changed & LOCATION_KEYS:
                for k in ("_geo", "_nws_points", "_nws_station", "_rev", "_acis"):
                    cfg.pop(k, None)
        store.persist()
        if changed & CREDENTIAL_KEYS or src_changed:
            store.reset_probes()
        if changed & LOCATION_KEYS or src_changed:
            store.forget_all()
            app.sel.update({"hourly": 0, "daily": 0, "stations": 0})
            app.radar_state.update({"lat": None, "lon": None})
        store.http.clear()
        if app.radar is not None:
            app.radar.invalidate()
        if changed & RADAR_KEYS:
            app.radar_from_cfg()
        watch_msg = ""
        if "background_watch" in changed and store.persist_enabled:
            from . import watch
            err = watch.sync(cfg)
            on_ = str(cfg.get("background_watch")) == "on"
            watch_msg = f" {err}." if err else (" The background watch is on, and starts when you log in." if on_
                                               else " The background watch is off.")
        app.units_override = None
        unsaved = dict(self.draft)
        self.sync()
        if only is not None:
            for k, val in unsaved.items():
                if k not in only and k not in ("lat", "lon", "nws_station", "location_name"):
                    self.draft[k] = val
        if changed & DISPLAY_KEYS or "radar_span" in changed or "radar_layer" in changed:
            app.apply_display()
        app.request_refresh()
        if "zip" in changed:
            what = f"ZIP {parsed['zip']}" if str(parsed["zip"]).isdigit() else parsed["zip"]
            self.say(f"Saved. Pulling weather for {what}…{watch_msg}", "good")
        elif changed or src_changed:
            self.say(f"Saved. Pulling fresh data…{watch_msg}", "good")
        else:
            self.say("Nothing changed. Pulling fresh data anyway…", "good")
        return True

    def test_alarm(self):
        """Play the tornado alarm with the sound and voice settings on screen (saved or not)."""
        trial = dict(self.app.cfg)
        for k in ("tornado_alarm_sound", "tornado_alarm_voice", "voice_engine", "voice", "voice_rate", "audio_player"):
            val, err = parse_field(FIELD_BY_KEY[k], self.draft.get(k))
            if not err:
                trial[k] = val
        if self.app.test_alarm(trial):
            self.say("Playing the tornado alarm test… press s or Esc to stop the voice.", "good")
        else:
            self.say(no_player_hint(), "bad")

    def revert(self):
        self.sync()
        self.editing = False
        self.say("Changes discarded.", "dim")

    def restore(self):
        self.draft["__removed__"] = ""
        self.build_items()
        self.say("Deleted sources restored. Choose Save and refresh to keep this.", "accent")

    def voice_dl(self):
        name = self.draft.get("voice") or VOICE_CHOICES[0]
        sp = self.app.speech
        if sp.download_voice(name):
            self.say(f"Downloading {name}…", "accent")
        else:
            self.say(f"Couldn't start the download: {(sp.download or ('', 0, 0, 'busy'))[3]}", "bad")

    def voice_test(self):
        sp = self.app.speech
        trial = {}  # the voice settings on screen, saved or not
        for k in ("voice_engine", "voice", "voice_rate", "audio_player"):
            val, err = parse_field(FIELD_BY_KEY[k], self.draft.get(k))
            if not err:
                trial[k] = val
        ok = sp.speak("Voice test", "This is skydrop. Here is your local forecast: mostly sunny, with a high "
                                    "near 75 degrees and a light south wind.", overrides=trial)
        if not ok:
            self.say(f"Voice test failed: {sp.error}", "bad")
        else:
            self.say("Speaking… press s or Esc to stop.", "good")

    def start_test(self):
        if self.testing:
            return
        cfg = dict(self.app.cfg)
        for it in FIELDS:
            if it[0] == "fld":
                val, err = parse_field(it, self.draft.get(it[1], ""))
                if not err:
                    cfg[it[1]] = val
        srcs = {}
        for s in S.SOURCES:
            srcs[s.id] = {"enabled": self.draft.get(f"src:{s.id}:on") == "on"}
            if self.draft.get(f"src:{s.id}:url") and self.draft[f"src:{s.id}:url"] != s.url:
                srcs[s.id]["url"] = self.draft[f"src:{s.id}:url"]
        cfg["sources"] = srcs
        cfg["sources_removed"] = [x for x in self.draft.get("__removed__", "").split(",") if x]
        self.testing = True
        self.results = [("Testing", None, "checking every enabled source…")]
        self.bump()
        saved_loc = self.app.store.get("loc") or {}
        same_place = (cfg.get("zip") == self.app.cfg.get("zip") and cfg.get("country") == self.app.cfg.get("country"))

        def work():
            res = []
            try:
                lat, lon = num(cfg.get("lat")), num(cfg.get("lon"))
                if lat is None or lon is None:
                    if same_place and saved_loc:
                        lat, lon = saved_loc.get("lat"), saved_loc.get("lon")
                    else:
                        place = cfg.get("zip") or ""
                        try:
                            found = geocode(Http(timeout=10), cfg, place)
                            lat, lon = found["lat"], found["lon"]
                            where = ", ".join(str(x) for x in (found.get("name"), found.get("state")) if x)
                            res.append(("Location lookup", True, f"{place} is {where} ({lat:.3f}, {lon:.3f}) "
                                                                 f"via {found.get('how')}"))
                        except Exception as e:
                            lat, lon = FALLBACK_COORDS.get(place, (None, None))
                            res.append(("Location lookup", False, str(e)))
                if lat is None:
                    res.append(("Connections", False, "no coordinates to test with"))
                else:
                    res += check_connections(cfg, lat, lon, self.app.store.usage)
            except Exception as e:
                res.append(("Test", False, f"{type(e).__name__}: {e}"))
            self.results = res
            self.testing = False
            self.bump()
            self.app.term.wake()

        threading.Thread(target=work, daemon=True).start()

    # -- drawing -------------------------------------------------------------------------
    def title_row(self, it, width, dirty):
        """A section's title: ► closed, ▼ open, how many settings it holds while closed, and
        how many of them have unsaved changes."""
        title = it[1]
        is_open = title in self.open
        on = bool(self.items) and self.items[self.focus] == it
        arrow = ("v" if is_open else ">") if self.app.ascii else ("▼" if is_open else "►")
        rows = [x for x in FIELDS if SECTION_OF.get(x) == title and x[0] != "btn"]
        if on:
            line = [("  ", "text"), (f" {arrow} {title} ", "tabon")]
        else:
            line = [("  ", "text"), (f" {arrow} ", "dim"), (f"{title} ", "title")]
        if is_open:
            hints = {"Data sources": ("Enter or ← → on/off · x delete · e address",
                                      "← → on/off · x delete · e address", "← → on/off · x · e")}.get(title, ())
            room = width - len(title) - 9
            hint = next((h for h in hints if len(h) <= room), None)
            if hint:
                line.append(("  " + hint, "dim"))
        else:
            if title == "Data sources":
                removed = set(self.draft.get("__removed__", "").split(",")) - {""}
                srcs = [x for x in rows if x[0] == "src" and x[1] not in removed]
                on_n = sum(1 for x in srcs if self.draft.get(f"src:{x[1]}:on") == "on")
                line.append((f"  {len(srcs)} sources, {on_n} on", "dim"))
            else:
                line.append((f"  {len(rows)} setting{'s' if len(rows) != 1 else ''}", "dim"))
            changed = sum(1 for x in rows if (x[0] in ("fld", "crt") and x[1] in dirty) or
                          (x[0] == "src" and {f"src:{x[1]}:on", f"src:{x[1]}:url"} & dirty))
            if changed:
                line.append((f"  · {changed} unsaved", "accent"))
        return clip(line, width)

    def accordion(self, width):
        """Every section's title, the open sections' settings set in under theirs, and Save,
        Test and Discard at the foot. Returns (lines, cursor row, cursor column,
        [(item index, row)], [(row, x0, x1, item index)])."""
        lw = 18
        cw = width - 2                       # a section's rows are set in two cells
        vw = max(12, min(56, cw - lw - 8))
        dirty = self.dirty()
        index_of = {it: i for i, it in enumerate(self.items)}
        focus_it = self.items[self.focus] if self.items else None
        removed = set(self.draft.get("__removed__", "").split(",")) - {""}
        A, placed, hits = [], [], []
        cursor = ccol = None
        drawn_buttons = set()
        sec = None

        def mark(it, x0=0, x1=width):
            nonlocal cursor
            row = len(A) - 1
            i = index_of.get(it)
            if i is not None:
                placed.append((i, row))
                hits.append((row, x0, x1, i))
            if it == focus_it and cursor is None:
                cursor = row

        def add(line, it=None):
            A.append([("  ", "text")] + line)
            if it is not None:
                mark(it)

        def buttons(row, indent):
            nonlocal cursor
            A.append([])
            line, x = [(" " * indent, "text")], indent
            for act in row:
                b = next(x for x in FIELDS if x[0] == "btn" and x[1] == act)
                on = b == focus_it
                sc = BUTTON_KEYS.get(act)
                w = len(b[2]) + 2 + (len(sc) + 2 if sc else 0)
                if x > indent and x + w > width:          # wrap within the column
                    A.append(line)
                    line, x = [(" " * indent, "text")], indent
                r = len(A)
                if on:
                    cursor = r
                if b in index_of:
                    placed.append((index_of[b], r))
                    hits.append((r, x, x + w, index_of[b]))
                line += [(f" {b[2]} ", "tabon" if on else "btnact")]
                if sc:
                    line.append((f" {sc} ", "stkey"))          # its shortcut key
                line.append(("  ", "text"))
                x += w + 2
            A.append(line)

        for it in FIELDS:
            if it[0] == "sec":
                if sec in self.open:
                    A.append([])                 # a gap under an open section
                sec = it[1]
                A.append(self.title_row(it, width, dirty))
                mark(it)
                continue
            if it[0] == "btn" and it[1] in GLOBAL_BUTTONS or sec not in self.open:
                continue
            if it[0] == "crt":
                on = it == focus_it
                add(clip(self.crt_row(it, on, lw, cw), cw), it)
                continue
            if it[0] == "btn":
                row = next((r for r in BUTTON_ROWS if it[1] in r), (it[1],))
                if row not in drawn_buttons:
                    drawn_buttons.add(row)
                    buttons(row, 4)
                continue
            if it[0] == "src":
                if it[1] in removed:
                    continue
                src = S.SOURCE_BY_ID[it[1]]
                on = it == focus_it
                state = self.draft.get(f"src:{src.id}:on") == "on"
                url = self.draft.get(f"src:{src.id}:url") or src.url
                mark_ = "*" if f"src:{src.id}:on" in dirty or f"src:{src.id}:url" in dirty else " "
                prefix = "  " + ("▸ " if on else "  ")
                badge = ("■ on  " if not self.app.ascii else "[x] on ", "tgonmk") if state else \
                    ("□ off " if not self.app.ascii else "[ ] off", "tgoffmk")
                if on and self.editing and self.edit_key_name == f"src:{src.id}:url":
                    start = max(0, self.pos - vw + 1)
                    shown = self.buf[start:start + vw]
                    ccol = 2 + len(prefix) + len(badge[0]) + 1 + (self.pos - start)
                    add([(prefix, "value"), badge, (" ", "text"), (shown.ljust(vw), "fieldon")], it)
                else:
                    name = src.name[:max(10, cw - 14)]
                    keyless = src.id == "owm" and state and not str(self.draft.get("owm_api_key") or "").strip()
                    add(clip([(prefix, "value" if on else "label"), badge, (mark_, "accent"),
                              (name, "value" if on else "text")] +
                             ([("  idle: needs your key", "dim")] if keyless else []), cw), it)
                if on:
                    add(clip([("          ", "text"), (url, "dim")], cw))
                    for ln in wrap(src.provides, cw - 10, "dim", 0):
                        add([("          ", "text")] + ln)
                    for ln in wrap(f"Key: {src.auth}.  License: {src.license}.", cw - 10, "faint", 0):
                        add([("          ", "text")] + ln)
                continue
            key, label, typ = it[1], it[2], it[3]
            on = it == focus_it
            prefix = "  " + ("▸ " if on else "  ") + label.ljust(lw - 1) + ("*" if key in dirty else " ") + " "
            choice = typ.startswith("choice:")
            if typ == "color" and not (on and self.editing and self.edit_key_name == key):
                val = background_of(self.draft.get(key)) or "theme"
                add([(prefix, "value" if on else "label")] + self.color_segs(val, on), it)
                continue
            if on and self.editing and self.edit_key_name == key:
                start = max(0, self.pos - vw + 1)
                shown = self.buf[start:start + vw]
                ccol = 2 + len(prefix) + (self.pos - start)
                val_seg = (shown.ljust(vw), "fieldon")
            elif choice:
                # a choice cycles with Enter or ← →: a switch (■ on, □ off) or ‹ value ›, never a box
                val = str(self.draft.get(key, ""))
                if typ == ONOFF:
                    segs = control_segs(val, on, self.app.ascii)
                else:
                    segs = [("‹ ", "dim" if not on else "fieldon"), (with_unit(key, val), "value" if not on else "fieldon"),
                            (" ›", "dim" if not on else "fieldon")]
                add([(prefix, "value" if on else "label")] + segs, it)
                continue
            else:
                val = self.draft.get(key, "")
                shown = mask(val) if typ == "secret" and not self.show else val
                if key == "refresh_seconds" and val:
                    shown = f"{val} s" if int(num(val) or 0) < 120 else f"{val} s ({int(num(val)) // 60} min)"
                elif key == "tornado_alarm_repeat" and val is not None and val != "":
                    shown = "play once" if str(val) == "0" else f"{val} min"
                elif key in ("station_radius",) and val:
                    shown = f"{val} mi"
                elif key == "voice_rate" and val:
                    shown = f"{val}%"
                elif key in UNITS and val not in ("", None):
                    shown = with_unit(key, val)
                elif not val:
                    shown = {"lat": "(from ZIP code)", "lon": "(from ZIP code)", "nws_station": "(nearest)",
                             "location_name": "(from NWS)", "nws_feature_flags": "(none)",
                             "nws_user_agent": "(not set: a generic skydrop one is sent)",
                             "placefiles": "(none yet: paste placefile addresses here)",
                             "map_markers": "(none yet: Name: lat, lon; ...)"}.get(
                        key, "(not set)" if typ == "secret" else "")
                if key == "voice":
                    shown += "  ✓ downloaded" if os.path.exists(voice_path(val)) else "  (not downloaded)"
                placeholder = not val and shown.startswith("(")
                val_seg = (shown[:vw + 18].ljust(vw), "fieldon" if on else "inputdim" if placeholder else "input")
                if typ.startswith("int:"):
                    # a number steps with ← → (Enter types one): drawn like a choice, ‹ value ›
                    add([(prefix, "value" if on else "label"), ("‹ ", "dim" if not on else "fieldon"),
                         (shown, "value" if not on else "fieldon"), (" ›", "dim" if not on else "fieldon")], it)
                    continue
            add(clip([(prefix, "value" if on else "label"), val_seg], cw), it)
        buttons(next(r for r in BUTTON_ROWS if r[0] in GLOBAL_BUTTONS), 2)
        return A, cursor, ccol, placed, hits

    def render(self, W, H):
        app, cfg, store = self.app, self.app.cfg, self.app.store
        F = app.fmt
        # the settings in one column (two of three when they fit), the status panels beside them
        ncols, col_w = ui.flow_columns(W, min_col=52, max_cols=3)
        main_w = W
        two = ncols > 1
        asc = self.app.ascii
        L = []
        # the keys, as keycaps, wrapped to the width
        line, x = [("  ", "text")], 2
        for key, what in KEY_LEGEND:
            w = len(key) + 2 + 1 + len(what) + 2
            if x + w > main_w - 1 and x > 2:
                L.append(line)
                line, x = [("  ", "text")], 2
            line += [(f" {key} ", "btnact"), (f" {what}  ", "dim")]
            x += w
        L.append(line)
        errors_on_top = len(LOG) > 0
        if errors_on_top:
            # put current errors where they'll be seen, not 100 lines down
            L += ui.cardify(active_errors_panel(F, main_w - 2), main_w, asc)[0]
        # the settings, an accordion in a card: two columns' width of three, one of two, the
        # whole width of one; the status cards in the column beside it
        acc_w = col_w * (ncols - 1) + 3 * (ncols - 2) if ncols >= 2 else main_w
        col_w = main_w - acc_w - 1 if two else main_w
        A, cursor, ccol, placed, hits = self.accordion(acc_w - 2)
        it = self.items[self.focus] if self.items else None
        helptext = ""
        preview = None
        if it and it[0] == "sec":
            n = sum(1 for x in FIELDS if SECTION_OF.get(x) == it[1] and x[0] != "btn")
            what = "sources" if it[1] == "Data sources" else "settings"
            helptext = (SECTION_HELP.get(it[1], "") + (f" Enter or ← closes its {n} {what}."
                                                       if it[1] in self.open else f" Enter or → opens its {n} {what}."))
        elif it and it[0] in ("fld", "btn", "crt"):
            helptext = it[4]
            if it[0] == "crt" and it[1] == "crt_font":
                helptext += " " + self.font_status()
            if it[0] == "crt" and it[1] in ("meter_style", "meter_blocks"):
                preview = ([("  looks like  ", "dim")] + C.meter_spans(0.75, max(4, min(24, main_w - 30)), "good") +
                           [("  ", "text")] + C.meter_spans(0.4, 10, "accent"))
        # the focused setting's help and the last message stay pinned at the bottom, always in view
        foot = []
        if self.message[0]:
            foot += wrap(self.message[0], min(main_w, 120), self.message[1], 2)
        if helptext:
            foot += wrap(helptext, min(main_w, 120), "dim", 2)
        if preview:
            foot.append(preview)
        foot_title = (it[2] if it and it[0] in ("fld", "btn", "crt") else
                      S.SOURCE_BY_ID[it[1]].name if it and it[0] == "src" else
                      it[1] if it and it[0] == "sec" else "Settings")
        # the status panels: beside the list, or under it (built two cells narrower: the
        # cards' edges take them)
        S_ = []
        sw = col_w - 2
        if self.results:
            S_.append(ui.section("Connection test", sw))
            for name, ok, msg in self.results:
                badge = ("  ok  ", "good") if ok is True else ("  ..  ", "dim") if ok is None else ("  no  ", "bad")
                body = labeled_wrap(name, msg, sw - 6, lw=16, indent=0)
                S_.append([badge] + body[0])
                S_ += [[("      ", "text")] + ln for ln in body[1:]]
            S_.append([])
        caps = app.term.caps if app.term else None
        S_.append(ui.section("Terminal", sw))
        if caps:
            S_.append(ui.kv("Detected", caps.name or "--", sw, 12))
            S_ += [[("  ", "text")] + ln for ln in wrap(caps.describe(), sw - 2, "dim", 12)]
        S_.append(ui.kv("Drawing", f"{app.gmode} graphics, {app.color_mode} color", sw, 12, "good"))
        crt = app.renderer.crt
        if crt.on:
            current, _ = C.terminal_font()
            pick = C.font_of(cfg)
            font = current or ("" if pick == "own" else C.font_label(pick) + " picked")
            S_ += [[("  ", "text")] + ln for ln in wrap(
                "CRT: " + C.PHOSPHOR_LABEL[crt.phosphor] + " phosphor · " +
                ("the glass on top" if crt.pictures else "no glass (it needs kitty or Ghostty)") + " · effects " +
                C.summary(cfg) + (f" · font {font}" if font else "") + " (c)", sw - 2, "dim", 0)]
        S_.append([])
        S_.append(ui.section("Voice", sw))
        S_ += [[("  ", "text")] + ln for ln in wrap(app.speech.describe(), sw - 4, "value", 0)]
        es = engine_status()
        S_.append(clip([("  Engines  ", "label")] + [(f"{k}{'✓' if v else '✗'} ", "good" if v else "dim")
                                                       for k, v in es.items()], sw))
        S_.append(ui.kv("Player", find_player(cfg.get("audio_player") or "auto") or "none found", sw, 9))
        dl = app.speech.download
        if dl:
            name, done, total, err = dl
            if err:
                S_ += wrap(f"Download of {name} failed: {err}", sw, "bad", 2)
            elif total and done < total:
                S_.append(ui.kv("Download", f"{name} {done * 100 // total}% of {total // 1048576} MB", sw, 9, "accent"))
            elif done:
                S_.append(ui.kv("Download", f"{name} ready", sw, 9, "good"))
        S_.append([])
        S_.append(ui.section("Status", sw))
        loc = store.get("loc") or {}
        pts = store.get("nws_points") or {}
        sta = store.get("nws_station") or {}
        mode = store.onecall_mode(cfg)
        live = store.oc_version in ("4.0", "3.0")
        if mode == "try":
            oc_txt = (f"version {store.oc_version} active" if live else "not checked yet"
                      if store.oc_version is None else "retrying on the next refresh")
        else:
            oc_txt = ONECALL_MSG.get(mode, mode)
        rows = [
            ("One Call", oc_txt, "good" if live and mode == "try" else "dim"),
            ("Calls today", f"{store.usage.get()} of {cfg.get('owm_daily_cap')} (UTC day)"),
            ("Location", f"{loc['lat']:.4f}, {loc['lon']:.4f} from {loc.get('how')}" if loc else "--"),
            ("NWS office", f"{pts.get('gridId')}, grid {pts.get('gridX')},{pts.get('gridY')}, "
                           f"{pts.get('city') or ''} {pts.get('state') or ''}" if pts else "--"),
            ("Station", f"{sta.get('id')} {sta.get('name') or ''}" if sta else "--"),
            ("Last update", F.time(store.last_done) if store.last_done else "not yet"),
            ("Data used", f"{store.http.requests} requests, {store.http.bytes_in / 1048576:.1f} MB this session"),
        ]
        for row in rows:
            S_ += labeled_wrap(row[0], row[1], sw, lw=13, style=row[2] if len(row) > 2 else "value", indent=2)
        if not errors_on_top:
            S_.append([])
            S_ += active_errors_panel(F, sw)
        right = ui.cardify(S_, col_w, asc)[0]
        # the list's rows keep their columns in the card (each loses its two-cell indent and
        # the card's edge puts two cells back) and move down a row, under its top edge
        box = ui.card("Settings", [ui.dedent(ln, 2) for ln in A], acc_w, f"saved in {pretty_path(CONFIG_PATH)}", asc)
        top = len(L) + 1
        if two:                            # the status cards in the last column, beside the list
            for r in range(max(len(box), len(right))):
                L.append((padto(box[r], acc_w) if r < len(box) else [(" " * acc_w, "text")]) + [(" ", "text")] +
                         (right[r] if r < len(right) else []))
        else:                              # one column: the status cards under the list
            L += box + right
        self.pos_of = {i: (0, r) for i, r in placed}
        self.hits = [(top + r, x0, x1, i) for r, x0, x1, i in hits]
        if cursor is not None:
            cursor += top
        out = {"lines": L, "cursor": cursor, "ccol": ccol}
        if foot:
            out["footer"], out["footer_title"] = foot, foot_title
        return out
