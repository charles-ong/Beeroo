# Deployment

## Architecture

```
 your Mac ──(scheduled scraper, visible browser)──┐
 manual captures (push_captures.py) ──────────────┤  POST /api/admin/ingest  (admin token)
                                                  ▼
   shoppers ── https ──►  Beeroo server (FastAPI + SQLite on a volume)
   extension (opt-in) ─►  POST /api/contrib   [CLOSED by default in production]
```

The server only serves the app and stores data. It never scrapes (it would be blocked).

## Status

Prepared and unit-tested, **not deployed**. Not yet verified: the Docker image build (Docker's daemon wasn't running here), the CI workflows (the repo has no remote), and any real host. Please verify the checklist at the bottom after the first deploy.

## Deploy to Fly.io (example; any Docker host works)

Requires a Fly account and `flyctl`. You pay for the machine and volume (a small always-on machine plus 1 GB volume; check Fly's current pricing).

```bash
fly auth login
# edit fly.toml: set a unique `app` name
fly launch --no-deploy --copy-config
fly volumes create beeroo_data --region syd --size 1
fly secrets set BEEROO_SALT="$(openssl rand -hex 32)" BEEROO_ADMIN_TOKEN="$(openssl rand -hex 32)"
fly deploy
curl https://<app>.fly.dev/healthz        # {"status":"ok","products":0,...}
```

Save the admin token somewhere safe and put it in `~/.config/beeroo/env` (see `docs/SCHEDULED_SCRAPE.md`). Use `fly secrets list` to confirm it's set; you can't read it back.

Other hosts: `docker build -t beeroo .` and run with a persistent volume at `/data`, `BEEROO_SALT`, `BEEROO_ADMIN_TOKEN`, port 8080, behind HTTPS.

## Environment

| Variable | Required | Purpose |
|---|---|---|
| `BEEROO_ENV=production` | set by the image | Enables guards: refuses to start without secrets, hides `/api/docs`, adds HSTS, closes public contributions |
| `BEEROO_SALT` | yes (prod) | Keys the hash of contributor install IDs. Changing it orphans contributor history |
| `BEEROO_ADMIN_TOKEN` | yes (prod, >= 24 chars) | Protects `/api/admin/ingest` and `/api/contrib/promote` |
| `BEEROO_DB` | no | Defaults to `/data/beeroo.sqlite3` |
| `BEEROO_CONTRIB_ENABLED` | no | `1` opens public contributions. **Leave `0` until the retailer terms are reviewed** |
| `BEEROO_MIN_INSTALL_AGE_HOURS` | no | Sybil resistance for contributions |

## Put real data on it

```bash
export BEEROO_SERVER=https://<app>.fly.dev BEEROO_ADMIN_TOKEN=...
python scripts/push_captures.py bws --set-pickup tests/fixtures/bws_2606_set_pickup.json tests/fixtures/bws_2606_products.json
python scripts/push_captures.py liquorland tests/fixtures/liquorland_act_products.json tests/fixtures/liquorland_wa_products.json
```

(Use your full raw captures for more than the trimmed fixtures.) Matching runs automatically. The `demo` database is for development only; **never ship `demo.sqlite3`**: its history is simulated.

## Backups

- Online, consistent: `fly ssh console -C "python scripts/backup_db.py"` writes to `/data/backups/` (keeps 14). Download with `fly ssh sftp get`.
- Fly also snapshots volumes daily (short retention); don't rely on that alone.
- Restore: stop the machine, replace `/data/beeroo.sqlite3`, delete `-wal`/`-shm` files, start.

## Monitoring

Poll `GET /healthz`. It returns `latest_data`; alert if that is older than ~10 days (the scraper has been blocked or stopped). `GET /api/contrib/health` shows contribution drift once contributions are open.

## Security checklist

- [x] App refuses to start in production without secrets; admin endpoints need a token (constant-time compare)
- [x] Strict CSP, no inline scripts/styles, `X-Frame-Options: DENY`, `nosniff`, HSTS in production, API docs hidden
- [x] No access log (client IPs not recorded); container runs as non-root
- [x] Public contributions closed by default
- [ ] **Rate limiting at the edge** (not configured; add via your proxy/CDN, e.g. limit `/api/*`)
- [ ] Privacy policy + terms page (needed before inviting users or opening contributions)
- [ ] Review retailer terms of use / legal advice
- [ ] Custom domain (`fly certs add`), uptime monitor

## Rollback

`fly releases` then `fly deploy --image <previous image>`. The database is on the volume and unaffected by releases (take a backup before schema-changing releases).

## After the first deploy, verify

- [ ] `docker build` succeeds and the container passes its healthcheck
- [ ] `/healthz` returns ok over https; `/api/docs` returns 404
- [ ] A `push_captures.py` run reports new listings; `/?postcode=2606` shows prices
- [ ] Restarting the machine keeps the data (volume mounted at `/data`)
- [ ] `POST /api/contrib` returns 503 (closed) and `POST /api/admin/ingest` returns 403 without the token
- [ ] Nothing in `fly logs` contains client IPs or tokens
