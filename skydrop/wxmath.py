"""Weather math. All internal values are metric: C, m/s, hPa, m, mm."""

import datetime as dt
import math
import re

from .util import cap, first, num

COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
COMPASS_WORDS = {
    "N": "north", "NNE": "north northeast", "NE": "northeast", "ENE": "east northeast",
    "E": "east", "ESE": "east southeast", "SE": "southeast", "SSE": "south southeast",
    "S": "south", "SSW": "south southwest", "SW": "southwest", "WSW": "west southwest",
    "W": "west", "WNW": "west northwest", "NW": "northwest", "NNW": "north northwest",
}


def compass(deg):
    if deg is None:
        return ""
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def compass8(deg):
    if deg is None:
        return ""
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((deg % 360) / 45 + 0.5) % 8]


def compass_deg(name):
    try:
        return COMPASS.index((name or "").strip().upper()) * 22.5
    except ValueError:
        return None


ARROWS = "↓↙←↖↑↗→↘"  # wind FROM north blows toward the south: ↓


def wind_arrow(deg):
    if deg is None:
        return " "
    return ARROWS[int((deg % 360) / 45 + 0.5) % 8]


def dewpoint_c(temp, rh):
    if temp is None or not rh:
        return None
    a, b = 17.625, 243.04
    g = math.log(rh / 100.0) + a * temp / (b + temp)
    return b * g / (a - g)


def rh_from(temp, dew):
    if temp is None or dew is None:
        return None
    a, b = 17.625, 243.04
    return max(0.0, min(100.0, 100 * math.exp(a * dew / (b + dew)) / math.exp(a * temp / (b + temp))))


def heat_index_c(t, rh):
    """NWS Rothfusz regression; returns None when it doesn't apply."""
    if t is None or rh is None:
        return None
    f = t * 9 / 5 + 32
    if f < 80:
        return None
    hi = (-42.379 + 2.04901523 * f + 10.14333127 * rh - .22475541 * f * rh - .00683783 * f * f
          - .05481717 * rh * rh + .00122874 * f * f * rh + .00085282 * f * rh * rh - .00000199 * f * f * rh * rh)
    return (hi - 32) * 5 / 9


def wind_chill_c(t, ms):
    if t is None or ms is None:
        return None
    f, mph = t * 9 / 5 + 32, ms * 2.23694
    if f > 50 or mph < 3:
        return None
    wc = 35.74 + 0.6215 * f - 35.75 * mph ** 0.16 + 0.4275 * f * mph ** 0.16
    return (wc - 32) * 5 / 9


_BF_LIMITS = [0.5, 1.6, 3.4, 5.5, 8.0, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7]
_BF_NAMES = ["Calm", "Light air", "Light breeze", "Gentle breeze", "Moderate breeze",
             "Fresh breeze", "Strong breeze", "Near gale", "Gale", "Strong gale",
             "Storm", "Violent storm", "Hurricane force"]


def beaufort(ms):
    if ms is None:
        return None
    for i, lim in enumerate(_BF_LIMITS):
        if ms < lim:
            return i, _BF_NAMES[i]
    return 12, _BF_NAMES[12]


def comfort(dew_c):
    if dew_c is None:
        return "", "dim"
    for lim, label, style in ((10.0, "Dry", "good"), (15.6, "Comfortable", "good"),
                              (18.3, "A little humid", "text"), (21.1, "Muggy", "warn"),
                              (23.9, "Oppressive", "bad")):
        if dew_c < lim:
            return label, style
    return "Miserable", "bad"


def uv_cat(uvi):
    if uvi is None:
        return "", "dim"
    for lim, label, style in ((3, "Low", "good"), (6, "Moderate", "warn"),
                              (8, "High", "warn"), (11, "Very high", "bad")):
        if uvi < lim:
            return label, style
    return "Extreme", "bad"


SEVERITY_RANK = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}


def unit_name(code):
    """Strip the deprecated wmo:/wmoUnit:/nwsUnit:/uc: namespace from an NWS unit code."""
    return (code or "").split(":")[-1]


