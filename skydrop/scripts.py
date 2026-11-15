"""What gets read aloud on each screen (plain sentences)."""

import datetime as dt
import time

from . import icons, ui
from .radar import summary_lines
from .util import cap
from .wxmath import COMPASS_WORDS, aqi_cat, compass, moon_info, uv_cat


def _deg(F, c):
    v = F.tv(c)
    if v is None:
        return None
    return f"{v:.0f} degrees"


def _wind(F, deg, ms, gust=None):
    if ms is None:
        return ""
    if ms < 0.5:
        return "Winds are calm."
    d = COMPASS_WORDS.get(compass(deg), "")
    s = f"Winds {('from the ' + d + ' ') if d else ''}at {F.wind(ms)}"
    if gust and gust > ms + 2:
        s += f", gusting to {F.wind(gust)}"
    return s + "."


def now_script(m, v):
    F = v.fmt
    loc = m.get("loc") or {}
    now = m.get("now") or {}
    temp = now.get("temp")
    desc = now.get("desc") or ""
    out = [f"Here is the latest weather for {loc.get('name') or 'your area'}."]
    if temp is not None:
        s = f"It's {desc.lower() + ' and ' if desc else ''}{_deg(F, temp)}"
        feels = now.get("feels")
        if feels is not None and abs(F.tv(feels) - F.tv(temp)) >= 3:
            s += f", but it feels like {_deg(F, feels)}"
        out.append(s + ".")
    rh, dew = now.get("rh"), now.get("dew")
    if rh is not None:
        out.append(f"The humidity is {rh:.0f} percent" + (f", with a dew point of {_deg(F, dew)}." if dew is not None else "."))
    w = _wind(F, now.get("wind_deg"), now.get("wind"), now.get("gust"))
    if w:
        out.append(w)
    alerts = m.get("alerts") or []
    if alerts:
        names = ", ".join(sorted({a["event"] for a in alerts}))
        out.append(f"Attention: {names} {'is' if len(alerts) == 1 else 'are'} in effect for your area.")
    res = getattr(v, "last_radar", None)
    if res is not None:
        for txt, _sty in summary_lines(res, F, compact=True):
            out.append(txt.replace("Arrival estimate:", "Radar suggests"))
    for name, text in ui.periods_text(m, 3):
        out.append(f"{name}: {text}")
    return "Current conditions", out


def hourly_script(m, v):
    F = v.fmt
    rows = (m.get("hourly") or [])[:12]
    if not rows:
        return "Hourly forecast", []
    temps = [(r.get("temp"), r["t"]) for r in rows if r.get("temp") is not None]
    out = ["Here is your hourly forecast for the next twelve hours."]
    if temps:
        hi, lo = max(temps), min(temps)
        first_t = temps[0]
        out.append(f"Temperatures go from {_deg(F, first_t[0])} now, "
                   f"{'up to ' + _deg(F, hi[0]) + ' around ' + F.hour(hi[1]) if hi[1] != first_t[1] else ''}"
                   f"{' and ' if hi[1] != first_t[1] and lo[1] != first_t[1] else ''}"
                   f"{'down to ' + _deg(F, lo[0]) + ' around ' + F.hour(lo[1]) if lo[1] != first_t[1] else ''}.")
    pops = [(r.get("pop") or 0, r["t"]) for r in rows]
    peak = max(pops)
    if peak[0] >= 20:
        out.append(f"The best chance of rain is {peak[0]:.0f} percent around {F.hour(peak[1])}.")
    else:
        out.append("Little or no rain is expected.")
    kinds = [r.get("desc") for r in rows if r.get("desc")]
    if kinds:
        out.append(f"Expect {cap(kinds[0]).lower()} to start, then {cap(kinds[-1]).lower()} by {F.hour(rows[-1]['t'])}.")
    sel = (m.get("hourly") or [])[max(0, min(v.sel.get("hourly", 0), len(m.get("hourly") or []) - 1))]
    if sel and v.sel.get("hourly", 0):
        out.append(f"At {F.hour(sel['t'])}: {cap(sel.get('desc') or '')}, {_deg(F, sel.get('temp'))}, "
                   f"{(sel.get('pop') or 0):.0f} percent chance of rain.")
    return "Hourly forecast", out


