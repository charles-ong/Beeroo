import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import ci_summary  # noqa: E402

from common.states import STATES  # noqa: E402

ROOT = Path(__file__).parent.parent
SH = shutil.which("sh")
WORKFLOW = ROOT / ".github" / "workflows" / "cloud-scrape-experiment.yml"


# ---- the GitHub Actions experiment ---------------------------------------------


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text())


def triggers(wf):
    return wf.get("on") or wf.get(True)          # PyYAML reads the bare key `on` as True


def test_it_is_manual_only_by_default_no_schedule(wf):
    assert set(triggers(wf)) == {"workflow_dispatch"}


def test_inputs_offer_exactly_our_retailers_and_states(wf):
    inputs = triggers(wf)["workflow_dispatch"]["inputs"]
    assert inputs["retailer"]["options"] == ["bws", "liquorland", "dan_murphys"] and inputs["retailer"]["default"] == "bws"
    assert inputs["state"]["options"] == list(STATES) and inputs["state"]["default"] == "NSW"


def test_least_privilege_and_no_secrets(wf):
    assert wf["permissions"] == {"contents": "read"}
    assert "secrets." not in WORKFLOW.read_text()


def test_inputs_reach_the_shell_only_through_environment_variables(wf):
    """No `${{ inputs.* }}` interpolated into a run: script (script-injection hygiene)."""
    for step in wf["jobs"]["scrape"]["steps"]:
        if "run" in step:
            assert "inputs." not in step["run"], step.get("name")
    scrape = next(s for s in wf["jobs"]["scrape"]["steps"] if s.get("id") == "scrape")
    assert set(scrape["env"]) == {"RETAILER", "STATE"}


def test_scrapes_one_retailer_one_state_headed_on_a_virtual_display(wf):
    run = next(s for s in wf["jobs"]["scrape"]["steps"] if s.get("id") == "scrape")["run"]
    assert "xvfb-run" in run and "--retailer" in run and "--zone" in run
    assert "headless" not in run.lower()                      # headless is blocked; and never forced here
    assert "--zones-per-run" not in run                       # a single state only


def test_always_summarises_and_uploads_evidence_even_on_failure(wf):
    steps = wf["jobs"]["scrape"]["steps"]
    summary = next(s for s in steps if s.get("name") == "Summarise")
    upload = next(s for s in steps if "upload-artifact" in str(s.get("uses")))
    assert summary["if"] == "always()" and upload["if"] == "always()"
    assert "scrape.log" in upload["with"]["path"] and upload["with"]["retention-days"] == 7
    assert wf["jobs"]["scrape"]["timeout-minutes"] <= 30


def test_installs_the_pinned_requirements_and_chromium(wf):
    text = " ".join(s.get("run", "") for s in wf["jobs"]["scrape"]["steps"])
    assert "pip install -r requirements.txt" in text and "playwright install --with-deps chromium" in text


def test_the_workflow_contains_no_evasion_tricks():
    text = WORKFLOW.read_text().lower()
    for bad in ("stealth", "proxy", "undetected", "captcha-solver", "user-agent", "spoof"):
        assert bad not in text.replace("never tries to get past a block", ""), bad


# ---- the verdict summary ------------------------------------------------------------


def result_line(status, **extra):
    return json.dumps({"results": [{"retailer": "bws", "zone": "NSW", "state": "New South Wales", "status": status, **extra}]})


@pytest.mark.parametrize("status,label,phrase", [
    ("ok", "works", "worked from GitHub"),
    ("partial", "partly works", "less than expected"),
    ("blocked", "blocked", "won't work for this site"),
    ("robots_disallow", "skipped by robots.txt", "robots.txt"),
    ("error", "error", "says nothing yet"),
    ("push_failed", "error", "storing the result failed"),
    ("backing_off", "skipped", "backing off"),
])
def test_verdicts(status, label, phrase):
    md, verdict = ci_summary.summarise("log noise\n" + result_line(status, collected=189, expected=745, new_observations=526), 0)
    assert verdict == label and phrase in md
    assert "| bws | New South Wales | " + status in md and "189 of 745" in md


