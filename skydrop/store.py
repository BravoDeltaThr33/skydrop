"""Background refresh of every data source.

Each source writes an entry (data, error, timestamp). Builders in model.py
merge the entries into one normalized model for the screens.
"""

import datetime as dt
import json
import os
import re
import threading
import time
import urllib.parse
from . import APP, VERSION
from . import sources as S
from .alerts import county_label
from .config import DEFAULT_NWS_BASE, intval, save_config
from .errlog import LOG
from .net import ApiError, Http, prune_disk_cache, rebase, with_params
from .paths import CACHE_DIR, USAGE_PATH
from .util import dig, distance_mi, num
from .wxmath import nws_val

OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com"

SOURCE_LABELS = {
    "loc": "Location", "internal": "Internal",
    "owm_onecall": "OpenWeather One Call", "owm_current": "OpenWeather current",
    "owm_forecast": "OpenWeather 3-hour forecast", "owm_daily16": "OpenWeather 16-day",
    "owm_air": "OpenWeather air quality", "owm_air_fc": "OpenWeather air forecast",
    "owm_air_hist": "OpenWeather air history",
    "nws_points": "NWS point lookup", "nws_station": "NWS station",
    "nws_obs": "NWS latest observation", "nws_obs_hist": "NWS observation history",
    "nws_taf": "NWS airport forecast (TAF)",
    "nws_forecast": "NWS 7-day forecast", "nws_hourly": "NWS hourly forecast",
    "nws_grid": "NWS gridpoint data", "nws_alerts": "NWS alerts",
    "nws_alerts_area": "NWS alerts for your state",
    "nws_afd": "NWS forecast discussion", "nws_hwo": "NWS hazardous weather outlook",
    "nws_headlines": "NWS office headlines", "nws_stories": "NWS weather stories",
    "nws_briefing": "NWS office briefing", "nws_radio": "NOAA Weather Radio",
    "om_forecast": "Open-Meteo forecast", "om_air": "Open-Meteo air quality",
    "iem_currents": "IEM station feed", "iem_outlook": "SPC/WPC outlooks (IEM)",
    "iem_lsr": "Local storm reports (IEM)", "iem_sbw": "Warning polygons (IEM)",
    "iem_watches": "SPC watches (IEM)",
    "acis": "Almanac (ACIS)", "zcta": "ZIP outline (TIGERweb)",
    "stations": "Station feed (NWS)", "stations_meta": "Station list (NWS)",
}

ONECALL_MSG = {
    "off": "One Call is turned off in Settings; using the free endpoints",
    "capped": "Daily One Call cap reached; using the free endpoints until 00:00 UTC",
    "unavailable": "One Call isn't enabled for this key; using the free endpoints",
    "backoff": "OpenWeather asked us to slow down; using cached data for a few minutes",
    "nokey": "No OpenWeather key; using NWS and Open-Meteo",
    "disabled": "OpenWeather is turned off in Settings",
}
OWM_MIN_AGE = 120
ONECALL_VERSIONS = ("auto", "4.0", "3.0", "off")

FALLBACK_COORDS = {"75201": (32.7904, -96.8044)}   # the default ZIP, if every lookup fails

OM_CURRENT = ("temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,rain,showers,"
              "snowfall,weather_code,cloud_cover,pressure_msl,surface_pressure,wind_speed_10m,"
              "wind_direction_10m,wind_gusts_10m,dew_point_2m,visibility,uv_index")
OM_HOURLY = ("temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,precipitation_probability,"
             "precipitation,rain,showers,snowfall,snow_depth,weather_code,pressure_msl,cloud_cover,visibility,"
             "wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,is_day,cape")
OM_DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,"
            "apparent_temperature_min,sunrise,sunset,daylight_duration,sunshine_duration,uv_index_max,"
            "precipitation_sum,rain_sum,showers_sum,snowfall_sum,precipitation_hours,"
            "precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant")
# a smaller, conservative set if the full request is ever rejected
OM_SAFE = {"current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,"
                      "cloud_cover,pressure_msl,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
           "hourly": "temperature_2m,relative_humidity_2m,dew_point_2m,apparent_temperature,"
                     "precipitation_probability,precipitation,weather_code,cloud_cover,wind_speed_10m,"
                     "wind_direction_10m,wind_gusts_10m,is_day",
           "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_sum,"
                    "precipitation_probability_max,wind_speed_10m_max,wind_direction_10m_dominant"}


def owm_url(base, path, key, **params):
    params["appid"] = key
    return f"{base}{path}?{urllib.parse.urlencode(params)}"


def nws_headers(cfg, accept="application/geo+json"):
    ua = (cfg.get("nws_user_agent") or "").strip() or f"{APP}/{VERSION} (terminal weather app)"
    hdrs = {"User-Agent": ua, "Accept": accept}
    key = (cfg.get("nws_api_key") or "").strip()
    if key:
        hdrs["API-Key"] = key
        hdrs["X-Api-Key"] = key
    flags = ",".join(f.strip() for f in str(cfg.get("nws_feature_flags") or "").split(",") if f.strip())
    if flags:
        hdrs["Feature-Flags"] = flags
    return hdrs


def owm_ready(cfg):
    return S.enabled(cfg, "owm") and bool((cfg.get("owm_api_key") or "").strip())


US_AREAS = {"US", "PR", "GU", "VI", "AS", "MP", "UM"}   # where NWS, IEM and the station feed work


def is_postal(q):
    """True for a ZIP or postal code ('75201', 'K1A 0B1', 'SW1A 1AA'), not a place name."""
    q = (q or "").strip()
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]{1,9}", q)) and any(ch.isdigit() for ch in q)


def _postal_tries(q, country):
    """Zippopotam.us only knows the first part of some countries' codes (GB 'SW1A', CA 'K1A')."""
    tries = [q]
    if " " in q:
        tries.append(q.split()[0])
    elif country == "CA" and len(q) == 6:
        tries.append(q[:3])
    elif country == "GB" and len(q) > 4:
        tries.append(q[:-3])
    return list(dict.fromkeys(t.upper() for t in tries))


def geocode(http, cfg, query):
    """A ZIP code or a place like 'Memphis, TN' -> {lat, lon, name, state, how}.

    Tries OpenWeather (when a key is set), then Zippopotam.us for postal codes
    and Open-Meteo's geocoder for place names; neither of those needs a key."""
    q = (query or "").strip()
    country = (cfg.get("country") or "US").strip().upper() or "US"
    if not q:
        raise ApiError(0, "nothing to look up")
    problems = []
    is_zip = is_postal(q)
    if owm_ready(cfg):
        base, key = S.url(cfg, "owm"), cfg["owm_api_key"].strip()
        try:
            if is_zip:
                j = http.get_json(owm_url(base, "/geo/1.0/zip", key, zip=f"{q},{country}"))
                return {"lat": float(j["lat"]), "lon": float(j["lon"]), "name": j.get("name"),
                        "state": None, "how": "OpenWeather"}
            parts = [p.strip() for p in q.split(",") if p.strip()]
            if parts[-1].upper() != country:
                parts.append(country)
            j = http.get_json(owm_url(base, "/geo/1.0/direct", key, q=",".join(parts), limit=1))
            if j:
                top = j[0]
                return {"lat": float(top["lat"]), "lon": float(top["lon"]), "name": top.get("name"),
                        "state": top.get("state"), "how": "OpenWeather"}
            problems.append("OpenWeather doesn't know that place")
        except (ApiError, KeyError, TypeError, ValueError, IndexError) as e:
            problems.append(f"OpenWeather: {e}")
    if is_zip and S.enabled(cfg, "zippopotam"):
        for code in _postal_tries(q, country):
            try:
                j = http.get_json(f"{S.url(cfg, 'zippopotam')}/{country.lower()}/{urllib.parse.quote(code)}",
                                  ttl=86400, disk_ttl=30 * 86400)
                pl = (j.get("places") or [{}])[0]
                return {"lat": float(pl["latitude"]), "lon": float(pl["longitude"]),
                        "name": pl.get("place name"), "state": pl.get("state abbreviation") or pl.get("state"),
                        "how": "Zippopotam.us"}
            except (ApiError, KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
                problems.append(f"Zippopotam.us: {e}")
                if not (isinstance(e, ApiError) and e.code == 404):
                    break
    if not is_zip and S.enabled(cfg, "openmeteo"):
        parts = [p.strip() for p in q.split(",") if p.strip()]
        try:
            j = http.get_json(f"{OPEN_METEO_GEOCODE}/v1/search?" + urllib.parse.urlencode(
                {"name": parts[0], "count": 10, "language": "en", "format": "json", "countryCode": country}),
                ttl=86400, disk_ttl=30 * 86400)
            res = j.get("results") or []
            want = parts[1].strip().lower() if len(parts) > 1 else ""
            pick = None
            for r in res:
                admin = (r.get("admin1") or "").lower()
                if not want or admin.startswith(want) or US_STATES.get(want.upper(), "~").lower() == admin:
                    pick = r
                    break
            pick = pick or (res[0] if res else None)
            if pick:
                st = pick.get("admin1") or ""
                abbr = next((k for k, v in US_STATES.items() if v.lower() == st.lower()), st)
                return {"lat": float(pick["latitude"]), "lon": float(pick["longitude"]), "name": pick.get("name"),
                        "state": abbr, "how": "Open-Meteo geocoder"}
            problems.append("Open-Meteo doesn't know that place")
        except (ApiError, KeyError, TypeError, ValueError, IndexError) as e:
            problems.append(f"Open-Meteo: {e}")
    msg = "; ".join(dict.fromkeys(problems)) or f"couldn't look up {q}"
    if is_zip and country == "US" and not re.fullmatch(r"\d{5}(-\d{4})?", q):
        msg += " (that isn't a U.S. ZIP code; set Country code in Settings for postal codes elsewhere)"
    raise ApiError(0, msg)


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico", "GU": "Guam",
    "VI": "U.S. Virgin Islands",
}