def conv_si(val, unit):
    """Convert an NWS value to internal units (C, m/s, hPa, m, mm)."""
    if val is None:
        return None
    if unit in ("degF", "[degF]"):
        return (val - 32) * 5 / 9
    if unit in ("K", "degK"):
        return val - 273.15
    if unit in ("km_h-1", "km/h"):
        return val / 3.6
    if unit in ("Pa",):
        return val / 100.0
    if unit in ("kPa",):
        return val * 10
    if unit in ("kt", "kn", "[kn_i]", "kn_i"):
        return val * 0.514444
    if unit in ("[mi_i]/h", "mi_h-1", "[mi_i]_h-1", "mph", "[mph]"):
        return val * 0.44704
    if unit in ("[in_i]", "in", "in_i"):
        return val * 25.4
    if unit in ("[ft_i]", "ft", "ft_i"):
        return val * 0.3048
    if unit in ("[mi_i]", "mi"):
        return val * 1609.344
    if unit == "cm":
        return val * 10
    if unit == "km":
        return val * 1000
    return val


def nws_val(q, prefer="value"):
    """NWS QuantitativeValue -> internal units; ranges fall back to maxValue."""
    if not isinstance(q, dict):
        return None
    v = num(q.get(prefer))
    if v is None:
        v = first(num(q.get("value")), num(q.get("maxValue")), num(q.get("minValue")))
    return conv_si(v, unit_name(q.get("unitCode")))


def nws_temp(period):
    t = period.get("temperature")
    if isinstance(t, dict):
        return nws_val(t)
    val = num(t)
    if val is None:
        return None
    if (period.get("temperatureUnit") or "F").upper() == "F":
        return (val - 32) * 5 / 9
    return val


def parse_wind_text(val):
    """'5 to 10 mph' or a QuantitativeValue range -> upper bound in m/s."""
    if isinstance(val, dict):
        return nws_val(val, prefer="maxValue")
    if not isinstance(val, str):
        return None
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", val)]
    if not nums:
        return None
    top = max(nums)
    low = val.lower()
    if "km/h" in low:
        return top / 3.6
    if "kt" in low or "knot" in low:
        return top * 0.514444
    return top * 0.44704


def nws_wind_range(F, val):
    if isinstance(val, dict):
        lo, hi = nws_val(val, "minValue"), nws_val(val, "maxValue")
        mid = nws_val(val)
        if lo is not None and hi is not None and F.wind(lo, False) != F.wind(hi, False):
            return f"{F.wind(lo, False)} to {F.wind(hi)}"
        return F.wind(first(mid, hi, lo))
    if isinstance(val, str) and val:
        if F.imp or "mph" not in val:
            return val
        return re.sub(r"\d+", lambda mt: f"{float(mt.group(0)) * 1.609344:.0f}", val).replace("mph", "km/h")
    return ""