def daily_script(m, v):
    F = v.fmt
    rows = m.get("daily") or []
    start = max(0, min(v.sel.get("daily", 0), len(rows) - 1))
    out = ["Here is your extended forecast."]
    for r in rows[start:start + 7]:
        did = False
        for key in ("nws_day", "nws_night"):
            p = r.get(key)
            if p and p.get("text"):
                out.append(f"{p['name']}: {p['text']}")
                did = True
        if not did:
            day = "Today" if r["date"] == F.today() else f"{r['date']:%A}"
            bits = [f"{day}: {cap(r.get('desc') or icons.label(r.get('kind')) or 'no description')}"]
            if r.get("hi") is not None:
                bits.append(f"high {_deg(F, r['hi'])}")
            if r.get("lo") is not None:
                bits.append(f"low {_deg(F, r['lo'])}")
            if r.get("pop"):
                bits.append(f"{r['pop']:.0f} percent chance of precipitation")
            out.append(", ".join(bits) + ".")
    return "Extended forecast", out


def radar_script(m, v):
    from .wxmath import compass
    F = v.fmt
    res = getattr(v, "last_radar", None)
    out = []
    if res is None:
        return "Radar nowcast", ["Here's the latest from local radar.", "The radar is still loading."]
    site_mode = res.view.source == "site" and res.product is not None
    if site_mode:
        info = res.site or {}
        name = str(info.get("name") or "").title()
        when = (res.scan or {}).get("time")
        out.append(f"Here's the latest from the {res.view.site} radar" + (f" at {name}" if name else "") +
                   (f", scanned at {F.time(when)}." if when else "."))
        if (res.scan or {}).get("mode") == "clear air":
            out.append("The radar is in clear air mode, its slow and sensitive scan for when there is little or "
                       "no precipitation.")
    else:
        out.append("Here's the latest from the national radar mosaic.")
    for txt, _sty in summary_lines(res, F):
        out.append(txt)
    cells = res.cells or []
    if site_mode and cells:
        out.append(f"The radar is tracking {len(cells)} storm cell{'s' if len(cells) != 1 else ''}.")
        for c in cells[:2]:
            if c.get("dist") is None:
                continue
            bits = [f"Cell {c.get('id') or ''}, {F.dist(c['dist'])} {compass(c.get('bearing'))} of you"]
            if c.get("dbz") is not None:
                bits.append(f"{c['dbz']:.0f} d B Z")
            if c.get("toward") is not None and c.get("speed_mph"):
                bits.append(f"moving {compass(c['toward'])} at {F.speed_mph(c['speed_mph'])}")
            hail = c.get("hail") or {}
            if (hail.get("posh") or 0) >= 50:
                bits.append("with a good chance of severe hail")
            if c.get("tvs_flag"):
                bits.append("with a tornado vortex signature")
            elif c.get("meso_rank"):
                bits.append("with a mesocyclone")
            line = ", ".join(bits) + "."
            if c.get("eta"):
                line += f" It could reach you around {F.time(c['eta']['t'])}."
            out.append(line)
    area = [a for a in m.get("area_alerts") or [] if a.get("sig") == "W" and a.get("dist") is not None
            and a["dist"] <= 80]
    if area:
        for a in area[:4]:
            where = "covering your location" if a.get("in_zip") or a.get("inside") else \
                f"about {F.dist(a['dist'])} {compass(a.get('bearing'))}"
            until = f" until {F.time(a['end'])}" if a.get("end") else ""
            out.append(f"A {a['event']} is in effect {where}{until}.")
    else:
        for w in [w for w in m.get("sbw") or [] if (w.get("dist") or 999) <= 80][:4]:
            where = "including your location" if w["inside"] else f"about {F.dist(w['dist'])} away"
            out.append(f"A {w['name']} is in effect {where}.")
    return "Radar nowcast", out


