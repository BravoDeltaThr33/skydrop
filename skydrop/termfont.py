"""CRT mode's font in the terminal itself (Settings, Display: CRT font and CRT font size).

In kitty skydrop sets it directly: it writes the font and its size into kitty.conf, in a
block of its own at the end of the file (the file is backed up first, once), and tells
kitty to reload its configuration (SIGUSR1, which kitty documents for this). kitty then
draws every one of its windows in the new font at once. "The terminal's own" font takes
the block out again, and kitty goes back to whatever kitty.conf sets otherwise.

skydrop only does this when it is sure the kitty is its own: kitty's process is one of
skydrop's ancestors on this computer (not over ssh, not inside tmux) and it catches
SIGUSR1. Anywhere else the lines that set the font are copied for the user to paste."""

import os
import re
import signal
import time

from . import osdeps

BEGIN = "# BEGIN skydrop font"
END = "# END skydrop font"
NOTE = ("# Set from skydrop (Settings > Display > CRT font and CRT font size); skydrop rewrites\n"
        "# this block. Pick \"the terminal's own\" font there, or delete the block, to go back.")
BACKUP = ".skydrop-backup"
DEFAULT_SIZE = 11.0         # kitty's font_size when kitty.conf sets none
RELOAD_GAP = 0.25           # seconds between reloads, so a held key does not queue a pile of them
PROC = "/proc"


# ---------------------------------------------------------------------------- the process
def _read(path, limit=65536):
    try:
        with open(path, "rb") as handle:
            return handle.read(limit)
    except OSError:
        return None


def _proc_here():
    """Linux keeps its processes in /proc; macOS has to be asked with ps."""
    return os.path.isdir(PROC)


def _parent(pid):
    """A process's parent (None when it cannot be read)."""
    if not _proc_here():
        return osdeps.parent_pid(pid)
    raw = _read(f"{PROC}/{pid}/stat")
    if not raw:
        return None
    text = raw.decode("utf-8", "replace")
    fields = text[text.rfind(")") + 2:].split()      # after the name, which may hold spaces
    try:
        return int(fields[1])
    except (IndexError, ValueError):
        return None


def _mask(raw, name):
    found = re.search(rb"^" + name + rb":\s*([0-9a-fA-F]+)", raw or b"", re.M)
    return int(found.group(1), 16) if found else 0


def _takes(pid, signum):
    """Whether a signal is safe to send a process - it has a handler for it, or every one
    of its threads holds it blocked to read it from a signalfd, as kitty does - rather
    than the signal's default, which ends the process."""
    bit = 1 << (signum - 1)
    status = _read(f"{PROC}/{pid}/status")
    if not status:
        return False
    if _mask(status, b"SigCgt") & bit:
        return True
    try:
        tasks = os.listdir(f"{PROC}/{pid}/task")
    except OSError:
        tasks = []
    masks = [_mask(_read(f"{PROC}/{pid}/task/{tid}/status"), b"SigBlk") for tid in tasks] or \
        [_mask(status, b"SigBlk")]
    return all(mask & bit for mask in masks)


def _is_ancestor(pid, start=None):
    p, steps = os.getpid() if start is None else start, 0
    while p and p > 1 and steps < 64:
        if p == pid:
            return True
        p, steps = _parent(p), steps + 1
    return False


_KNOWN = {"at": -60.0, "answer": (None, None)}


def kitty_process():
    """(kitty's process ID, None) when skydrop runs in a kitty it can tell to reload; else
    (None, why not). Asked again at most every couple of seconds."""
    now = time.monotonic()
    if now - _KNOWN["at"] < 2.0:
        return _KNOWN["answer"]
    _KNOWN["answer"] = answer = _kitty_process()
    _KNOWN["at"] = now
    return answer


def _kitty_process():
    raw = os.environ.get("KITTY_PID", "")
    if not raw.isdigit():
        return None, "skydrop isn't running in kitty"
    pid = int(raw)
    usr1 = getattr(signal, "SIGUSR1", None)
    if usr1 is None:
        return None, "kitty can't be told to reload here"
    if _proc_here():
        exists = os.path.isdir(f"{PROC}/{pid}")
        name = (_read(f"{PROC}/{pid}/comm") or b"").decode("utf-8", "replace").strip()
    else:                                   # macOS
        exists = osdeps.alive(pid)
        name = osdeps.process_name(pid) if exists else ""
    if not exists or not _is_ancestor(pid):
        return None, "kitty isn't skydrop's own terminal here (ssh or tmux)"
    if "kitty" not in name.lower():
        return None, "kitty isn't skydrop's own terminal here"
    # kitty reloads its settings on SIGUSR1 everywhere; only Linux lets skydrop check first
    if _proc_here() and not _takes(pid, usr1):
        return None, "this kitty doesn't reload its settings on request (update kitty)"
    return pid, None


