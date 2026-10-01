# Retailer access notes (checked 2026-10-01)

| Retailer | robots.txt | Automated access |
|---|---|---|
| Dan Murphy's | Unreadable: plain HTTP request got a Cloudflare block page | Blocked for plain HTTP clients. Earlier Playwright run (2026-09-19) succeeded. |
| BWS | Same Cloudflare block | Blocked for plain HTTP clients. Untested otherwise. |
| Liquorland | Readable. Disallows `/api/*`, `/search`, `/user`, multi-filter category URLs. Plain category pages not disallowed. | ShieldSquare CAPTCHA shown to an in-app browser on a terms page. |

Terms of Use for all three are **not yet reviewed** (Liquorland's is at `/termsconditions`, behind the CAPTCHA). Review manually in a normal browser.

Ground rules: no CAPTCHA solving, no stealth/proxy/fingerprint evasion, low request rates, honour robots.txt, attribute and link every price to its source page. If blocked, stop and report.
