"""Alert sounds, generated on the fly (skydrop ships no audio files).

noaa   The NOAA Weather Radio warning alarm: three SAME header "data bursts"
       (AFSK at 520.83 baud, 2083.3 Hz mark / 1562.5 Hz space) followed by the
       1050 Hz Warning Alarm Tone, as weather radios sound for a tornado warning.
nwr    The 1050 Hz Warning Alarm Tone alone.
eas    The Emergency Alert System two-tone attention signal (853 + 960 Hz).
chime  A soft two-note chime for less urgent alerts.

The SAME header carries the real event code and county codes of the warning
when they're known.  These sounds are for your own computer: U.S. law (47 CFR
11.45) forbids broadcasting EAS tones or simulations of them.
"""

import math
import os
import struct
import time
import wave
from array import array

from .paths import CACHE_DIR

RATE = 22050
BAUD = 520.0 + 5.0 / 6.0
MARK, SPACE = 2083.0 + 1.0 / 3.0, 1562.5
SOUND_DIR = os.path.join(CACHE_DIR, "sounds")


def _tone(freqs, secs, amp=0.45, fade=0.01):
    n = int(RATE * secs)
    out = array("h")
    f_n = max(1, int(RATE * fade))
    k = len(freqs)
    step = [2 * math.pi * f / RATE for f in freqs]
    for i in range(n):
        env = min(1.0, i / f_n, (n - 1 - i) / f_n)
        v = sum(math.sin(s * i) for s in step) / k
        out.append(int(32767 * amp * env * v))
    return out


def _silence(secs):
    return array("h", bytes(2 * int(RATE * secs)))


def _afsk(text, amp=0.5):
    """One SAME transmission: the 16-byte 0xAB preamble, then the text, LSB first."""
    data = bytes([0xAB] * 16) + text.encode("ascii", "replace")
    out = array("h")
    phase = 0.0
    spb = RATE / BAUD
    t = 0.0
    for byte in data:
        for bit in range(8):
            f = MARK if (byte >> bit) & 1 else SPACE
            t_end = t + spb
            while len(out) < int(t_end):
                phase += 2 * math.pi * f / RATE
                out.append(int(32767 * amp * math.sin(phase)))
            t = t_end
    ramp = min(40, len(out) // 4)
    for i in range(ramp):
        out[i] = int(out[i] * i / ramp)
        out[-1 - i] = int(out[-1 - i] * i / ramp)
    return out


def same_header(event="TOR", fips=None, minutes=45, issued=None, station="KWNS/NWS"):
    """ZCZC-WXR-TOR-048113+0045-3040010-KFWD/NWS-"""
    locs = [str(f)[-6:].rjust(6, "0") for f in (fips or ["000000"]) if str(f).strip()][:31] or ["000000"]
    if minutes <= 60:
        mins = max(15, int(math.ceil(minutes / 15.0)) * 15)
    else:
        mins = int(math.ceil(minutes / 30.0)) * 30
    purge = f"{min(99, mins // 60):02d}{mins % 60:02d}"
    t = time.gmtime(issued or time.time())
    stamp = f"{t.tm_yday:03d}{t.tm_hour:02d}{t.tm_min:02d}"
    station = (station or "KWNS/NWS")[:8].ljust(8)
    return f"ZCZC-WXR-{event[:3].upper()}-{'-'.join(locs)}+{purge}-{stamp}-{station}-"


def alarm_samples(kind="noaa", event="TOR", fips=None, minutes=45, station=None, issued=None, tone_secs=8.0):
    out = array("h")
    if kind == "noaa":
        header = same_header(event, fips, minutes, issued, station)
        for k in range(3):
            out += _afsk(header)
            out += _silence(1.0)
        out += _tone([1050.0], tone_secs, 0.42)
        out += _silence(0.6)
    elif kind == "eas":
        out += _tone([853.0, 960.0], tone_secs, 0.5)
        out += _silence(0.6)
    elif kind == "nwr":
        out += _tone([1050.0], tone_secs, 0.42)
        out += _silence(0.6)
    else:  # chime
        for freq, dur in ((988.0, 0.42), (784.0, 0.62)):
            n = int(RATE * dur)
            for i in range(n):
                env = math.exp(-4.0 * i / n) * min(1.0, i / 200.0)
                v = math.sin(2 * math.pi * freq * i / RATE) + 0.3 * math.sin(4 * math.pi * freq * i / RATE)
                out.append(int(0.32 * env * 32767 * v / 1.3))
    return out


def eom_samples():
    """End of message: NNNN three times."""
    out = array("h")
    for _ in range(3):
        out += _afsk("NNNN")
        out += _silence(1.0)
    return out


def write_wav(samples, name):
    os.makedirs(SOUND_DIR, exist_ok=True)
    path = os.path.join(SOUND_DIR, name)
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(samples.tobytes() if struct.pack("=h", 1) == struct.pack("<h", 1) else
                       _swap(samples).tobytes())
    os.replace(tmp, path)
    return path


def _swap(samples):
    a = array("h", samples)
    a.byteswap()
    return a


def alarm_file(kind="noaa", event="TOR", fips=None, minutes=45, station=None, issued=None):
    """A WAV file for the alarm; returns (path, seconds)."""
    samples = alarm_samples(kind, event, fips, minutes, station, issued)
    key = abs(hash((kind, event, tuple(fips or ()), minutes, station, int((issued or 0) // 60)))) % 10 ** 8
    path = write_wav(samples, f"alarm-{kind}-{key}.wav")
    return path, len(samples) / float(RATE)


def eom_file():
    path = os.path.join(SOUND_DIR, "eom.wav")
    if not os.path.exists(path):
        path = write_wav(eom_samples(), "eom.wav")
    return path


def clean(max_age=3600):
    now = time.time()
    try:
        for name in os.listdir(SOUND_DIR):
            p = os.path.join(SOUND_DIR, name)
            if name.startswith("alarm-") and now - os.path.getmtime(p) > max_age:
                os.remove(p)
    except OSError:
        pass
