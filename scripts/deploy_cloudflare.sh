#!/bin/sh
# Deploy the exported static site to Cloudflare Pages (free tier; works with a
# private repo because it uploads files directly, no git needed).
#
# One-time setup:
#   1. Free Cloudflare account.
#   2. `npx wrangler@3 login`   (opens a browser once)  -- or, for unattended runs
#      (launchd), create an API token with "Cloudflare Pages: Edit" and put
#      CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in ~/.config/beeroo/env.
#   3. Choose a project name:  BEEROO_CF_PROJECT=beeroo
#
#   BEEROO_CF_PROJECT=beeroo scripts/deploy_cloudflare.sh [site-dir]
#
# Needs Node (npx). Free-tier limits (check Cloudflare's current docs): 20,000
# files per site and 25 MiB per file; the script checks both before uploading.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SITE="${1:-$REPO/site}"
PROJECT="${BEEROO_CF_PROJECT:-}"
BRANCH="${BEEROO_CF_BRANCH:-main}"
WRANGLER="${BEEROO_WRANGLER:-wrangler@3}"
MAX_FILES="${BEEROO_CF_MAX_FILES:-20000}"
MAX_FILE_MB="${BEEROO_CF_MAX_FILE_MB:-25}"

[ -n "$PROJECT" ] || { echo "set BEEROO_CF_PROJECT to your Cloudflare Pages project name" >&2; exit 2; }
case "$PROJECT" in
  *[!a-z0-9-]*|-*|*-|"") echo "BEEROO_CF_PROJECT must be lowercase letters, digits and hyphens (not at the ends): $PROJECT" >&2; exit 2 ;;
esac
[ -f "$SITE/index.html" ] && [ -f "$SITE/data/manifest.json" ] || { echo "$SITE is not an exported site (run scripts/export_static.py)" >&2; exit 2; }
command -v npx >/dev/null 2>&1 || { echo "npx not found: install Node.js (https://nodejs.org)" >&2; exit 2; }

COUNT="$(find "$SITE" -type f | wc -l | tr -d ' ')"
[ "$COUNT" -le "$MAX_FILES" ] || { echo "$COUNT files exceeds the free-tier limit of $MAX_FILES" >&2; exit 2; }
BIG="$(find "$SITE" -type f -size +"${MAX_FILE_MB}"M | head -1)"
[ -z "$BIG" ] || { echo "$BIG is larger than ${MAX_FILE_MB} MiB (free-tier file limit)" >&2; exit 2; }

# Creating an existing project fails harmlessly; a real auth problem shows up in the deploy below.
npx --yes "$WRANGLER" pages project create "$PROJECT" --production-branch "$BRANCH" >/dev/null 2>&1 || true
npx --yes "$WRANGLER" pages deploy "$SITE" --project-name "$PROJECT" --branch "$BRANCH" --commit-dirty=true
echo "deployed $COUNT files to https://$PROJECT.pages.dev"
