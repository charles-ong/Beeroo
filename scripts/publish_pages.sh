#!/bin/sh
# Publish the exported static site to a FREE static host that serves a git
# branch (GitHub Pages / Cloudflare Pages / Netlify). Uses ONE orphan commit
# force-pushed each time, so the host repo never grows with daily history.
#
#   BEEROO_PAGES_REMOTE=git@github.com:you/beeroo-site.git  scripts/publish_pages.sh [site-dir]
#
# Use a SEPARATE public repo just for the published site (code stays private).
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SITE="${1:-$REPO/site}"
REMOTE="${BEEROO_PAGES_REMOTE:-}"
BRANCH="${BEEROO_PAGES_BRANCH:-gh-pages}"

[ -n "$REMOTE" ] || { echo "set BEEROO_PAGES_REMOTE to the git URL of your site repo" >&2; exit 2; }
[ -f "$SITE/index.html" ] && [ -f "$SITE/data/manifest.json" ] || { echo "$SITE is not an exported site (run scripts/export_static.py)" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp -R "$SITE"/. "$TMP"/
cd "$TMP"
git init -q
git checkout -q -b "$BRANCH"
git add -A
git -c user.name="Beeroo publisher" -c user.email="publisher@localhost" commit -q -m "Publish $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q --force "$REMOTE" "$BRANCH"
echo "published $(find . -type f -not -path './.git/*' | wc -l | tr -d ' ') files to $REMOTE ($BRANCH)"
