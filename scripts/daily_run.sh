#!/bin/sh
# The daily job on YOUR machine. Two modes:
#
# A) Dan Murphy's only, handing its data to the cloud job   (BEEROO_DATA_REMOTE is set)
#      GitHub Actions scrapes BWS and Liquorland, publishes the site, and keeps the
#      database (docs/CLOUD_SCRAPE.md). Dan Murphy's blocks cloud servers, so this
#      machine scrapes only it (visible browser) and pushes the pages to the
#      repo's `data` branch inbox for the next cloud run to ingest. Nothing is
#      exported or published from here.
#      BEEROO_DATA_REMOTE=git@github.com:you/beeroo.git
#
# B) Everything here (BEEROO_DATA_REMOTE unset), all free:
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

if [ -n "${BEEROO_DATA_REMOTE:-}" ]; then
  OUT="$REPO/data/outbox"
  "$PY" scripts/scheduled_scrape.py --retailer "${BEEROO_LOCAL_RETAILER:-dan_murphys}" --emit-dir "$OUT" \
    --jitter "${BEEROO_JITTER:-900}" --zones-per-run "${BEEROO_ZONES_PER_RUN:-8}" "$@"
  SCRAPE_CODE=$?
  "$PY" scripts/data_branch.py push-inbox --from "$OUT" --retailer dan_murphys || exit 1
  exit "$SCRAPE_CODE"
fi

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