def kitty_conf_path(pid=None):
    """The kitty.conf the kitty in `pid` reads last (its --config, else the usual place)."""
    if pid:
        raw = _read(f"{PROC}/{pid}/cmdline") or b""
        args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        given = []
        for i, arg in enumerate(args):
            if arg in ("-c", "--config") and i + 1 < len(args):
                given.append(args[i + 1])
            elif arg.startswith("--config="):
                given.append(arg.split("=", 1)[1])
        given = [path for path in given if path.upper() != "NONE" and not path.startswith("-")]
        if given:
            path = os.path.expanduser(given[-1])
            if not os.path.isabs(path):
                cwd = _proc_cwd(pid)
                path = os.path.join(cwd, path) if cwd else os.path.abspath(path)
            return path
    folder = os.environ.get("KITTY_CONFIG_DIRECTORY") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "kitty")
    path = os.path.join(folder, "kitty.conf")
    if osdeps.MACOS and not os.environ.get("KITTY_CONFIG_DIRECTORY") and not os.path.exists(path):
        mac = os.path.join(os.path.expanduser("~"), "Library", "Preferences", "kitty", "kitty.conf")
        if os.path.exists(mac):             # kitty on macOS also reads this one
            return mac
    return path


def _proc_cwd(pid):
    try:
        return os.readlink(f"{PROC}/{pid}/cwd")
    except OSError:
        return None


# ---------------------------------------------------------------------------- kitty.conf
def split_block(text):
    """(kitty.conf without skydrop's block, the block's lines or None)."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip().startswith(BEGIN)), None)
    if start is None:
        return text, None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith(END)), None)
    if end is None:                                   # a block someone cut short: take what's ours
        end = start
        while end + 1 < len(lines) and (lines[end + 1].lstrip().startswith("#") or
                                        lines[end + 1].split(None, 1)[:1] in (["font_family"], ["font_size"])):
            end += 1
    block = lines[start:end + 1]
    rest = lines[:start] + lines[end + 1:]
    # the blank line skydrop put before its block goes with it
    if start > 0 and rest[start - 1:start] == ["\n"]:
        del rest[start - 1]
    return "".join(rest), block


def block_text(family, size):
    """skydrop's block for kitty.conf (empty: nothing to set)."""
    lines = []
    if family:
        lines.append(f"font_family {family}")
    if size:
        lines.append(f"font_size {format_size(size)}")
    if not lines:
        return ""
    return "\n".join([BEGIN, NOTE] + lines + [END]) + "\n"


def format_size(size):
    text = f"{float(size):.2f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def own_size(path):
    """The font_size kitty.conf sets outside skydrop's block (kitty's 11 when none)."""
    raw = _read(path, 500_000)
    if raw is None:
        return DEFAULT_SIZE
    text, _ = split_block(raw.decode("utf-8", "replace"))
    size = DEFAULT_SIZE
    for line in text.splitlines():
        part = line.split()
        if len(part) >= 2 and part[0] == "font_size":
            try:
                size = float(part[1])
            except ValueError:
                pass
    return size


def write_block(path, family, size):
    """Put skydrop's block at the end of kitty.conf - or take it out, when there is
    nothing to set - keeping everything else as it is. The first time, the file is
    copied to kitty.conf.skydrop-backup. Returns None, or what went wrong."""
    real = os.path.realpath(path)
    raw = _read(real, 4_000_000)
    old = raw.decode("utf-8", "replace") if raw is not None else ""
    rest, _ = split_block(old)
    block = block_text(family, size)
    if block:
        new = rest + ("" if not rest or rest.endswith("\n") else "\n") + ("\n" if rest.strip() else "") + block
    else:
        new = rest
    if new == old:
        return None
    folder = os.path.dirname(real) or "."
    temp = None
    try:
        os.makedirs(folder, exist_ok=True)
        if raw is not None and not os.path.exists(real + BACKUP):
            with open(real + BACKUP, "wb") as handle:
                handle.write(raw)
        mode = os.stat(real).st_mode & 0o7777 if raw is not None else 0o644
        temp = os.path.join(folder, f".{os.path.basename(real)}.skydrop-{os.getpid()}")
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(new)
        os.chmod(temp, mode)
        os.replace(temp, real)
    except OSError as e:
        if temp:
            try:
                os.unlink(temp)
            except OSError:
                pass
        return f"{e.strerror or e} ({home_path(real)})"
    return None


def home_path(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


# ---------------------------------------------------------------------------- applying it
class KittyFont:
    """Applies the CRT font and its size to kitty. A change is written at once; kitty is
    told to reload at most every RELOAD_GAP seconds, and once more after the last of a
    quick run of changes (pump(), from the app's loop)."""

    def __init__(self):
        self.last = 0.0
        self.due = None             # when a reload held back is to go
        self.pid = None

    def available(self):
        """(kitty's process, None) or (None, why skydrop can't set kitty's font here)."""
        return kitty_process()

    def apply(self, family, size, now=None):
        """Write the font (None: the terminal's own) and size (None: the terminal's own)
        into kitty.conf and have kitty reload it. Returns (done, message)."""
        now = time.time() if now is None else now
        pid, why = kitty_process()
        if pid is None:
            return False, why
        path = kitty_conf_path(pid)
        problem = write_block(path, family, size)
        if problem:
            return False, f"Couldn't write kitty.conf: {problem}"
        self.pid = pid
        if now - self.last >= RELOAD_GAP:
            self._reload(now)
        else:
            self.due = self.last + RELOAD_GAP
        return True, home_path(os.path.realpath(path))

    def _reload(self, now):
        self.due = None
        self.last = now
        try:
            os.kill(self.pid, signal.SIGUSR1)
        except (OSError, AttributeError):
            pass

    def pump(self, now):
        """A reload held back: send it once it is due. The seconds until it is, or None."""
        if self.due is None:
            return None
        if now >= self.due:
            self._reload(now)
            return None
        return self.due - now


KITTY = KittyFont()
