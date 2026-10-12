# Price alerts

Visitors open a beer, pick a pack size and a price (blank = "its lowest price we've recorded"), enter an email, and get
an email when any retailer in their state reaches it. Price history stays free for everyone.

```
browser --POST /api/alerts--> Cloudflare Pages Function (functions/api/alerts/) --> KV namespace ALERTS
daily job: scripts/send_alerts.py --GET /api/alerts/export--> checks the database --> Resend email --POST /api/alerts/mark-->
```

* Double opt-in: the first alert for an email needs a confirmation click; later ones are live at once.
* Free = 3 alerts per email. **Premium** = a `premium:<sha256(lowercase email)>` key in KV (limit 100). Nothing creates that key
  yet: set it by hand for testers (`npx wrangler kv key put --binding ALERTS premium:<hash> 1`), and later have a Stripe
  webhook (a fourth function) set it when someone pays. Until then, "premium" is a plan flag, not a paywall.
* An alert doesn't email twice for the same price; if the price rises back above the target it re-arms.

## One-time setup (all free tiers; check current limits)

1. Cloudflare dashboard > Workers & Pages > KV > create a namespace `beeroo-alerts`.
2. Pages project > Settings > Bindings: add a **KV namespace binding** named `ALERTS` (production).
3. Pages project > Settings > Variables and secrets (production): `ALERTS_ADMIN_TOKEN` (random, 32+ characters),
   `RESEND_API_KEY`, `ALERTS_FROM` (for example `Beeroo <alerts@yourdomain>`, a domain you have verified in Resend).
4. GitHub repo > Settings > Secrets and variables > Actions: secrets `ALERTS_ADMIN_TOKEN` (same value) and `RESEND_API_KEY`;
   variables `BEEROO_SITE_URL` (for example `https://beeroo.pages.dev`, no trailing slash), `ALERTS_FROM`, and optionally
   `ALERTS_FOOTER` (a postal address or business name line for the email footer; the Spam Act wants a way to identify the sender).
5. Redeploy (push, or run the **Deploy site** workflow). Until step 2 is done the form says alerts "aren't switched on yet".

Test the email side without sending: `BEEROO_SITE_URL=... ALERTS_ADMIN_TOKEN=... python scripts/send_alerts.py --dry-run`.

## Before you collect emails for real

Fill in the contact email in `app/static/terms.html` and `privacy.html` (search for `REPLACE`), and have both reviewed.
