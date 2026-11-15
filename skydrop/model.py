"""Merge every source into one normalized model for the screens."""

import datetime as dt
import html
import json
import re
import time

from . import icons, statemap
from .errlog import LOG
from .alerts import _style as alert_colour, build_area_alerts, vtec_parse
from .util import (bearing_deg, dig, distance_mi, first, iso_epoch, num, parse_valid_time,
                   point_in_rings, safe_text, geojson_rings, sum_opt)
from .wxmath import (POLLUTANTS, EXTRA_POLLUTANTS, SEVERITY_RANK, VTEC_PHEN, VTEC_SIG, compass_deg,
                     conv_si, dewpoint_c, hazard_text, heat_index_c, nws_temp, nws_val, owm_level,
                     parse_wind_text, rh_from, unit_name, us_aqi, wind_chill_c, wmo_text)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None


def ssml_to_text(markup):
    """NOAA Weather Radio SSML script -> plain paragraphs."""
    if not markup:
        return ""
    s = re.sub(r"<\s*break[^>]*>", "\n", markup, flags=re.I)
    s = re.sub(r"</\s*(p|s|paragraph|sentence)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    out, blank = [], False
    for line in s.splitlines():
        line = " ".join(line.split())
        if line:
            out.append(line)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def strip_html(markup):
    if not markup:
        return ""
    s = re.sub(r"<\s*(br|/p|/div|/li|/h\d)[^>]*>", "\n", markup, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def resolve_tz(pts, oc, c25, f25, om=None):
    name = (pts or {}).get("timeZone") or (oc or {}).get("timezone") or (om or {}).get("timezone")
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    if name:
        from . import tzfile    # Python 3.8, or zoneinfo without tzdata
        tz = tzfile.load(name)
        if tz is not None:
            return tz
    off = first(num((oc or {}).get("timezone_offset")), num((c25 or {}).get("timezone")),
                num(dig(f25, "city", "timezone")), num((om or {}).get("utc_offset_seconds")))
    if off is not None:
        return dt.timezone(dt.timedelta(seconds=int(off)))
    return None


def local_date(epoch, tz):
    return dt.datetime.fromtimestamp(epoch, tz).date()


def precip_amount(val):
    if isinstance(val, dict):
        return first(num(val.get("1h")), num(val.get("3h")))
    return num(val)


def parse_obs(p):
    baro = nws_val(p.get("barometricPressure"))
    slp = nws_val(p.get("seaLevelPressure"))
    clouds = []
    for layer in p.get("cloudLayers") or []:
        if isinstance(layer, dict):
            clouds.append((layer.get("amount"), nws_val(layer.get("base"))))
    present = []
    for wx in p.get("presentWeather") or []:
        if isinstance(wx, dict):
            words = [wx.get("intensity"), wx.get("modifier"), (wx.get("weather") or "").replace("_", " ")]
            text = " ".join(w for w in words if w)
            if wx.get("inVicinity"):
                text += " in the vicinity"
            present.append(text)
    return {
        "t": iso_epoch(p.get("timestamp")),
        "desc": safe_text(p.get("textDescription") or ""),
        "station_id": p.get("stationId"), "station_name": p.get("stationName"),
        "elevation": nws_val(p.get("elevation")),
        "temp": nws_val(p.get("temperature")),
        "dew": nws_val(p.get("dewpoint")),
        "rh": nws_val(p.get("relativeHumidity")),
        "wind": nws_val(p.get("windSpeed")),
        "wind_deg": nws_val(p.get("windDirection")),
        "gust": nws_val(p.get("windGust")),
        "press": first(baro, slp),
        "slp": slp,
        "vis": nws_val(p.get("visibility")),
        "heat": nws_val(p.get("heatIndex")),
        "chill": nws_val(p.get("windChill")),
        "p1": nws_val(p.get("precipitationLastHour")),
        "p3": nws_val(p.get("precipitationLast3Hours")),
        "p6": nws_val(p.get("precipitationLast6Hours")),
        "max24": nws_val(p.get("maxTemperatureLast24Hours")),
        "min24": nws_val(p.get("minTemperatureLast24Hours")),
        "clouds": clouds,
        "present": present,
        "metar": p.get("rawMessage") or "",
    }


def build_history(j):
    out = []
    cutoff = time.time() - 26 * 3600
    for feat in (j or {}).get("features") or []:
        row = parse_obs(feat.get("properties") or {})
        if row["t"] is not None and row["t"] >= cutoff:
            out.append(row)
    out.sort(key=lambda r: r["t"], reverse=True)
    return out


def build_obs(j, hist):
    p = (j or {}).get("properties") or {}
    o = parse_obs(p) if p else {}
    if hist and (not o or o.get("temp") is None):
        for row in hist:
            if row.get("temp") is not None:
                o = dict(row)
                o["fallback"] = True
                break
    if not o or not o.get("t"):
        return {}
    now = time.time()
    day = [r for r in hist if r["t"] >= now - 86400]
    temps = [(r["temp"], r["t"]) for r in day if r["temp"] is not None]
    if o.get("temp") is not None:
        temps.append((o["temp"], o["t"]))
    if temps:
        o["max24"], o["max24_t"] = max(temps)
        o["min24"], o["min24_t"] = min(temps)
    if o.get("press") is not None:
        target, best = o["t"] - 3 * 3600, None
        for r in hist:
            if r.get("press") is None:
                continue
            gap = abs(r["t"] - target)
            if gap <= 2400 and (best is None or gap < best[0]):
                best = (gap, r)
        if best:
            o["trend"] = o["press"] - best[1]["press"]
    groups = {}
    for r in day:
        if r.get("p1") is not None:
            g = (r["t"] + 7 * 60) // 3600
            groups[g] = max(groups.get(g, 0.0), r["p1"])
    o["p24"] = sum(groups.values()) if groups else None
    o["kind"] = icons.from_text(o.get("desc") or "; ".join(o.get("present") or []))
    return o


# ----------------------------------------------------------------------------
# Open-Meteo
# ----------------------------------------------------------------------------

def build_om(om, tz):
    """Open-Meteo response (timeformat=unixtime) -> current/hourly/daily/min15."""
    if not om:
        return {}
    out = {"current": {}, "hourly": {}, "daily": {}, "min15": [], "elevation": num(om.get("elevation"))}
    c = om.get("current") or {}
    if c:
        code = num(c.get("weather_code"))
        day = bool(num(c.get("is_day")) if c.get("is_day") is not None else True)
        out["current"] = {
            "src": "Open-Meteo", "dt": num(c.get("time")),
            "temp": num(c.get("temperature_2m")), "feels": num(c.get("apparent_temperature")),
            "humidity": num(c.get("relative_humidity_2m")), "dew": num(c.get("dew_point_2m")),
            "pressure": num(c.get("pressure_msl")), "uvi": num(c.get("uv_index")),
            "clouds": num(c.get("cloud_cover")), "vis": num(c.get("visibility")),
            "wind": num(c.get("wind_speed_10m")), "wind_deg": num(c.get("wind_direction_10m")),
            "gust": num(c.get("wind_gusts_10m")), "desc": wmo_text(code, day),
            "rain_1h": num(c.get("precipitation")), "snow_1h": (num(c.get("snowfall")) or 0) * 10 or None,
            "kind": icons.from_wmo(code), "is_day": day, "wmo": code,
        }
        if out["current"]["dew"] is None:
            out["current"]["dew"] = dewpoint_c(out["current"]["temp"], out["current"]["humidity"])
    h = om.get("hourly") or {}
    times = h.get("time") or []

    def col(name, i):
        arr = h.get(name)
        return num(arr[i]) if isinstance(arr, list) and i < len(arr) else None

    for i, t in enumerate(times):
        t = int(t)
        code = col("weather_code", i)
        day = col("is_day", i)
        day = True if day is None else bool(day)
        snow_cm = col("snowfall", i)
        out["hourly"][t] = {
            "t": t, "temp": col("temperature_2m", i), "feels": col("apparent_temperature", i),
            "hum": col("relative_humidity_2m", i), "dew": col("dew_point_2m", i),
            "pop": col("precipitation_probability", i), "press": col("pressure_msl", i),
            "clouds": col("cloud_cover", i), "vis": col("visibility", i), "wind": col("wind_speed_10m", i),
            "wind_deg": col("wind_direction_10m", i), "gust": col("wind_gusts_10m", i),
            "uvi": col("uv_index", i),
            "rain": first(sum_opt(col("rain", i), col("showers", i)), col("precipitation", i)),
            "snow": (snow_cm or 0.0) * 10, "precip": col("precipitation", i), "cape": col("cape", i),
            "snow_depth": col("snow_depth", i), "desc": wmo_text(code, day), "wmo": code,
            "kind": icons.from_wmo(code), "is_day": day,
        }
    d = om.get("daily") or {}
    dtimes = d.get("time") or []

    def dcol(name, i):
        arr = d.get(name)
        return num(arr[i]) if isinstance(arr, list) and i < len(arr) else None

    for i, t in enumerate(dtimes):
        date = local_date(int(t) + 6 * 3600, tz)  # +6h guards against DST/offset edge cases
        code = dcol("weather_code", i)
        out["daily"][date] = {
            "date": date, "hi": dcol("temperature_2m_max", i), "lo": dcol("temperature_2m_min", i),
            "f_hi": dcol("apparent_temperature_max", i), "f_lo": dcol("apparent_temperature_min", i),
            "sunrise": dcol("sunrise", i), "sunset": dcol("sunset", i),
            "daylight": dcol("daylight_duration", i), "sunshine": dcol("sunshine_duration", i),
            "uvi": dcol("uv_index_max", i), "precip": dcol("precipitation_sum", i),
            "rain": dcol("rain_sum", i), "showers": dcol("showers_sum", i),
            "snow": (dcol("snowfall_sum", i) or 0.0) * 10, "precip_hours": dcol("precipitation_hours", i),
            "pop": dcol("precipitation_probability_max", i), "wind": dcol("wind_speed_10m_max", i),
            "gust": dcol("wind_gusts_10m_max", i), "wind_deg": dcol("wind_direction_10m_dominant", i),
            "desc": wmo_text(code, True), "wmo": code, "kind": icons.from_wmo(code),
        }
    m = om.get("minutely_15") or {}
    mt = m.get("time") or []
    now = time.time()
    for i, t in enumerate(mt):
        t = int(t)
        if now - 900 <= t <= now + 12 * 3600:
            p = m.get("precipitation")
            tt = m.get("temperature_2m")
            out["min15"].append({"t": t, "precip": num(p[i]) if p and i < len(p) else None,
                                 "temp": num(tt[i]) if tt and i < len(tt) else None})
    return out


# ----------------------------------------------------------------------------
# current conditions
# ----------------------------------------------------------------------------

def build_current(oc, c25, omc=None):
    c = (oc or {}).get("current")
    if c:
        w = (c.get("weather") or [{}])[0]
        cur = {
            "src": f"OpenWeather One Call {oc.get('version', '')}".strip(), "dt": c.get("dt"),
            "temp": num(c.get("temp")), "feels": num(c.get("feels_like")),
            "humidity": num(c.get("humidity")), "dew": num(c.get("dew_point")),
            "pressure": num(c.get("pressure")), "uvi": num(c.get("uvi")),
            "clouds": num(c.get("clouds")), "vis": num(c.get("visibility")),
            "wind": num(c.get("wind_speed")), "wind_deg": num(c.get("wind_deg")),
            "gust": num(c.get("wind_gust")), "desc": w.get("description"),
            "rain_1h": precip_amount(c.get("rain")), "snow_1h": precip_amount(c.get("snow")),
            "sunrise": c.get("sunrise"), "sunset": c.get("sunset"),
            "kind": icons.from_owm(w.get("id")), "is_day": "n" not in str(w.get("icon") or "d"),
        }
    elif c25:
        mn = c25.get("main") or {}
        w = (c25.get("weather") or [{}])[0]
        t, rh = num(mn.get("temp")), num(mn.get("humidity"))
        cur = {
            "src": "OpenWeather (free)", "dt": c25.get("dt"),
            "temp": t, "feels": num(mn.get("feels_like")), "humidity": rh,
            "dew": dewpoint_c(t, rh), "pressure": num(mn.get("pressure")),
            "sea_level": num(mn.get("sea_level")), "grnd_level": num(mn.get("grnd_level")),
            "uvi": None, "clouds": num(dig(c25, "clouds", "all")), "vis": num(c25.get("visibility")),
            "wind": num(dig(c25, "wind", "speed")), "wind_deg": num(dig(c25, "wind", "deg")),
            "gust": num(dig(c25, "wind", "gust")), "desc": w.get("description"),
            "rain_1h": precip_amount(c25.get("rain")), "snow_1h": precip_amount(c25.get("snow")),
            "sunrise": dig(c25, "sys", "sunrise"), "sunset": dig(c25, "sys", "sunset"),
            "free": True, "kind": icons.from_owm(w.get("id")), "is_day": "n" not in str(w.get("icon") or "d"),
        }
    elif omc:
        return dict(omc)
    else:
        return {}
    if omc:  # Open-Meteo fills what the OpenWeather endpoint left out (UV on the free tier)
        for k in ("uvi", "vis", "gust", "clouds"):
            if cur.get(k) is None and omc.get(k) is not None:
                cur[k] = omc[k]
                cur.setdefault("filled", []).append(k)
    return cur


# ----------------------------------------------------------------------------
# NWS gridpoints and the hourly forecast
# ----------------------------------------------------------------------------

GRID_LAYERS = [
    ("skyCover", "sky", False), ("apparentTemperature", "apparent", False),
    ("windGust", "nws_gust", False), ("probabilityOfThunder", "thunder", False),
    ("ceilingHeight", "ceiling", False), ("mixingHeight", "mixing", False),
    ("relativeHumidity", "grid_rh", False), ("dewpoint", "grid_dew", False),
    ("visibility", "grid_vis", False), ("pressure", "grid_press", False),
    ("heatIndex", "heat_index", False), ("windChill", "wind_chill", False),
    ("wetBulbGlobeTemperature", "wbgt", False), ("heatRisk", "heatrisk", False),
    ("snowLevel", "snow_level", False), ("lightningActivityLevel", "lal", False),
    ("transportWindSpeed", "transport", False), ("grasslandFireDangerIndex", "fire_danger", False),
    ("redFlagThreatIndex", "red_flag", False), ("dispersionIndex", "dispersion", False),
    ("probabilityOfTropicalStormWinds", "p_ts_wind", False),
    ("probabilityOfHurricaneWinds", "p_hu_wind", False),
    ("potentialOf40mphWindGusts", "p_gust40", False), ("potentialOf60mphWindGusts", "p_gust60", False),
    ("weather", "grid_wx", False), ("hazards", "hazards", False),
    ("quantitativePrecipitation", "qpf", True), ("snowfallAmount", "snowfall", True),
    ("iceAccumulation", "ice", True),
]


def build_grid(j, hours=52):
    p = (j or {}).get("properties") or {}
    if not p:
        return {}
    lo = int(time.time()) // 3600 * 3600 - 3600
    hi = lo + (hours + 4) * 3600
    day_hi = lo + 11 * 86400

    def expand(name, blocks, limit):
        layer = p.get(name) or {}
        unit = unit_name(layer.get("uom"))
        out = {}
        for item in layer.get("values") or []:
            try:
                t0, secs = parse_valid_time(item["validTime"])
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
            raw = item.get("value")
            val = raw if isinstance(raw, (list, dict)) else conv_si(num(raw), unit)
            for k in range(max(1, secs // 3600)):
                h = t0 + k * 3600
                if h > limit:
                    break
                if h >= lo:
                    out[h] = (val, t0, secs) if blocks else val
        return out

    grid = {key: expand(name, block, hi) for name, key, block in GRID_LAYERS}
    grid["heatrisk_days"] = expand("heatRisk", False, day_hi)
    grid["hazard_days"] = expand("hazards", False, day_hi)
    grid["updated"] = iso_epoch(p.get("updateTime"))
    return grid


def nearest_3h(f25, h):
    best = None
    for x in (f25 or {}).get("list") or []:
        gap = abs(int(x.get("dt") or 0) - h)
        if gap <= 5400 and (best is None or gap < best[0]):
            best = (gap, x)
    return best[1] if best else None


def owm_step(x):
    w = (x.get("weather") or [{}])[0]
    pop = num(x.get("pop"))
    return {
        "temp": num(x.get("temp")), "feels": num(x.get("feels_like")),
        "press": num(x.get("pressure")), "hum": num(x.get("humidity")), "dew": num(x.get("dew_point")),
        "uvi": num(x.get("uvi")), "clouds": num(x.get("clouds")), "vis": num(x.get("visibility")),
        "wind": num(x.get("wind_speed")), "wind_deg": num(x.get("wind_deg")),
        "gust": num(x.get("wind_gust")), "desc": w.get("description"),
        "pop": None if pop is None else pop * 100,
        "rain": precip_amount(x.get("rain")) or 0.0, "snow": precip_amount(x.get("snow")) or 0.0,
        "kind": icons.from_owm(w.get("id")), "is_day": "n" not in str(w.get("icon") or "d"),
    }


def build_min15(oc, om_min15=None):
    now = time.time()
    out = []
    for x in (oc or {}).get("min15") or []:
        t = int(x.get("dt") or 0)
        if now - 900 <= t <= now + 12 * 3600:
            out.append(dict(owm_step(x), t=t, src="owm"))
    if out:
        return out
    for x in om_min15 or []:
        out.append({"t": x["t"], "temp": x.get("temp"), "rain": x.get("precip"), "pop": None, "src": "om"})
    return out


_OM_FILL = ("uvi", "vis", "press", "clouds", "dew", "hum", "gust", "pop", "feels", "cape")


def build_hourly(oc, nh, grid, f25, om_hourly=None, hours=48, tz=None):
    now_h = int(time.time()) // 3600 * 3600
    end = now_h + hours * 3600
    rows = {}
    for x in (oc or {}).get("hourly") or []:
        h = int(x.get("dt") or 0) // 3600 * 3600
        if now_h <= h < end:
            rows[h] = dict(owm_step(x), t=h, src="owm")
    for per in dig(nh, "properties", "periods", default=[]) or []:
        st = iso_epoch(per.get("startTime"))
        if st is None:
            continue
        h = st // 3600 * 3600
        if not now_h <= h < end:
            continue
        r = rows.setdefault(h, {"t": h, "src": "nws"})
        r.update(
            nws_desc=per.get("shortForecast"), nws_temp=nws_temp(per),
            nws_pop=nws_val(per.get("probabilityOfPrecipitation")),
            nws_dew=nws_val(per.get("dewpoint")), nws_rh=nws_val(per.get("relativeHumidity")),
            nws_wind=parse_wind_text(per.get("windSpeed")), nws_wind_dir=per.get("windDirection"),
            nws_wind_raw=per.get("windSpeed"), nws_gust_period=parse_wind_text(per.get("windGust")),
            trend=per.get("temperatureTrend"), nws_day=per.get("isDaytime"),
        )
    grid = grid or {}
    om_hourly = om_hourly or {}
    for h, r in rows.items():
        for _name, key, _block in GRID_LAYERS:
            r[key] = (grid.get(key) or {}).get(h)
        r["nws_gust"] = first(r.get("nws_gust_period"), r.get("nws_gust"))
        if r["src"] != "nws":
            continue
        r["temp"] = r.get("nws_temp")
        r["feels"] = first(r.get("apparent"), r.get("nws_temp"))
        r["desc"] = r.get("nws_desc")
        r["pop"] = r.get("nws_pop")
        r["hum"] = first(r.get("nws_rh"), r.get("grid_rh"))
        r["dew"] = first(r.get("nws_dew"), r.get("grid_dew"))
        r["wind"] = r.get("nws_wind")
        r["wind_deg"] = compass_deg(r.get("nws_wind_dir"))
        r["gust"] = r.get("nws_gust")
        r["clouds"] = r.get("sky")
        r["vis"] = r.get("grid_vis")
        r["press"] = r.get("grid_press")
        r["kind"] = icons.from_text(r.get("nws_desc"))
        r["is_day"] = bool(r.get("nws_day")) if r.get("nws_day") is not None else None
        blk = nearest_3h(f25, h)
        if blk:
            if r["press"] is None:
                r["press"] = num(dig(blk, "main", "pressure"))
            if r["vis"] is None:
                r["vis"] = num(blk.get("visibility"))
            if r["gust"] is None:
                r["gust"] = num(dig(blk, "wind", "gust"))
        q = r.get("qpf")
        if q and q[0] is not None:
            r["rain"] = q[0] / max(1, q[2] // 3600)
    # Open-Meteo: fill gaps, then cover hours nobody else does
    for h in range(now_h, end, 3600):
        o = om_hourly.get(h)
        if not o:
            continue
        r = rows.get(h)
        if r is None:
            r = dict(o, src="om")
            rows[h] = r
            continue
        for k in _OM_FILL:
            if r.get(k) is None and o.get(k) is not None:
                r[k] = o[k]
        if r.get("is_day") is None:
            r["is_day"] = o.get("is_day")
        if r.get("rain") is None and o.get("rain") is not None:
            r["rain"] = o["rain"]
        r["om_temp"] = o.get("temp")
        r["om_desc"] = o.get("desc")
    out = [rows[h] for h in sorted(rows) if rows[h].get("temp") is not None]
    for r in out:
        if r.get("is_day") is None:
            hr = dt.datetime.fromtimestamp(r["t"], tz).hour
            r["is_day"] = 6 <= hr < 19
        if not r.get("kind") or r["kind"] == "unknown":
            r["kind"] = icons.from_text(r.get("desc")) if r.get("desc") else icons.from_clouds(r.get("clouds"), r.get("pop"))
    return out


# ----------------------------------------------------------------------------
# daily forecast
# ----------------------------------------------------------------------------

def longrange_desc(clouds, total, snowy):
    kind = "snow" if snowy else "rain"
    if total is not None and total >= 5:
        return f"{kind.capitalize()} likely (outlook)"
    if total is not None and total >= 1:
        return f"Chance of {kind} (outlook)"
    if clouds is None:
        return "Outlook"
    sky = ("Mostly sunny" if clouds < 25 else "Partly cloudy" if clouds < 60
           else "Mostly cloudy" if clouds < 85 else "Cloudy")
    return f"{sky} (outlook)"


def owm_day(x, src):
    w = (x.get("weather") or [{}])[0]
    T, FL = x.get("temp") or {}, x.get("feels_like") or {}
    if not isinstance(T, dict):
        T = {"day": T}
    pop = num(x.get("pop"))
    r = {
        "src": src, "hi": num(T.get("max")), "lo": num(T.get("min")),
        "morn": num(T.get("morn")), "noon": num(T.get("day")), "eve": num(T.get("eve")), "night": num(T.get("night")),
        "f_morn": num(FL.get("morn")), "f_noon": num(FL.get("day")),
        "f_eve": num(FL.get("eve")), "f_night": num(FL.get("night")),
        "desc": w.get("description"), "summary": x.get("summary"),
        "pop": None if pop is None else pop * 100,
        "rain": precip_amount(x.get("rain")) or 0.0, "snow": precip_amount(x.get("snow")) or 0.0,
        "wind": num(first(x.get("wind_speed"), x.get("speed"))),
        "wind_deg": num(first(x.get("wind_deg"), x.get("deg"))),
        "gust": num(first(x.get("wind_gust"), x.get("gust"))),
        "hum": num(x.get("humidity")), "dew": num(x.get("dew_point")), "uvi": num(x.get("uvi")),
        "press": num(x.get("pressure")), "clouds": num(x.get("clouds")),
        "sunrise": x.get("sunrise") or None, "sunset": x.get("sunset") or None,
        "moonrise": x.get("moonrise") or None, "moonset": x.get("moonset") or None,
        "moon_phase": num(x.get("moon_phase")), "kind": icons.from_owm(w.get("id")),
    }
    if r["dew"] is None:
        r["dew"] = dewpoint_c(r["noon"], r["hum"])
    return r


def build_daily(oc, d16, nf, f25, grid, tz, om_daily=None, ndays=10):
    days = {}

    def day(d):
        return days.setdefault(d, {"date": d})

    today = dt.datetime.now(tz).date()
    src = f"One Call {(oc or {}).get('version', '')}".strip()
    for x in (oc or {}).get("daily") or []:
        if x.get("dt"):
            day(local_date(x["dt"], tz)).update(owm_day(x, src))
    for s in (oc or {}).get("longrange") or []:
        try:
            d = dt.date.fromisoformat(str(s.get("date"))[:10])
        except ValueError:
            continue
        r = day(d)
        if r.get("src"):
            continue
        if s.get("error"):
            r.update(src="long-range", error=s["error"])
            continue
        T = s.get("temperature") or {}
        total = num(dig(s, "precipitation", "total"))
        hi = num(T.get("max"))
        snowy = hi is not None and hi <= 1.0
        r.update(
            src="long-range", hi=hi, lo=num(T.get("min")),
            morn=num(T.get("morning")), noon=num(T.get("afternoon")),
            eve=num(T.get("evening")), night=num(T.get("night")),
            hum=num(dig(s, "humidity", "afternoon")), press=num(dig(s, "pressure", "afternoon")),
            clouds=num(dig(s, "cloud_cover", "afternoon")),
            wind=num(dig(s, "wind", "max", "speed")), wind_deg=num(dig(s, "wind", "max", "direction")),
            rain=0.0 if snowy else total, snow=total if snowy else 0.0,
        )
        r["desc"] = longrange_desc(r["clouds"], total, snowy)
        r["dew"] = dewpoint_c(r["noon"], r["hum"])
        r["kind"] = icons.from_text(r["desc"])
    for x in (d16 or {}).get("list") or []:
        r = day(local_date(x["dt"], tz))
        if not r.get("src"):
            r.update(owm_day(x, "OpenWeather 16-day"))
    for per in dig(nf, "properties", "periods", default=[]) or []:
        st = iso_epoch(per.get("startTime"))
        if st is None:
            continue
        info = {
            "name": per.get("name") or "", "short": per.get("shortForecast") or "",
            "text": safe_text(per.get("detailedForecast") or ""), "temp": nws_temp(per),
            "pop": nws_val(per.get("probabilityOfPrecipitation")),
            "dir": per.get("windDirection") or "", "speed": per.get("windSpeed"),
            "gust_raw": per.get("windGust"), "trend": per.get("temperatureTrend"),
            "wind_ms": parse_wind_text(per.get("windSpeed")),
            "gust_ms": parse_wind_text(per.get("windGust")),
            "start": st, "is_day": bool(per.get("isDaytime")),
            "kind": icons.from_text(per.get("shortForecast")),
        }
        r = day(local_date(st, tz))
        is_day = bool(per.get("isDaytime"))
        r["nws_day" if is_day else "nws_night"] = info
        if r.get("src") not in (None, "NWS"):
            continue
        r["src"] = "NWS"
        if is_day:
            r["hi"], r["desc"] = info["temp"], info["short"]
            r["kind"] = info["kind"]
        else:
            r["lo"] = info["temp"]
            r["desc"] = r.get("desc") or info["short"]
            r["kind"] = r.get("kind") or info["kind"]
        if info["pop"] is not None:
            r["pop"] = max(r.get("pop") or 0, info["pop"])
        if info["wind_ms"] is not None:
            r["wind"] = max(r.get("wind") or 0, info["wind_ms"])
        if info["gust_ms"] is not None:
            r["gust"] = max(r.get("gust") or 0, info["gust_ms"])
        if r.get("wind_deg") is None:
            r["wind_deg"] = compass_deg(info["dir"])
    if f25:
        groups = {}
        for x in f25.get("list") or []:
            groups.setdefault(local_date(x["dt"], tz), []).append(x)
        for d, xs in groups.items():
            r = day(d)
            if r.get("src") not in (None, "NWS"):
                continue

            def vals(*path):
                return [v for v in (num(dig(x, *path)) for x in xs) if v is not None]

            def avg(v):
                return sum(v) / len(v) if v else None

            temps = vals("main", "temp")
            mid = min(xs, key=lambda x: abs(dt.datetime.fromtimestamp(x["dt"], tz).hour - 14))
            gust, speed, pops = vals("wind", "gust"), vals("wind", "speed"), vals("pop")
            agg = {
                "hi": max(temps) if temps else None, "lo": min(temps) if temps else None,
                "hum": avg(vals("main", "humidity")), "press": avg(vals("main", "pressure")),
                "clouds": avg(vals("clouds", "all")),
                "rain": sum(vals("rain", "3h")), "snow": sum(vals("snow", "3h")),
                "gust": max(gust) if gust else None, "wind": max(speed) if speed else None,
                "pop": max(pops) * 100 if pops else None,
                "desc": dig(mid, "weather", 0, "description"), "wind_deg": num(dig(mid, "wind", "deg")),
                "partial": len(xs) < 8, "kind": icons.from_owm(dig(mid, "weather", 0, "id")),
            }
            for k, val in agg.items():
                if r.get(k) is None and val is not None:
                    r[k] = val
            if not r.get("src"):
                r["src"] = "OpenWeather 3-hour"
            if r.get("dew") is None and r.get("hi") is not None:
                mean = (r["hi"] + first(r.get("lo"), r["hi"])) / 2
                r["dew"] = dewpoint_c(mean, r.get("hum"))
    # Open-Meteo: fill what's missing, then extend the outlook
    for d, o in (om_daily or {}).items():
        if d < today:
            continue
        r = days.get(d)
        if r is None or not (r.get("src") or r.get("error")):
            r = day(d)
            r.update({k: v for k, v in o.items() if k != "date"})
            r["src"] = "Open-Meteo"
            r["rain"] = first(o.get("precip"), o.get("rain"))
            continue
        for k in ("uvi", "sunshine", "daylight", "f_hi", "f_lo", "gust", "precip_hours"):
            if r.get(k) is None and o.get(k) is not None:
                r[k] = o[k]
        for k in ("hi", "lo", "pop", "wind", "wind_deg", "sunrise", "sunset"):
            if r.get(k) is None and o.get(k) is not None:
                r[k] = o[k]
        if r.get("rain") is None or (r.get("src") == "NWS" and not r.get("rain")):
            r["rain"] = first(o.get("rain"), o.get("precip"))
            r["rain_src"] = "Open-Meteo"
        if not r.get("snow") and o.get("snow"):
            r["snow"] = o["snow"]
        r["om"] = o
    grid = grid or {}
    for h, val in (grid.get("heatrisk_days") or {}).items():
        if val is None:
            continue
        r = days.get(local_date(h, tz))
        if r is not None:
            r["heatrisk"] = max(r.get("heatrisk") or 0, val)
    for h, val in (grid.get("hazard_days") or {}).items():
        r = days.get(local_date(h, tz))
        text = hazard_text(val)
        if r is not None and text:
            names = r.setdefault("hazards", [])
            for name in text.split(", "):
                if name not in names:
                    names.append(name)
    out = [days[d] for d in sorted(days) if d >= today and (days[d].get("src") or days[d].get("error"))][:ndays]
    for r in out:
        if not r.get("kind") or r["kind"] == "unknown":
            r["kind"] = icons.from_text(r.get("desc")) if r.get("desc") else "unknown"
    return out


# ----------------------------------------------------------------------------
# alerts, air, office
# ----------------------------------------------------------------------------

def build_alerts(na, oc):
    now = time.time()
    out, nws_events = [], set()
    for feat in (na or {}).get("features") or []:
        p = feat.get("properties") or {}
        end = iso_epoch(p.get("ends") or p.get("expires"))
        if end is not None and end < now:
            continue
        params = p.get("parameters") or {}
        extras = []
        for key, label in (("maxWindGust", "Max wind gust"), ("maxHailSize", "Max hail size"),
                           ("tornadoDetection", "Tornado"), ("tornadoDamageThreat", "Tornado damage threat"),
                           ("thunderstormDamageThreat", "Storm damage threat"),
                           ("flashFloodDetection", "Flash flood"), ("flashFloodDamageThreat", "Flood damage threat"),
                           ("waterspoutDetection", "Waterspout")):
            val = params.get(key)
            if isinstance(val, list) and val:
                extras.append((label, ", ".join(str(v) for v in val)))
        headline = first(*(params.get("NWSheadline") or [None]))
        vt = next((x for x in map(vtec_parse, params.get("VTEC") or []) if x), None)
        _rank, rgb = alert_colour(vt["phen"] if vt else "", vt["sig"] if vt else "", p.get("event"))
        out.append({
            "id": p.get("id") or feat.get("id"),
            "src": "NWS", "event": p.get("event") or "Alert", "severity": p.get("severity"),
            "urgency": p.get("urgency"), "certainty": p.get("certainty"),
            "msgtype": p.get("messageType"), "headline": p.get("headline"),
            "nws_headline": headline, "desc": safe_text(p.get("description")),
            "instruction": safe_text(p.get("instruction")), "note": p.get("note"),
            "start": iso_epoch(p.get("onset") or p.get("effective")), "end": end,
            "sender": p.get("senderName"), "area": p.get("areaDesc"), "extras": extras,
            "geometry": feat.get("geometry"), "rgb": rgb,
        })
        nws_events.add((p.get("event") or "").lower())
    for a in (oc or {}).get("alerts") or []:
        sender = a.get("sender_name") or ""
        event = a.get("event") or "Alert"
        end = num(a.get("end"))
        if end is not None and end < now:
            continue
        if na is not None and ("weather service" in sender.lower() or event.lower() in nws_events):
            continue
        out.append({"id": a.get("id"), "src": "OpenWeather", "event": event, "severity": None, "headline": None,
                    "nws_headline": None, "desc": safe_text(a.get("description")), "instruction": None,
                    "note": None, "start": a.get("start"), "end": a.get("end"),
                    "sender": sender, "area": None, "extras": [("Tags", ", ".join(a.get("tags") or []))]
                    if a.get("tags") else [], "rgb": alert_colour("", "", event)[1]})
    out.sort(key=lambda a: (SEVERITY_RANK.get(a.get("severity"), 4), a.get("start") or 0))
    return out


def air_row(components):
    subs = []
    for pol, key, factor, _u, _n in POLLUTANTS[:3]:
        c = num(components.get(key))
        if c is not None:
            subs.append(us_aqi(pol, c * factor))
    return max(subs) if subs else None


def build_air(air, fc, hist):
    cur = dig(air, "list", 0)
    if not cur:
        return {}
    comp = cur.get("components") or {}
    rows, best = [], (None, None)
    for pol, key, factor, unit, name in POLLUTANTS:
        c = num(comp.get(key))
        sub = us_aqi(pol, c * factor) if c is not None else None
        rows.append({"pol": pol, "name": name, "conc": c, "conv": None if c is None else c * factor,
                     "unit": unit, "aqi": sub, "owm": owm_level(pol, c)})
        if sub is not None and (best[0] is None or sub > best[0]):
            best = (sub, pol)
    extra = [{"pol": p, "name": n, "conc": num(comp.get(k))} for p, k, n in EXTRA_POLLUTANTS]
    forecast = [(x["dt"], air_row(x.get("components") or {})) for x in (fc or {}).get("list") or []
                if x.get("dt")]
    past = [(x["dt"], air_row(x.get("components") or {}), num(dig(x, "components", "pm2_5")))
            for x in (hist or {}).get("list") or [] if x.get("dt")]
    return {"src": "OpenWeather", "t": cur.get("dt"), "owm": num(dig(cur, "main", "aqi")), "us": best[0],
            "dominant": best[1], "rows": rows, "extra": extra,
            "forecast": [(t, a) for t, a in forecast if a is not None],
            "history": sorted((t, a, pm) for t, a, pm in past if a is not None)}


_OM_POLL = [("PM2.5", "pm2_5", "us_aqi_pm2_5", 1.0, "ug/m3", "Fine particles"),
            ("PM10", "pm10", "us_aqi_pm10", 1.0, "ug/m3", "Coarse particles"),
            ("O3", "ozone", "us_aqi_ozone", 24.45 / 48.00, "ppb", "Ozone"),
            ("NO2", "nitrogen_dioxide", "us_aqi_nitrogen_dioxide", 24.45 / 46.01, "ppb", "Nitrogen dioxide"),
            ("SO2", "sulphur_dioxide", "us_aqi_sulphur_dioxide", 24.45 / 64.07, "ppb", "Sulfur dioxide"),
            ("CO", "carbon_monoxide", "us_aqi_carbon_monoxide", 24.45 / 28.01 / 1000.0, "ppm", "Carbon monoxide")]


def build_air_om(j):
    c = (j or {}).get("current") or {}
    if not c:
        return {}
    rows, best = [], (None, None)
    for pol, key, aqk, factor, unit, name in _OM_POLL:
        conc = num(c.get(key))
        sub = num(c.get(aqk))
        sub = int(round(sub)) if sub is not None else None
        rows.append({"pol": pol, "name": name, "conc": conc, "conv": None if conc is None else conc * factor,
                     "unit": unit, "aqi": sub, "owm": owm_level(pol, conc)})
        if sub is not None and (best[0] is None or sub > best[0]):
            best = (sub, pol)
    h = j.get("hourly") or {}
    times = h.get("time") or []
    aq = h.get("us_aqi") or []
    pm = h.get("pm2_5") or []
    uv = h.get("uv_index") or []
    now = time.time()
    series = [(int(t), num(aq[i]) if i < len(aq) else None, num(pm[i]) if i < len(pm) else None,
               num(uv[i]) if i < len(uv) else None) for i, t in enumerate(times)]
    us = num(c.get("us_aqi"))
    return {"src": "Open-Meteo (CAMS)", "t": num(c.get("time")), "owm": None,
            "us": int(round(us)) if us is not None else best[0], "dominant": best[1], "rows": rows, "extra": [],
            "forecast": [(t, int(a)) for t, a, _p, _u in series if a is not None and t >= now - 3600],
            "history": [(t, int(a), p) for t, a, p, _u in series if a is not None and now - 86400 <= t < now],
            "uv": [(t, u) for t, _a, _p, u in series if u is not None and t >= now - 3600],
            "dust": num(c.get("dust")), "aod": num(c.get("aerosol_optical_depth"))}


def build_office(headlines, stories, briefing):
    now = time.time()
    heads = []
    for h in (headlines or {}).get("@graph") or []:
        if isinstance(h, dict):
            heads.append({"title": safe_text(h.get("title") or h.get("name") or ""),
                          "important": bool(h.get("important")), "issued": iso_epoch(h.get("issuanceTime")),
                          "text": safe_text(h.get("summary") or strip_html(h.get("content"))), "link": h.get("link")})
    heads.sort(key=lambda h: (not h["important"], -(h["issued"] or 0)))
    story_list = stories if isinstance(stories, list) else (
        (stories or {}).get("@graph") or (stories or {}).get("stories") or [])
    items = []
    for s in story_list:
        if not isinstance(s, dict):
            continue
        end = iso_epoch(s.get("endTime"))
        if end is not None and end < now:
            continue
        items.append({"title": safe_text(s.get("title") or ""), "text": safe_text(s.get("description") or ""),
                      "alt": safe_text(s.get("altText") or ""), "priority": bool(s.get("priority")),
                      "order": num(s.get("order")) or 9, "start": iso_epoch(s.get("startTime")),
                      "end": end, "link": s.get("download")})
    items.sort(key=lambda s: (not s["priority"], s["order"]))
    brief = None
    if isinstance(briefing, dict) and (briefing.get("title") or briefing.get("description")):
        end = iso_epoch(briefing.get("endTime"))
        if end is None or end >= now:
            brief = {"title": safe_text(briefing.get("title") or ""), "text": safe_text(briefing.get("description") or ""),
                     "updated": iso_epoch(briefing.get("updateTime")), "link": briefing.get("download")}
    return {"headlines": heads, "stories": items, "briefing": brief}


def build_astro(pts, tz):
    a = (pts or {}).get("astronomicalData") or {}
    if not a:
        return {}
    out = {k: iso_epoch(a.get(k)) for k in (
        "sunrise", "sunset", "transit", "civilTwilightBegin", "civilTwilightEnd",
        "nauticalTwilightBegin", "nauticalTwilightEnd", "astronomicalTwilightBegin",
        "astronomicalTwilightEnd")}
    ref = first(out.get("transit"), out.get("sunrise"), out.get("sunset"))
    if ref is None or local_date(ref, tz) != dt.datetime.now(tz).date():
        return {}
    return out


# ----------------------------------------------------------------------------
# new in v2: outlooks, storm reports, warning polygons, almanac, stations
# ----------------------------------------------------------------------------

OUTLOOK_LEVELS = {"TSTM": (0, "General thunderstorms", "label"), "MRGL": (1, "Marginal", "good"),
                  "SLGT": (2, "Slight", "warn"), "ENH": (3, "Enhanced", "accent"), "MDT": (4, "Moderate", "bad"),
                  "HIGH": (5, "High", "bad"), "ELEV": (1, "Elevated", "warn"), "CRIT": (2, "Critical", "accent"),
                  "EXTM": (3, "Extremely critical", "bad"), "SIGN": (6, "Significant", "bad")}


def build_outlooks(j):
    out = []
    now = time.time()
    for r in (j or {}).get("data") or []:
        issue, expire = iso_epoch(r.get("issue")), iso_epoch(r.get("expire"))
        if expire is not None and expire < now:
            continue
        kind = {"C": "Severe storms", "F": "Fire weather", "E": "Excessive rain"}.get(r.get("outlook_type"), "Outlook")
        cat = (r.get("category") or "").upper()
        thr = (r.get("threshold") or "").upper()
        level, word, style = OUTLOOK_LEVELS.get(thr, (None, thr, "text"))
        if cat != "CATEGORICAL":
            word = f"{thr.replace('0.', '')}% {cat.lower()}" if thr.startswith("0.") else f"{thr} {cat.lower()}"
            try:
                p = float(thr)
                word = f"{p * 100:.0f}% chance of {cat.lower()}"
                style = "bad" if p >= 0.3 else "accent" if p >= 0.15 else "warn"
            except ValueError:
                pass
        out.append({"day": r.get("day"), "kind": kind, "category": cat, "threshold": thr, "level": level,
                    "word": word, "style": style, "issue": issue, "expire": expire,
                    "type": r.get("outlook_type")})
    out.sort(key=lambda o: (o["day"] or 9, o["type"] or "", 0 if o["category"] == "CATEGORICAL" else 1))
    return out


LSR_TYPES = {"T": "Tornado", "H": "Hail", "G": "Wind gust", "D": "Wind damage", "F": "Flash flood",
             "E": "Flood", "R": "Heavy rain", "S": "Snow", "Z": "Freezing rain", "O": "Non-tstm wind",
             "W": "Waterspout", "M": "Marine tstm wind", "N": "Non-tstm wind gust", "C": "Funnel cloud",
             "L": "Lightning", "4": "Excessive heat", "Q": "Tropical storm", "X": "Dust storm"}


def build_lsrs(j, loc):
    out = []
    for r in (j or {}).get("data") or []:
        lat, lon = num(r.get("lat")), num(r.get("lon"))
        d = distance_mi(loc.get("lat"), loc.get("lon"), lat, lon) if loc else None
        out.append({"t": iso_epoch(r.get("valid")), "type": r.get("typetext") or LSR_TYPES.get(r.get("type"), r.get("type")),
                    "mag": num(r.get("magnitude")), "unit": r.get("unit") or "", "city": r.get("city") or "",
                    "county": r.get("county") or "", "state": r.get("state") or "", "source": r.get("source") or "",
                    "remark": safe_text(r.get("remark") or ""), "lat": lat, "lon": lon, "dist": d,
                    "bearing": bearing_deg(loc.get("lat"), loc.get("lon"), lat, lon) if loc else None})
    out.sort(key=lambda r: -(r["t"] or 0))
    return out


SBW_STYLE = {"TO": ("Tornado Warning", (255, 30, 30)), "SV": ("Severe Thunderstorm Warning", (255, 220, 0)),
             "FF": ("Flash Flood Warning", (40, 230, 70)), "FA": ("Flood Warning", (0, 160, 60)),
             "FL": ("Flood Warning", (0, 160, 60)), "MA": ("Special Marine Warning", (255, 150, 0)),
             "SQ": ("Snow Squall Warning", (230, 60, 230)), "EW": ("Extreme Wind Warning", (255, 120, 0)),
             "DS": ("Dust Storm Warning", (230, 200, 160))}


def build_sbw(j, loc, radius_mi=250):
    out = []
    now = time.time()
    for f in (j or {}).get("features") or []:
        p = f.get("properties") or {}
        rings = geojson_rings(f.get("geometry"))
        if not rings:
            continue
        exp = iso_epoch(p.get("expire_utc") or p.get("expire") or p.get("polygon_end"))
        if exp is not None and exp < now:
            continue
        lons = [pt[0] for ring in rings for pt in ring]
        lats = [pt[1] for ring in rings for pt in ring]
        clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
        d = distance_mi(loc["lat"], loc["lon"], clat, clon) if loc else None
        if d is not None and d > radius_mi:
            continue
        phen = p.get("phenomena") or ""
        name, rgb = SBW_STYLE.get(phen, (f"{VTEC_PHEN.get(phen, phen)} {VTEC_SIG.get(p.get('significance'), '')}",
                                         (255, 255, 255)))
        inside = point_in_rings(loc["lon"], loc["lat"], rings) if loc else False
        out.append({"phen": phen, "name": name, "rgb": rgb, "rings": rings, "wfo": p.get("wfo"),
                    "expire": exp, "issue": iso_epoch(p.get("issue")), "dist": d, "inside": inside,
                    "bbox": (min(lons), min(lats), max(lons), max(lats)),
                    "bearing": bearing_deg(loc["lat"], loc["lon"], clat, clon) if loc else None,
                    "wind": num(p.get("windtag")), "hail": num(p.get("hailtag")), "tornado": p.get("tornadotag"),
                    "damage": p.get("damagetag"), "sig": p.get("significance"),
                    "id": f"{p.get('wfo')}.{phen}.{p.get('significance')}.{p.get('eventid')}.{p.get('year') or ''}",
                    "emergency": bool(p.get("is_emergency")), "pds": bool(p.get("is_pds"))})
    out.sort(key=lambda w: (not w["inside"], w["dist"] if w["dist"] is not None else 9e9))
    return out


def build_watches(j, loc, radius_mi=300):
    out = []
    for f in (j or {}).get("features") or []:
        p = f.get("properties") or {}
        rings = geojson_rings(f.get("geometry"))
        if not rings:
            continue
        lons = [pt[0] for ring in rings for pt in ring]
        lats = [pt[1] for ring in rings for pt in ring]
        clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
        d = distance_mi(loc["lat"], loc["lon"], clat, clon) if loc else None
        if d is not None and d > radius_mi:
            continue
        typ = str(p.get("type") or p.get("sel") or "")
        name = "Tornado Watch" if typ.upper().startswith("TOR") else "Severe Thunderstorm Watch"
        out.append({"name": f"{name} {p.get('num') or ''}".strip(), "rings": rings,
                    "rgb": (255, 60, 60) if "Tornado" in name else (255, 200, 40),
                    "inside": point_in_rings(loc["lon"], loc["lat"], rings) if loc else False,
                    "expire": iso_epoch(p.get("expire") or p.get("expire_utc")), "dist": d})
    return out


def _acis_num(v):
    if v in (None, "M", "S", ""):
        return None
    if v == "T":
        return 0.001
    try:
        return float(str(v).rstrip("A"))
    except ValueError:
        return None


def build_almanac(a):
    """ACIS data -> normals, records, yesterday, month/year to date (internal units)."""
    if not a:
        return {}
    rows = dig(a, "daily", "data", default=[]) or []
    if not rows:
        return {}

    def f2c(v):
        return None if v is None else (v - 32) * 5 / 9

    def in2mm(v):
        return None if v is None else v * 25.4

    today = rows[-1]
    yest = rows[-2] if len(rows) >= 2 else None
    out = {"station": a.get("station") or {}, "name": dig(a, "daily", "meta", "name"),
           "normal_hi": f2c(_acis_num(today[5])), "normal_lo": f2c(_acis_num(today[6])),
           "mtd_precip": in2mm(_acis_num(today[7])), "mtd_normal": in2mm(_acis_num(today[8])),
           "ytd_precip": in2mm(_acis_num(today[9])), "ytd_normal": in2mm(_acis_num(today[10])),
           "mtd_avg": f2c(_acis_num(today[11])), "mtd_avg_normal": f2c(_acis_num(today[12]))}
    if yest:
        out.update(y_hi=f2c(_acis_num(yest[1])), y_lo=f2c(_acis_num(yest[2])), y_precip=in2mm(_acis_num(yest[3])),
                   y_snow=in2mm(_acis_num(yest[4])), y_date=yest[0],
                   y_normal_hi=f2c(_acis_num(yest[5])), y_normal_lo=f2c(_acis_num(yest[6])))
        if (out["mtd_precip"] is None or today[7] in ("M",)) and yest[7] not in ("M",):
            out["mtd_precip"], out["mtd_normal"] = in2mm(_acis_num(yest[7])), in2mm(_acis_num(yest[8]))
            out["ytd_precip"], out["ytd_normal"] = in2mm(_acis_num(yest[9])), in2mm(_acis_num(yest[10]))
    rec = dig(a, "records", "smry", default=[]) or []

    def rec_pair(i):
        try:
            val, date = rec[i][0]
        except (IndexError, TypeError, ValueError):
            return None, None
        year = str(date)[:4] if date else None
        return _acis_num(val), year

    hi, hi_y = rec_pair(0)
    lo, lo_y = rec_pair(1)
    pr, pr_y = rec_pair(2)
    out.update(rec_hi=f2c(hi), rec_hi_year=hi_y, rec_lo=f2c(lo), rec_lo_year=lo_y,
               rec_precip=in2mm(pr), rec_precip_year=pr_y)
    month = []
    for r in rows:
        try:
            d = dt.date.fromisoformat(r[0])
        except (ValueError, TypeError):
            continue
        month.append({"date": d, "hi": f2c(_acis_num(r[1])), "lo": f2c(_acis_num(r[2])),
                      "precip": in2mm(_acis_num(r[3])), "nhi": f2c(_acis_num(r[5])), "nlo": f2c(_acis_num(r[6]))})
    out["days"] = month
    return out


NETWORK_LABEL = [
    ("APRSWXNET", "CWOP", "Citizen weather station (CWOP)"),
    ("ASOS", "ASOS", "Airport (ASOS)"), ("AWOS", "AWOS", "Airport (AWOS)"),
    ("NonFedAWOS", "AWOS", "Airport (AWOS)"), ("OTHER-MTR", "METAR", "Airport (METAR)"),
    ("RWIS", "DOT", "Road weather sensor (DOT)"), ("RDOT", "DOT", "Road weather sensor (DOT)"),
    ("ARDOT", "DOT", "Road weather sensor (DOT)"), ("DOT", "DOT", "Road weather sensor (DOT)"),
    ("RAWS", "RAWS", "Fire weather station (RAWS)"), ("COOP", "COOP", "Co-op observer (daily)"),
    ("DCP", "GAUGE", "River/lake gauge (DCP)"), ("HADS", "GAUGE", "River/lake gauge (HADS)"),
    ("SCAN", "SCAN", "Soil climate station (SCAN)"), ("USCRN", "USCRN", "Climate reference station"),
    ("MesoWest", "MESO", "Mesonet (MesoWest)"), ("UPR", "RAIL", "Railroad weather station"),
    ("ISUSM", "SOIL", "Soil moisture station"),
]


def network_label(provider, sub="", network=""):
    hay = " ".join(x for x in (provider, sub, network) if x)
    for key, short, long_ in NETWORK_LABEL:
        if key.lower() in hay.lower():
            if key == "MesoWest" and sub:
                for k2, s2, l2 in NETWORK_LABEL:
                    if k2.lower() in sub.lower():
                        return s2, l2
            return short, long_
    if hay.strip():
        return hay.split()[0][:6].upper(), hay
    return "OTHER", "Other network"


def _iem_obs(r):
    def f2c(v):
        v = num(v)
        return None if v is None else (v - 32) * 5 / 9

    def kt(v):
        v = num(v)
        return None if v is None else v * 0.514444

    def inch(v):
        v = num(v)
        return None if v is None else v * 25.4

    temp, dew = f2c(r.get("tmpf")), f2c(r.get("dwpf"))
    rh = num(r.get("relh"))
    alti = num(r.get("alti"))
    press = num(r.get("mslp")) or (alti * 33.8639 if alti else None)
    sky = [r.get(f"skyc{i}") for i in range(1, 5) if r.get(f"skyc{i}")]
    return {"t": iso_epoch(r.get("utc_valid")), "temp": temp, "dew": dew,
            "rh": rh if rh is not None else rh_from(temp, dew), "wind": kt(r.get("sknt")),
            "wind_deg": num(r.get("drct")), "gust": kt(r.get("gust")), "press": press,
            "vis": (num(r.get("vsby")) or 0) * 1609.344 if r.get("vsby") is not None else None,
            "p1": inch(r.get("phour")), "pday": inch(r.get("pday")), "hi": f2c(r.get("max_tmpf")),
            "lo": f2c(r.get("min_tmpf")), "feels": f2c(r.get("feel")), "snowd": inch(r.get("snowd")),
            "desc": " ".join(sky) if sky else "", "wx": r.get("wxcodes") or "", "raw": r.get("raw") or "",
            "max_gust": kt(r.get("max_gust"))}


def _nws_obs_compact(p):
    if not p or p.get("_error"):
        return None
    o = parse_obs(p)
    return {"t": o["t"], "temp": o["temp"], "dew": o["dew"], "rh": o["rh"], "wind": o["wind"],
            "wind_deg": o["wind_deg"], "gust": o["gust"], "press": o["press"], "vis": o["vis"],
            "p1": o["p1"], "pday": None, "feels": first(o["heat"], o["chill"]), "desc": o["desc"],
            "raw": o["metar"], "hi": None, "lo": None, "wx": "; ".join(o["present"])}


def build_stations(stations, iem, loc, zcta, primary_id=None, radius=None):
    """Merge the NWS station list (plus its latest observations) with IEM's
    bulk feed into one list sorted by distance."""
    if not loc:
        return {}
    lat, lon = loc["lat"], loc["lon"]
    rings = (zcta or {}).get("rings") or []
    radius = radius or (stations or {}).get("radius") or 10
    merged = {}
    order = []

    def norm(sid):
        s = str(sid or "").upper()
        if s.startswith("COOP"):
            s = s[4:]
        return s

    for s in (stations or {}).get("near") or []:
        key = norm(s["id"])
        if key in merged:  # e.g. MAUA4 and COOPMAUA4 are the same site
            continue
        obs = _nws_obs_compact(((stations or {}).get("latest") or {}).get(s["id"]))
        err = (((stations or {}).get("latest") or {}).get(s["id"]) or {}).get("_error")
        short, long_ = network_label(s.get("provider"), s.get("sub"))
        rec = {"id": s["id"], "name": " ".join(safe_text(s.get("name") or s["id"]).split()), "net": short, "net_long": long_,
               "provider": s.get("provider"), "lat": s["lat"], "lon": s["lon"], "elev": s.get("elev"),
               "dist": s["dist"], "obs": obs, "src": "NWS", "error": err}
        merged[key] = rec
        order.append(key)
    now = time.time()
    for r in (iem or {}).get("rows") or []:
        d = r.get("_dist")
        if d is None or d > radius:
            continue
        sid = norm(r.get("station"))
        net = r.get("network") or ""
        keys = [sid]
        if net.endswith(("ASOS", "AWOS")) and len(sid) == 3:
            keys.insert(0, "K" + sid)
        obs = _iem_obs(r)
        hit = next((k for k in keys if k in merged), None)
        if hit:
            rec = merged[hit]
            if rec["obs"] is None or (obs["t"] and (rec["obs"].get("t") or 0) < obs["t"]):
                rec["obs"] = obs
                rec["src"] = "IEM"
            else:
                for k, v in obs.items():
                    if rec["obs"].get(k) is None and v is not None:
                        rec["obs"][k] = v
            continue
        short, long_ = network_label(net, "", net)
        key = keys[0]
        merged[key] = {"id": ("K" + sid) if keys[0].startswith("K") and len(sid) == 3 else sid,
                       "name": " ".join((safe_text(r.get("name") or sid).title() if (r.get("name") or "").isupper()
                                         else safe_text(r.get("name") or sid)).split()),
                       "net": short, "net_long": long_, "provider": net, "lat": num(r.get("lat")),
                       "lon": num(r.get("lon")), "elev": None, "dist": d, "obs": obs, "src": "IEM", "error": None}
        order.append(key)
    out = []
    for key in order:
        rec = merged[key]
        rec["bearing"] = bearing_deg(lat, lon, rec["lat"], rec["lon"])
        rec["in_zip"] = bool(rings) and rec["lat"] is not None and point_in_rings(rec["lon"], rec["lat"], rings)
        o = rec.get("obs") or {}
        rec["has_met"] = any(o.get(k) is not None for k in ("temp", "wind", "dew", "press"))
        rec["age"] = (now - o["t"]) if o.get("t") else None
        rec["stale"] = rec["age"] is None or rec["age"] > 3 * 3600
        rec["primary"] = bool(primary_id) and norm(rec["id"]) == norm(primary_id)
        if o.get("temp") is not None and o.get("rh") is not None and o.get("feels") is None:
            o["feels"] = first(heat_index_c(o["temp"], o["rh"]), wind_chill_c(o["temp"], o.get("wind")))
        out.append(rec)
    out.sort(key=lambda r: (not r["has_met"], r["dist"]))
    live = [r for r in out if r["has_met"] and not r["stale"]]
    temps = [r["obs"]["temp"] for r in live if r["obs"].get("temp") is not None]
    winds = [(r["obs"].get("gust") or r["obs"].get("wind") or 0, r) for r in live]
    summary = {}
    if temps:
        warm = max(live, key=lambda r: r["obs"].get("temp") if r["obs"].get("temp") is not None else -999)
        cool = min(live, key=lambda r: r["obs"].get("temp") if r["obs"].get("temp") is not None else 999)
        summary = {"n": len(live), "avg": sum(temps) / len(temps), "max": max(temps), "min": min(temps),
                   "warm": warm, "cool": cool}
    if winds:
        top = max(winds, key=lambda x: x[0])
        if top[0]:
            summary["windiest"] = top[1]
    nets = {}
    for r in out:
        nets[r["net"]] = nets.get(r["net"], 0) + 1
    return {"list": out, "summary": summary, "nets": nets, "radius": radius,
            "in_zip": sum(1 for r in out if r["in_zip"]), "ts": max((stations or {}).get("ts") or 0,
                                                                      (iem or {}).get("ts") or 0)}


def build_station_history(j):
    return build_history(j)


# ----------------------------------------------------------------------------
# the model
# ----------------------------------------------------------------------------

MODEL_DEFAULTS = {
    "loc": {}, "pts": {}, "tz": None, "oc_ok": False, "oc_version": None, "oc_notes": {}, "station": {},
    "obs_hist": [], "obs": {}, "taf": {}, "current": {}, "om": {}, "grid": {}, "min15": [], "hourly": [],
    "daily": [], "alerts": [], "air": {}, "air_om": {}, "overview": None, "minutely": [], "afd": None,
    "hwo": None, "office": {}, "radio": {}, "astro": {}, "outlooks": [], "lsrs": [], "sbw": [], "watches": [],
    "almanac": {}, "zcta": {}, "stations": {}, "errors": {}, "off": {}, "notes": {}, "updated": 0.0, "now": {},
    "markers": [], "area_alerts": [], "placefiles": [],
    "alerts_at": None, "state_map": None, "state_alerts": [],
}


def empty_model(store=None):
    m = json.loads(json.dumps({k: v for k, v in MODEL_DEFAULTS.items() if k != "tz"}))
    m["tz"] = None
    if store is not None:
        m["loc"] = dict(store.get("loc") or {})
        m["errors"] = store.errors()
    return m


def _safe(name, fn, *args, default=None):
    """Run one builder; a payload it can't handle is reported instead of crashing the app."""
    try:
        out = fn(*args)
    except Exception as e:
        LOG.report(f"model:{name}", f"Reading {name}", f"{type(e).__name__}: {e}")
        return default
    LOG.resolve(f"model:{name}")
    return out


def build_model(store, cfg):
    from .config import intval
    g = store.get
    pts = g("nws_points") or {}
    # OpenWeather data that stopped updating (key removed, rate limit) mustn't beat fresh data
    oc = store.fresh("owm_onecall", 3 * 3600)
    c25, f25 = store.fresh("owm_current", 2 * 3600), store.fresh("owm_forecast", 6 * 3600)
    om_raw = store.fresh("om_forecast", 12 * 3600)
    tz = _safe("time zone", resolve_tz, pts, oc, c25, f25, om_raw)
    om = _safe("Open-Meteo forecast", build_om, om_raw, tz, default={})
    loc = dict(g("loc") or {})
    name = (cfg.get("location_name") or "").strip()
    if not name and pts.get("city"):
        name = f"{pts['city']}, {pts.get('state') or ''}".strip(", ")
    if name:
        loc["name"] = name
    hist = _safe("NWS observation history", build_history, g("nws_obs_hist"), default=[])
    hours = intval(cfg, "hourly_hours", 12, 96)
    ndays = intval(cfg, "daily_days", 5, 16)
    grid = _safe("NWS gridpoint data", build_grid, g("nws_grid"), hours, default={})
    minute_src = store.fresh("owm_onecall", 1200) or {}
    station = g("nws_station") or {}
    zcta = g("zcta") or {}
    air = _safe("OpenWeather air quality", build_air, store.fresh("owm_air", 3 * 3600),
                store.fresh("owm_air_fc", 6 * 3600), store.fresh("owm_air_hist", 6 * 3600), default={})
    air_om = _safe("Open-Meteo air quality", build_air_om, store.fresh("om_air", 6 * 3600), default={})
    # One Call's current conditions only while they're recent; otherwise the free endpoint
    oc_now = oc if oc and time.time() - (num(dig(oc, "current", "dt")) or 0) < 1800 else None
    has_loc = loc.get("lat") is not None
    m = dict(MODEL_DEFAULTS)
    m.update({
        "loc": loc, "pts": pts, "tz": tz, "oc_ok": bool(oc),
        "oc_version": (oc or {}).get("version"), "oc_notes": (oc or {}).get("notes") or {},
        "station": station,
        "obs_hist": hist,
        "obs": _safe("NWS observation", build_obs, g("nws_obs"), hist, default={}),
        "taf": g("nws_taf") or {},
        "current": _safe("current conditions", build_current, oc_now, None if oc_now else c25, om.get("current"),
                         default={}),
        "om": om,
        "grid": grid,
        "min15": _safe("15-minute forecast", build_min15, oc, om.get("min15"), default=[]),
        "hourly": _safe("hourly forecast", build_hourly, oc, g("nws_hourly"), grid, None if oc else f25,
                        om.get("hourly"), hours, tz, default=[]),
        "daily": _safe("daily forecast", build_daily, oc, None if oc else store.fresh("owm_daily16", 12 * 3600),
                       g("nws_forecast"), None if oc else f25, grid, tz, om.get("daily"), ndays, default=[]),
        "alerts": _safe("alerts", build_alerts, g("nws_alerts"), oc, default=[]),
        "air": air or air_om,
        "air_om": air_om,
        "overview": (oc or {}).get("overview"),
        "minutely": [(x.get("dt"), num(x.get("precipitation")))
                     for x in minute_src.get("minutely") or [] if isinstance(x, dict)],
        "afd": g("nws_afd"), "hwo": g("nws_hwo"),
        "office": _safe("NWS office news", build_office, g("nws_headlines"), g("nws_stories"), g("nws_briefing"),
                        default={}),
        "radio": g("nws_radio") or {},
        "astro": _safe("sun times", build_astro, pts, tz, default={}),
        "outlooks": _safe("storm outlooks", build_outlooks, g("iem_outlook"), default=[]),
        "lsrs": _safe("storm reports", build_lsrs, g("iem_lsr"), loc, default=[]) if loc else [],
        "sbw": _safe("warning polygons", build_sbw, g("iem_sbw"), loc, default=[]) if has_loc else [],
        "watches": _safe("storm watches", build_watches, g("iem_watches"), loc, default=[]) if has_loc else [],
        "almanac": _safe("almanac", build_almanac, g("acis"), default={}),
        "zcta": zcta,
        "markers": parse_markers(cfg.get("map_markers")),
        "placefiles": _safe("placefiles", loaded_placefiles, store, cfg, default=[]),
        "area_alerts": _safe("alerts around you", build_area_alerts, g("nws_alerts_area"), g("nws_alerts"),
                             _safe("warning polygons", build_sbw, g("iem_sbw"), loc, 300, default=[])
                             if has_loc else [], loc, pts, zcta, None, 250, g("iem_watches"), default=[])
        if has_loc else [],
        "state_map": _safe("the warnings map", statemap.parse, g("state_map")),
        "state_alerts": _safe("the state's alerts", statemap.state_alerts, g("nws_alerts_area"), default=[]),
        "stations": _safe("station feed", build_stations, g("stations"), g("iem_currents"),
                          loc if has_loc else None, zcta, station.get("id"),
                          intval(cfg, "station_radius", 1, 100), default={}),
        "errors": store.errors(),
        "off": {k: e.get("off") for k, e in list(store.entries.items()) if e.get("off")},
        "notes": store.notes(),
        "updated": store.last_done,
        "alerts_at": (time.time() - store.age("nws_alerts")) if store.age("nws_alerts") is not None else None,
    })
    _safe("today's high", fill_today_high, m, tz)
    m["now"] = _safe("current conditions", now_view, m, default={}) or {}
    return m


def loaded_placefiles(store, cfg):
    """The placefiles from Settings that have loaded, in the order they're listed."""
    from .placefile import urls
    out = []
    for u in urls(cfg.get("placefiles")):
        pf = store.get("pf:" + u)
        if pf:
            out.append(pf)
    return out


def parse_markers(text):
    """'Home: 32.84, -96.78; Work: 32.78, -96.80' (or one per line) -> [{name, lat, lon}]."""
    out = []
    for part in re.split(r"[;\n]", str(text or "")):
        if ":" not in part:
            continue
        name, _, coords = part.rpartition(":")
        nums = re.findall(r"-?\d+(?:\.\d+)?", coords)
        if len(nums) < 2:
            continue
        lat, lon = float(nums[0]), float(nums[1])
        if -90 <= lat <= 90 and -180 <= lon <= 180 and name.strip():
            out.append({"name": safe_text(name.strip())[:30], "lat": lat, "lon": lon})
    return out[:50]


def now_view(m):
    """What "now" means everywhere (big temperature, ticker, speech, Local on the 8s),
    taken from one source so the numbers agree: recent OpenWeather conditions, else a
    recent NWS observation, else the Open-Meteo model, else whatever is left."""
    cur, obs = m.get("current") or {}, m.get("obs") or {}
    now = time.time()
    cur_age = now - cur["dt"] if cur.get("dt") else None
    obs_ok = obs.get("temp") is not None and obs.get("t") and now - obs["t"] < 5400
    cur_ok = cur.get("temp") is not None and (cur_age is None or cur_age < 3600)
    owm = cur_ok and str(cur.get("src") or "").startswith("OpenWeather") and (cur_age is None or cur_age < 1800)
    if owm:
        base = "cur"
    elif obs_ok:
        base = "obs"
    elif cur_ok:
        base = "cur"
    elif obs.get("temp") is not None:
        base = "obs"
    elif cur.get("temp") is not None:
        base = "cur"
    else:
        return {}
    o = obs if base == "obs" else {}
    c = cur if base == "cur" else {}
    temp = (o or c).get("temp")
    rh = first(o.get("rh"), c.get("humidity"), cur.get("humidity") if base == "obs" else obs.get("rh"))
    wind = first(o.get("wind"), c.get("wind"))
    if base == "obs" and wind is None:
        wind = cur.get("wind")
    if base == "cur":
        feels = first(c.get("feels"), temp)
    else:
        feels = first(o.get("heat"), o.get("chill"), heat_index_c(temp, rh), wind_chill_c(temp, wind), temp)
    desc = (o.get("desc") if base == "obs" else c.get("desc")) or cur.get("desc") or obs.get("desc") or ""
    kind = (o.get("kind") if base == "obs" and o.get("desc") else None) or c.get("kind") or cur.get("kind") \
        or icons.from_text(desc)
    return {
        "src": "obs" if base == "obs" else cur.get("src") or "model",
        "src_name": (obs.get("station_id") or "NWS station") if base == "obs" else cur.get("src") or "",
        "t": o.get("t") if base == "obs" else cur.get("dt"),
        "temp": temp, "feels": feels, "desc": desc, "kind": kind, "is_day": cur.get("is_day"),
        "rh": rh, "dew": first(o.get("dew"), c.get("dew"), cur.get("dew"), obs.get("dew")),
        "wind": wind, "wind_deg": first(o.get("wind_deg"), c.get("wind_deg"), cur.get("wind_deg"), obs.get("wind_deg")),
        "gust": first(o.get("gust"), c.get("gust")),
        "vis": first(o.get("vis"), c.get("vis"), cur.get("vis"), obs.get("vis")),
        "press": first(o.get("press"), c.get("pressure"), cur.get("pressure"), obs.get("press")),
        "uvi": cur.get("uvi"),
    }


def fill_today_high(m, tz):
    """In the evening the NWS forecast has no daytime period for today, so use the
    highest temperature observed since midnight when nothing else gave one."""
    today = dt.datetime.now(tz).date()
    row = next((d for d in m.get("daily") or [] if d.get("date") == today), None)
    if row is None or row.get("hi") is not None:
        return
    temps = [r["temp"] for r in m.get("obs_hist") or []
             if r.get("temp") is not None and r.get("t") and local_date(r["t"], tz) == today]
    obs = m.get("obs") or {}
    if obs.get("temp") is not None and obs.get("t") and local_date(obs["t"], tz) == today:
        temps.append(obs["temp"])
    if temps:
        row["hi"] = max(temps)
        row["hi_observed"] = True
