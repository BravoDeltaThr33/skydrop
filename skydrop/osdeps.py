"""What differs between Linux, macOS and Windows.

Everything skydrop asks of the operating system outside the terminal goes through
here: whether a process is still running and how to stop it, a process's parent and
name, starting a helper program in the background, desktop notifications, the login
item for the background tornado watch, and the folders that hold fonts.
"""

import os
import shutil
import signal
import subprocess
import sys

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"
LINUX = not WINDOWS and not MACOS          # Linux and the other Unix-likes

PROC = "/proc"

# Windows process creation flags (subprocess has some of them only on Windows)
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
STILL_ACTIVE = 259


def system_name():
    if WINDOWS:
        return "Windows"
    if MACOS:
        return "macOS"
    return "Linux" if sys.platform.startswith("linux") else sys.platform


# ---------------------------------------------------------------------------- helpers
def background(detach=False):
    """Popen keyword arguments for a helper that runs on its own. On Unix it gets its own
    session, so a Ctrl-C in the terminal doesn't reach it; on Windows it gets no console
    window of its own (detach: and none of the terminal's either, so it outlives it)."""
    if WINDOWS:
        flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP) if detach else CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def windowless_python(exe=None):
    """The Python that runs without a console window (pythonw.exe on Windows), for the
    background watch; the same Python elsewhere."""
    exe = exe or sys.executable
    if WINDOWS and exe:
        folder, name = os.path.split(exe)
        if name.lower() == "python.exe":
            quiet = os.path.join(folder, "pythonw.exe")
            if os.path.isfile(quiet):
                return quiet
    return exe


def _kernel32():
    import ctypes
    return ctypes.WinDLL("kernel32", use_last_error=True)


# ---------------------------------------------------------------------------- processes
def alive(pid):
    """Is process `pid` running? (Never sends it anything: on Windows os.kill would end it.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        k32 = _kernel32()
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5             # ERROR_ACCESS_DENIED: it exists
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def terminate(pid):
    """Ask process `pid` to stop (SIGTERM; on Windows, end it). True when it was asked."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if WINDOWS:
        from ctypes import wintypes
        k32 = _kernel32()
        k32.OpenProcess.restype = wintypes.HANDLE
        handle = k32.OpenProcess(0x0001, False, pid)        # PROCESS_TERMINATE
        if not handle:
            return False
        try:
            return bool(k32.TerminateProcess(handle, 1))
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _read(path, limit=65536):
    try:
        with open(path, "rb") as handle:
            return handle.read(limit)
    except OSError:
        return None


def _ps(pid, field):
    """One field of `ps` for a process (macOS and other Unixes without /proc)."""
    exe = shutil.which("ps")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-o", f"{field}=", "-p", str(int(pid))], capture_output=True,
                             text=True, timeout=3)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    text = (out.stdout or "").strip()
    return text or None


def command_line(pid):
    """The command a process runs, as one string (None when it can't be told)."""
    if WINDOWS:
        return None
    raw = _read(f"{PROC}/{pid}/cmdline")
    if raw is not None:
        return " ".join(part.decode("utf-8", "replace") for part in raw.split(b"\0") if part)
    return _ps(pid, "command")


def parent_pid(pid):
    """A process's parent (None when it can't be told)."""
    if WINDOWS:
        return None
    raw = _read(f"{PROC}/{pid}/stat")
    if raw:
        text = raw.decode("utf-8", "replace")
        fields = text[text.rfind(")") + 2:].split()      # after the name, which may hold spaces
        try:
            return int(fields[1])
        except (IndexError, ValueError):
            return None
    if os.path.isdir(PROC):
        return None
    found = _ps(pid, "ppid")
    try:
        return int(found) if found else None
    except ValueError:
        return None


def process_name(pid):
    """A process's program name (no folder), or ''."""
    if WINDOWS:
        return ""
    raw = _read(f"{PROC}/{pid}/comm")
    if raw is not None:
        return raw.decode("utf-8", "replace").strip()
    if os.path.isdir(PROC):
        return ""
    found = _ps(pid, "comm") or ""
    return os.path.basename(found.strip())