def source_label(key):
    if key.startswith("hist:"):
        return f"Station history ({key[5:]})"
    if key.startswith("internal:"):
        return f"Internal error ({key[9:]})"
    if key.startswith("pf:"):
        from .placefile import is_local, local_path
        url = key[3:]
        short = local_path(url) if is_local(url) else urllib.parse.urlsplit(url).netloc
        return f"Placefile ({short})"
    return SOURCE_LABELS.get(key, key)


def scrub_text(err):
    from .net import scrub
    return scrub(str(err))


class Usage:
    """Counts billed One Call requests per UTC day so the free 1,000 isn't exceeded."""

    def __init__(self):
        self.lock = threading.Lock()
        self.day = None
        self.count = 0
        try:
            with open(USAGE_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            self.day = data.get("day")
            self.count = int(data.get("count") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._roll()

    @staticmethod
    def _today():
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    def _roll(self):
        today = self._today()
        if self.day != today:
            self.day, self.count = today, 0

    def add(self, n=1):
        with self.lock:
            self._roll()
            try:  # another copy of skydrop may share the key: count its calls too
                with open(USAGE_PATH, encoding="utf-8") as fh:
                    data = json.load(fh)
                if data.get("day") == self.day:
                    self.count = max(self.count, int(data.get("count") or 0))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            self.count += n
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                tmp = f"{USAGE_PATH}.{os.getpid()}"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"day": self.day, "count": self.count}, fh)
                os.replace(tmp, USAGE_PATH)
            except OSError:
                pass

    def get(self):
        with self.lock:
            self._roll()
            return self.count