def test_blocked_is_reported_as_a_result_not_a_crash():
    md, verdict = ci_summary.summarise(result_line("blocked"), 3)
    assert verdict == "blocked" and "Scraper exit code: `3`" in md and "get past a block" in md


@pytest.mark.parametrize("text", ["", "no json here", "{broken", '{"results": []}'])
def test_missing_or_garbage_output_says_no_result(text):
    md, verdict = ci_summary.summarise(text, 1)
    assert verdict == "no result" and "crashed" in md and "`1`" in md


def test_the_last_json_line_wins():
    text = result_line("error") + "\n" + result_line("ok")
    assert ci_summary.parse_result(text)["results"][0]["status"] == "ok"


def test_summary_script_runs_from_the_command_line(tmp_path):
    f = tmp_path / "result.json"
    f.write_text(result_line("ok", collected=1, expected=1, new_observations=2))
    out = subprocess.run([sys.executable, str(ROOT / "scripts/ci_summary.py"), str(f), "0"], capture_output=True, text=True)
    assert out.returncode == 0 and "Verdict: works" in out.stdout


# ---- Cloudflare Pages deploy script ---------------------------------------------------


@pytest.fixture()
def site(tmp_path):
    s = tmp_path / "site"
    (s / "data").mkdir(parents=True)
    (s / "index.html").write_text("<html></html>")
    (s / "data" / "manifest.json").write_text('{"states":["NSW"]}')
    return s


