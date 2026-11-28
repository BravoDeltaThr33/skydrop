"""Background tornado watch (`skydrop --watch`).

Runs without a window. At every refresh it fetches only the alerts for your location; for a
new Tornado Warning covering your ZIP code it sounds the same alarm as the app (SAME bursts,
the 1050 Hz tone, the warning read aloud, the end-of-message bursts) and shows a desktop
notification. While a skydrop window is open the window does the alerting instead (with its
banner, repeats and acknowledgement); the two share 'claim' files, so a warning sounds once.

Turned on and off with Settings > Warnings and alarm > Watch when closed, which also adds or
removes the login item that starts it: ~/.config/autostart/skydrop-watch.desktop on Linux, a
launch agent in ~/Library/LaunchAgents on macOS, and a value under HKEY_CURRENT_USER's Run key
on Windows (where it runs with pythonw, without a window).
"""

import os
import shutil
import signal
import subprocess
import sys
import time

from . import APP, VERSION, osdeps, sounds
from .paths import CACHE_DIR, CONFIG_PATH, ensure

PID_PATH = os.path.join(CACHE_DIR, "watch.pid")
LOG_PATH = os.path.join(CACHE_DIR, "watch.log")
UI_ALIVE = os.path.join(CACHE_DIR, "ui.alive")
STOP_PATH = os.path.join(CACHE_DIR, "watch.stop")     # Windows: "please stop" (no SIGTERM there)
AUTOSTART_PATH = osdeps.autostart_place(APP)
BEAT = 15               # seconds between the watcher's touches of its pid file
UI_FRESH = 120          # seconds: a window that touched ui.alive this recently is in charge


# ----------------------------------------------------------------------------
# small helpers shared with the app
# ----------------------------------------------------------------------------

def log(msg):
    ensure(CACHE_DIR)
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass
    if sys.stdout is not None and sys.stdout.isatty():
        print(time.strftime("%H:%M:%S ") + msg, flush=True)


