#!/usr/bin/env bash
# skydrop installer for Linux (Ubuntu, Debian, Mint and most other distros) and macOS.
#
#   ./install.sh                install or upgrade; your settings are kept
#   ./install.sh --voice NAME   use another Piper voice, e.g. en_US-lessac-medium
#   ./install.sh --no-voice     skip the Piper neural voice (read-aloud uses the system's voice)
#   ./install.sh --no-apt       never run sudo apt-get (Linux)
#   ./install.sh --yes          answer yes to every question
#
# Everything goes in your home folder: ~/.local/share/skydrop (program, private
# Python, voices), ~/.local/bin/skydrop (the command) and, on Linux, an
# applications-menu entry. Remove it all with ~/.local/share/skydrop/uninstall.sh.

if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -u

VERSION="3.17.0"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
DATA="$SHARE/skydrop"
APPDIR="$DATA/app"
VENV="$DATA/venv"
BIN="$HOME/.local/bin"
LAUNCHER="$BIN/skydrop"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/skydrop"
APPS="$SHARE/applications"
ICON_DIR="$SHARE/icons/hicolor/256x256/apps"
MARK="skydrop launcher"
WATCHING=0

VOICE=""                       # empty: the voice chosen in Settings (en_US-ryan-medium at first)
WANT_VOICE=1
USE_APT=1
ASSUME_YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --no-voice) WANT_VOICE=0 ;;
    --voice) shift; VOICE="${1:-}" ;;
    --voice=*) VOICE="${1#--voice=}" ;;
    --no-apt) USE_APT=0 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

OS="$(uname -s 2>/dev/null || echo unknown)"
IS_MAC=0
if [ "$OS" = "Darwin" ]; then
  IS_MAC=1
  USE_APT=0
fi

if [ -t 1 ]; then
  B=$'\033[1m'; Y=$'\033[1;33m'; G=$'\033[1;32m'; R=$'\033[1;31m'; C=$'\033[1;36m'; D=$'\033[2m'; N=$'\033[0m'
else
  B=""; Y=""; G=""; R=""; C=""; D=""; N=""
