"""What may fall each hour, and how hard: rain, thunderstorms, snow, sleet, freezing rain
or a wintry mix, in light, moderate and heavy shades, for the charts that color
precipitation by type the way weather radar does (rain green, snow white, ice pink)."""

import re

from . import icons

# each type's style for a light, a moderate and a heavy shade
STYLES = {
    "rain": ("p_rain1", "p_rain2", "p_rain3"),
    "storm": ("p_storm",) * 3,
    "snow": ("p_snow1", "p_snow2", "p_snow3"),
    "sleet": ("p_sleet",) * 3,
    "ice": ("p_ice",) * 3,
    "mix": ("p_mix",) * 3,
}

# the key's name for each style, in the key's order
NAMES = {
    "p_rain1": "Light rain", "p_rain2": "Rain", "p_rain3": "Heavy rain", "p_storm": "Storms",
    "p_mix": "Wintry mix", "p_sleet": "Sleet", "p_ice": "Freezing rain",
    "p_snow1": "Light snow", "p_snow2": "Snow", "p_snow3": "Heavy snow",
}

# a narrow key names each type once, in its middle shade
SHORT = {"p_rain1": ("p_rain2", "Rain"), "p_rain3": ("p_rain2", "Rain"),
         "p_snow1": ("p_snow2", "Snow"), "p_snow3": ("p_snow2", "Snow")}

# the weather kinds read from forecast text (icons.from_text) and what they bring
KIND_TYPE = {
    "drizzle": ("rain", 0), "rain": ("rain", 1), "showers": ("rain", 1), "heavy_rain": ("rain", 2),
    "thunder": ("storm", 1), "flurries": ("snow", 0), "snow": ("snow", 1),
    "sleet": ("sleet", 1), "freezing": ("ice", 1), "mix": ("mix", 1),
}

# WMO weather codes (Open-Meteo): each says how hard as well as what
WMO_TYPE = {
    51: ("rain", 0), 53: ("rain", 0), 55: ("rain", 1), 56: ("ice", 0), 57: ("ice", 1),
    61: ("rain", 0), 63: ("rain", 1), 65: ("rain", 2), 66: ("ice", 1), 67: ("ice", 2),
    71: ("snow", 0), 73: ("snow", 1), 75: ("snow", 2), 77: ("snow", 0),
    80: ("rain", 0), 81: ("rain", 1), 82: ("rain", 2), 85: ("snow", 0), 86: ("snow", 2),
    95: ("storm", 1), 96: ("storm", 2), 99: ("storm", 2),
}

# the NWS forecast grid's weather layer
GRID_TYPE = {
    "rain": "rain", "rain_showers": "rain", "drizzle": "rain", "thunderstorms": "storm", "hail": "storm",
    "snow": "snow", "snow_showers": "snow", "sleet": "sleet", "freezing_rain": "ice", "freezing_drizzle": "ice",
}
GRID_SHADE = {"very_light": 0, "light": 0, "moderate": 1, "heavy": 2}

# how hard, in so many words (whole words: a "slight chance" isn't light rain)
HEAVY_WORDS = re.compile(r"\b(heavy|blizzard|downpours?|torrential|intense|violent|extreme)\b")
LIGHT_WORDS = re.compile(r"\b(light|very light|drizzle|sprinkles?|flurr(y|ies)|slight (rain|snow))\b")


def from_words(text):
    """(type, shade) from forecast words ("Chance Light Rain", "Snow Showers Likely",
    "heavy intensity rain"), or None when they name no precipitation."""
    t = (text or "").lower()
    if not t:
        return None
    if "thunder" not in t and ("wintry mix" in t or "mixed precip" in t):
        hit = ("mix", 1)
    elif "ice pellets" in t and "freezing" not in t:
        hit = ("sleet", 1)                      # sleet, by its other name
    elif "sprinkle" in t and "thunder" not in t:
        hit = ("rain", 0)
    else:
        hit = KIND_TYPE.get(icons.from_text(t))
    if hit is None:
        return None
    typ, shade = hit
    if HEAVY_WORDS.search(t):
        shade = 2
    elif LIGHT_WORDS.search(t):
        shade = 0
    return typ, shade


