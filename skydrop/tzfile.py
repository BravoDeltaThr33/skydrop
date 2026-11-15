"""Time zones on Python 3.8, which has no zoneinfo module, and where zoneinfo finds no
zone files.

Reads the system's compiled zone files (/usr/share/zoneinfo) so local times stay right
across daylight saving changes; Windows has none, so there the files come from the tzdata
package that the Windows installer adds.  On Python 3.9+ with zone files at hand skydrop
uses zoneinfo instead and this module isn't needed."""

import bisect
import datetime as dt
import os
import re
import struct

ZONE_DIRS = ("/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo", "/etc/zoneinfo")
_EPOCH = dt.datetime(1970, 1, 1)
_cache = {}


def load(name):
    """Zone name like 'America/Chicago' -> tzinfo, or None if it can't be read."""
    if not name or not re.fullmatch(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*", name):
        return None
    if name in _cache:
        return _cache[name]
    tz = None
    for base in ZONE_DIRS + _tzdata_dirs():
        path = os.path.join(base, name)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    tz = _parse(name, fh.read())
            except (OSError, ValueError, struct.error, IndexError):
                tz = None
            if tz is not None:
                break
    _cache[name] = tz
    return tz


def _tzdata_dirs():
    """The zone files of the tzdata package, when it is installed."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("tzdata")
    except (ImportError, ValueError):
        return ()
    if spec is None or not spec.origin:
        return ()
    return (os.path.join(os.path.dirname(spec.origin), "zoneinfo"),)


def _parse(name, data):
    if data[:4] != b"TZif":
        raise ValueError("not a zone file")
    version = data[4:5]

    def block(pos, tsize):
        cnt = struct.unpack(">6l", data[pos + 20:pos + 44])
        isutc, isstd, leap, timecnt, typecnt, charcnt = cnt
        p = pos + 44
        fmt = "q" if tsize == 8 else "l"
        trans = list(struct.unpack(f">{timecnt}{fmt}", data[p:p + timecnt * tsize]))
        p += timecnt * tsize
        idx = list(data[p:p + timecnt])
        p += timecnt
        infos = []
        for _ in range(typecnt):
            off, isdst, ai = struct.unpack(">lBB", data[p:p + 6])
            infos.append((off, isdst, ai))
            p += 6
        chars = data[p:p + charcnt]
        p += charcnt + leap * (tsize + 4) + isstd + isutc
        abbr = [chars[ai:chars.index(b"\0", ai)].decode("ascii", "replace") if b"\0" in chars[ai:] else ""
                for _off, _dst, ai in infos]
        types = [(off, bool(dst), ab) for (off, dst, _ai), ab in zip(infos, abbr)]
        return trans, idx, types, p

    trans, idx, types, end = block(0, 4)
    footer = ""
    if version >= b"2":
        trans, idx, types, end = block(end, 8)
        rest = data[end:]
        if rest.startswith(b"\n"):
            footer = rest[1:rest.index(b"\n", 1)].decode("ascii", "replace") if b"\n" in rest[1:] else ""
    if not types:
        raise ValueError("zone file has no offsets")
    return ZoneFile(name, trans, idx, types, _PosixRule.parse(footer))


class _PosixRule:
    """A TZ string such as 'CST6CDT,M3.2.0,M11.1.0' for times after the file's last change."""

    _NAME = r"(<[^>]+>|[A-Za-z]{3,})"
    _OFF = r"([+-]?\d{1,3}(?::\d{1,2}(?::\d{1,2})?)?)"

    def __init__(self, std, std_off, dst=None, dst_off=None, start=None, end=None):
        self.std, self.std_off = std, std_off
        self.dst, self.dst_off = dst, dst_off
        self.start, self.end = start, end

    @staticmethod
    def _secs(text):
        sign = -1 if text.startswith("-") else 1
        parts = [int(x) for x in text.lstrip("+-").split(":")]
        parts += [0] * (3 - len(parts))
        return sign * (parts[0] * 3600 + parts[1] * 60 + parts[2])

    @classmethod
    def parse(cls, text):
        if not text:
            return None
        m = re.match(cls._NAME + cls._OFF + "(?:" + cls._NAME + cls._OFF + "?)?(?:,(.+?),(.+))?$", text)
        if not m:
            return None
        std, soff, dst, doff, start, end = m.groups()
        std_off = -cls._secs(soff)           # POSIX counts hours west of UTC
        if not dst:
            return cls(std.strip("<>"), std_off)
        dst_off = -cls._secs(doff) if doff else std_off + 3600
        if not (start and end):
            start, end = "M3.2.0", "M11.1.0"
        return cls(std.strip("<>"), std_off, dst.strip("<>"), dst_off, cls._date(start), cls._date(end))

    @classmethod
    def _date(cls, text):
        when, _, tm = text.partition("/")
        secs = cls._secs(tm) if tm else 7200
        if when.startswith("M"):
            mo, wk, wd = (int(x) for x in when[1:].split("."))
            return ("M", mo, wk, wd, secs)
        if when.startswith("J"):
            return ("J", int(when[1:]), 0, 0, secs)
        return ("N", int(when), 0, 0, secs)

    @staticmethod
    def _day(year, rule):
        kind, a, b, c, secs = rule
        if kind == "M":
            first = dt.date(year, a, 1)
            shift = (c - (first.weekday() + 1) % 7) % 7
            day = first + dt.timedelta(days=shift + 7 * (b - 1))
            while day.month != a:
                day -= dt.timedelta(days=7)
        elif kind == "J":       # 1..365, Feb 29 never counted
            day = dt.date(year, 1, 1) + dt.timedelta(days=a - 1)
            if a >= 60 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
                day += dt.timedelta(days=1)
        else:                   # 0..365, Feb 29 counted
            day = dt.date(year, 1, 1) + dt.timedelta(days=a)
        return (dt.datetime(day.year, day.month, day.day) - _EPOCH).total_seconds() + secs

    def info(self, ts):
        """UTC seconds -> (offset, is_dst, abbreviation)."""
        if not self.dst:
            return self.std_off, False, self.std
        year = (_EPOCH + dt.timedelta(seconds=ts + self.std_off)).year
        on = self._day(year, self.start) - self.std_off
        off = self._day(year, self.end) - self.dst_off
        if on < off:
            is_dst = on <= ts < off
        else:                   # southern hemisphere: DST spans the new year
            is_dst = not (off <= ts < on)
        return (self.dst_off, True, self.dst) if is_dst else (self.std_off, False, self.std)


class ZoneFile(dt.tzinfo):
    def __init__(self, name, trans, idx, types, rule):
        self.key = name
        self._trans = trans
        self._idx = idx
        self._types = types
        self._rule = rule
        std = [t for t in types if not t[1]]
        self._before = std[0] if std else types[0]

    def __repr__(self):
        return f"ZoneFile({self.key!r})"

    def __str__(self):
        return self.key

    def __reduce__(self):
        return load, (self.key,)

    def _info(self, ts):
        i = bisect.bisect_right(self._trans, ts)
        if i == 0:
            return self._before
        if i == len(self._trans) and self._rule is not None:
            return self._rule.info(ts)
        return self._types[self._idx[i - 1]]

    def _local(self, d):
        """Wall-clock datetime -> (offset, is_dst, abbreviation), honoring fold."""
        secs = (d.replace(tzinfo=None) - _EPOCH).total_seconds()
        seen = []
        for probe in (-86400, 0, 86400):
            off = self._info(secs + probe)[0]
            if off not in seen:
                seen.append(off)
        fits = [o for o in seen if self._info(secs - o)[0] == o]
        if not fits:                      # skipped hour (spring forward)
            return self._info(secs - 86400) if not d.fold else self._info(secs + 86400)
        fits.sort(reverse=True)           # repeated hour: fold 0 is the first (larger offset)
        o = fits[-1] if d.fold and len(fits) > 1 else fits[0]
        return self._info(secs - o)

    def utcoffset(self, d):
        if d is None:
            return None
        return dt.timedelta(seconds=self._local(d)[0])

    def dst(self, d):
        if d is None:
            return None
        off, is_dst, _ab = self._local(d)
        if not is_dst:
            return dt.timedelta(0)
        std = self._rule.std_off if self._rule else self._before[0]
        return dt.timedelta(seconds=max(0, off - std)) or dt.timedelta(hours=1)

    def tzname(self, d):
        if d is None:
            return None
        return self._local(d)[2]

    def fromutc(self, d):
        if not isinstance(d, dt.datetime) or d.tzinfo is not self:
            raise ValueError("fromutc: datetime.tzinfo is not self")
        secs = (d.replace(tzinfo=None) - _EPOCH).total_seconds()
        off = self._info(secs)[0]
        local = d + dt.timedelta(seconds=off)
        # The second pass through a repeated hour (fall back) gets fold=1.
        for probe in (-86400, 86400):
            other = self._info(secs + probe)[0]
            if other != off:
                twin = secs + off - other
                if twin < secs and self._info(twin)[0] == other:
                    return local.replace(fold=1)
        return local
