"""Formats internal metric values for the chosen unit system and time zone."""

import datetime as dt
import time

from .wxmath import compass


class Fmt:
    def __init__(self, units="imperial", tz=None):
        self.imp = units != "metric"
        self.tz = tz

    @property
    def u_temp(self):
        return "°F" if self.imp else "°C"

    @property
    def u_wind(self):
        return "mph" if self.imp else "km/h"

    @property
    def u_press(self):
        return "inHg" if self.imp else "hPa"

    @property
    def u_vis(self):
        return "mi" if self.imp else "km"

    @property
    def u_precip(self):
        return "in" if self.imp else "mm"

    @property
    def u_dist(self):
        return "mi" if self.imp else "km"

    def tv(self, c):
        if c is None:
            return None
        return c * 9 / 5 + 32 if self.imp else c

    def t(self, c, unit=False):
        val = self.tv(c)
        if val is None:
            return "--"
        return f"{int(round(val))}{self.u_temp if unit else '°'}"  # never "-0°"

    def t1(self, c):
        val = self.tv(c)
        return "--" if val is None else f"{val:.1f}°"

    def tdelta(self, dc):
        if dc is None:
            return "--"
        val = dc * 9 / 5 if self.imp else dc
        return f"{int(round(val)):+d}°"

    def windv(self, ms):
        if ms is None:
            return None
        return ms * 2.23694 if self.imp else ms * 3.6

    def wind(self, ms, unit=True):
        if ms is None:
            return "--"
        val = self.windv(ms)
        return f"{val:.0f} {self.u_wind}" if unit else f"{val:.0f}"

    def press(self, hpa, unit=True):
        if hpa is None:
            return "--"
        txt = f"{hpa * 0.0295300:.2f}" if self.imp else f"{hpa:.0f}"
        return f"{txt} {self.u_press}" if unit else txt

    def press_delta(self, hpa):
        if hpa is None:
            return "--"
        return f"{hpa * 0.0295300:+.2f} inHg" if self.imp else f"{hpa:+.1f} hPa"

    def vis(self, m, unit=True):
        if m is None:
            return "--"
        val = m / 1609.344 if self.imp else m / 1000.0
        txt = f"{val:.1f}" if val < 10 else f"{val:.0f}"
        return f"{txt} {self.u_vis}" if unit else txt

    def precip(self, mm, unit=True):
        if mm is None:
            return "--"
        txt = f"{mm / 25.4:.2f}" if self.imp else f"{mm:.1f}"
        return f"{txt} {self.u_precip}" if unit else txt

    def height(self, m):
        if m is None:
            return "--"
        return f"{m * 3.28084:,.0f} ft" if self.imp else f"{m:,.0f} m"

    def dist(self, miles, unit=True, digits=0):
        if miles is None:
            return "--"
        val = miles if self.imp else miles * 1.609344
        txt = f"{val:.{digits}f}"
        return f"{txt} {self.u_dist}" if unit else txt

    def speed_mph(self, mph):
        if mph is None:
            return "--"
        return f"{mph:.0f} mph" if self.imp else f"{mph * 1.609344:.0f} km/h"

    @staticmethod
    def pct(val):
        return "--" if val is None else f"{val:.0f}%"

    @staticmethod
    def duration(secs):
        if secs is None:
            return "--"
        secs = int(abs(secs))
        return f"{secs // 3600}h {secs % 3600 // 60:02d}m"

    @staticmethod
    def age(epoch, now=None):
        if epoch is None:
            return "--"
        secs = int((now or time.time()) - epoch)
        if secs < 0:
            secs = 0
        if secs < 90:
            return f"{secs}s"
        if secs < 5400:
            return f"{secs // 60}m"
        if secs < 172800:
            return f"{secs // 3600}h"
        return f"{secs // 86400}d"

    def local(self, epoch):
        return dt.datetime.fromtimestamp(epoch, self.tz)

    def now(self):
        return dt.datetime.now(self.tz)

    def today(self):
        return dt.datetime.now(self.tz).date()

    def time(self, epoch):
        if epoch is None:
            return "--"
        d = self.local(epoch)
        return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"

    def clock(self, seconds=True):
        d = self.now()
        if seconds:
            return f"{d.hour % 12 or 12}:{d.minute:02d}:{d.second:02d} {'AM' if d.hour < 12 else 'PM'}"
        return f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"

    def hour(self, epoch):
        if epoch is None:
            return "--"
        d = self.local(epoch)
        return f"{d.hour % 12 or 12} {'AM' if d.hour < 12 else 'PM'}"

    def hour_short(self, epoch):
        if epoch is None:
            return "--"
        d = self.local(epoch)
        return f"{d.hour % 12 or 12}{'a' if d.hour < 12 else 'p'}"

    def day_label(self, day):
        today = self.today()
        if day == today:
            return "Today"
        return f"{day:%a} {day.month}/{day.day}"

    def tzname(self):
        try:
            return self.now().strftime("%Z")
        except Exception:
            return ""

    def dtime(self, epoch):
        if epoch is None:
            return "--"
        d = self.local(epoch).date()
        today = self.today()
        if d == today:
            return self.time(epoch)
        if d == today + dt.timedelta(days=1):
            return f"tomorrow {self.time(epoch)}"
        if d == today - dt.timedelta(days=1):
            return f"yesterday {self.time(epoch)}"
        return f"{d:%a} {d.month}/{d.day} {self.time(epoch)}"


def wind_text(F, deg, ms, unit=True):
    if ms is None:
        return "--"
    if ms < 0.5:
        return "Calm"
    return f"{compass(deg)} {F.wind(ms, unit)}".strip()
