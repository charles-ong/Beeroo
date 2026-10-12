"""Email people whose price alerts have fired. Run after the data is updated (the daily job does).

    BEEROO_SITE_URL=https://beeroo.pages.dev ALERTS_ADMIN_TOKEN=... RESEND_API_KEY=... \\
    ALERTS_FROM="Beeroo <alerts@yourdomain>" python scripts/send_alerts.py --db data/beeroo.sqlite3 [--dry-run]

It asks the site's /api/alerts/export for the confirmed alerts (functions/api/alerts/), checks each against the
database (common/alerts.py), sends one email per fired alert through Resend, then tells the site what it sent
so the same price isn't emailed twice. Without the env vars it does nothing (exit 0), so the workflow step is safe
before alerts are set up. See docs/ALERTS.md.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common import db  # noqa: E402
from common.alerts import Matcher  # noqa: E402


def http_json(url, token=None, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET")
    req.add_header("user-agent", "beeroo-alerts/1.0")
    if body is not None:
        req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
        return json.loads(raw) if raw else {}


def compose(alert, deal, site, footer=""):
    """(subject, text) for one fired alert."""
    goal = f"${alert['target']:.2f}" if alert.get("target") is not None else "its lowest price we've recorded"
    subject = f"{deal['product_name']} is ${deal['price']:.2f} at {deal['retailer_name']}"
    unsub = f"{site}/api/alerts/unsubscribe?t={alert['token']}"
    text = (
        f"{deal['product_name']} ({deal['label']}) is now ${deal['price']:.2f} at {deal['retailer_name']}"
        f"{' (member offer)' if deal['member_only'] else ''}, at or below {goal}.\n\n"
        f"Buy: {deal['url']}\n"
        f"Compare all retailers: {site}/?state={alert['state']}\n\n"
        "Prices are as last collected and may have changed; check the retailer's site before buying. "
        "Please drink responsibly (18+).\n\n"
        f"Stop this alert: {unsub}\n"
        f"{footer}\n"
    )
    return subject, text


def send_resend(api_key, sender, to, subject, text):
    http_json("https://api.resend.com/emails", token=api_key,
              body={"from": sender, "to": [to], "subject": subject, "text": text})


def run(conn, site, admin_token, send, dry_run=False, footer="", fetch=http_json, now=None):
    alerts = fetch(f"{site}/api/alerts/export", token=admin_token).get("alerts", [])
    matcher, updates, sent, failed = Matcher(conn, now), [], 0, 0
    for alert in alerts:
        deal = matcher.check(alert)
        if deal is None:
            # price is back above the target (or the beer is gone): allow a future drop to notify again
            if alert.get("last_notified_price") is not None and not _still_ok(matcher, alert):
                updates.append({"id": alert["id"], "last_notified_price": None})
            continue
        subject, text = compose(alert, deal, site, footer)
        if dry_run:
            print(f"[dry run] to {alert['email']}: {subject}")
            continue
        try:
            send(alert["email"], subject, text)
        except Exception as e:  # one bad address must not stop the rest
            failed += 1
            print(f"send failed for alert {alert['id']}: {e}", file=sys.stderr)
            continue
        sent += 1
        updates.append({"id": alert["id"], "last_notified_price": deal["price"]})
    if updates and not dry_run:
        fetch(f"{site}/api/alerts/mark", token=admin_token, body={"updates": updates})
    return {"alerts": len(alerts), "sent": sent, "failed": failed, "reset": sum(1 for u in updates if u["last_notified_price"] is None)}


def _still_ok(matcher, alert):
    """True when the price is still at/below the target (so only the 'already told you' rule stopped the email)."""
    probe = {**alert, "last_notified_price": None}
    return matcher.check(probe) is not None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/beeroo.sqlite3")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    site = os.environ.get("BEEROO_SITE_URL", "").rstrip("/")
    admin = os.environ.get("ALERTS_ADMIN_TOKEN", "")
    key, sender = os.environ.get("RESEND_API_KEY", ""), os.environ.get("ALERTS_FROM", "")
    if not (site and admin and (args.dry_run or (key and sender))):
        print("alerts not configured (BEEROO_SITE_URL, ALERTS_ADMIN_TOKEN, RESEND_API_KEY, ALERTS_FROM): skipping")
        return
    conn = db.connect(args.db)
    stats = run(conn, site, admin, lambda to, s, t: send_resend(key, sender, to, s, t),
                dry_run=args.dry_run, footer=os.environ.get("ALERTS_FOOTER", ""))
    print(stats)


if __name__ == "__main__":
    main()
