"""Settings file: ~/.config/skydrop/config.json (mode 600).

v2 reads a v1 config as-is; new settings simply take their defaults.
"""

import json
import os
import time

from .paths import CONFIG_DIR, CONFIG_PATH

DEFAULT_OWM_BASE = "https://api.openweathermap.org"
DEFAULT_NWS_BASE = "https://api.weather.gov"
DEFAULT_NWS_FLAGS = "forecast_temperature_qv,forecast_wind_speed_qv"

# First-run defaults. After the first launch the config file (edited from the
# Settings screen) wins.
DEFAULTS = {
    # --- carried over from v1 -------------------------------------------
    # Keys and IDs are yours to supply (Settings, key 0); none is needed to run.
    "owm_api_key": "",
    "owm_shared_secret": "",
    "nws_user_agent": "",          # empty: a generic skydrop User-Agent
    "nws_api_key": "",
    "nws_shared_secret": "",
    "nws_station": "",
    "nws_feature_flags": DEFAULT_NWS_FLAGS,
    "nws_base_url": DEFAULT_NWS_BASE,
    "owm_onecall_version": "auto",
    "owm_base_url": DEFAULT_OWM_BASE,
    "zip": "75201",                # downtown Dallas, TX until you pick your own (z)
    "country": "US",
    "location_name": "",           # empty: the name the NWS gives the place
    "lat": "",
    "lon": "",
    "units": "imperial",
    "owm_daily_cap": 900,
    # --- new in v2 -----------------------------------------------------
    "graphics": "auto",            # auto | kitty | sixel | blocks | ascii
    "color_mode": "auto",          # auto | truecolor | 256 | 16
    "crt": False,                  # CRT effect: the look of a cathode-ray tube (Settings > Display)
    "crt_phosphor": "green",       # green (P1) | amber (P3) | white (P4) | color (the theme's own)
    "crt_font": "own",             # own (the terminal's) | vt323 | glasstty | ibm3270 | px437 | terminus
    "crt_font_sizes": {},          # each font's size in points (none set: the size it starts at)
    "meter_style": "led",          # every meter: led | dots | bricks | raised | solid | shade
    "meter_blocks": "auto",        # auto | legacy (Unicode's legacy computing blocks) | plain
    "background": "theme",         # the page: theme (its own) | #RRGGBB | terminal (the terminal's own)
    "rounded_borders": "on",       # ╭╮ rounded box corners, or ┌┐ square ones
    "crt_levels": {                # each effect 0 (off) to 10, 5 the standard look (scale: which scale)
        "blacks": 5, "power": 5, "trails": 5, "burn": 5, "slips": 5, "type": 5, "post": 5, "blink": 5,
        "flash": 5, "scanlines": 5, "grille": 5, "bloom": 5, "focus": 5, "fringes": 5, "vignette": 5,
        "grain": 5, "flicker": 5, "hum": 5, "curve": 5, "glare": 5, "bezel": 5, "scale": 2},
    "theme": "weatherstar",        # weatherstar | midnight | retro8s | star4000 | magnetic
    "mouse": "on",
    "ticker": "paged",             # paged | crawl | off
    "clock_seconds": "on",
    "hourly_hours": 48,
    "daily_days": 10,
    "radar_span": 120,             # miles across the map
    "radar_frames": 8,
    "radar_step": 10,              # minutes between loop frames
    "radar_layer": "reflectivity", # reflectivity | type | satellite | radar+sat
    "radar_basemap": "classic",    # classic | imagery | topo | osm | plain
    "radar_labels": "on",
    "radar_stations": "on",
    "radar_warnings": "on",
    "radar_speed": 4,              # loop frames per second
    "station_radius": 10,          # miles around the ZIP for the station feed
    "station_max": 40,
    "voice_engine": "auto",        # auto | piper | pico | espeak | festival | spd | off
    "voice": "en_US-ryan-medium",
    "voice_rate": 100,             # percent of normal speed
    "audio_player": "auto",        # auto | pw-play | paplay | aplay | ffplay
    "narrate": "off",              # read pages aloud in Local on the 8s
    "auto_read_warnings": "off",
    "local8s_seconds": 12,
    "sources": {},
    "sources_removed": [],
    # --- new in v3 -----------------------------------------------------
    "refresh_seconds": 30,         # how often to check for new data (alerts, radar, observations)
    "radar_source": "site",        # site (one NEXRAD) | mosaic (national MRMS)
    "radar_site": "auto",          # auto (nearest to the map) or a radar ID such as KFWS
    "radar_product": "ref",        # ref vel srv cc zdr kdp hc hhc et vil rate p1h pst cr
    "radar_panes": 1,              # 1, 2 or 4 linked maps
    "radar_pane_products": "ref,vel,cc,zdr",
    "radar_smooth": "on",
    "radar_cells": "on",           # storm tracks, hail, mesocyclones, TVS
    "radar_min_dbz": 5,            # hide reflectivity weaker than this
    "radar_opacity": 90,           # percent
    "radar_hover": "on",           # show values under the mouse pointer
    "radar_extra_layers": "markers,placefiles",   # range, sites, markers, placefiles shown on the radar
    "placefiles": "",              # GRLevelX placefile URLs, one per line or comma separated
    "map_markers": "",             # Name: lat, lon; Name 2: lat, lon
    "alert_ticker": "on",          # the warnings crawl along the bottom
    "ticker_area": "100",          # zip | county | 25 | 50 | 100 | 200 (miles) | state
    "ticker_advisories": "on",     # include advisories and statements, not just watches and warnings
    "ticker_reports": "on",        # include storm reports (tornado, hail, wind damage) as they come in
    "ticker_speed": "normal",      # slow | normal | fast
    "tornado_alarm": "on",         # sound for a new Tornado Warning covering your ZIP code
    "tornado_alarm_sound": "noaa", # noaa (SAME bursts + 1050 Hz tone) | nwr (tone only) | eas (two-tone)
    "tornado_alarm_repeat": 0,     # minutes between repeats until you press a key; 0 = play once
    "tornado_alarm_voice": "on",   # read the warning aloud after the tone
    "warning_alarm": "chime",      # other new warnings covering your ZIP: off | chime | nwr
    "background_watch": "off",     # keep watching for tornado warnings after skydrop closes
    "window_font": "jetbrains",    # skydrop's own window (skydrop.exe, --window): jetbrains | dejavu
    "window_font_size": 12,        # the window's font size in points
}