# ---------------------------------------------------------------------------- notifications
_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$t = [Security.SecurityElement]::Escape($env:SKYDROP_TITLE)
$b = [Security.SecurityElement]::Escape($env:SKYDROP_BODY)
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast duration='long'><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$n = [Windows.UI.Notifications.ToastNotification]::new($x)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($n)
"""


def powershell_command(script):
    """argv that runs a PowerShell script (no profile, no window, no quoting worries)."""
    import base64
    exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]


def notification_command(app, title, body, critical=True):
    """(argv, extra environment) that shows a desktop notification here, or (None, None)."""
    body = (body or "")[:600]
    if WINDOWS:
        return powershell_command(_TOAST), {"SKYDROP_TITLE": title, "SKYDROP_BODY": body}
    if MACOS:
        exe = shutil.which("osascript")
        if not exe:
            return None, None
        return [exe, "-e", "on run argv", "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)",
                "-e", "end run", title, body], {}
    exe = shutil.which("notify-send")
    if not exe:
        return None, None
    return [exe, "-a", app, "-i", "dialog-warning", "-u", "critical" if critical else "normal", title, body], {}


def notify(app, title, body, critical=True):
    """Show a desktop notification. True when one was sent."""
    args, extra = notification_command(app, title, body, critical)
    if not args:
        return False
    env = dict(os.environ, **extra) if extra else None
    try:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=env, **background())
        return True
    except OSError:
        return False


def notifier_hint():
    """What to install for desktop notifications, or '' when nothing is needed."""
    if WINDOWS or MACOS:
        return ""
    return "" if shutil.which("notify-send") else "install libnotify-bin (notify-send) for desktop pop-ups"


# ---------------------------------------------------------------------------- login item
AUTOSTART_LABEL = "skydrop.watch"          # the macOS launch agent's label
RUN_VALUE = "skydrop tornado watch"        # the Windows Run key's value name
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_place(app="skydrop"):
    """Where the login item lives, for messages."""
    home = os.path.expanduser("~")
    if WINDOWS:
        return "HKEY_CURRENT_USER\\" + RUN_KEY + " (" + RUN_VALUE + ")"
    if MACOS:
        return os.path.join(home, "Library", "LaunchAgents", AUTOSTART_LABEL + ".plist")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, "autostart", f"{app}-watch.desktop")


def _desktop_quote(arg):
    if all(c.isalnum() or c in "/._-+=:," for c in arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'


def set_login_item(enabled, argv, app="skydrop", version=""):
    """Start `argv` at every login (enabled) or stop doing so. Returns an error message or ''."""
    place = autostart_place(app)
    if WINDOWS:
        return _windows_run_key(enabled, argv)
    if not enabled:
        try:
            os.remove(place)
        except FileNotFoundError:
            pass
        except OSError as e:
            return f"couldn't remove {place}: {e.strerror or e}"
        return ""
    if MACOS:
        import plistlib
        data = plistlib.dumps({"Label": AUTOSTART_LABEL, "ProgramArguments": list(argv), "RunAtLoad": True,
                               "KeepAlive": False, "ProcessType": "Background",
                               "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"})
    else:
        cmd = " ".join(_desktop_quote(c) for c in argv)
        data = (f"[Desktop Entry]\nType=Application\nName={app} tornado watch\n"
                f"Comment=Sounds the tornado alarm for your ZIP code while {app} is closed\n"
                f"Exec={cmd}\nIcon={app}\nTerminal=false\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n"
                f"X-{app}-version={version}\n").encode("utf-8")
    try:
        os.makedirs(os.path.dirname(place), exist_ok=True)
        tmp = place + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, place)
    except OSError as e:
        return f"couldn't write {place}: {e.strerror or e}"
    return ""


def _windows_run_key(enabled, argv):
    try:
        import winreg
    except ImportError:
        return "no Windows registry here"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, subprocess.list2cmdline(list(argv)))
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as e:
        return f"couldn't change the login item: {e.strerror or e}"
    return ""


# ---------------------------------------------------------------------------- fonts
def font_dirs():
    """The folders that hold the system's and the user's fonts."""
    home = os.path.expanduser("~")
    if WINDOWS:
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return [os.path.join(windir, "Fonts"), os.path.join(local, "Microsoft", "Windows", "Fonts")]
    if MACOS:
        return ["/System/Library/Fonts", "/System/Library/Fonts/Supplemental", "/Library/Fonts",
                os.path.join(home, "Library", "Fonts")]
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return ["/usr/share/fonts", "/usr/local/share/fonts", os.path.join(data, "fonts"), os.path.join(home, ".fonts")]


def font_families(limit=4000):
    """{family in lower case: family} for the fonts in font_dirs(), read from the font files
    themselves (for systems without fontconfig's fc-list). Empty without Pillow."""
    try:
        from PIL import ImageFont
    except ImportError:
        return {}
    names, seen = {}, 0
    for top in font_dirs():
        for folder, _dirs, files in os.walk(top):
            for name in files:
                if not name.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                seen += 1
                if seen > limit:
                    return names
                try:
                    family = ImageFont.truetype(os.path.join(folder, name), 12).getname()[0]
                except (OSError, ValueError, TypeError):
                    continue
                if family:
                    names.setdefault(family.lower(), family)
    return names