def ui_heartbeat():
    """The app calls this now and then: 'a window is open and watching'."""
    ensure(CACHE_DIR)
    try:
        with open(UI_ALIVE, "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def ui_alive():
    try:
        if time.time() - os.stat(UI_ALIVE).st_mtime > UI_FRESH:
            return False
        with open(UI_ALIVE) as fh:
            pid = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return False
    return _alive(pid)


def ui_gone():
    try:
        with open(UI_ALIVE) as fh:
            if int(fh.read().strip() or 0) == os.getpid():
                os.remove(UI_ALIVE)
    except (OSError, ValueError):
        pass


def _alive(pid):
    return osdeps.alive(pid)


def watcher_pid():
    """The running watcher's process ID, or None."""
    try:
        with open(PID_PATH) as fh:
            pid = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return None
    if not _alive(pid):
        return None
    cmd = osdeps.command_line(pid)
    if cmd is not None and "--watch" not in cmd:
        return None                     # the process ID went to another program
    if cmd is None:
        # Windows can't read another program's command line: a live watcher touches its file
        try:
            if time.time() - os.stat(PID_PATH).st_mtime > 4 * BEAT:
                return None
        except OSError:
            return None
    return pid


def command():
    """How to start this skydrop from outside, by absolute paths (a login session may not
    have ~/.local/bin on its PATH yet)."""
    if getattr(sys, "frozen", False):
        return [os.path.abspath(sys.executable)]      # skydrop.exe
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if os.path.basename(script) == "run.py" and os.path.exists(script):
        return [sys.executable, script]       # the installed program
    exe = shutil.which(APP)
    if exe:
        return [os.path.abspath(exe)]
    if script.endswith(".py") and os.path.exists(script):
        return [sys.executable, script]
    return [sys.executable, "-m", APP]


def watch_command():
    """The command that runs the background watch (with pythonw on Windows: no window)."""
    argv = command() + ["--watch"]
    if osdeps.WINDOWS and argv and os.path.basename(argv[0]).lower() == "python.exe":
        argv[0] = osdeps.windowless_python(argv[0])
    return argv


def start_watcher():
    """Start the background watch now (if it isn't running). True when one is running."""
    if watcher_pid():
        return True
    env = None
    if getattr(sys, "frozen", False):
        # skydrop.exe: a watch of its own, which outlives this one (and the files it unpacked)
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    try:
        subprocess.Popen(watch_command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, env=env, **osdeps.background(detach=True))
    except OSError as e:
        log(f"couldn't start the watcher: {e}")
        return False
    for _ in range(20):
        time.sleep(0.05)
        if watcher_pid():
            return True
    return True


def stop_watcher():
    pid = watcher_pid()
    if pid and osdeps.WINDOWS:
        # ask it to stop (it looks twice a second) rather than end a process by its number
        try:
            ensure(CACHE_DIR)
            open(STOP_PATH, "w").close()
        except OSError:
            pass
        for _ in range(40):
            if not watcher_pid():
                break
            time.sleep(0.1)
    elif pid:
        osdeps.terminate(pid)
    return pid


def _stop_asked():
    if not os.path.exists(STOP_PATH):
        return False
    try:
        os.remove(STOP_PATH)
    except OSError:
        pass
    return True


def set_autostart(enabled):
    """Add or remove the login item that starts the watch. Returns an error message or ''."""
    return osdeps.set_login_item(enabled, watch_command(), APP, VERSION)


def sync(cfg):
    """Make the watcher match the setting (called at start-up and after Settings saves)."""
    from .config import on
    if on(cfg, "background_watch"):
        err = set_autostart(True)
        ok = start_watcher()
        return err or ("" if ok else "couldn't start the background watch")
    stop_watcher()
    return set_autostart(False)


def notify(title, body, critical=True):
    return osdeps.notify(APP, title, body, critical)


# ----------------------------------------------------------------------------
# the alarm, start to finish (blocking)
# ----------------------------------------------------------------------------

def _wait_players(speech, limit):
    end = time.time() + limit
    for p in list(getattr(speech, "_bg", [])):
        try:
            p.wait(timeout=max(0.1, end - time.time()))
        except Exception:
            pass


def sound_tornado(cfg, speech, a, test=False):
    """SAME bursts + 1050 Hz tone (or the chosen sound), the warning read aloud, then the
    end-of-message bursts. Returns False when nothing could play."""
    from .config import on
    snd = (cfg.get("tornado_alarm_sound") or "noaa").lower()
    if snd not in ("noaa", "nwr", "eas"):
        snd = "noaa"
    now = time.time()
    mins = max(15, int(((a.get("end") or now + 2700) - now) / 60))
    path, secs = sounds.alarm_file(snd, "RWT" if test else "TOR", None if test else (a.get("same") or None), mins,
                                   "SKYDROP" if test else f"{a.get('office') or 'KWNS'}/NWS",
                                   None if test else a.get("issued"))
    speech.stop()
    if not speech.play_file(path):
        return False
    _wait_players(speech, secs + 3)
    if on(cfg, "tornado_alarm_voice"):
        if test:
            text = ["This is a test of the skydrop tornado alarm.",
                    "If this were a real tornado warning for your area, you would hear the warning here.",
                    "This concludes the test."]
        else:
            text = [f"{a['event']} for your area.", a.get("nws_headline") or "",
                    (a.get("desc") or "").split("\n\n")[0], a.get("instruction") or ""]
        if speech.speak(a.get("event") or "Tornado Warning", [t for t in text if t]):
            t0 = time.time()
            time.sleep(0.5)
            while speech.active and time.time() - t0 < 180:
                time.sleep(0.2)
    if snd == "noaa":
        try:
            if speech.play_file(sounds.eom_file()):
                _wait_players(speech, 6)
        except OSError:
            pass
    return True


# ----------------------------------------------------------------------------
# the watch loop
# ----------------------------------------------------------------------------

def run(cfg):
    """`skydrop --watch`: watch until stopped. Returns an exit status."""
    from .alerts import AlertWatcher, alarm_events, threat_text, where_text
    from .config import intval, load_config, on
    from .fmt import Fmt
    from .model import build_model
    from .speech import Speaker, no_player_hint
    from .store import DataStore

    other = watcher_pid()
    if other and other != os.getpid():
        print(f"{APP}: the background watch is already running (process {other}).")
        return 0
    ensure(CACHE_DIR)
    try:
        with open(PID_PATH, "w") as fh:
            fh.write(str(os.getpid()))
    except OSError as e:
        print(f"{APP}: can't write {PID_PATH}: {e}", file=sys.stderr)
        return 1
    _stop_asked()                           # an old request isn't for this watcher
    state = {"stop": False, "reload": False}

    def on_term(_sig, _frm):
        state["stop"] = True

    def on_hup(_sig, _frm):
        state["reload"] = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, on_hup)
    store = DataStore(cfg, persist=False)   # never writes config.json: the app owns it
    speech = Speaker(cfg)
    try:
        mtime = os.stat(CONFIG_PATH).st_mtime
    except OSError:
        mtime = 0
    log(f"watch started ({APP} {VERSION}, ZIP {cfg.get('zip') or '?'}, every "
        f"{intval(cfg, 'refresh_seconds', 15, 3600)} s)")
    announced = set()
    try:
        while not state["stop"]:
            # follow changes made in Settings (or by hand) without a restart
            try:
                m_now = os.stat(CONFIG_PATH).st_mtime
            except OSError:
                m_now = mtime
            if m_now != mtime or state["reload"]:
                mtime, state["reload"] = m_now, False
                new = load_config()
                if not on(new, "background_watch") and not os.environ.get("SKYDROP_WATCH_FORCE"):
                    log("watch turned off in Settings: stopping")
                    break
                moved = any(new.get(k) != cfg.get(k) for k in ("zip", "lat", "lon", "country"))
                with store.cfg_lock:
                    cfg.clear()
                    cfg.update(new)
                if moved:
                    store.forget_all()
                    log(f"location changed: now watching ZIP {cfg.get('zip') or '?'}")
            started = time.time()
            try:
                store.refresh_alerts()
                model = build_model(store, cfg)
                alerts = model.get("area_alerts") or []
                F = Fmt(cfg.get("units") or "imperial", None)
                window = ui_alive()
                for kind, a, key in alarm_events(alerts, cfg):
                    if window:
                        break                       # an open window is in charge
                    if not AlertWatcher.claim(key):
                        continue
                    title = a.get("event") or "Warning"
                    where = where_text(a, F)
                    body = " · ".join(x for x in (a.get("area") or "", where, threat_text(a),
                                                  (a.get("instruction") or "").split("\n")[0]) if x)
                    log(f"ALARM {kind}: {key} {title} {where}")
                    notify(("⚠ " if kind == "tornado" else "") + title.upper(), body, critical=kind == "tornado")
                    if kind == "tornado":
                        if not sound_tornado(cfg, speech, a):
                            log(no_player_hint())
                    else:
                        snd = (cfg.get("warning_alarm") or "chime").lower()
                        if snd in ("chime", "nwr"):
                            speech.tone(snd)
                            _wait_players(speech, 6)
                        if on(cfg, "auto_read_warnings"):
                            speech.speak(title, [x for x in (title + ".", a.get("nws_headline") or "",
                                                             a.get("instruction") or "") if x])
                errs = {k: v for k, v in store.errors().items() if k in ("nws_alerts_area", "nws_alerts", "loc")}
                for k, v in errs.items():
                    if (k, v) not in announced:
                        announced.add((k, v))
                        log(f"problem: {k}: {v}")
            except Exception as e:  # keep watching whatever happens
                log(f"error: {type(e).__name__}: {e}")
            every = max(20, intval(cfg, "refresh_seconds", 15, 3600))
            beat = 0.0
            while not state["stop"] and not state["reload"] and time.time() - started < every:
                if time.time() - beat >= BEAT:
                    beat = time.time()
                    try:
                        os.utime(PID_PATH, None)
                    except OSError:
                        pass
                if _stop_asked():
                    log("asked to stop")
                    state["stop"] = True
                    break
                time.sleep(0.5)
    finally:
        log("watch stopped")
        try:
            with open(PID_PATH) as fh:
                if int(fh.read().strip() or 0) == os.getpid():
                    os.remove(PID_PATH)
        except (OSError, ValueError):
            pass
        speech.shutdown()
    return 0


def test_alarm(cfg):
    """`skydrop --test-alarm`: the tornado alarm as it will sound, with a test message."""
    from .speech import Speaker, no_player_hint
    speech = Speaker(cfg)
    print(f"{APP}: playing the tornado alarm test ({cfg.get('tornado_alarm_sound') or 'noaa'} sound, "
          f"through {speech.player() or 'no audio player'})...")
    ok = sound_tornado(cfg, speech, {"event": "Required Weekly Test", "end": time.time() + 900}, test=True)
    speech.shutdown()
    if not ok:
        print(f"{APP}: {no_player_hint()}", file=sys.stderr)
        return 1
    print(f"{APP}: done.")
    return 0
