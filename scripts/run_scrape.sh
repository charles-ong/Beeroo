#!/bin/sh
# Wrapper used by launchd/cron. Loads secrets from a private env file (not from
# the plist or the repo) and runs one scheduled scrape.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${BEEROO_ENV_FILE:-$HOME/.config/beeroo/env}"

if [ -f "$ENV_FILE" ]; then
  mode="$(stat -f %Lp "$ENV_FILE" 2>/dev/null || stat -c %a "$ENV_FILE")"
  case "$mode" in
    600|400) ;;
    *) echo "refusing to read $ENV_FILE: permissions are $mode, run: chmod 600 $ENV_FILE" >&2; exit 1 ;;
  esac
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

exec "$REPO/.venv/bin/python" "$REPO/scripts/scheduled_scrape.py" "$@"
