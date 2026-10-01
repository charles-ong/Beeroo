# Deployment (optional, paid server)

> **You don't need this.** The default, free setup is `docs/FREE_HOSTING.md` (a static site published from your own computer). Use this document only if you later want a live API and are willing to pay for a small always-on server.

## Architecture

```
 your computer (scheduled scrape, visible browser)
        │  POST /api/admin/ingest   (admin token; scripts/scheduled_scrape.py --push)
        ▼
   shoppers ── https ──►  Beeroo server (FastAPI + SQLite on a volume)
```

The server only serves the app and stores data. It never scrapes (it would be blocked).

## Status

Prepared and unit-tested, **not deployed**. Not yet verified: the Docker image build (Docker's daemon wasn't running here), the CI workflow (the repo has no remote), and any real host. Please verify the checklist at the bottom after the first deploy.

## Deploy to Fly.io (example; any Docker host works)

Requires a Fly account and `flyctl`. You pay for the machine and volume (a small always-on machine plus 1 GB volume; check Fly's current pricing).

```bash
fly auth login
# edit fly.toml: set a unique `app` name
fly launch --no-deploy --copy-config
fly volumes create beeroo_data --region syd --size 1
fly secrets set BEEROO_ADMIN_TOKEN="$(openssl rand -hex 32)"
fly deploy
curl https://<app>.fly.dev/healthz        # {"status":"ok","products":0,...}
```

Save the admin token somewhere safe and put it in `~/.config/beeroo/env` as `BEEROO_ADMIN_TOKEN` (with `BEEROO_SERVER=https://<app>.fly.dev`). You can't read a Fly secret back.

Other hosts: `docker build -t beeroo .` and run with a persistent volume at `/data`, `BEEROO_ADMIN_TOKEN`, port 8080, behind HTTPS.

## Environment

| Variable | Required | Purpose |
|---|---|---|
| `BEEROO_ENV=production` | set by the image | Enables guards: refuses to start without an admin token, hides `/api/docs`, adds HSTS |
| `BEEROO_ADMIN_TOKEN` | yes (prod, >= 24 chars) | Protects `POST /api/admin/ingest` |
| `BEEROO_DB` | no | Defaults to `/data/beeroo.sqlite3` |

## Put real data on it

Run the scraper on your own computer and push to the server:

```bash
export BEEROO_SERVER=https://<app>.fly.dev BEEROO_ADMIN_TOKEN=...
python scripts/scheduled_scrape.py --push          # all sites, all states (see docs/SCHEDULED_SCRAPE.md)
```

Matching across retailers runs automatically. A database built by `build_demo_db.py` is for development only; **never ship `demo.sqlite3`**: its history is simulated.

## Backups

- Online, consistent: `fly ssh console -C "python scripts/backup_db.py"` writes to `/data/backups/` (keeps 14). Download with `fly ssh sftp get`.
- Fly also snapshots volumes daily (short retention); don't rely on that alone.
- Restore: stop the machine, replace `/data/beeroo.sqlite3`, delete `-wal`/`-shm` files, start.

## Monitoring

Poll `GET /healthz`. It returns `latest_data`; alert if that is older than ~10 days (the scraper has been blocked or stopped).

## Security checklist

- [x] App refuses to start in production without an admin token; the ingest endpoint needs it (constant-time compare)
- [x] Strict CSP, no inline scripts/styles, `X-Frame-Options: DENY`, `nosniff`, HSTS in production, API docs hidden
- [x] No access log (client IPs not recorded); container runs as non-root
- [ ] **Rate limiting at the edge** (not configured; add via your proxy/CDN, e.g. limit `/api/*`)
- [ ] Review retailer terms of use / legal advice
- [ ] Custom domain (`fly certs add`), uptime monitor

## Rollback

`fly releases` then `fly deploy --image <previous image>`. The database is on the volume and unaffected by releases (take a backup before schema-changing releases).

## After the first deploy, verify

- [ ] `docker build` succeeds and the container passes its healthcheck
- [ ] `/healthz` returns ok over https; `/api/docs` returns 404
- [ ] `POST /api/admin/ingest` returns 403 without the token
- [ ] A `scheduled_scrape.py --push --zone NSW --retailer bws` run reports new listings; `/?state=NSW` shows prices
- [ ] Restarting the machine keeps the data (volume mounted at `/data`)
- [ ] Nothing in `fly logs` contains client IPs or tokens