KNOWN_KEYS = set(DEFAULTS)


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            _migrate_crt(data)
            for k, val in data.items():
                if (k in KNOWN_KEYS or k.startswith("_")) and k != "_runonly":
                    cfg[k] = val
    except FileNotFoundError:
        try:
            save_config(cfg)
        except OSError:
            pass
    except ValueError as e:
        # A typo in a hand edit: keep the file (it holds the user's ZIP and keys) and say so,
        # rather than quietly overwriting it with the defaults later.
        from .errlog import LOG
        bad = f"{CONFIG_PATH}.bad-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(CONFIG_PATH, bad)
            where = f"it was moved to {bad}"
        except OSError:
            where = "it couldn't be moved aside"
        LOG.report("config", "Settings file", f"config.json isn't valid JSON ({e}); {where}, and skydrop "
                                               "started with default settings. Fix that file and copy it back, "
                                               "or set things again on this screen.")
    except OSError as e:
        from .errlog import LOG
        LOG.report("config", "Settings file", f"couldn't read config.json: {e}")
    if not isinstance(cfg.get("sources"), dict):
        cfg["sources"] = {}
    from .theme import theme_name
    if cfg.get("theme") != theme_name(cfg.get("theme")):
        cfg["theme"] = theme_name(cfg.get("theme"))   # amber and green were retired in 3.1
    if not isinstance(cfg.get("sources_removed"), list):
        cfg["sources_removed"] = []
    return cfg


def _migrate_crt(data):
    """3.2.0 named some CRT settings differently: carry them over."""
    if "crt_meter" in data and "meter_style" not in data:
        data["meter_style"] = {"segments": "led"}.get(data["crt_meter"], data["crt_meter"])
    data.pop("crt_meter", None)
    if isinstance(data.get("crt"), str):
        data["crt"] = data["crt"].strip().lower() in ("on", "true", "1", "yes")
    if data.get("crt_phosphor") == "colour":
        data["crt_phosphor"] = "color"
    if data.get("meter_style") == "shaded":                 # 3.3's name for the shaded blocks
        data["meter_style"] = "shade"
    fonts = {"VT323": "vt323", "Glass TTY VT220": "glasstty", "IBM 3270": "ibm3270",
             "Px437 IBM VGA 8x16": "px437", "Terminus": "terminus"}
    if data.get("crt_font") in fonts:                       # 3.2 and 3.3 kept the family's name
        data["crt_font"] = fonts[data["crt_font"]]
    lv = data.get("crt_levels")
    if isinstance(lv, dict):
        old = {"poweron": "power", "burnin": "burn", "typewriter": "type", "humbar": "hum", "curvature": "curve"}
        for a, b in old.items():
            if a in lv:
                v = lv.pop(a)
                lv.setdefault(b, v)
        full = dict(DEFAULTS["crt_levels"])
        full.update(lv)
        data["crt_levels"] = full


def run_only(cfg, **vals):
    """Use these settings for this run without writing them to the config file."""
    keep = cfg.setdefault("_runonly", {})
    for k, val in vals.items():
        if k not in keep:
            keep[k] = cfg.get(k, DEFAULTS.get(k))
        cfg[k] = val


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    snapshot = dict(cfg)
    for k, orig in (snapshot.pop("_runonly", None) or {}).items():
        snapshot[k] = orig
    tmp = CONFIG_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, sort_keys=True)
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def on(cfg, key):
    """Boolean settings are stored as "on"/"off" (true/false also accepted)."""
    val = cfg.get(key, DEFAULTS.get(key))
    if isinstance(val, str):
        return val.strip().lower() in ("on", "true", "yes", "1")
    return bool(val)


def intval(cfg, key, lo=None, hi=None):
    try:
        val = int(float(cfg.get(key, DEFAULTS.get(key))))
    except (TypeError, ValueError):
        val = int(DEFAULTS.get(key) or 0)
    if lo is not None:
        val = max(lo, val)
    if hi is not None:
        val = min(hi, val)
    return val
