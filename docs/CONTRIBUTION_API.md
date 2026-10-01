# Contribution API

Lets consenting users (via a future browser extension) share the product data their own browser already received while shopping. The server validates it, stages it, and only promotes a price into the real price history when independent contributors agree.

> Legal status: retailer terms of use have **not** been reviewed. A user's consent does not change a retailer's terms. Get the clauses reviewed (and legal advice) before enabling this publicly.

## Endpoint

`POST /api/contrib` (JSON, max 3 MB)

```json
{
  "schema_version": 1,
  "install_id": "3f1c9a52-8e6b-4b1d-9d0a-6f2f5b7a1c11",
  "extension_version": "0.1.0",
  "kind": "bws_products",
  "logged_in": false,
  "location": {"store_id": "6723", "store_name": "Woden", "suburb": "Woden", "state": "ACT", "postcode": "2606"},
  "payload": { "...the retailer's product-list JSON response, unmodified..." }
}
```

| `kind` | Payload is | Location |
|---|---|---|
| `dan_murphys_browse` | response of `POST /apis/ui/Browse` | `location` required (the *store's* details) |
| `bws_products` | response of `GET /apis/ui/ProductGroup/Products/<group>` | `location` required |
| `liquorland_products` | response of `GET /api/products/<site>/<category>` | derived from the payload's `sitestate`; any supplied `location` is ignored |

Responses: `202` accepted (`{status, contribution_id, products, staged, skipped, promoted, pending}`), `200` duplicate, `413` too large, `422` invalid (message says why), `429` rate limited (30/hour/install).

Other endpoints: `GET /api/contrib/health` (counts and `drift_suspected` per retailer), `POST /api/contrib/promote` (header `X-Admin-Token` = `$BEEROO_ADMIN_TOKEN`; promotion + expiry pass; also `scripts/promote_contributions.py` for cron).

## Rules the extension MUST follow

- Opt-in only, with a first-run consent screen showing exactly what is sent, a pause switch, and a log of what was sent.
- Send **only** the allowlisted product-list responses above, plus the store's id/name/suburb/state/postcode. **Never** delivery addresses, cookies, tokens, cart, account, or personalised responses.
- Do not send while the user is logged in (`logged_in: true` is rejected), so member-only prices don't leak into public prices.
- Make **no requests of its own** to retailers: only read what the page already loaded.
- `install_id` is a random UUID generated locally; no account, no email.

## How consensus works

1. The payload is parsed with the same tested parsers as our own captures; the **raw payload is discarded**, only parsed prices are staged.
2. A price (per retailer + location + product + pack + member flag) is promoted when **2 independent installs report the exact same price within 24 h** (newest report per install counts). Differing prices are marked `outlier` once a quorum forms elsewhere; ties are held.
3. A move of more than 30% versus the last accepted price needs **3** confirmations.
4. A lone contributor can update a price only with a proven record (>= 10 accepted, trust >= 0.85) and a move of at most 15%.
5. Unconfirmed prices expire after 72 h. Promoted prices go into the append-only history; new products are matched across retailers automatically.
6. Format drift: if more than 30% of items fail to parse for a reason other than "not buyable", the contribution is rejected; `drift_suspected` flips when most recent contributions for a retailer fail.

## Privacy

- `install_id` is stored only as an HMAC-SHA256 hash keyed with `BEEROO_SALT` (**set this in production**; the default is dev-only).
- The app never reads or stores IP addresses or user agents. **Run uvicorn with `--no-access-log`** (or strip IPs in your proxy), otherwise the server log records them.
- Publish a privacy policy before launch (Australian Privacy Act / APPs).

## Known limitations

- **Sybil attacks:** anyone can create many install IDs, so a determined attacker could fake a quorum. Mitigations in place: exact-match quorum, 3-vote rule for big moves, trust scores, per-install rate limit. Optional: `BEEROO_MIN_INSTALL_AGE_HOURS=24` stops brand-new installs voting. Stronger options (not built): rate limiting by IP at the proxy without storing it, attestation, or requiring a trusted first-party capture to agree.
- **Cold start:** with few contributors most prices stay pending. First-party captures (`scripts/ingest.py`) remain the baseline.
- **Postcode/state check** uses Australia Post ranges; a few border postcodes could be wrongly rejected.
- Quorum assumes contributors in the same `location_key` see the same price. If a retailer prices per store, two people must use the same store.