# P-VTEC phenomenon and significance codes
VTEC_PHEN = {
    "AF": "Ashfall", "AS": "Air Stagnation", "BH": "Beach Hazards", "BW": "Brisk Wind",
    "BZ": "Blizzard", "CF": "Coastal Flood", "CW": "Cold Weather", "DF": "Debris Flow",
    "DS": "Dust Storm", "DU": "Blowing Dust", "EC": "Extreme Cold", "EH": "Excessive Heat",
    "XH": "Extreme Heat", "EW": "Extreme Wind", "FA": "Areal Flood", "FF": "Flash Flood",
    "FG": "Dense Fog", "FL": "Flood", "FR": "Frost", "FW": "Fire Weather", "FZ": "Freeze",
    "GL": "Gale", "HF": "Hurricane Force Wind", "HT": "Heat", "HU": "Hurricane",
    "HW": "High Wind", "HY": "Hydrologic", "HZ": "Hard Freeze", "IS": "Ice Storm",
    "LE": "Lake Effect Snow", "LO": "Low Water", "LS": "Lakeshore Flood", "LW": "Lake Wind",
    "MA": "Special Marine", "MF": "Marine Dense Fog", "MH": "Marine Ashfall",
    "MS": "Marine Dense Smoke", "RB": "Small Craft for Rough Bar", "RP": "Rip Current",
    "SC": "Small Craft", "SE": "Hazardous Seas", "SI": "Small Craft for Winds",
    "SM": "Dense Smoke", "SQ": "Snow Squall", "SR": "Storm", "SS": "Storm Surge", "SU": "High Surf",
    "SV": "Severe Thunderstorm", "SW": "Small Craft for Hazardous Seas", "TO": "Tornado",
    "TR": "Tropical Storm", "TS": "Tsunami", "TY": "Typhoon", "UP": "Heavy Freezing Spray",
    "WC": "Wind Chill", "WI": "Wind", "WS": "Winter Storm", "WW": "Winter Weather",
    "ZF": "Freezing Fog", "ZR": "Freezing Rain", "ZY": "Freezing Spray",
}
VTEC_SIG = {"W": "Warning", "A": "Watch", "Y": "Advisory", "S": "Statement",
            "F": "Forecast", "O": "Outlook", "N": "Synopsis"}

HEATRISK = [("Little to none", "good"), ("Minor", "aqi2"), ("Moderate", "aqi3"),
            ("Major", "aqi4"), ("Extreme", "aqi5")]
LIGHTNING_LAL = {1: "none", 2: "isolated", 3: "widely scattered", 4: "scattered",
                 5: "numerous", 6: "dry lightning"}
_COVERAGE = {"slight_chance": "slight chance of", "chance": "chance of", "areas": "areas of",
             "periods": "periods of", "few": "a few", "definite": ""}


def heatrisk_label(val):
    if val is None:
        return None, "dim"
    idx = max(0, min(4, int(round(val))))
    return HEATRISK[idx]


def hazard_text(items):
    names = []
    for hz in items or []:
        if not isinstance(hz, dict):
            continue
        phen = VTEC_PHEN.get(hz.get("phenomenon") or "", hz.get("phenomenon") or "")
        sig = VTEC_SIG.get(hz.get("significance") or "", "")
        name = f"{phen} {sig}".strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def grid_weather_text(items):
    parts = []
    for w in items or []:
        if not isinstance(w, dict) or not w.get("weather"):
            continue
        wx = w["weather"].replace("_", " ")
        inten = (w.get("intensity") or "").replace("_", " ")
        cov = w.get("coverage") or ""
        if cov == "likely":
            text = " ".join(x for x in (inten, wx, "likely") if x)
        else:
            text = " ".join(x for x in (_COVERAGE.get(cov, cov.replace("_", " ")), inten, wx) if x)
        attrs = [a.replace("_", " ") for a in w.get("attributes") or [] if a]
        if attrs:
            text += f" ({', '.join(attrs)})"
        parts.append(text)
    return cap("; ".join(parts))


# ----------------------------------------------------------------------------
# air quality (US EPA AQI)
# ----------------------------------------------------------------------------

