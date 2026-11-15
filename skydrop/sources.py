"""Every online data source skydrop talks to.

Each source can be switched off or deleted on the Settings screen (0).  A
disabled source is never contacted; a deleted source is also hidden from the
list until "Restore deleted sources" is used.  Base addresses can be edited
in case a provider moves.
"""

from collections import namedtuple

Source = namedtuple("Source", "id name url provides auth license default_on kind")

SOURCES = [
    Source("nws", "National Weather Service API", "https://api.weather.gov",
           "7-day, hourly and gridpoint forecasts, observations, alerts, office products, "
           "NOAA Weather Radio scripts, station lists",
           "none (sends a User-Agent)", "U.S. Government work, public domain", True, "forecast"),
    Source("owm", "OpenWeather", "https://api.openweathermap.org",
           "One Call 4.0/3.0, current conditions, 3-hour forecast, air pollution, geocoding",
           "API key", "commercial service with a free tier", True, "forecast"),
    Source("openmeteo", "Open-Meteo forecast", "https://api.open-meteo.com",
           "16-day hourly and daily model forecasts, UV index, works anywhere",
           "none", "open source (AGPL-3.0); data CC BY 4.0", True, "forecast"),
    Source("openmeteo_aq", "Open-Meteo air quality", "https://air-quality-api.open-meteo.com",
           "US AQI and pollutant forecast from CAMS, used when OpenWeather isn't available",
           "none", "open source (AGPL-3.0); data CC BY 4.0", True, "air"),
    Source("nexrad_aws", "NEXRAD Level 3 on NOAA Open Data (AWS)", "https://unidata-nexrad-level3.s3.amazonaws.com",
           "Single-site radar: reflectivity, velocity, dual-pol, echo tops, VIL and rainfall at every tilt, "
           "with history for loops",
           "none", "NOAA Open Data Dissemination, public domain", True, "radar"),
    Source("tgftp", "NWS tgftp radar products", "https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar",
           "Latest storm tracks, hail, mesocyclone and tornado vortex signatures; backup for site radar",
           "none", "U.S. Government work, public domain", True, "radar"),
    Source("noaa_radar", "NOAA MRMS radar (opengeo GeoServer)", "https://opengeo.ncep.noaa.gov/geoserver",
           "Radar mosaic loops: base reflectivity and precipitation type",
           "none", "U.S. Government work, public domain", True, "radar"),
    Source("iem", "Iowa Environmental Mesonet", "https://mesonet.agron.iastate.edu",
           "Live station feed (airports, DOT road sensors, co-op, river and soil networks), "
           "SPC and WPC outlooks for your point, storm reports, warning polygons, radar fallback",
           "none", "Iowa State University; open data, open-source code (pyIEM, MIT)", True, "obs"),
    Source("acis", "NOAA RCC ACIS (almanac)", "https://data.rcc-acis.org",
           "Daily normals, record highs and lows, month-to-date precipitation",
           "none", "NOAA Regional Climate Centers, public", True, "climate"),
    Source("tigerweb", "U.S. Census TIGERweb", "https://tigerweb.geo.census.gov",
           "Radar map boundaries, interstates, water, city names and ZIP code outlines",
           "none", "U.S. Government work, public domain", True, "map"),
    Source("zippopotam", "Zippopotam.us", "https://api.zippopotam.us",
           "ZIP code to location lookup that needs no API key",
           "none", "open source (MIT); GeoNames data CC BY 4.0", True, "geo"),
    Source("rainviewer", "RainViewer", "https://api.rainviewer.com",
           "Worldwide radar fallback (coarser, zoom 7 max)",
           "none", "free for personal use", True, "radar"),
    Source("gibs", "NASA GIBS (GOES satellite)", "https://gibs.earthdata.nasa.gov",
           "Infrared satellite cloud layer for the radar map",
           "none", "NASA open data", True, "radar"),
    Source("usgs_basemap", "USGS The National Map", "https://basemap.nationalmap.gov",
           "Imagery and topo backgrounds for the radar map (when chosen in Settings)",
           "none", "U.S. Government work, public domain", True, "map"),
    Source("osm_tiles", "OpenStreetMap tiles", "https://tile.openstreetmap.org",
           "Street-map background for the radar map (light use only, per the OSM tile policy)",
           "none", "© OpenStreetMap contributors, ODbL", False, "map"),
    Source("piper_voices", "Piper voices (Hugging Face)", "https://huggingface.co/rhasspy/piper-voices/resolve/main",
           "Downloads natural-sounding offline voices for read-aloud",
           "none", "MIT code; per-voice dataset licenses", True, "voice"),
]

SOURCE_BY_ID = {s.id: s for s in SOURCES}

# v1 kept these two addresses as top-level settings; they still live there.
LEGACY_URL_KEYS = {"owm": "owm_base_url", "nws": "nws_base_url"}


def _entry(cfg, sid):
    srcs = cfg.get("sources")
    if isinstance(srcs, dict) and isinstance(srcs.get(sid), dict):
        return srcs[sid]
    return {}


def removed(cfg, sid):
    return sid in (cfg.get("sources_removed") or [])


def enabled(cfg, sid):
    """True when the source may be contacted."""
    src = SOURCE_BY_ID.get(sid)
    if src is None or removed(cfg, sid):
        return False
    val = _entry(cfg, sid).get("enabled")
    return src.default_on if val is None else bool(val)


def enabled_flag(cfg, sid):
    """The on/off switch, ignoring whether the source was deleted."""
    src = SOURCE_BY_ID.get(sid)
    if src is None:
        return False
    val = _entry(cfg, sid).get("enabled")
    return src.default_on if val is None else bool(val)


def url(cfg, sid):
    src = SOURCE_BY_ID[sid]
    legacy = LEGACY_URL_KEYS.get(sid)
    raw = cfg.get(legacy) if legacy else _entry(cfg, sid).get("url")
    raw = (raw or "").strip().rstrip("/")
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    return src.url


def set_enabled(cfg, sid, on):
    srcs = cfg.get("sources")
    if not isinstance(srcs, dict):
        srcs = {}
    ent = dict(srcs.get(sid) or {})
    ent["enabled"] = bool(on)
    srcs[sid] = ent
    cfg["sources"] = srcs


def set_url(cfg, sid, value):
    legacy = LEGACY_URL_KEYS.get(sid)
    value = (value or "").strip().rstrip("/")
    if legacy:
        cfg[legacy] = value or SOURCE_BY_ID[sid].url
        return
    srcs = cfg.get("sources")
    if not isinstance(srcs, dict):
        srcs = {}
    ent = dict(srcs.get(sid) or {})
    if value and value != SOURCE_BY_ID[sid].url:
        ent["url"] = value
    else:
        ent.pop("url", None)
    srcs[sid] = ent
    cfg["sources"] = srcs


def remove(cfg, sid):
    lst = [x for x in (cfg.get("sources_removed") or []) if x != sid]
    lst.append(sid)
    cfg["sources_removed"] = lst


def restore_all(cfg):
    cfg["sources_removed"] = []


def visible(cfg):
    return [s for s in SOURCES if not removed(cfg, s.id)]