class DataStore:
    def __init__(self, cfg, persist=True):
        self.cfg = cfg
        self.persist_enabled = persist
        self.http = Http()
        self.usage = Usage()
        self.lock = threading.RLock()
        self.cfg_lock = threading.Lock()
        self.entries = {}
        self.version = 0
        self.busy = False
        self.last_refresh = 0.0
        self.last_done = 0.0
        self.alert_cache = {}
        self.history_busy = set()
        self.stations_busy = False
        self.stations_visible = False
        self.gen = 0                      # bumped when the location changes
        self._tls = threading.local()     # the generation a worker thread writes for
        self.last_prune = time.time()
        self.reset_probes()

    def reset_probes(self):
        self.oc_version = None
        self.oc_fail_ts = 0.0
        self.oc_extra_ok = {}
        self.daily16_ok = None
        self.owm_backoff_until = 0.0
        self.om_backoff_until = 0.0
        self.om_safe = False

    # -- entries ------------------------------------------------------------
    def _stale(self):
        """True in a worker thread that is still writing for an old location."""
        gen = getattr(self._tls, "gen", None)
        return gen is not None and gen != self.gen

    def set(self, key, data=None, error=None):
        with self.lock:
            if self._stale():
                return
            e = self.entries.setdefault(key, {"data": None, "error": None, "ts": 0.0})
            if error is None:
                e["data"], e["error"], e["ts"] = data, None, time.time()
                e.pop("note", None)
                e.pop("off", None)
            else:
                e["error"] = str(error)
            self.version += 1
        if error is None:
            LOG.resolve("src:" + key)
        else:
            LOG.report("src:" + key, source_label(key), scrub_text(error))

    def clear(self, key):
        with self.lock:
            if self._stale():
                return
            if key in self.entries:
                del self.entries[key]
                self.version += 1
        LOG.resolve("src:" + key)

    def get(self, key):
        with self.lock:
            return dig(self.entries, key, "data")

    def err(self, key):
        with self.lock:
            return dig(self.entries, key, "error")

    def is_off(self, key):
        with self.lock:
            return dig(self.entries, key, "off")

    def age(self, key):
        with self.lock:
            e = self.entries.get(key)
            return time.time() - e["ts"] if e and e["ts"] else None

    def fresh(self, key, max_age):
        a = self.age(key)
        return self.get(key) if a is not None and a <= max_age else None

    def errors(self):
        with self.lock:
            return {k: e["error"] for k, e in self.entries.items() if e.get("error") and not k.startswith("hist:")}

    def notes(self):
        with self.lock:
            return {k: e["note"] for k, e in self.entries.items() if e.get("note")}

    def forget_all(self):
        """The location or sources changed: drop everything, including writes still on their way."""
        with self.lock:
            self.gen += 1
            self.entries.clear()
            self.history_busy.clear()
            self.version += 1
        LOG.resolve_prefix("src:")

    def persist(self):
        if not self.persist_enabled:
            return
        with self.cfg_lock:
            try:
                save_config(self.cfg)
            except OSError:
                pass

    def remember(self, key, value):
        if self._stale():
            return
        with self.cfg_lock:
            self.cfg[key] = value
        self.persist()

    def cfg_copy(self):
        with self.cfg_lock:
            return json.loads(json.dumps(self.cfg))

    # -- refresh ------------------------------------------------------------
    def refresh(self, force=False):
        with self.lock:
            if self.busy:
                return False
            self.busy = True
            self.last_refresh = time.time()
            self.version += 1
            gen = self.gen
        threading.Thread(target=self._in_gen, args=(gen, self._run, (force,)), daemon=True).start()
        return True

    def refresh_sync(self, force=False):
        with self.lock:
            self.busy = True
            self.last_refresh = time.time()
            gen = self.gen
        self._in_gen(gen, self._run, (force,))

    def _in_gen(self, gen, fn, args):
        """Run fn in this thread on behalf of refresh generation gen."""
        self._tls.gen = gen
        try:
            fn(*args)
        except Exception as e:  # the wrappers below catch everything; this is a last resort
            self.set("internal", error=f"{type(e).__name__}: {e}")
        finally:
            self._tls.gen = None

    def _parallel(self, calls, limit=8):
        """Run calls [(fn, arg, ...)] at most `limit` at a time on daemon threads (so quitting
        never waits for a slow server) and wait for them all."""
        gen = getattr(self._tls, "gen", None)
        if gen is None:
            gen = self.gen
        todo = list(calls)
        lock = threading.Lock()

        def worker():
            while True:
                with lock:
                    if not todo:
                        return
                    fn, *args = todo.pop(0)
                self._in_gen(gen, fn, args)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(limit, len(todo))))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _run(self, force):
        try:
            cfg = self.cfg_copy()
            loc = self._location(cfg)
            if loc and not self._stale():
                self._parallel([(self._guard, self._owm_all, cfg, loc, force),
                                (self._guard, self._owm_air, cfg, loc, force),
                                (self._guard, self._nws_all, cfg, loc, force),
                                (self._guard, self._om_all, cfg, loc, force),
                                (self._guard, self._iem_all, cfg, loc, force),
                                (self._guard, self._acis, cfg, loc, force),
                                (self._guard, self._zcta, cfg, loc),
                                (self._guard, self._placefiles, cfg, force)])
                if not self._stale():
                    self._guard(self._stations, cfg, loc, force)
            self.clear("internal")
        except Exception as e:
            self.set("internal", error=f"{type(e).__name__}: {e}")
        finally:
            with self.lock:
                self.busy = False
                self.last_done = time.time()
                self.version += 1
            if time.time() - self.last_prune > 3600:
                self.last_prune = time.time()
                prune_disk_cache()

    def refresh_alerts(self, force=False):
        """Only what the background tornado watch needs: the location, its NWS point, the ZIP
        outline, alerts for the state and the point, and IEM's warning polygons."""
        try:
            cfg = self.cfg_copy()
            loc = self._location(cfg)
            if not loc:
                return False
            fast = self.fast_ttl(cfg)
            calls = [(self._guard, self._zcta, cfg, loc)]
            if S.enabled(cfg, "nws"):
                base, geo = S.url(cfg, "nws"), nws_headers(cfg)
                try:
                    pts = self._nws_points(base, loc, geo)
                    self.set("nws_points", pts)
                except ApiError as e:
                    pts = None
                    self.set("nws_points", error=str(e))
                if pts:
                    calls.append((self._task, "nws_alerts_area",
                                  lambda: self._area_alerts(base, pts, loc, geo, fast, force)))
                calls.append((self._task, "nws_alerts",
                              lambda: self.http.get_json(f"{base}/alerts/active?status=actual&point="
                                                         f"{loc['lat']:.4f},{loc['lon']:.4f}", geo, ttl=fast,
                                                         force=force, min_age=10)))
            if S.enabled(cfg, "iem"):
                ibase = S.url(cfg, "iem")
                calls.append((self._task, "iem_sbw",
                              lambda: self.http.get_json(f"{ibase}/geojson/sbw.geojson", ttl=max(45, fast),
                                                         force=force, min_age=30)))
            self._parallel(calls, 4)
            return True
        finally:
            with self.lock:
                self.last_done = time.time()
                self.version += 1

    def _guard(self, fn, *args):
        name = fn.__name__.strip("_")
        try:
            fn(*args)
        except Exception as e:
            self.set(f"internal:{name}", error=f"{type(e).__name__}: {e}")
        else:
            self.clear(f"internal:{name}")

    def _task(self, key, fn, quiet404=False):
        try:
            self.set(key, fn())
        except ApiError as e:
            if quiet404 and e.code == 404:
                self.set(key, {})
            else:
                self.set(key, error=e)
        except Exception as e:
            self.set(key, error=f"{type(e).__name__}: {e}")

    def _note(self, key, text):
        """Record an expected state (not a problem): keeps any data, clears the error."""
        with self.lock:
            if self._stale():
                return
            e = self.entries.setdefault(key, {"data": None, "error": None, "ts": 0.0})
            e["error"], e["note"] = None, text
            self.version += 1
        LOG.resolve("src:" + key)

    def outside_us(self, loc):
        """True when the location is somewhere the U.S.-only sources can't help."""
        if str((loc or {}).get("country") or "US").upper() not in US_AREAS:
            return True
        with self.lock:
            note = (self.entries.get("nws_points") or {}).get("note") or ""
        return "U.S. locations only" in note

    def _off(self, keys, sid):
        name = S.SOURCE_BY_ID[sid].name
        for k in keys:
            with self.lock:
                if self._stale():
                    return
                self.entries[k] = {"data": None, "error": None, "ts": 0.0, "off": name}
                self.version += 1
            LOG.resolve("src:" + k)

    # -- location -----------------------------------------------------------
    def _location(self, cfg):
        lat, lon = num(cfg.get("lat")), num(cfg.get("lon"))
        name = (cfg.get("location_name") or "").strip()
        place = str(cfg.get("zip") or "").strip()
        country = (cfg.get("country") or "US").strip().upper() or "US"
        if lat is not None and lon is not None:
            loc = {"lat": lat, "lon": lon, "name": name, "zip": "", "state": None, "country": country,
                   "how": "coordinates from Settings"}
            if not name and owm_ready(cfg):
                rkey = f"{lat:.4f},{lon:.4f}"
                rev = cfg.get("_rev")
                if isinstance(rev, dict) and rev.get("key") == rkey:
                    loc["name"] = rev.get("name") or ""
                else:
                    try:
                        j = self.http.get_json(owm_url(S.url(cfg, "owm"), "/geo/1.0/reverse",
                                                       cfg["owm_api_key"].strip(), lat=lat, lon=lon, limit=1))
                        top = j[0]
                        loc["name"] = ", ".join(x for x in (top.get("name"), top.get("state")) if x)
                        self.remember("_rev", {"key": rkey, "name": loc["name"]})
                    except (ApiError, IndexError, KeyError, TypeError):
                        pass
            loc["name"] = loc["name"] or f"{lat:.3f}, {lon:.3f}"
            self.set("loc", loc)
            return loc
        is_zip = is_postal(place)
        gkey = f"{place},{country}".lower()
        geo = cfg.get("_geo")
        if isinstance(geo, dict) and geo.get("key") == gkey and num(geo.get("lat")) is not None:
            loc = {"lat": geo["lat"], "lon": geo["lon"], "name": name or geo.get("name") or place,
                   "zip": place if is_zip else "", "state": geo.get("state"), "country": country,
                   "how": f"lookup of {place} ({geo.get('name') or '?'})"}
            self.set("loc", loc)
            return loc
        problem = "nothing to look up"
        if place:
            try:
                found = geocode(self.http, cfg, place)
                geo = dict(found, key=gkey)
                self.remember("_geo", geo)
                loc = {"lat": geo["lat"], "lon": geo["lon"], "name": name or geo.get("name") or place,
                       "zip": place if is_zip else "", "state": geo.get("state"), "country": country,
                       "how": f"{found.get('how')} lookup of {place} ({geo.get('name') or '?'})"}
                self.set("loc", loc)
                return loc
            except (ApiError, KeyError, TypeError, ValueError, IndexError) as e:
                problem = str(e)
        if place in FALLBACK_COORDS:
            lat, lon = FALLBACK_COORDS[place]
            loc = {"lat": lat, "lon": lon, "name": name or place, "zip": place, "state": "TX", "country": country,
                   "how": f"built-in coordinates (lookup failed: {problem})"}
            self.set("loc", loc)
            return loc
        self.set("loc", error=f"Couldn't find {place or '(blank)'}: {problem.rstrip('.')}. "
                              "Check the ZIP code in Settings or enter latitude and longitude.")
        return None

    # -- OpenWeather --------------------------------------------------------
    def onecall_mode(self, cfg):
        if not S.enabled(cfg, "owm"):
            return "disabled"
        if not (cfg.get("owm_api_key") or "").strip():
            return "nokey"
        setting = str(cfg.get("owm_onecall_version") or "auto").lower()
        cap_n = int(num(cfg.get("owm_daily_cap")) or 0)
        if setting == "off" or cap_n <= 0:
            return "off"
        if self.usage.get() >= cap_n:
            return "capped"
        if time.time() < self.owm_backoff_until:
            return "backoff"
        if self.oc_version == "none" and time.time() - self.oc_fail_ts < 3600:
            return "unavailable"
        return "try"

    def _billed(self, url, ttl, force):
        return self.http.get_json(url, ttl=ttl, force=force, on_fetch=self.usage.add, min_age=OWM_MIN_AGE)

    def _owm_all(self, cfg, loc, force):
        keys = ("owm_onecall", "owm_current", "owm_forecast", "owm_daily16")
        if not S.enabled(cfg, "owm"):
            self._off(keys, "owm")
            return
        key = (cfg.get("owm_api_key") or "").strip()
        if not key:
            for k in keys:
                self.clear(k)
            return
        base = S.url(cfg, "owm")
        lat, lon = round(loc["lat"], 4), round(loc["lon"], 4)
        mode = self.onecall_mode(cfg)
        oc = None
        if mode == "try":
            setting = str(cfg.get("owm_onecall_version") or "auto").lower()
            if setting in ("4.0", "3.0"):
                order = [setting]
            elif self.oc_version in ("4.0", "3.0"):
                order = [self.oc_version]
            else:
                order = ["4.0", "3.0"]
            last = None
            for ver in order:
                try:
                    fetch = self._onecall40 if ver == "4.0" else self._onecall30
                    oc = fetch(key, base, lat, lon, force)
                    self.oc_version = ver
                    break
                except ApiError as e:
                    last = e
                    if e.code == 429:
                        self.owm_backoff_until = time.time() + 600
                        break
                    if e.bad_key or e.code not in (401, 404):
                        break
            if oc:
                self.set("owm_onecall", oc)
            elif last is not None:
                if last.code in (401, 404) and not last.bad_key:
                    self.oc_version = "none"
                    self.oc_fail_ts = time.time()
                    self._note("owm_onecall", ONECALL_MSG["unavailable"])
                else:
                    self.set("owm_onecall", error=last)
        elif mode == "backoff":
            self.set("owm_onecall", error=ONECALL_MSG[mode])
        else:
            self._note("owm_onecall", ONECALL_MSG[mode])
        if oc:
            return

        if time.time() < self.owm_backoff_until:
            return  # keep what we have; the model stops using it once it gets old

        def free(path, ttl):
            return lambda: self._owm_get(owm_url(base, path, key, lat=lat, lon=lon, units="metric"),
                                         ttl=ttl, force=force)

        calls = [(self._task, "owm_current", free("/data/2.5/weather", 570)),
                 (self._task, "owm_forecast", free("/data/2.5/forecast", 1800))]
        if self.daily16_ok is not False:
            calls.append((self._guard, self._owm_daily16, key, base, lat, lon, force))
        self._parallel(calls, 3)

    def _owm_get(self, url, **kw):
        """A free OpenWeather request; a 429 pauses every OpenWeather request for 10 minutes."""
        try:
            return self.http.get_json(url, **kw)
        except ApiError as e:
            if e.code == 429:
                self.owm_backoff_until = time.time() + 600
            raise

    def _onecall40(self, key, base, lat, lon, force):
        def url(path, **extra):
            return owm_url(base, "/data/4.0/onecall" + path, key, lat=lat, lon=lon, units="metric", **extra)

        cur = self._billed(url("/current"), 570, force)
        now = int(time.time())
        off = int(num(cur.get("timezone_offset")) or 0)
        out = {"version": "4.0", "timezone": cur.get("timezone"), "timezone_offset": off,
               "current": dig(cur, "data", 0, default={}), "notes": {}}

        def part(name, fn, default):
            try:
                return fn()
            except ApiError as e:
                out["notes"][name] = str(e)
                if e.code == 429:
                    self.owm_backoff_until = time.time() + 600
                return default

        out["minutely"] = part("1-minute timeline", lambda: self._billed(
            url("/timeline/1min"), 570, force).get("data") or [], [])
        out["min15"] = []          # nothing shows the 15-minute timeline any more: no call for it
        hour0 = now - now % 3600
        out["hourly"] = part("hourly timeline", lambda: self._paged(
            url("/timeline/1h", start=hour0), key, hour0 + 49 * 3600, 1800, force, pages=4,
            retry_url=url("/timeline/1h")), [])
        midnight = int(dt.datetime.fromtimestamp(now, dt.timezone(dt.timedelta(seconds=off)))
                       .replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        out["daily"] = part("daily timeline", lambda: self._paged(
            url("/timeline/1day", start=midnight), key, midnight + 10 * 86400, 3600, force, pages=1,
            retry_url=url("/timeline/1day")), [])
        ids = [a for a in (out["current"].get("alerts") or []) if isinstance(a, str)]
        out["alerts"] = [a for a in (self._owm_alert(base, key, aid) for aid in ids[:8]) if a]
        if self.oc_extra_ok.get("overview") is not False:
            try:
                ov = self._billed(owm_url(base, "/data/3.0/onecall/overview", key, lat=lat, lon=lon,
                                          units="metric"), 1800, force)
                out["overview"] = ov.get("weather_overview")
                self.oc_extra_ok["overview"] = True
            except ApiError as e:
                if e.code in (401, 404):
                    self.oc_extra_ok["overview"] = False
                out["notes"]["summary"] = str(e)
        return out

    def _paged(self, first_url, key, until, ttl, force, pages=2, retry_url=None):
        items, seen, url = [], set(), first_url
        for n in range(pages):
            try:
                page = self._billed(url, ttl, force)
            except ApiError as e:
                if n == 0 and e.code == 400 and retry_url:
                    url, retry_url = retry_url, None
                    page = self._billed(url, ttl, force)
                else:
                    raise
            data = page.get("data") or []
            for rec in data:
                t = rec.get("dt")
                if t not in seen:
                    seen.add(t)
                    items.append(rec)
            nxt = page.get("next")
            if not data or not nxt or max(r.get("dt") or 0 for r in data) >= until:
                break
            url = with_params(nxt, appid=key, units="metric")
        items.sort(key=lambda r: r.get("dt") or 0)
        return items

    def _owm_alert(self, base, key, aid):
        hit = self.alert_cache.get(aid)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        try:
            j = self.http.get_json(owm_url(base, f"/data/4.0/onecall/alert/{urllib.parse.quote(aid)}", key),
                                   ttl=6 * 3600, on_fetch=self.usage.add)
        except ApiError:
            return None
        desc = j.get("description")
        if isinstance(desc, list):
            english = [d for d in desc if str(d.get("language", "")).lower().startswith("en")]
            pick = (english or desc or [{}])[0]
            desc = pick.get("description") if isinstance(pick, dict) else str(pick)
        alert = {"id": j.get("id") or aid, "sender_name": j.get("sender_name"),
                 "event": j.get("event") or ", ".join(j.get("tags") or []) or "Weather alert",
                 "start": j.get("start"), "end": j.get("end"), "description": desc or "",
                 "tags": j.get("tags") or []}
        self.alert_cache[aid] = (time.time(), alert)
        return alert

    def _onecall30(self, key, base, lat, lon, force):
        oc = self._billed(owm_url(base, "/data/3.0/onecall", key, lat=lat, lon=lon, units="metric"), 570, force)
        out = dict(oc)
        out["version"] = "3.0"
        out["notes"] = {}
        out["min15"] = []
        try:
            ov = self._billed(owm_url(base, "/data/3.0/onecall/overview", key, lat=lat, lon=lon,
                                      units="metric"), 1800, force)
            out["overview"] = ov.get("weather_overview")
        except ApiError as e:
            out["notes"]["summary"] = str(e)
        daily = oc.get("daily") or []
        off = int(num(oc.get("timezone_offset")) or 0)
        tzs = f"{'+' if off >= 0 else '-'}{abs(off) // 3600:02d}:{abs(off) % 3600 // 60:02d}"
        longrange = []
        if daily:
            day0 = dt.datetime.fromtimestamp(daily[0]["dt"], dt.timezone(dt.timedelta(seconds=off))).date()
            for i in range(len(daily), 10):
                d = day0 + dt.timedelta(days=i)
                try:
                    longrange.append(self._billed(owm_url(base, "/data/3.0/onecall/day_summary", key, lat=lat,
                                                          lon=lon, date=d.isoformat(), tz=tzs, units="metric"),
                                                  3 * 3600, False))
                except ApiError as e:
                    longrange.append({"date": d.isoformat(), "error": str(e)})
        out["longrange"] = longrange
        return out

    def _owm_daily16(self, key, base, lat, lon, force):
        try:
            data = self.http.get_json(owm_url(base, "/data/2.5/forecast/daily", key, lat=lat, lon=lon,
                                              cnt=10, units="metric"), ttl=1800, force=force)
            self.daily16_ok = True
            self.set("owm_daily16", data)
        except ApiError as e:
            # OpenWeather answers "Invalid API key" here even for good keys whose plan lacks this
            # endpoint; when the key works elsewhere, quietly stop asking.
            key_ok = bool(self.get("owm_current") or self.get("owm_forecast") or self.get("owm_onecall"))
            if e.code == 401 and (key_ok or not e.bad_key):
                self.daily16_ok = False
                self._note("owm_daily16", "not included in this OpenWeather plan")
            else:
                self.set("owm_daily16", error=e)

    def _owm_air(self, cfg, loc, force):
        keys = ("owm_air", "owm_air_fc", "owm_air_hist")
        if not S.enabled(cfg, "owm"):
            self._off(keys, "owm")
            return
        key = (cfg.get("owm_api_key") or "").strip()
        if not key:
            for k in keys:
                self.clear(k)
            return
        if time.time() < self.owm_backoff_until:
            return
        base = S.url(cfg, "owm")
        lat, lon = round(loc["lat"], 4), round(loc["lon"], 4)
        get = self._owm_get
        end = int(time.time()) // 3600 * 3600
        self._task("owm_air", lambda: get(owm_url(base, "/data/2.5/air_pollution", key, lat=lat, lon=lon),
                                          ttl=1800, force=force))
        self._task("owm_air_fc", lambda: get(owm_url(base, "/data/2.5/air_pollution/forecast", key,
                                                     lat=lat, lon=lon), ttl=3600, force=force))
        self._task("owm_air_hist", lambda: get(owm_url(base, "/data/2.5/air_pollution/history", key, lat=lat,
                                                       lon=lon, start=end - 86400, end=end), ttl=3600))

    # -- National Weather Service -------------------------------------------
    def _nws_all(self, cfg, loc, force):
        keys = ("nws_points", "nws_forecast", "nws_hourly", "nws_grid", "nws_alerts", "nws_alerts_area", "nws_afd",
                "nws_hwo", "nws_headlines", "nws_stories", "nws_briefing", "nws_radio", "nws_station", "nws_obs",
                "nws_obs_hist", "nws_taf")
        if not S.enabled(cfg, "nws"):
            self._off(keys, "nws")
            return
        base = S.url(cfg, "nws")
        geo = nws_headers(cfg)
        ld = nws_headers(cfg, "application/ld+json")
        try:
            pts = self._nws_points(base, loc, geo)
        except ApiError as e:
            if e.code == 404:  # outside the U.S.: nothing from NWS here, and that's fine
                for k in keys:
                    self._note(k, "the National Weather Service covers U.S. locations only")
                    with self.lock:
                        if k in self.entries:
                            self.entries[k]["data"] = None
                return
            self.set("nws_points", error=str(e))
            return
        self.set("nws_points", pts)
        get = self.http.get_json
        office = pts.get("gridId") or pts.get("cwa")
        units = "si" if cfg.get("units") == "metric" else "us"
        fast = self.fast_ttl(cfg)

        def link(field):
            url = pts.get(field)
            if not url:
                raise ApiError(0, f"NWS has no {field} link for this point")
            return rebase(url, base, DEFAULT_NWS_BASE)

        def need_office():
            if not office:
                raise ApiError(0, "no NWS office for this point")
            return office

        jobs = [
            ("nws_forecast", lambda: get(with_params(link("forecast"), units=units), geo, ttl=900, force=force), False),
            ("nws_hourly", lambda: get(with_params(link("forecastHourly"), units=units), geo, ttl=900,
                                       force=force), False),
            ("nws_grid", lambda: get(link("forecastGridData"), geo, ttl=1800, force=force), False),
            ("nws_alerts", lambda: get(f"{base}/alerts/active?status=actual&point={loc['lat']:.4f},"
                                       f"{loc['lon']:.4f}", geo, ttl=fast, force=force, min_age=10), False),
            ("nws_alerts_area", lambda: self._area_alerts(base, pts, loc, geo, fast, force), False),
            ("state_map", lambda: self._state_map(cfg, pts, loc), True),
            ("nws_afd", lambda: self._nws_product(base, "AFD", need_office(), ld, force), False),
            ("nws_hwo", lambda: self._nws_product(base, "HWO", need_office(), ld, force), True),
            ("nws_headlines", lambda: get(f"{base}/offices/{need_office()}/headlines", ld, ttl=1800,
                                          force=force), True),
            ("nws_stories", lambda: get(f"{base}/offices/{need_office()}/weatherstories", ld, ttl=1800,
                                        force=force), True),
            ("nws_briefing", lambda: get(f"{base}/offices/{need_office()}/briefing", ld, ttl=1800,
                                         force=force), True),
            ("nws_radio", lambda: self._nws_radio(cfg, base, pts, loc, force), True),
        ]
        self._parallel([(self._task, k, fn, quiet) for k, fn, quiet in jobs] +
                       [(self._guard, self._nws_obs, cfg, base, pts, geo, ld, force)], 4)

    def _placefiles(self, cfg, force):
        """GRLevelX placefiles from Settings, each fetched as often as it asks (Refresh:)."""
        from . import placefile
        want = placefile.urls(cfg.get("placefiles"))
        keys = {"pf:" + u for u in want}
        with self.lock:
            gone = [k for k in self.entries if k.startswith("pf:") and k not in keys]
        for k in gone:
            self.clear(k)

        def one(url):
            prev = self.get("pf:" + url) or {}
            every = max(15, min(3600, prev.get("refresh") or 60))
            if placefile.is_local(url):
                path = placefile.local_path(url)
                try:
                    mtime = os.stat(path).st_mtime
                except OSError as e:
                    raise ApiError(0, f"can't read {path}: {e.strerror or e}")
                if prev and prev.get("mtime") == mtime and not force:
                    return prev
                try:
                    pf = placefile.parse(placefile.read_local(url), url)
                except placefile.PlacefileError as e:
                    raise ApiError(0, str(e))
                pf["mtime"] = mtime
            else:
                if prev and time.time() - prev.get("fetched", 0) < every - 2 and not force:
                    return prev
                pf = placefile.parse(self.http.get_text(url, ttl=every - 2, force=force, min_age=10), url)
            pf["fetched"] = time.time()
            if not pf["items"] and pf["bad"] > 5:
                raise ApiError(0, f"that address didn't return a placefile ({pf['bad']} lines weren't understood)")
            return pf

        self._parallel([(self._task, "pf:" + u, (lambda u=u: one(u))) for u in want], 4)

    @staticmethod
    def fast_ttl(cfg):
        """Alerts, warnings and storm reports are checked at every refresh."""
        return max(10, min(600, intval(cfg, "refresh_seconds", 15, 3600) - 3))

    def _area_alerts(self, base, pts, loc, hdr, ttl, force):
        """Every alert in the home state (the ticker's area and the tornado alarm use it)."""
        state = (pts.get("state") or loc.get("state") or "").upper()
        if len(state) != 2:
            raise ApiError(0, "no state for this location")
        return self.http.get_json(f"{base}/alerts/active?status=actual&area={state}", hdr, ttl=ttl, force=force,
                                  min_age=10)

    def _state_map(self, cfg, pts, loc):
        """The home state's counties and largest places from the Census TIGERweb service,
        for the Alerts screen's warnings map (kept for a month: they hardly change)."""
        from . import statemap
        state = (pts.get("state") or loc.get("state") or "").upper()
        fips = statemap.STATES.get(state, (None,))[0]
        if not fips:
            raise ApiError(0, "no state for this location")
        if not S.enabled(cfg, "tigerweb"):
            raise ApiError(0, "the Census TIGERweb source is turned off")
        base = S.url(cfg, "tigerweb")
        counties = self.http.get_json(statemap.counties_url(base, fips), ttl=86400 * 30, disk_ttl=86400 * 90)
        try:
            places = self.http.get_json(statemap.places_url(base, fips), ttl=86400 * 30, disk_ttl=86400 * 90)
        except ApiError:
            places = {}
        return {"state": state, "counties": counties, "places": places}

    def _nws_points(self, base, loc, hdr):
        key = f"{loc['lat']:.4f},{loc['lon']:.4f}"
        cached = self.cfg.get("_nws_points")
        try:
            j = self.http.get_json(f"{base}/points/{key}", hdr, ttl=6 * 3600)
        except ApiError:
            if isinstance(cached, dict) and cached.get("key") == key and cached.get("props"):
                return dict(cached["props"], astronomicalData=None)
            raise
        p = j.get("properties") or {}
        props = {k: p.get(k) for k in ("gridId", "gridX", "gridY", "forecast", "forecastHourly",
                                        "forecastGridData", "observationStations", "timeZone",
                                        "radarStation", "forecastZone", "county", "fireWeatherZone",
                                        "cwa", "forecastOffice", "type")}
        rel = dig(p, "relativeLocation", "properties") or dig(p, "relativeLocation") or {}
        props["city"], props["state"] = rel.get("city"), rel.get("state")
        props["distance"], props["bearing"] = nws_val(rel.get("distance")), nws_val(rel.get("bearing"))
        props["astronomicalData"] = p.get("astronomicalData")
        props["nwr"] = p.get("nwr") or {}
        props["county_name"], props["county_zone"] = self._county(base, props.get("county"), hdr, cached)
        if not (isinstance(cached, dict) and cached.get("key") == key
                and time.time() - (cached.get("ts") or 0) < 7 * 86400):
            saved = {k: v for k, v in props.items() if k != "astronomicalData"}
            self.remember("_nws_points", {"key": key, "ts": time.time(), "props": saved})
        return props

    def _county(self, base, url, hdr, cached):
        """The county (or parish) the NWS point is filed under, from its county zone: its
        name as people say it and the zone's own ('Dallas County', 'Dallas'); blanks when
        that can't be had."""
        code = str(url or "").rstrip("/").rsplit("/", 1)[-1].upper()
        if not re.fullmatch(r"[A-Z]{2}C\d{3}", code):
            return "", ""
        try:
            j = self.http.get_json(f"{base}/zones/county/{code}", hdr, ttl=30 * 86400, disk_ttl=90 * 86400)
            p = j.get("properties") or {}
            name = " ".join(str(p.get("name") or "").split())
            return county_label(name, p.get("state")), name
        except (ApiError, AttributeError, TypeError):
            old = cached.get("props") if isinstance(cached, dict) else None
            if isinstance(old, dict) and old.get("county") == url:
                return old.get("county_name") or "", old.get("county_zone") or ""
            return "", ""

    def _nws_obs(self, cfg, base, pts, geo, ld, force):
        get = self.http.get_json
        station = (cfg.get("nws_station") or "").strip().upper()
        name = None
        try:
            if station:
                try:
                    info = get(f"{base}/stations/{station}", geo, ttl=86400)
                    name = dig(info, "properties", "name")
                except ApiError as e:
                    if e.code == 404:
                        raise ApiError(404, f"station {station} doesn't exist")
            else:
                src = pts.get("observationStations")
                cached = self.cfg.get("_nws_station")
                if isinstance(cached, dict) and cached.get("key") == src and cached.get("id"):
                    station, name = cached["id"], cached.get("name")
                else:
                    if not src:
                        raise ApiError(0, "NWS lists no observation stations for this point")
                    feats = get(with_params(rebase(src, base, DEFAULT_NWS_BASE), limit=5), geo).get("features") or []
                    if not feats:
                        raise ApiError(0, "NWS lists no observation stations for this point")
                    props = feats[0].get("properties") or {}
                    station, name = props.get("stationIdentifier"), props.get("name")
                    self.remember("_nws_station", {"key": src, "id": station, "name": name})
        except ApiError as e:
            self.set("nws_station", error=e)
            return
        self.set("nws_station", {"id": station, "name": name})
        hour = int(time.time()) // 3600 * 3600
        since = dt.datetime.fromtimestamp(hour - 26 * 3600, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._task("nws_obs", lambda: get(f"{base}/stations/{station}/observations/latest",
                                          geo, ttl=300, force=force))
        self._task("nws_obs_hist", lambda: get(with_params(f"{base}/stations/{station}/observations",
                                                           start=since, limit=100), geo, ttl=600, force=force))
        if re.fullmatch(r"[KPT][A-Z0-9]{3}", station or ""):
            self._task("nws_taf", lambda: self._nws_taf(base, station, ld, force), quiet404=True)
        else:
            self.clear("nws_taf")  # only airports have a TAF

    def _nws_taf(self, base, station, ld, force):
        j = self.http.get_json(f"{base}/stations/{station}/tafs", ld, ttl=1800, force=force)
        text, issued = find_taf(j, station)
        if not text:
            if isinstance(j, dict):
                items = j.get("@graph") or j.get("features") or []
            else:
                items = j if isinstance(j, list) else []
            first_item = items[0] if items and isinstance(items[0], dict) else {}
            link = first_item.get("@id") or first_item.get("id")
            issued = issued or first_item.get("issueTime") or first_item.get("issuanceTime")
            if isinstance(link, str) and link.startswith("http"):
                link = rebase(link, base, DEFAULT_NWS_BASE)
                try:
                    text, when = find_taf(self.http.get_json(link, ld, ttl=1800), station)
                    issued = issued or when
                except ApiError:
                    raw = self.http.get_text(link, dict(ld, Accept="text/plain, */*"), ttl=1800)
                    text = raw if re.search(r"\bTAF\b", raw or "") else None
        return {"station": station, "text": text, "issued": issued}

    def _nws_product(self, base, ptype, office, hdr, force):
        """The latest text product of one type from the local office.

        Many offices no longer issue some products (some have stopped issuing the
        Hazardous Weather Outlook), and for those the API's /latest address answers
        HTTP 500 instead of 404. So first ask which offices issue the product."""
        none = {"text": None, "issued": None, "office": office, "missing": True}
        try:
            issuers = self.http.get_json(f"{base}/products/types/{ptype}/locations", hdr, ttl=6 * 3600,
                                         disk_ttl=86400).get("locations")
        except ApiError:
            issuers = None
        if isinstance(issuers, dict) and issuers and office not in issuers:
            return none
        try:
            prod = self.http.get_json(f"{base}/products/types/{ptype}/locations/{office}/latest",
                                      hdr, ttl=1800, force=force)
        except ApiError as e:
            if e.code != 404 and e.code < 500:
                raise
            lst = self.http.get_json(f"{base}/products/types/{ptype}/locations/{office}",
                                     hdr, ttl=1800, force=force)
            graph = lst.get("@graph") or []
            if not graph:
                return none
            pid = graph[0].get("id") or graph[0].get("@id") or ""
            url = rebase(pid, base, DEFAULT_NWS_BASE) if pid.startswith("http") else f"{base}/products/{pid}"
            prod = self.http.get_json(url, hdr, ttl=86400)
        if isinstance(prod.get("@graph"), list) and prod["@graph"]:
            prod = prod["@graph"][0]
        return {"text": prod.get("productText"), "issued": prod.get("issuanceTime"),
                "name": prod.get("productName"), "office": office}

    def _nws_radio(self, cfg, base, pts, loc, force):
        from .model import ssml_to_text
        nwr = pts.get("nwr") or {}
        url = nwr.get("pointBroadcast") or f"{base}/points/{loc['lat']:.4f},{loc['lon']:.4f}/radio"
        text = self.http.get_text(rebase(url, base, DEFAULT_NWS_BASE),
                                  nws_headers(cfg, "application/ssml+xml"), ttl=1800, force=force)
        return {"transmitter": nwr.get("transmitter"), "same": nwr.get("sameCode"),
                "text": ssml_to_text(text), "area_url": nwr.get("areaBroadcast")}

    # -- Open-Meteo -----------------------------------------------------------
    @staticmethod
    def _om_wait(msg):
        """How long to leave Open-Meteo alone after it says we've hit a limit."""
        low = msg.lower()
        if "minutely" in low:
            return 65
        if "hourly" in low:
            return 3600 - time.time() % 3600 + 30
        return 3600 if "daily" in low else 300

    def _om_limited(self, fn):
        def run():
            try:
                return fn()
            except ApiError as e:
                if e.code == 429:
                    self.om_backoff_until = time.time() + self._om_wait(str(e))
                raise
        return run

    def _om_all(self, cfg, loc, force):
        lat, lon = round(loc["lat"], 4), round(loc["lon"], 4)
        if not S.enabled(cfg, "openmeteo"):
            self._off(("om_forecast",), "openmeteo")
        if not S.enabled(cfg, "openmeteo_aq"):
            self._off(("om_air",), "openmeteo_aq")
        if time.time() < self.om_backoff_until:
            return      # over Open-Meteo's free limit: keep what we have until it resets
        if S.enabled(cfg, "openmeteo"):
            self._task("om_forecast", self._om_limited(lambda: self._om_forecast(cfg, lat, lon, force)))
        if S.enabled(cfg, "openmeteo_aq"):
            self._task("om_air", self._om_limited(lambda: self.http.get_json(
                f"{S.url(cfg, 'openmeteo_aq')}/v1/air-quality?" + urllib.parse.urlencode({
                    "latitude": lat, "longitude": lon, "timezone": "auto", "timeformat": "unixtime",
                    "forecast_days": 4, "past_days": 1,
                    "current": "us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide,sulphur_dioxide,carbon_monoxide,"
                               "us_aqi_pm2_5,us_aqi_pm10,us_aqi_ozone,us_aqi_nitrogen_dioxide,"
                               "us_aqi_sulphur_dioxide,us_aqi_carbon_monoxide,uv_index,dust,aerosol_optical_depth",
                    "hourly": "us_aqi,pm2_5,pm10,ozone,uv_index"}), ttl=1800, force=force, min_age=300)))

    def _om_forecast(self, cfg, lat, lon, force):
        base = S.url(cfg, "openmeteo")
        params = {"latitude": lat, "longitude": lon, "timezone": "auto", "timeformat": "unixtime",
                  "wind_speed_unit": "ms", "forecast_days": 16}
        varsets = OM_SAFE if self.om_safe else {"current": OM_CURRENT, "hourly": OM_HOURLY, "daily": OM_DAILY}
        params.update(varsets)
        url = f"{base}/v1/forecast?" + urllib.parse.urlencode(params)
        try:
            return self.http.get_json(url, ttl=900, force=force, min_age=120)
        except ApiError as e:
            if e.code == 400 and not self.om_safe:
                self.om_safe = True  # a variable was rejected; retry with the basic set
                return self._om_forecast(cfg, lat, lon, force)
            raise

    # -- Iowa Environmental Mesonet --------------------------------------------
    def _iem_all(self, cfg, loc, force):
        keys = ("iem_currents", "iem_outlook", "iem_lsr", "iem_sbw", "iem_watches")
        if not S.enabled(cfg, "iem"):
            self._off(keys, "iem")
            return
        base = S.url(cfg, "iem")
        lat, lon = loc["lat"], loc["lon"]
        get = self.http.get_json
        now = dt.datetime.fromtimestamp((int(time.time()) // 900 + 1) * 900, dt.timezone.utc)
        b, e = (now - dt.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%MZ"), now.strftime("%Y-%m-%dT%H:%MZ")
        fast = self.fast_ttl(cfg)
        try:
            lsr_miles = int(max(60, min(200, float(cfg.get("ticker_area") or 100))))
        except ValueError:
            lsr_miles = 100
        jobs = [
            ("iem_outlook", lambda: get(f"{base}/api/1/nws/outlook_by_point.json?lat={lat:.4f}&lon={lon:.4f}",
                                        ttl=1800, force=force, min_age=300)),
            ("iem_lsr", lambda: get(f"{base}/api/1/nws/lsrs_by_point.json?lat={lat:.4f}&lon={lon:.4f}"
                                    f"&radius_miles={lsr_miles}&begints={b}&endts={e}", ttl=max(60, fast),
                                    force=force, min_age=45)),
            ("iem_sbw", lambda: get(f"{base}/geojson/sbw.geojson", ttl=max(45, fast), force=force, min_age=30)),
            ("iem_watches", lambda: get(f"{base}/api/1/spc_watch_outline.geojson", ttl=120, force=force,
                                        min_age=60)),
        ]
        calls = [(self._task, k, fn) for k, fn in jobs]
        if self.outside_us(loc):
            self._note("iem_currents", "the live station feed covers U.S. locations only")
        else:
            calls.append((self._task, "iem_currents", lambda: self._iem_currents(cfg, loc, force)))
        self._parallel(calls, 4)

    def _iem_currents(self, cfg, loc, force):
        base = S.url(cfg, "iem")
        pts = self.get("nws_points") or {}
        wfo = pts.get("cwa") or pts.get("gridId")
        state = loc.get("state") or pts.get("state")
        if not wfo:
            # NWS may still be answering; wait briefly for the point lookup
            for _ in range(20):
                time.sleep(0.25)
                pts = self.get("nws_points") or {}
                wfo = pts.get("cwa") or pts.get("gridId")
                state = state or pts.get("state")
                if wfo or self.err("nws_points") or not S.enabled(cfg, "nws"):
                    break
        ttl = 240 if self.stations_visible else 540
        rows, seen = [], set()
        queries = []
        if wfo:
            queries.append(f"wfo={wfo}")
        if state and len(state) == 2:
            queries.append(f"state={state.upper()}")
        if not queries:
            raise ApiError(0, "no NWS office or state to ask IEM about")
        last_err = None
        for q in queries:
            try:
                j = self.http.get_json(f"{base}/api/1/currents.json?{q}&minutes=180", ttl=ttl, force=force,
                                       min_age=120)
            except ApiError as e:
                last_err = e
                continue
            for r in j.get("data") or []:
                sid = (r.get("station"), r.get("network"))
                if sid in seen:
                    continue
                seen.add(sid)
                d = distance_mi(loc["lat"], loc["lon"], num(r.get("lat")), num(r.get("lon")))
                if d is not None and d <= 75:
                    r["_dist"] = d
                    rows.append(r)
        if not rows and last_err:
            raise last_err
        rows.sort(key=lambda r: r["_dist"])
        return {"rows": rows, "ts": time.time()}

    # -- ACIS almanac -------------------------------------------------------------
    def _acis(self, cfg, loc, force):
        if not S.enabled(cfg, "acis"):
            self._off(("acis",), "acis")
            return
        self._task("acis", lambda: self._acis_fetch(cfg, loc, force))

    def _acis_station(self, cfg, loc):
        base = S.url(cfg, "acis")
        lat, lon = loc["lat"], loc["lon"]
        key = f"{lat:.2f},{lon:.2f}"
        cached = self.cfg.get("_acis")
        if isinstance(cached, dict) and cached.get("key") == key and time.time() - (cached.get("ts") or 0) < 30 * 86400:
            return cached
        today = dt.date.today()
        best = None
        for pad in (0.5, 1.0, 1.6):
            j = self.http.post_json(f"{base}/StnMeta", {
                "bbox": f"{lon - pad:.3f},{lat - pad * 0.8:.3f},{lon + pad:.3f},{lat + pad * 0.8:.3f}",
                "elems": "maxt", "meta": "name,sids,ll,valid_daterange,uid"}, ttl=86400, disk_ttl=7 * 86400)
            for m in j.get("meta") or []:
                try:
                    start, end = m["valid_daterange"][0]
                    start, end = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
                    mlon, mlat = m["ll"]
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                if (today - end).days > 12 or (today - start).days < 30 * 365:
                    continue
                faa = next((s.split()[0] for s in m.get("sids") or [] if s.endswith(" 3")), None)
                d = distance_mi(lat, lon, mlat, mlon)
                score = d - (8 if faa else 0) - min(10, (today - start).days / 3650.0)
                if best is None or score < best[0]:
                    best = (score, {"name": m.get("name"), "uid": m.get("uid"), "faa": faa, "dist": d,
                                    "lat": mlat, "lon": mlon, "since": start.isoformat()})
            if best:
                break
        if not best:
            raise ApiError(0, "no long-running climate station nearby")
        rec = dict(best[1], key=key, ts=time.time())
        self.remember("_acis", rec)
        return rec

    def _acis_fetch(self, cfg, loc, force):
        base = S.url(cfg, "acis")
        st = self._acis_station(cfg, loc)
        today = dt.date.today()
        start = today.replace(day=1) - dt.timedelta(days=1)
        md = f"{today.month:02d}-{today.day:02d}"
        elems = [{"name": "maxt"}, {"name": "mint"}, {"name": "pcpn"}, {"name": "snow"},
                 {"name": "maxt", "normal": "1"}, {"name": "mint", "normal": "1"},
                 {"name": "pcpn", "duration": "mtd", "reduce": "sum"},
                 {"name": "pcpn", "duration": "mtd", "reduce": "sum", "normal": "1"},
                 {"name": "pcpn", "duration": "ytd", "reduce": "sum"},
                 {"name": "pcpn", "duration": "ytd", "reduce": "sum", "normal": "1"},
                 {"name": "avgt", "duration": "mtd", "reduce": "mean"},
                 {"name": "avgt", "duration": "mtd", "reduce": "mean", "normal": "1"}]
        rec_elems = [
            {"name": "maxt", "interval": "dly", "duration": "dly", "smry": {"reduce": "max", "add": "date"},
             "smry_only": 1, "groupby": ["year", md, md]},
            {"name": "mint", "interval": "dly", "duration": "dly", "smry": {"reduce": "min", "add": "date"},
             "smry_only": 1, "groupby": ["year", md, md]},
            {"name": "pcpn", "interval": "dly", "duration": "dly", "smry": {"reduce": "max", "add": "date"},
             "smry_only": 1, "groupby": ["year", md, md]},
        ]
        sids = []
        if st.get("faa"):
            sids.append({"sid": f"{st['faa']}thr"})
        if st.get("uid"):
            sids.append({"uid": st["uid"]})
        last = None
        for ident in sids:
            try:
                daily = self.http.post_json(f"{base}/StnData", dict(ident, sdate=start.isoformat(),
                                                                     edate=today.isoformat(), elems=elems),
                                            ttl=6 * 3600, force=False)
                if daily.get("error"):
                    raise ApiError(0, daily["error"])
                recs = self.http.post_json(f"{base}/StnData", dict(ident, sdate="por", edate="por",
                                                                    elems=rec_elems), ttl=12 * 3600)
                if recs.get("error"):
                    recs = {}
                return {"station": st, "ident": ident, "daily": daily, "records": recs, "date": today.isoformat()}
            except ApiError as e:
                last = e
        raise last or ApiError(0, "almanac unavailable")

    # -- ZIP outline ----------------------------------------------------------------
    def _zcta(self, cfg, loc):
        z = loc.get("zip") or ""
        if not re.fullmatch(r"\d{5}", z) or (loc.get("country") or "US") != "US":
            self.clear("zcta")
            return
        if not S.enabled(cfg, "tigerweb"):
            self._off(("zcta",), "tigerweb")
            return

        def fetch():
            base = S.url(cfg, "tigerweb")
            url = (f"{base}/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/2/query?"
                   + urllib.parse.urlencode({"where": f"ZCTA5='{z}'", "outFields": "ZCTA5,CENTLAT,CENTLON,AREALAND",
                                             "returnGeometry": "true", "outSR": 4326, "geometryPrecision": 5,
                                             "f": "json"}))
            j = self.http.get_json(url, ttl=30 * 86400, disk_ttl=60 * 86400)
            feats = j.get("features") or []
            if not feats:
                return {"zip": z, "rings": [], "missing": True}
            f = feats[0]
            rings = [[(p[0], p[1]) for p in ring] for ring in dig(f, "geometry", "rings", default=[])]
            a = f.get("attributes") or {}
            return {"zip": z, "rings": rings, "lat": num(a.get("CENTLAT")), "lon": num(a.get("CENTLON")),
                    "area_km2": (num(a.get("AREALAND")) or 0) / 1e6}

        self._task("zcta", fetch)

    # -- station feed -----------------------------------------------------------------
    def _station_meta(self, cfg, states):
        """All NWS stations in the given states (cached for a day)."""
        base = S.url(cfg, "nws")
        hdr = nws_headers(cfg)
        feats = []
        for st in states:
            url = f"{base}/stations?state={st}&limit=500"
            for _page in range(6):
                j = self.http.get_json(url, hdr, ttl=86400, disk_ttl=86400)
                page = j.get("features") or []
                feats += page
                nxt = dig(j, "pagination", "next")
                if len(page) < 500 or not nxt:
                    break
                url = rebase(nxt, base, DEFAULT_NWS_BASE)
        return feats

    def _stations(self, cfg, loc, force):
        if not S.enabled(cfg, "nws"):
            self._off(("stations",), "nws")
            return
        with self.lock:
            if self.stations_busy:
                return
            self.stations_busy = True
        try:
            self._stations_inner(cfg, loc, force)
        finally:
            with self.lock:
                self.stations_busy = False

    def _stations_inner(self, cfg, loc, force):
        pts = self.get("nws_points") or {}
        state = (pts.get("state") or loc.get("state") or "").upper()
        if len(state) != 2 or self.outside_us(loc):
            self._note("stations", "the live station feed covers U.S. locations only")
            return
        radius = intval(cfg, "station_radius", 1, 100)
        cap = intval(cfg, "station_max", 5, 120)
        states = [state]
        try:
            meta = self._station_meta(cfg, states)
        except ApiError as e:
            self.set("stations", error=e)
            return
        lat, lon = loc["lat"], loc["lon"]
        near = []
        for f in meta:
            p = f.get("properties") or {}
            coords = dig(f, "geometry", "coordinates")
            if not coords:
                continue
            d = distance_mi(lat, lon, coords[1], coords[0])
            if d is None or d > radius:
                continue
            near.append({"id": p.get("stationIdentifier"), "name": p.get("name"), "provider": p.get("provider") or "",
                         "sub": p.get("subProvider") or "", "lat": coords[1], "lon": coords[0],
                         "elev": nws_val(p.get("elevation")), "dist": d})
        near.sort(key=lambda s: s["dist"])
        near = near[:cap]
        iem = self.get("iem_currents") or {}
        iem_ids = set()
        for r in iem.get("rows") or []:
            sid = str(r.get("station") or "").upper()
            iem_ids.add(sid)
            if (r.get("network") or "").endswith(("ASOS", "AWOS")) and len(sid) == 3:
                iem_ids.add("K" + sid)
        base = S.url(cfg, "nws")
        hdr = nws_headers(cfg)
        ttl = 240 if self.stations_visible else 540
        latest = {}
        want = [s for s in near if s["id"] and (s["id"].upper() not in iem_ids or s["provider"] == "APRSWXNET")]

        def one(s):
            try:
                j = self.http.get_json(f"{base}/stations/{s['id']}/observations/latest", hdr, ttl=ttl,
                                       force=force, min_age=90)
                return s["id"], j.get("properties") or {}, None
            except ApiError as e:
                return s["id"], None, str(e)

        results = {}

        def run(s):
            results[s["id"]] = one(s)

        self._parallel([(run, s) for s in want], 6)
        for s in want:
            sid, props, err = results.get(s["id"]) or (s["id"], None, "no answer")
            latest[sid] = props if props is not None else {"_error": err}
        self.set("stations", {"near": near, "latest": latest, "radius": radius, "state": state,
                              "ts": time.time()})

    def refresh_stations(self, force=False):
        """Refresh just the station feed (used while the Stations screen is open)."""
        loc = self.get("loc")
        if not loc or self.busy or self.stations_busy or self.outside_us(loc):
            return False
        cfg = self.cfg_copy()

        def work():
            self._task("iem_currents", lambda: self._iem_currents(cfg, loc, force))
            self._guard(self._stations, cfg, loc, force)

        threading.Thread(target=self._in_gen, args=(self.gen, work, ()), daemon=True).start()
        return True

    def request_history(self, sid):
        """24-hour observation history for any station, fetched on demand."""
        key = f"hist:{sid}"
        if sid in self.history_busy:
            return
        if self.fresh(key, 600) is not None:
            return
        if self.err(key) and (self.age(key) or 0) < 600 and self.entries.get(key, {}).get("tried", 0) > time.time() - 120:
            return  # failed a moment ago; don't hammer the server while the screen redraws
        cfg = self.cfg_copy()
        if not S.enabled(cfg, "nws"):
            return
        self.history_busy.add(sid)
        with self.lock:  # keep only the dozen most recently viewed histories
            hist = sorted((e.get("ts") or e.get("tried") or 0, k) for k, e in self.entries.items() if k.startswith("hist:"))
            for _t, k in hist[:-12]:
                del self.entries[k]
                LOG.resolve("src:" + k)
            self.entries.setdefault(key, {"data": None, "error": None, "ts": 0.0})["tried"] = time.time()

        def work():
            try:
                base = S.url(cfg, "nws")
                hour = int(time.time()) // 3600 * 3600
                since = dt.datetime.fromtimestamp(hour - 26 * 3600, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                self._task(key, lambda: self.http.get_json(with_params(f"{base}/stations/{sid}/observations",
                                                                       start=since, limit=150),
                                                           nws_headers(cfg), ttl=600))
            finally:
                self.history_busy.discard(sid)

        threading.Thread(target=self._in_gen, args=(self.gen, work, ()), daemon=True).start()


def find_taf(obj, station, depth=0):
    if depth > 6:
        return None, None
    if isinstance(obj, str):
        if re.search(r"\bTAF\b", obj) and station in obj:
            return obj.strip(), None
        return None, None
    if isinstance(obj, dict):
        issued = obj.get("issueTime") or obj.get("issuanceTime") or obj.get("issued")
        for k in ("rawAviationText", "rawText", "raw", "text", "productText", "rawTAF", "message"):
            if isinstance(obj.get(k), str):
                text, _ = find_taf(obj[k], station, depth + 1)
                if text:
                    return text, issued
        for val in obj.values():
            text, when = find_taf(val, station, depth + 1)
            if text:
                return text, when or issued
    if isinstance(obj, list):
        for val in obj[:5]:
            text, when = find_taf(val, station, depth + 1)
            if text:
                return text, when
    return None, None


def explain_onecall(ver, e):
    if e.bad_key:
        return f"can't check until the key itself works ({e})"
    if e.code == 401:
        return ("not enabled for this key. Subscribe to One Call by Call (1,000 free calls a day) for "
                "OpenWeather's minute-by-minute, hourly and 10-day data; NWS and Open-Meteo cover it until then")
    if e.code == 404:
        return f"OpenWeather doesn't offer version {ver} at this address"
    return str(e)


def check_connections(cfg, lat, lon, usage=None):
    """Probe each source with fresh (uncached) requests."""
    http = Http(timeout=12)
    out = []
    if not S.enabled(cfg, "owm"):
        out.append(("OpenWeather", None, "turned off in Settings"))
    else:
        base, key = S.url(cfg, "owm"), (cfg.get("owm_api_key") or "").strip()
        if not key:
            out.append(("OpenWeather key", None, "not set (optional; NWS and Open-Meteo cover forecasts)"))
        else:
            try:
                j = http.get_json(owm_url(base, "/data/2.5/weather", key, lat=lat, lon=lon, units="metric"))
                out.append(("OpenWeather key", True, f"works (nearest place: {j.get('name') or 'unnamed'})"))
            except ApiError as e:
                hint = "; new keys can take a couple of hours to start working" if e.code == 401 else ""
                out.append(("OpenWeather key", False, f"{e}{hint}"))
            setting = str(cfg.get("owm_onecall_version") or "auto").lower()
            if setting == "off":
                out.append(("One Call", None, "turned off in Settings"))
            else:
                missing = []
                for ver in (["4.0", "3.0"] if setting == "auto" else [setting]):
                    path, extra = (("/data/4.0/onecall/current", {}) if ver == "4.0" else
                                   ("/data/3.0/onecall", {"exclude": "minutely,hourly,daily,alerts"}))
                    try:
                        http.get_json(owm_url(base, path, key, lat=lat, lon=lon, **extra),
                                      on_fetch=usage.add if usage else None)
                        out.append((f"One Call {ver}", True, "subscribed and working"))
                        missing = []
                        break
                    except ApiError as e:
                        if e.code in (401, 404) and not e.bad_key:
                            missing.append(ver)
                            continue
                        out.append((f"One Call {ver}", False, explain_onecall(ver, e)))
                        missing = []
                        break
                if missing:
                    out.append(("One Call", None, "optional, and not part of this key's plan; NWS and Open-Meteo "
                                                  "supply the hourly and 10-day data instead"))

    def probe(label, sid, fn):
        if not S.enabled(cfg, sid):
            out.append((label, None, "turned off in Settings"))
            return
        try:
            out.append((label, True, fn()))
        except ApiError as e:
            out.append((label, False, str(e)))
        except Exception as e:  # noqa
            out.append((label, False, f"{type(e).__name__}: {e}"))

    def nws():
        j = http.get_json(f"{S.url(cfg, 'nws')}/points/{lat:.4f},{lon:.4f}", nws_headers(cfg))
        p = j.get("properties") or {}
        return f"works: office {p.get('gridId')}, grid {p.get('gridX')},{p.get('gridY')}, radar {p.get('radarStation')}"

    def om():
        j = http.get_json(f"{S.url(cfg, 'openmeteo')}/v1/forecast?latitude={lat:.3f}&longitude={lon:.3f}"
                          "&current=temperature_2m")
        return f"works: {j.get('current', {}).get('temperature_2m')} °C now"

    def omaq():
        j = http.get_json(f"{S.url(cfg, 'openmeteo_aq')}/v1/air-quality?latitude={lat:.3f}&longitude={lon:.3f}"
                          "&current=us_aqi")
        return f"works: US AQI {j.get('current', {}).get('us_aqi')}"

    def iem():
        j = http.get_json(f"{S.url(cfg, 'iem')}/api/1/nws/outlook_by_point.json?lat={lat:.3f}&lon={lon:.3f}")
        return f"works: {len(j.get('data') or [])} outlooks cover this point"

    def radar():
        b = f"{lon - 0.5:.3f},{lat - 0.5:.3f},{lon + 0.5:.3f},{lat + 0.5:.3f}"
        raw = http.get_bytes(f"{S.url(cfg, 'noaa_radar')}/conus/conus_bref_qcd/ows?service=WMS&version=1.1.1"
                             f"&request=GetMap&layers=conus_bref_qcd&styles=&srs=EPSG:4326&bbox={b}"
                             "&width=64&height=64&format=image/png&transparent=true")
        if not raw.startswith(b"\x89PNG"):
            raise ApiError(0, "didn't return an image")
        return "works: radar image received"

    def acis():
        j = http.post_json(f"{S.url(cfg, 'acis')}/StnMeta", {"bbox": f"{lon - .5},{lat - .4},{lon + .5},{lat + .4}",
                                                            "elems": "maxt", "meta": "name"})
        return f"works: {len(j.get('meta') or [])} climate stations nearby"

    def tiger():
        j = http.get_json(f"{S.url(cfg, 'tigerweb')}/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer?f=json")
        return f"works: {len(j.get('layers') or [])} map layers available"

    def zipp():
        z = str(cfg.get("zip") or "75201")
        if not re.fullmatch(r"\d{5}", z):
            return "skipped (location isn't a ZIP code)"
        j = http.get_json(f"{S.url(cfg, 'zippopotam')}/us/{z}")
        pl = (j.get("places") or [{}])[0]
        return f"works: {z} is {pl.get('place name')}, {pl.get('state abbreviation')}"

    def rv():
        j = http.get_json(f"{S.url(cfg, 'rainviewer')}/public/weather-maps.json")
        return f"works: {len(dig(j, 'radar', 'past', default=[]))} radar frames listed"

    site = {}

    def nearest_site():
        if not site:
            from . import nexrad
            try:
                j = http.get_json(f"{S.url(cfg, 'nws')}/radar/stations?stationType=WSR-88D", nws_headers(cfg))
                near = nexrad.nearest_sites(nexrad.parse_sites(j), lat, lon, n=1)
            except ApiError:
                near = []
            site["id"] = near[0]["id"] if near else "KFWS"
        return site["id"]

    def l3():
        from . import nexrad
        sid = nearest_site()
        keys = nexrad.list_keys(http, S.url(cfg, "nexrad_aws"), nexrad.site3(sid), "N0B", time.time() - 3 * 3600)
        if not keys:
            raise ApiError(0, f"no reflectivity scans from {sid} in the last 3 hours (it may be down for maintenance)")
        newest = nexrad.key_time(keys[-1])
        return f"works: {len(keys)} scans from {sid}, newest {int((time.time() - newest) / 60)} min old"

    def tgftp():
        from . import nexrad
        sid = nearest_site()
        raw = http.get_bytes(nexrad.tgftp_url(sid, "DS.58sti", S.url(cfg, "tgftp")))
        return f"works: storm tracking product from {sid} ({len(raw) // 1024} KB)"

    probe("NWS", "nws", nws)
    probe("Open-Meteo", "openmeteo", om)
    probe("Open-Meteo air", "openmeteo_aq", omaq)
    probe("Iowa Mesonet", "iem", iem)
    probe("NEXRAD Level 3 (AWS)", "nexrad_aws", l3)
    probe("NWS radar products", "tgftp", tgftp)
    probe("NOAA radar mosaic", "noaa_radar", radar)
    probe("ACIS almanac", "acis", acis)
    probe("TIGERweb maps", "tigerweb", tiger)
    probe("Zippopotam.us", "zippopotam", zipp)
    probe("RainViewer", "rainviewer", rv)
    return out