def stations_script(m, v):
    F = v.fmt
    st = m.get("stations") or {}
    sm = st.get("summary") or {}
    out = []
    if sm.get("n"):
        out.append(f"{sm['n']} weather stations are reporting within {F.dist(st.get('radius'))}.")
        out.append(f"Temperatures range from {_deg(F, sm['min'])} to {_deg(F, sm['max'])}, "
                   f"averaging {_deg(F, sm['avg'])}.")
        if sm.get("warm"):
            out.append(f"The warmest is {sm['warm']['name']} at {_deg(F, sm['warm']['obs']['temp'])}.")
        if sm.get("cool"):
            out.append(f"The coolest is {sm['cool']['name']} at {_deg(F, sm['cool']['obs']['temp'])}.")
    from .screens2 import station_rows
    rows = station_rows(m, v)
    if rows:
        s = rows[max(0, min(v.sel.get("stations", 0), len(rows) - 1))]
        o = s.get("obs") or {}
        if o.get("temp") is not None:
            out.append(f"Selected station {s['name']} reports {_deg(F, o['temp'])}. " + _wind(F, o.get("wind_deg"), o.get("wind"), o.get("gust")))
    return "Station feed", out


def alerts_script(m, v):
    F = v.fmt
    alerts = m.get("alerts") or []
    out = []
    if not alerts:
        out.append("There are no active watches, warnings, or advisories for your location.")
    for a in alerts:
        out.append(f"{a['event']}.")
        if a.get("nws_headline") or a.get("headline"):
            out.append(a.get("nws_headline") or a.get("headline"))
        if a.get("desc"):
            out.append(a["desc"])
        if a.get("instruction"):
            out.append("Here is what to do. " + a["instruction"])
    today = F.today()
    for o in (m.get("outlooks") or [])[:4]:
        if o["category"] != "CATEGORICAL":
            continue
        day = today + dt.timedelta(days=(o["day"] or 1) - 1)
        lab = "today" if day == today else "tomorrow" if day == today + dt.timedelta(days=1) else f"on {day:%A}"
        out.append(f"There is a {o['word'].lower()} risk of {o['kind'].lower()} {lab}.")
    return "Alerts", out


def air_script(m, v):
    F = v.fmt
    air = m.get("air") or {}
    out = []
    if air.get("us") is not None:
        lab, _sty, advice = aqi_cat(air["us"])
        out.append(f"The air quality index is {air['us']}, which is {lab.lower()}"
                   + (f", driven by {air['dominant']}." if air.get("dominant") else "."))
        out.append(advice)
    uv = (m.get("air_om") or {}).get("uv") or []
    today = F.today()
    ut = [(t, u) for t, u in uv if F.local(t).date() == today]
    if ut:
        t, u = max(ut, key=lambda x: x[1])
        out.append(f"The U V index peaks at {u:.0f}, {uv_cat(u)[0].lower()}, around {F.hour(t)}.")
    return "Air quality", out


def sky_script(m, v):
    F = v.fmt
    sr, ss, _ = ui.sun_pair(m, F)
    out = []
    if sr and ss:
        out.append(f"Sunrise is at {F.time(sr)} and sunset at {F.time(ss)}, for {F.duration(ss - sr).replace('h', ' hours').replace('m', ' minutes')} of daylight.")
    age, illum, name = moon_info(time.time())
    out.append(f"The moon is {name.lower()}, {illum * 100:.0f} percent illuminated.")
    a = m.get("almanac") or {}
    if a.get("normal_hi") is not None and a.get("normal_lo") is not None:
        out.append(f"The normal high for today is {_deg(F, a['normal_hi'])}, and the normal low is {_deg(F, a['normal_lo'])}.")
    if a.get("rec_hi") is not None:
        out.append(f"The record high is {_deg(F, a['rec_hi'])}, set in {a.get('rec_hi_year')}, "
                   f"and the record low is {_deg(F, a.get('rec_lo'))}, set in {a.get('rec_lo_year')}.")
    if a.get("mtd_precip") is not None:
        line = f"So far this month, {F.precip(a['mtd_precip'])} of precipitation has fallen"
        if a.get("mtd_normal") is not None:
            line += f", compared with a normal of {F.precip(a['mtd_normal'])}"
        out.append(line + ".")
    return "Sky and almanac", out


def office_script(m, v):
    text = (m.get("radio") or {}).get("text")
    if text:
        return "NOAA Weather Radio", [text]
    afd = (m.get("hwo") or {}).get("text") or ""
    return "Hazardous weather outlook", [afd[:3000]] if afd else ["No weather radio script is available right now."]


SCRIPTS = {"now": now_script, "hourly": hourly_script, "daily": daily_script, "radar": radar_script,
           "stations": stations_script, "alerts": alerts_script, "air": air_script, "sky": sky_script,
           "office": office_script}
