#!/usr/bin/env bash
# Remove skydrop from your home folder.
#
#   ~/.local/share/skydrop/uninstall.sh           remove the program; keep your settings
#   ~/.local/share/skydrop/uninstall.sh --purge   also delete your settings (ZIP code, keys)
#
# Works on Linux and macOS. System packages the installer may have added on Linux
# (python3-venv, espeak-ng, alsa-utils, libnotify-bin) are left alone because other
# programs use them too.

if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -u

SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
DATA="$SHARE/skydrop"
BIN="$HOME/.local/bin"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/skydrop"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/skydrop"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
MARK="skydrop launcher"
PURGE=0
case "${1:-}" in
  --purge) PURGE=1 ;;
  -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "") ;;
  *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
esac

if [ "$(id -u)" -eq 0 ]; then
  echo "Please run this as your normal user, not with sudo."
  exit 1
fi

say() { printf '  %s\n' "$*"; }
echo "Removing skydrop..."

# the background tornado watch and its login entry
WATCH_PID_FILE="$CACHE_DIR/watch.pid"
if [ -f "$WATCH_PID_FILE" ]; then
  WPID="$(cat "$WATCH_PID_FILE" 2>/dev/null)"
  if [ -n "$WPID" ] && kill -0 "$WPID" 2>/dev/null && ps -p "$WPID" -o args= 2>/dev/null | grep -q -- "--watch"; then
    if kill "$WPID" 2>/dev/null; then
      for i in $(seq 50); do kill -0 "$WPID" 2>/dev/null || break; sleep 0.1; done
      say "stopped the background tornado watch"
    fi
  fi
fi
if [ -f "$AUTOSTART_DIR/skydrop-watch.desktop" ]; then
  rm -f "$AUTOSTART_DIR/skydrop-watch.desktop" && say "removed the tornado watch from your login programs"
fi
AGENT="$HOME/Library/LaunchAgents/skydrop.watch.plist"     # macOS
if [ -f "$AGENT" ]; then
  launchctl unload "$AGENT" >/dev/null 2>&1
  rm -f "$AGENT" && say "removed the tornado watch from your login items"
fi

# the command (only if this program wrote it)
if [ -f "$BIN/skydrop" ] && grep -q "$MARK" "$BIN/skydrop" 2>/dev/null; then
  rm -f "$BIN/skydrop" && say "removed the skydrop command"
fi
if [ -f "$SHARE/applications/skydrop.desktop" ]; then
  rm -f "$SHARE/applications/skydrop.desktop" "$SHARE/icons/hicolor/256x256/apps/skydrop.png"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$SHARE/applications" >/dev/null 2>&1
  say "removed the applications-menu entry"
fi
rm -rf "$CACHE_DIR" && say "removed cached maps, radar frames and speech files"
rm -rf "$DATA" && say "removed the program, its private Python and the downloaded voices"

if [ "$PURGE" = 1 ]; then
  rm -rf "$CONF_DIR" && say "deleted your settings"
elif [ -d "$CONF_DIR" ]; then
  say "kept your settings in ${CONF_DIR/#$HOME/\~} (run with --purge to delete them)"
fi
echo "Done."