AQI_BP = {
    "PM2.5": [(0.0, 9.0, 0, 50), (9.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
              (55.5, 125.4, 151, 200), (125.5, 225.4, 201, 300), (225.5, 325.4, 301, 500)],
    "PM10": [(0, 54, 0, 50), (55, 154, 51, 100), (155, 254, 101, 150),
             (255, 354, 151, 200), (355, 424, 201, 300), (425, 604, 301, 500)],
    "O3": [(0, 54, 0, 50), (55, 70, 51, 100), (71, 85, 101, 150),
           (86, 105, 151, 200), (106, 200, 201, 300)],
    "NO2": [(0, 53, 0, 50), (54, 100, 51, 100), (101, 360, 101, 150),
            (361, 649, 151, 200), (650, 1249, 201, 300), (1250, 2049, 301, 500)],
    "SO2": [(0, 35, 0, 50), (36, 75, 51, 100), (76, 185, 101, 150),
            (186, 304, 151, 200), (305, 604, 201, 300), (605, 1004, 301, 500)],
    "CO": [(0.0, 4.4, 0, 50), (4.5, 9.4, 51, 100), (9.5, 12.4, 101, 150),
           (12.5, 15.4, 151, 200), (15.5, 30.4, 201, 300), (30.5, 50.4, 301, 500)],
}

# pollutant: (component key, factor from ug/m3 to AQI units, AQI unit, name)
POLLUTANTS = [
    ("PM2.5", "pm2_5", 1.0, "ug/m3", "Fine particles"),
    ("PM10", "pm10", 1.0, "ug/m3", "Coarse particles"),
    ("O3", "o3", 24.45 / 48.00, "ppb", "Ozone"),
    ("NO2", "no2", 24.45 / 46.01, "ppb", "Nitrogen dioxide"),
    ("SO2", "so2", 24.45 / 64.07, "ppb", "Sulfur dioxide"),
    ("CO", "co", 24.45 / 28.01 / 1000.0, "ppm", "Carbon monoxide"),
]
EXTRA_POLLUTANTS = [("NO", "no", "Nitric oxide"), ("NH3", "nh3", "Ammonia")]

AQI_CATS = [
    (50, "Good", "aqi1", "Air quality is fine for everyone."),
    (100, "Moderate", "aqi2", "Acceptable. People who are unusually sensitive to pollution may "
                              "want to ease off long or hard outdoor exertion."),
    (150, "Unhealthy for sensitive groups", "aqi3",
     "Children, older adults, and people with heart or lung conditions should cut back on long "
     "or intense outdoor activity."),
    (200, "Unhealthy", "aqi4", "Everyone should cut back on prolonged outdoor exertion; sensitive "
                               "groups should avoid it."),
    (300, "Very unhealthy", "aqi5", "Everyone should avoid prolonged outdoor exertion; sensitive "
                                    "groups should stay indoors."),
    (10 ** 9, "Hazardous", "aqi6", "Health alert: everyone should avoid outdoor activity."),
]
OWM_AQI = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very poor"}
OWM_AQI_STYLE = {1: "aqi1", 2: "aqi2", 3: "aqi3", 4: "aqi4", 5: "aqi5"}
OWM_BANDS = {"SO2": (20, 80, 250, 350), "NO2": (40, 70, 150, 200), "PM10": (20, 50, 100, 200),
             "PM2.5": (10, 25, 50, 75), "O3": (60, 100, 140, 180), "CO": (4400, 9400, 12400, 15400)}


def owm_level(pollutant, conc):
    bands = OWM_BANDS.get(pollutant)
    if bands is None or conc is None:
        return None
    for i, hi in enumerate(bands, start=1):
        if conc < hi:
            return i
    return 5


def us_aqi(pollutant, conc):
    if conc is None or pollutant not in AQI_BP:
        return None
    for lo, hi, ilo, ihi in AQI_BP[pollutant]:
        if conc <= hi:
            c = max(conc, lo)
            return int(round((ihi - ilo) / (hi - lo) * (c - lo) + ilo))
    return 500


def aqi_cat(aqi):
    for lim, label, style, advice in AQI_CATS:
        if aqi is not None and aqi <= lim:
            return label, style, advice
    return "Unknown", "dim", ""


# ----------------------------------------------------------------------------
# sun and moon (computed locally)
# ----------------------------------------------------------------------------

def _sun_ut_hours(day, lat, lon, zenith, rising):
    n = day.timetuple().tm_yday
    lng_hour = lon / 15.0
    t = n + ((6 if rising else 18) - lng_hour) / 24.0
    m = 0.9856 * t - 3.289
    L = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))) % 360
    ra += math.floor(L / 90) * 90 - math.floor(ra / 90) * 90
    ra /= 15.0
    sin_dec = 0.39782 * math.sin(math.radians(L))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = ((math.cos(math.radians(zenith)) - sin_dec * math.sin(math.radians(lat)))
             / (cos_dec * math.cos(math.radians(lat))))
    if cos_h > 1 or cos_h < -1:
        return None
    h = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    big_t = h / 15.0 + ra - 0.06571 * t - 6.622
    return (big_t - lng_hour) % 24