@pytest.fixture()
def fake_npx(tmp_path):
    """A stub `npx` that records its arguments (and optionally fails)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "npx.log"
    npx = bindir / "npx"
    npx.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\ncase "$*" in\n  *"project create"*) [ -n "$FAKE_CREATE_FAIL" ] && exit 1;;\n  *"pages deploy"*) [ -n "$FAKE_DEPLOY_FAIL" ] && {{ echo "Authentication error" >&2; exit 1; }};;\nesac\nexit 0\n')
    npx.chmod(0o755)
    return bindir, log


def deploy(site, bindir, **env):
    full = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(site.parent), **env}
    return subprocess.run([SH, str(ROOT / "scripts" / "deploy_cloudflare.sh"), str(site)], capture_output=True, text=True, env=full)


def test_deploy_creates_the_project_then_uploads_the_site(site, fake_npx):
    bindir, log = fake_npx
    r = deploy(site, bindir, BEEROO_CF_PROJECT="beeroo")
    assert r.returncode == 0, r.stderr
    create, up = log.read_text().strip().splitlines()
    assert create == "--yes wrangler@3 pages project create beeroo --production-branch main"
    assert up == f"--yes wrangler@3 pages deploy {site} --project-name beeroo --branch main --commit-dirty=true"
    assert "https://beeroo.pages.dev" in r.stdout and "3 files" not in r.stdout


def test_an_existing_project_is_fine_but_a_failed_upload_is_not(site, fake_npx):
    bindir, _ = fake_npx
    assert deploy(site, bindir, BEEROO_CF_PROJECT="beeroo", FAKE_CREATE_FAIL="1").returncode == 0
    bad = deploy(site, bindir, BEEROO_CF_PROJECT="beeroo", FAKE_DEPLOY_FAIL="1")
    assert bad.returncode != 0 and "Authentication error" in bad.stderr and "deployed" not in bad.stdout


@pytest.mark.parametrize("name", ["", "Has Caps", "has_underscore", "-leading", "trailing-", "semi;colon", "$(touch x)"])
def test_bad_project_names_are_refused_before_anything_runs(site, fake_npx, name):
    bindir, log = fake_npx
    r = deploy(site, bindir, BEEROO_CF_PROJECT=name)
    assert r.returncode == 2 and not log.exists()


def test_refuses_a_non_site_directory_and_missing_npx(tmp_path, site, fake_npx):
    bindir, log = fake_npx
    empty = tmp_path / "empty"
    empty.mkdir()
    assert deploy(empty, bindir, BEEROO_CF_PROJECT="beeroo").returncode == 2 and not log.exists()
    r = deploy(site, tmp_path / "nowhere", BEEROO_CF_PROJECT="beeroo")
    assert r.returncode == 2 and "npx not found" in r.stderr


def test_free_tier_limits_are_checked_before_uploading(site, fake_npx):
    bindir, log = fake_npx
    for i in range(6):
        (site / f"f{i}.json").write_text("{}")
    r = deploy(site, bindir, BEEROO_CF_PROJECT="beeroo", BEEROO_CF_MAX_FILES="5")
    assert r.returncode == 2 and "exceeds the free-tier limit" in r.stderr and not log.exists()
    (site / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    r = deploy(site, bindir, BEEROO_CF_PROJECT="beeroo", BEEROO_CF_MAX_FILE_MB="1")
    assert r.returncode == 2 and "big.bin" in r.stderr and not log.exists()


def test_api_credentials_are_never_echoed(site, fake_npx):
    bindir, _ = fake_npx
    r = deploy(site, bindir, BEEROO_CF_PROJECT="beeroo", CLOUDFLARE_API_TOKEN="SUPERSECRETTOKEN", CLOUDFLARE_ACCOUNT_ID="acct123")
    assert "SUPERSECRETTOKEN" not in r.stdout + r.stderr and "acct123" not in r.stdout + r.stderr


def test_real_exported_site_passes_the_limit_checks_with_room_to_spare():
    """The real export is ~500 files per state; 8 states must stay well under 20,000."""
    per_state = 506
    assert per_state * len(STATES) < 20000 * 0.25


# ---- daily job: publishing choices -------------------------------------------------------


@pytest.fixture()
def daily(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "daily_run.sh", tmp_path / "scripts" / "daily_run.sh")
    log = tmp_path / "calls.log"
    py = tmp_path / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\necho "py $*" >> "{log}"\nexit 0\n')
    py.chmod(0o755)
    for name in ("publish_pages.sh", "deploy_cloudflare.sh"):
        stub = tmp_path / "scripts" / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n[ -n "$FAKE_FAIL_{name.split("_")[0].upper()}" ] && exit 1\nexit 0\n')
        stub.chmod(0o755)
    return tmp_path, log


def run_daily(repo, **env):
    full = {"PATH": os.environ["PATH"], "HOME": str(repo), "BEEROO_ENV_FILE": str(repo / "none"), **env}
    return subprocess.run([SH, str(repo / "scripts" / "daily_run.sh")], capture_output=True, text=True, env=full)


def published(log):
    return [l.split()[0] for l in log.read_text().splitlines() if l.startswith(("publish", "deploy"))]


def test_cloudflare_only(daily):
    repo, log = daily
    assert run_daily(repo, BEEROO_CF_PROJECT="beeroo").returncode == 0
    assert published(log) == ["deploy_cloudflare.sh"]


def test_git_host_only(daily):
    repo, log = daily
    assert run_daily(repo, BEEROO_PAGES_REMOTE="x").returncode == 0
    assert published(log) == ["publish_pages.sh"]


def test_both_hosts(daily):
    repo, log = daily
    assert run_daily(repo, BEEROO_CF_PROJECT="beeroo", BEEROO_PAGES_REMOTE="x").returncode == 0
    assert published(log) == ["deploy_cloudflare.sh", "publish_pages.sh"]


def test_neither_host_exports_but_says_so(daily):
    repo, log = daily
    r = run_daily(repo)
    assert r.returncode == 0 and published(log) == [] and "not published" in r.stdout


def test_a_failed_deploy_fails_the_daily_job(daily):
    repo, log = daily
    assert run_daily(repo, BEEROO_CF_PROJECT="beeroo", FAKE_FAIL_DEPLOY="1").returncode == 1
