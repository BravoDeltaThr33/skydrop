"""Where skydrop keeps its files.

Linux and macOS: the XDG locations (~/.config/skydrop, ~/.cache/skydrop and
~/.local/share/skydrop unless XDG_*_HOME says otherwise). Windows: %APPDATA%\\skydrop
for the settings and %LOCALAPPDATA%\\skydrop for the cache and downloads."""

import os

from . import APP

if os.name == "nt":
    _HOME = os.path.expanduser("~")
    _ROAMING = os.environ.get("APPDATA") or os.path.join(_HOME, "AppData", "Roaming")
    _LOCAL = os.environ.get("LOCALAPPDATA") or os.path.join(_HOME, "AppData", "Local")
    _CFG_HOME = os.environ.get("XDG_CONFIG_HOME") or _ROAMING
    _CACHE_HOME = os.environ.get("XDG_CACHE_HOME") or os.path.join(_LOCAL, APP)
    _DATA_HOME = os.environ.get("XDG_DATA_HOME") or _LOCAL
else:
    _CFG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    _CACHE_HOME = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    _DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")

CONFIG_DIR = os.path.join(_CFG_HOME, APP)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CACHE_DIR = (os.path.join(_CACHE_HOME, "cache") if os.name == "nt" and not os.environ.get("XDG_CACHE_HOME")
             else os.path.join(_CACHE_HOME, APP))
USAGE_PATH = os.path.join(CACHE_DIR, "owm_usage.json")
DISK_CACHE_DIR = os.path.join(CACHE_DIR, "http")
TTS_DIR = os.path.join(CACHE_DIR, "speech")
DATA_DIR = os.path.join(_DATA_HOME, APP)
VOICES_DIR = os.path.join(DATA_DIR, "voices")
LOG_PATH = os.path.join(CACHE_DIR, "skydrop.log")


def pretty_path(p):
    home = os.path.expanduser("~")
    if os.name == "nt":
        for var in ("LOCALAPPDATA", "APPDATA"):
            base = os.environ.get(var)
            if base and p.lower().startswith(base.lower() + os.sep):
                return f"%{var}%" + p[len(base):]
    return "~" + p[len(home):] if p.startswith(home) else p


def ensure(path):
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path
