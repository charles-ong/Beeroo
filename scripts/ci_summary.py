"""Turn the scheduled scraper's result into a plain-English verdict for a CI run.

    python scripts/ci_summary.py data/result.json <exit-code> >> "$GITHUB_STEP_SUMMARY"

The scraper prints one JSON line on stdout; `exit-code` is its exit status
(0 ok, 1 error, 3 blocked). An optional third argument sets the heading.
Used by .github/workflows/cloud-scrape-experiment.yml and daily-scrape.yml.
"""
import json
import sys
from pathlib import Path

VERDICTS = {
    "ok": ("works", "The scrape **worked from GitHub's servers** for this site and state. A free cloud schedule is viable for it."),
    "partial": ("partly works", "It ran from GitHub's servers but collected less than expected. Look at the log before relying on it."),
    "blocked": ("blocked", "The site served bot protection to GitHub's servers. Cloud scraping **won't work for this site**; keep it on a home connection. (We don't try to get past a block.)"),
    "robots_disallow": ("skipped by robots.txt", "The site's robots.txt disallows the page, so it was not scraped."),
    "error": ("error", "The run failed for another reason (see the log). That says nothing yet about whether the site blocks cloud servers."),
    "push_failed": ("error", "The scrape worked but storing the result failed (see the log)."),
    "backing_off": ("skipped", "The scraper skipped this site because it is still backing off from an earlier block."),
}


def parse_result(text):
    """The last JSON object printed by the scraper, or None."""
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def summarise(result_text, exit_code, title="Cloud scrape experiment"):
    data = parse_result(result_text)
    lines = [f"## {title}", ""]

    if not data or not data.get("results"):
        lines += ["**Verdict: no result.** The scraper printed no result line, so it probably crashed before finishing.",
                  f"Exit code: `{exit_code}`. See the uploaded `scrape.log`."]
        return "\n".join(lines) + "\n", "no result"

    lines += ["| Retailer | State | Status | Collected | Prices stored |", "|---|---|---|---|---|"]
    verdict = None
    for r in data["results"]:
        status = r.get("status", "?")
        label, text = VERDICTS.get(status, (status, "Unrecognised status."))
        verdict = verdict or (label, text)
        lines.append(f"| {r.get('retailer')} | {r.get('state') or r.get('zone') or ''} | {status} | "
                     f"{r.get('collected', '')} of {r.get('expected', '')} | {r.get('new_observations', '')} |")
    lines += ["", f"**Verdict: {verdict[0]}.** {verdict[1]}", "", f"Scraper exit code: `{exit_code}`."]
    return "\n".join(lines) + "\n", verdict[0]


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    path, code, title = (argv + ["", "", ""])[:3]
    text = Path(path).read_text() if path and Path(path).exists() else ""
    markdown, _ = summarise(text, code or "?", title or "Cloud scrape experiment")
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