def sun_event(day, lat, lon, tz, zenith=90.833, rising=True):
    ut = _sun_ut_hours(day, lat, lon, zenith, rising)
    if ut is None:
        return None
    ev = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc) + dt.timedelta(hours=ut)
    local = ev.astimezone(tz) if tz else ev.astimezone()
    if local.date() < day:
        ev += dt.timedelta(days=1)
    elif local.date() > day:
        ev -= dt.timedelta(days=1)
    return int(ev.timestamp())


def sun_day(day, lat, lon, tz):
    out = {}
    for name, zen in (("sun", 90.833), ("civil", 96.0), ("nautical", 102.0),
                      ("astro", 108.0), ("golden", 84.0)):
        out[name] = (sun_event(day, lat, lon, tz, zen, True),
                     sun_event(day, lat, lon, tz, zen, False))
    rise, sset = out["sun"]
    out["noon"] = (rise + sset) // 2 if rise and sset else None
    out["length"] = (sset - rise) if rise and sset else None
    out["polar"] = None
    if rise is None and sset is None:
        # No sunrise or sunset at all: midnight sun or polar night.  Look at
        # the sun's height at local solar noon to tell which.
        base = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp()
        noon = int(base + (12 - lon / 15.0) * 3600)
        up = sun_altitude(noon, lat, lon) > -0.833
        out["polar"] = "day" if up else "night"
        out["length"] = 86400 if up else 0
        out["noon"] = noon
    return out


def sun_altitude(epoch, lat, lon):
    """Approximate solar elevation angle in degrees."""
    d = epoch / 86400.0 + 2440587.5 - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    e = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(e) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(e) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians((gmst * 15 + lon) % 360) - ra
    la = math.radians(lat)
    alt = math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha))
    return math.degrees(alt)


SYNODIC = 29.530588853
MOON_NAMES = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
              "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]


def moon_name(frac):
    frac %= 1.0
    tol = 1.0 / SYNODIC
    for center, idx in ((0.0, 0), (0.25, 2), (0.5, 4), (0.75, 6), (1.0, 0)):
        if abs(frac - center) <= tol:
            return MOON_NAMES[idx]
    return MOON_NAMES[1 + 2 * int(frac * 4)]


def moon_info(epoch):
    jd = epoch / 86400.0 + 2440587.5
    age = (jd - 2451550.1) % SYNODIC
    illum = (1 - math.cos(2 * math.pi * age / SYNODIC)) / 2
    return age, illum, moon_name(age / SYNODIC)


def next_moon_phases(epoch, count=4):
    """Approximate dates of the next principal phases: [(name, epoch), ...]."""
    age, _illum, _name = moon_info(epoch)
    out = []
    for k in range(12):
        for frac, name in ((0.0, "New"), (0.25, "First"), (0.5, "Full"), (0.75, "Last")):
            target = (frac * SYNODIC - age) % SYNODIC + k * SYNODIC
            if target < 0.2 and k == 0:
                continue
            out.append((epoch + target * 86400, name))
    out.sort()
    seen, res = set(), []
    for t, name in out:
        key = (name, int(t // 86400))
        if key in seen:
            continue
        seen.add(key)
        res.append((name, t))
        if len(res) >= count:
            break
    return res


# ----------------------------------------------------------------------------
# WMO weather codes (Open-Meteo)
# ----------------------------------------------------------------------------

WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Light freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorms", 96: "Thunderstorms with hail", 99: "Severe thunderstorms with hail",
}


def wmo_text(code, is_day=True):
    code = None if code is None else int(code)
    if code is None:
        return None
    text = WMO.get(code, f"Weather code {code}")
    if not is_day and code in (0, 1):
        text = {0: "Clear", 1: "Mostly clear"}[code]
    elif is_day and code == 0:
        text = "Sunny"
    elif is_day and code == 1:
        text = "Mostly sunny"
    return text
