# Scheduled Dan Murphy's run

## Status: built and tested with fakes; NOT verified live

- **Live attempt 2026-10-01 15:59 (Sydney):** blocked by Cloudflare on the very first request, ~2.5 hours after the previous block. The safeguards worked (stopped at once, no retry, 2-day backoff recorded), but it means this IP is still blocked and the location-selection steps remain unverified against the live site.
- Do **not** install the schedule until a manual run succeeds (see "First run").
- Retailer terms of use are still unreviewed. Automated access may breach them.

## Why it runs on your Mac, not a server

Dan Murphy's (Cloudflare) blocks headless browsers and datacenter IPs. A normal, visible browser from a home connection worked initially, then got blocked after about nine loads. So: one machine you control, a visible browser window, very low volume. We do not use proxies, stealth plugins, CAPTCHA solving or rotating IPs, and never will in this project.

## What one run does

1. Picks **one** location: the never-scraped or least-recently-updated of Sydney 2000, Melbourne 3000, Brisbane 4000, Perth 6000, Adelaide 5000, Hobart 7000, Canberra 2600, Darwin 0800 (the site's own store selector chooses the nearest store).
2. Opens a visible Chromium window, loads the beer category, picks the store, clicks "Load more" like a user (2-4 s between clicks, ~17 pages).
3. Reads the product JSON the page loads (no extra requests) and sends it to your server (`--push`) or a local DB.
4. Stops, whatever happens.

Coverage: one location per day, so each of the 8 locations refreshes about **weekly**, not daily. Crowd contributions and manual captures fill the gaps. "Every store in Australia daily" isn't achievable this way.

## Safety behaviour (tested)

- Any block page → stop immediately, never retry that day, back off 2, 4, 8, 14 days (capped); a later success resets it.
- Backoff is checked **before** a browser is launched.
- A result with under 80% of the site's reported product count is saved but not counted as a fresh zone.
- Another run already in progress → exits.
- The admin token is only sent over https (or localhost) and is read from a private file, never the plist or repo.

## Setup (not done for you)

```bash
# 1. secrets, readable only by you
mkdir -p ~/.config/beeroo && cat > ~/.config/beeroo/env <<'EOF'
BEEROO_SERVER=https://your-app.fly.dev
BEEROO_ADMIN_TOKEN=...the token you set on the server...
BEEROO_CHROMIUM_PATH=/path/to/Chromium   # optional
EOF
chmod 600 ~/.config/beeroo/env

# 2. FIRST RUN, by hand, small, and watch it
scripts/run_scrape.sh --push --zone 2600 --max-pages 2

# 3. only if that worked: schedule it (daily 11:30; a window will appear)
scripts/install_launchd.sh --print      # review what will be installed
scripts/install_launchd.sh --hour 11 --minute 30
# remove later:  scripts/install_launchd.sh --uninstall
```

A LaunchAgent runs in your logged-in session; if the Mac is asleep at the scheduled time the job runs when it wakes. Logs: `data/scrape.log`, `data/launchd.log`. State: `data/scrape_state.json` (delete `blocked` there to clear a backoff).

## If it keeps getting blocked

Stop. Don't try to defeat the block. Use the other sources instead: manual captures (`docs/CAPTURE_GUIDE.md`, `scripts/push_captures.py`), the opt-in extension, or ask Endeavour Group for a data feed.
