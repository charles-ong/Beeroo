#!/bin/sh
# The whole daily job, on YOUR machine, all free:
#   1. scrape BWS, Liquorland and Dan Murphy's for every state/territory (visible browser)
#   2. export the static website
#   3. report anything stale
#   4. publish to your free static host:
#        BEEROO_CF_PROJECT=...      Cloudflare Pages (scripts/deploy_cloudflare.sh)
#        BEEROO_PAGES_REMOTE=...    a git branch host such as GitHub Pages (scripts/publish_pages.sh)
#      (either, both, or neither)
#
# Secrets/settings come from ~/.config/beeroo/env (chmod 600), e.g.:
#   BEEROO_PAGES_REMOTE=git@github.com:you/beeroo-site.git
#   BEEROO_CHROMIUM_PATH=/path/to/chromium      (optional)
#   BEEROO_ZONES_PER_RUN=8                       (1-8 states per retailer per day; default 8 = all)
#   BEEROO_SKIP_RETAILERS=liquorland             (comma list of sites to leave out)
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${BEEROO_ENV_FILE:-$HOME/.config/beeroo/env}"

if [ -f "$ENV_FILE" ]; then
  mode="$(stat -f %Lp "$ENV_FILE" 2>/dev/null || stat -c %a "$ENV_FILE")"
  case "$mode" in
    600|400) ;;
    *) echo "refusing to read $ENV_FILE: permissions are $mode, run: chmod 600 $ENV_FILE" >&2; exit 1 ;;
  esac
  set -a; . "$ENV_FILE"; set +a
fi

PY="$REPO/.venv/bin/python"
cd "$REPO"

# 1. scrape (exit 3 = blocked, 1 = error; we still export what we have)
"$PY" scripts/scheduled_scrape.py --jitter "${BEEROO_JITTER:-900}" --zones-per-run "${BEEROO_ZONES_PER_RUN:-8}" "$@"
SCRAPE_CODE=$?

# 2. export + 3. freshness report
"$PY" scripts/export_static.py --db data/beeroo.sqlite3 --out site || exit 1
"$PY" scripts/check_freshness.py --db data/beeroo.sqlite3

# 4. publish
PUBLISHED=0
if [ -n "${BEEROO_CF_PROJECT:-}" ]; then
  scripts/deploy_cloudflare.sh site || exit 1
  PUBLISHED=1
fi
if [ -n "${BEEROO_PAGES_REMOTE:-}" ]; then
  scripts/publish_pages.sh site || exit 1
  PUBLISHED=1
fi
if [ "$PUBLISHED" = "0" ]; then
  echo "neither BEEROO_CF_PROJECT nor BEEROO_PAGES_REMOTE set: site exported to $REPO/site but not published"
fi
exit "$SCRAPE_CODE"