fi
step() { printf '\n%s==>%s %s%s%s\n' "$Y" "$N" "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
note() { printf '  %s•%s %s\n' "$C" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$R" "$N" "$*"; }
die()  { printf '\n%sskydrop install stopped:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
tilde() { printf '%s' "${1/#$HOME/\~}"; }
ask() {  # ask "question?" -> success for yes (the default)
  [ "$ASSUME_YES" = 1 ] && return 0
  [ -t 0 ] || return 0
  local reply
  read -r -p "  $1 [Y/n] " reply || return 0
  case "$reply" in [nN]*) return 1 ;; *) return 0 ;; esac
}
have() { command -v "$1" >/dev/null 2>&1; }
stop_watch() {  # stop_watch PID: ask it to stop, then wait (up to 5 s) until it has
  kill "$1" 2>/dev/null || return 1
  local i
  for i in $(seq 50); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.1; done
  return 0
}
apt_install() {  # apt_install pkg... (asks for the sudo password once)
  [ "$USE_APT" = 1 ] && have apt-get || return 1
  sudo -v || { USE_APT=0; return 1; }
  local lock=(-o DPkg::Lock::Timeout=300)
  sudo apt-get "${lock[@]}" install -y "$@" && return 0
  sudo apt-get "${lock[@]}" update -qq  # may complain about an unrelated repository; try anyway
  sudo apt-get "${lock[@]}" install -y "$@"
}

[ "$(id -u)" -ne 0 ] || die "please run ./install.sh as your normal user, not with sudo.
It asks for your password only if it needs to add a system package."
[ -f "$SRC/skydrop/__main__.py" ] || die "run this from the unpacked skydrop folder (the skydrop/ folder is missing)."

printf '\n%s  💧 skydrop %s%s   terminal weather: Supercell-style NEXRAD radar, a warnings crawl and a tornado alarm\n' "$B" "$VERSION" "$N"

# --------------------------------------------------------------------------- python
step "Checking Python"
PY_SYS=""
if [ "$IS_MAC" = 1 ]; then
  # Homebrew's Python first; Apple's /usr/bin/python3 only once the command line tools
  # are there (before that it only offers to install them)
  for cand in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if [ "$cand" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1; then continue; fi
    if "$cand" -c 'import sys' >/dev/null 2>&1; then PY_SYS="$cand"; break; fi
  done
  [ -n "$PY_SYS" ] || die "skydrop needs Python 3.8 or newer. Install it with Homebrew (brew install python)
or Apple's command line tools (xcode-select --install), then run ./install.sh again."
else
  PY_SYS="$(command -v python3 || true)"
  if [ -z "$PY_SYS" ]; then
    note "python3 is missing; installing it"
    apt_install python3 || die "python3 is required (sudo apt install python3)"
    PY_SYS="$(command -v python3)"
  fi
fi
PYV="$("$PY_SYS" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PY_SYS" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
  || die "skydrop needs Python 3.8 or newer (this system has $PYV)."
ok "Python $PYV"

venv_works() {
  local t rc
  t="$(mktemp -d)"
  "$PY_SYS" -m venv "$t/v" >/dev/null 2>&1 && "$t/v/bin/python" -m pip --version >/dev/null 2>&1
  rc=$?
  rm -rf "$t"
  return $rc
}

# --------------------------------------------------------------------------- system packages
step "Checking system packages"
NEED=()
if [ "$IS_MAC" = 1 ]; then
  : # macOS has what skydrop uses: afplay (sound), say (voice), osascript (notifications)
elif [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  :  # already have a working private Python from an earlier install
elif ! venv_works; then
  NEED+=("python3-venv")
fi
if [ "$IS_MAC" = 0 ]; then
  if ! have pw-play && ! have paplay && ! have aplay && ! have ffplay; then
    NEED+=("alsa-utils")
  fi
  if ! have espeak-ng && ! have espeak; then
    NEED+=("espeak-ng")
  fi
  if ! have notify-send; then
    NEED+=("libnotify-bin")   # desktop pop-ups from the background tornado watch
  fi
fi
if [ "$IS_MAC" = 1 ]; then
  ok "macOS has everything needed (afplay for sound, say for the voice, notifications)"
elif [ ${#NEED[@]} -gt 0 ]; then
  note "skydrop would like: ${NEED[*]}"
  printf '    %spython3-venv = a private Python for skydrop, espeak-ng = backup voice, alsa-utils = audio player,\n' "$D"
  printf '     libnotify-bin = desktop notifications for tornado warnings%s\n' "$N"
  if apt_install "${NEED[@]}"; then
    ok "installed ${NEED[*]}"
  else
    warn "couldn't install ${NEED[*]} (continuing; install them later for the full experience)"
  fi
else
  ok "everything needed is already here"
fi

# --------------------------------------------------------------------------- private python
step "Setting up skydrop's private Python"
mkdir -p "$DATA" || die "can't create $DATA"
if [ -e "$VENV" ]; then
  VV="$("$VENV/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo broken)"
  if [ "$VV" != "$PYV" ] || ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    note "rebuilding it (Python $PYV)"
    rm -rf "$VENV"
  fi
fi
[ -x "$VENV/bin/python" ] || "$PY_SYS" -m venv "$VENV" >/dev/null 2>&1 || rm -rf "$VENV"

pipi() {  # pip install with a short progress log; returns pip's status
  "$PY" -m pip install --upgrade --disable-pip-version-check "$@" 2>&1 | while IFS= read -r line; do
    case "$line" in
      Collecting*|Downloading*|Successfully*|ERROR*) printf '    %s%s%s\n' "$D" "${line:0:110}" "$N" ;;
    esac
  done
  return "${PIPESTATUS[0]}"
}

PIPER=0
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  PY="$VENV/bin/python"
  ok "virtual environment in $(tilde "$VENV")"
  "$PY" -m pip install --quiet --upgrade --disable-pip-version-check pip >/dev/null 2>&1 || true
  if pipi "pillow>=8.2"; then
    ok "Pillow (pictures: radar maps, icons, charts)"
  else
    warn "Pillow didn't install; skydrop will draw everything with text characters"
  fi
  if [ "$WANT_VOICE" = 1 ]; then
    note "installing Piper, an offline neural text-to-speech engine (about 70 MB, one time)"
    if pipi --only-binary=:all: "piper-tts>=1.3"; then
      ok "Piper voice engine"
      PIPER=1
    elif [ "$IS_MAC" = 1 ]; then
      warn "Piper didn't install (it needs Python 3.9+); read-aloud will use the macOS voice"
    else
      warn "Piper didn't install (it needs Python 3.9+ on a 64-bit PC); read-aloud will use espeak-ng"
    fi
  fi
else
  rm -rf "$VENV"
  PY="$PY_SYS"
  warn "couldn't create a private Python, so skydrop will use the system Python"
  if ! "$PY" -c 'import PIL' >/dev/null 2>&1; then
    if apt_install python3-pil; then ok "Pillow (python3-pil)"; else warn "no Pillow: pictures will be drawn with text"; fi
  fi
fi

# --------------------------------------------------------------------------- program files
step "Installing skydrop $VERSION"
# A background tornado watch from an earlier install is running the old files: stop it
# now and start the new one at the end.
WATCH_PID_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/skydrop/watch.pid"
if [ -f "$WATCH_PID_FILE" ]; then
  WPID="$(cat "$WATCH_PID_FILE" 2>/dev/null)"
  if [ -n "$WPID" ] && kill -0 "$WPID" 2>/dev/null && ps -p "$WPID" -o args= 2>/dev/null | grep -q -- "--watch"; then
    stop_watch "$WPID" && WATCHING=1 && note "paused the background tornado watch while updating"
  fi
fi
rm -rf "$APPDIR.new" "$APPDIR.old"
mkdir -p "$APPDIR.new" || die "can't write to $DATA"
cp -R "$SRC/skydrop" "$APPDIR.new/" || die "couldn't copy the program files"
find "$APPDIR.new" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
cat > "$APPDIR.new/run.py" <<'EOF'
"""Starts skydrop from its install folder (keeps the current folder out of sys.path)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from skydrop.cli import main  # noqa: E402

sys.exit(main())
EOF
[ -d "$APPDIR" ] && mv "$APPDIR" "$APPDIR.old"
mv "$APPDIR.new" "$APPDIR" || die "couldn't move the program into place"
rm -rf "$APPDIR.old"
"$PY" -m compileall -q "$APPDIR/skydrop" >/dev/null 2>&1 || true
for f in README.md LICENSE uninstall.sh; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DATA/$f"
done
chmod +x "$DATA/uninstall.sh" 2>/dev/null
ok "program files in $(tilde "$APPDIR")"

mkdir -p "$BIN"

# --------------------------------------------------------------------------- launcher
cat > "$LAUNCHER" <<EOF
#!/bin/sh
# $MARK (written by install.sh; remove with $(tilde "$DATA")/uninstall.sh)
exec "$PY" "$APPDIR/run.py" "\$@"
EOF
chmod +x "$LAUNCHER"
ok "command: $(tilde "$LAUNCHER")"

# --------------------------------------------------------------------------- settings
step "Your settings"
if [ -f "$CONF_DIR/config.json" ]; then
  ok "keeping your ZIP code, keys and preferences ($(tilde "$CONF_DIR")/config.json)"
else
  ok "first start creates $(tilde "$CONF_DIR")/config.json (change everything on the Settings screen, key 0)"
fi

# --------------------------------------------------------------------------- menu entry
if [ "$IS_MAC" = 0 ]; then
step "Adding skydrop to the applications menu"
mkdir -p "$APPS" "$ICON_DIR"
ICON="utilities-terminal"
if "$LAUNCHER" --write-icon "$ICON_DIR/skydrop.png" >/dev/null 2>&1; then
  ICON="$ICON_DIR/skydrop.png"
fi
cat > "$APPS/skydrop.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=skydrop
GenericName=Terminal Weather
Comment=Retro Weather Channel style forecasts, NEXRAD radar, warnings and a tornado alarm
Exec="$LAUNCHER"
Icon=$ICON
Terminal=true
Categories=Utility;
Keywords=weather;forecast;radar;terminal;
EOF
have update-desktop-database && update-desktop-database "$APPS" >/dev/null 2>&1
ok "menu entry: skydrop (opens in your default terminal)"
fi

# --------------------------------------------------------------------------- PATH
PATH_NOTE=0
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    PATH_NOTE=1
    if [ "$IS_MAC" = 1 ]; then
      RCS="$HOME/.zshrc $HOME/.bash_profile"; FIRST="$HOME/.zshrc"      # zsh is macOS's shell
    else
      RCS="$HOME/.bashrc $HOME/.zshrc"; FIRST="$HOME/.bashrc"
    fi
    for rc in $RCS; do
      [ -f "$rc" ] || [ "$rc" = "$FIRST" ] || continue
      if ! grep -qs 'skydrop: put ~/.local/bin on PATH' "$rc"; then
        printf '\n# skydrop: put ~/.local/bin on PATH\ncase ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac\n' >> "$rc"
      fi
    done
    ;;
esac

# --------------------------------------------------------------------------- voice
if [ "$WANT_VOICE" = 1 ] && [ "$PIPER" = 1 ]; then
  step "Piper voice ${VOICE:-(the one in your settings)}"
  if "$LAUNCHER" --get-voice ${VOICE:+"$VOICE"}; then
    if "$LAUNCHER" --voice-check >/dev/null 2>&1; then
      ok "voice ready"
    else
      # e.g. a CPU too old for the ONNX runtime: keep the app safe and use another engine
      warn "Piper couldn't run on this computer, so read-aloud will use espeak-ng"
      "$LAUNCHER" --set voice_engine=espeak >/dev/null 2>&1
    fi
  else
    warn "the voice didn't download; try again later with: skydrop --get-voice $VOICE"
  fi
fi

# --------------------------------------------------------------------------- check
step "Checking the installation"
"$LAUNCHER" --version || die "the skydrop command didn't start; see the messages above"
# the background tornado watch: match the setting (start it, and its login entry, if it's on)
if "$LAUNCHER" --watch-sync >/dev/null 2>&1; then
  if grep -qs '"background_watch": *"on"' "$CONF_DIR/config.json"; then
    ok "background tornado watch running (Settings > Tornado alarm > Watch when closed)"
  elif [ "$WATCHING" = 1 ]; then
    note "the background tornado watch is off in Settings, so it stays off"
  fi
fi
if ask "Test the weather data sources now? (takes a few seconds)"; then
  "$LAUNCHER" --check || note "a source that failed above is skipped automatically; you can turn sources off in Settings"
fi

printf '\n%s%s 💧 skydrop %s is installed.%s\n\n' "$G" "$B" "$VERSION" "$N"
if [ "$PATH_NOTE" = 1 ]; then
  printf '  Start it:   %sopen a new terminal window%s and type %sskydrop%s\n' "$B" "$N" "$B" "$N"
  printf '              (or right now: %s)\n' "$(tilde "$LAUNCHER")"
else
  printf '  Start it:   type %sskydrop%s\n' "$B" "$N"
fi
if [ "$IS_MAC" = 1 ]; then
  printf '  Graphics:   full-color pictures in kitty and Ghostty (brew install --cask kitty, or ghostty),\n'
  printf '              sixel pictures in iTerm2 and WezTerm, a block-graphics look in Terminal; chosen automatically\n'
else
  printf '  Graphics:   full-color pictures in kitty and Ghostty, a block-graphics look in Tilix and others,\n'
  printf '              chosen automatically (Settings, key 0, to change)\n'
fi
printf '  Themes:     weatherstar, midnight, retro8s, star4000 and magnetic (light), in Settings\n'
printf '  CRT mode:   an old tube: Settings, Display, CRT effect; %sc%s there opens the effects card (try %sskydrop --crt%s)\n' "$B" "$N" "$B" "$N"
printf '  Settings:   key %s0%s; %sEnter%s opens a section, %s/%s finds a setting, %sS%s saves, %sT%s tests, %sD%s discards\n' "$B" "$N" "$B" "$N" "$B" "$N" "$B" "$N" "$B" "$N" "$B" "$N"
printf '  Radar:      key %s4%s: one NEXRAD site like Supercell Wx; press %si%s there for the guide card\n' "$B" "$N" "$B" "$N"
printf '  Alarm:      a new Tornado Warning for your ZIP code sounds the NOAA alarm; test it with %sskydrop --test-alarm%s\n' "$B" "$N"
printf '  Read aloud: press %ss%s on any forecast screen      TV mode: press %sL%s for Local on the 8s\n' "$B" "$N" "$B" "$N"
printf '  Help:       press %s?%s inside the app, or see %s\n' "$B" "$N" "$(tilde "$DATA")/README.md"
printf '  Uninstall:  %s\n\n' "$(tilde "$DATA")/uninstall.sh"