def from_grid(items):
    """(type, shade) from the NWS grid's weather for the hour: several types at once are a
    wintry mix (or the icier, or the storm, of them); None when it names none."""
    found = {}
    for w in items or []:
        if not isinstance(w, dict):
            continue
        typ = GRID_TYPE.get(w.get("weather") or "")
        if typ:
            shade = GRID_SHADE.get(w.get("intensity") or "", 1)
            found[typ] = max(found.get(typ, 0), shade)
    if not found:
        return None
    for typ in ("storm", "ice", "sleet"):
        if typ in found:
            return typ, found[typ]
    if "rain" in found and "snow" in found:
        return "mix", 1
    typ = next(iter(found))
    return typ, found[typ]


def _per_hour(value):
    """An hour's share of a grid amount kept as (value, start, seconds), or a plain amount."""
    if isinstance(value, (tuple, list)):
        if not value or value[0] is None:
            return None
        secs = value[2] if len(value) > 2 and value[2] else 3600
        return value[0] / max(1, secs // 3600)
    return value if isinstance(value, (int, float)) else None


def hour_type(r):
    """(type, shade) of the precipitation hour `r` may bring: the type one of rain, storm,
    snow, sleet, ice (freezing rain or drizzle) and mix; the shade 0 light, 1 moderate,
    2 heavy. NWS's words for the hour come first, then the model's weather code, then any
    other description, then the NWS grid's weather; with none of them naming any (a low
    chance in a cloudy hour), light snow when it's freezing and light rain otherwise."""
    hit = None
    if r.get("nws_desc"):
        hit = from_words(r["nws_desc"])
    if hit is None and r.get("wmo") is not None:
        try:
            hit = WMO_TYPE.get(int(r["wmo"]))
        except (TypeError, ValueError):
            hit = None
    if hit is None and not r.get("nws_desc"):
        hit = from_words(r.get("desc"))
    if hit is None:
        hit = from_grid(r.get("grid_wx"))
    if hit is None:
        hit = KIND_TYPE.get(r.get("kind"))
    rain = _per_hour(r.get("rain"))
    snow = _per_hour(r.get("snow"))
    if snow is None:
        snow = _per_hour(r.get("snowfall"))
    if hit is None:
        temp = r.get("temp")
        cold = temp is not None and temp <= 0.0
        hit = ("snow", 0) if ((snow or 0) > 0 and not (rain or 0) > 0) or cold else ("rain", 0)
    typ, shade = hit
    # how much the models expect lifts the shade (rain: 2.5 mm an hour moderate, 7.6 heavy;
    # snow: 1 cm an hour moderate, 2.5 heavy)
    if typ in ("rain", "storm") and rain:
        shade = max(shade, 2 if rain >= 7.6 else 1 if rain >= 2.5 else 0)
    elif typ == "snow" and snow:
        shade = max(shade, 2 if snow >= 25 else 1 if snow >= 10 else 0)
    return typ, max(0, min(2, shade))


def style_of(kind):
    """The style a (type, shade) is drawn in."""
    if not kind:
        return "p_rain2"
    typ, shade = kind
    return STYLES.get(typ, STYLES["rain"])[max(0, min(2, int(shade)))]


def legend(kinds, width, ascii_only=False, when="in the next 12 hours"):
    """The key under a chart: 'Precipitation', then a swatch and a name for each type and
    shade shown, the types in the order they come (`kinds` hour by hour), each one's shades
    lightest first; each type named once where there's no room for its shades; with none,
    a line saying none is expected."""
    head = [("Precipitation  ", "label")]
    styles = {style_of(k) for k in kinds if k}
    if not styles:
        return head + [(f"None expected {when}".strip(), "dim")]
    swatch = "#" if ascii_only else "■"

    def build(entries):
        out = list(head)
        for k, (sty, name) in enumerate(entries):
            out += [("   " if k else "", "text"), (swatch, sty), (" " + name, "text")]
        return [s for s in out if s[0]]

    order = []
    for k in kinds:
        if k and k[0] in STYLES and k[0] not in order:
            order.append(k[0])
    full = [(s, NAMES[s]) for typ in order for s in dict.fromkeys(STYLES[typ]) if s in styles]
    spans = build(full)
    if sum(len(t) for t, _s in spans) > width:
        short, seen = [], set()
        for s, name in full:
            s2, name2 = SHORT.get(s, (s, name))
            if name2 not in seen:
                seen.add(name2)
                short.append((s2, name2))
        spans = build(short)
    out, used = [], 0
    for text, sty in spans:                     # clipped to the width, whole entries where it can
        if used + len(text) > width:
            break
        out.append((text, sty))
        used += len(text)
    while len(out) > 1 and (out[-1][0] == swatch or not out[-1][0].strip()):
        out.pop()                               # no swatch left without its name
    return out
