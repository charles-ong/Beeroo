#!/bin/sh
# Install (or remove) the daily job (scrape -> export -> publish) as a macOS LaunchAgent.
#
#   scripts/install_launchd.sh --print                 # show the plist, change nothing
#   scripts/install_launchd.sh [--hour 11 --minute 30] # install + load
#   scripts/install_launchd.sh --uninstall
#
# A LaunchAgent runs in your logged-in session (needed: the browser window is
# visible). If your Mac is asleep at the scheduled time, launchd runs the job
# when it wakes.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.beeroo.scrape"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
HOUR=11; MINUTE=30; MODE=install

while [ $# -gt 0 ]; do
  case "$1" in
    --hour) HOUR="$2"; shift 2 ;;
    --minute) MINUTE="$2"; shift 2 ;;
    --print) MODE=print; shift ;;
    --uninstall) MODE=uninstall; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

case "$HOUR" in ''|*[!0-9]*) echo "--hour must be 0-23" >&2; exit 2 ;; esac
case "$MINUTE" in ''|*[!0-9]*) echo "--minute must be 0-59" >&2; exit 2 ;; esac
[ "$HOUR" -le 23 ] && [ "$MINUTE" -le 59 ] || { echo "invalid time" >&2; exit 2; }

render() {
  sed -e "s|@REPO@|$REPO|g" -e "s|@HOUR@|$HOUR|g" -e "s|@MINUTE@|$MINUTE|g" \
    "$REPO/deploy/launchd/$LABEL.plist.template"
}

case "$MODE" in
  print) render ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$TARGET"
    echo "removed $LABEL" ;;
  install)
    [ -x "$REPO/.venv/bin/python" ] || { echo "no $REPO/.venv; create it first" >&2; exit 1; }
    mkdir -p "$REPO/data" "$HOME/Library/LaunchAgents"
    render > "$TARGET"
    plutil -lint "$TARGET"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$TARGET"
    echo "installed: runs daily at $(printf '%02d:%02d' "$HOUR" "$MINUTE") (+ up to 15 min random delay)"
    echo "logs: $REPO/data/launchd.log and $REPO/data/scrape.log"
    echo "test now: launchctl kickstart gui/$(id -u)/$LABEL" ;;
esac
