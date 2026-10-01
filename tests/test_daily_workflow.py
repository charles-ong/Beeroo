import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import ci_summary  # noqa: E402

ROOT = Path(__file__).parent.parent
SH = shutil.which("sh")
WORKFLOW = ROOT / ".github" / "workflows" / "daily-scrape.yml"


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def steps(wf):
    return wf["jobs"]["scrape"]["steps"]


def names(steps):
    return [s.get("name", s.get("uses")) for s in steps]


def index(steps, fragment):
    return next(i for i, s in enumerate(steps) if fragment in s.get("name", ""))


def test_runs_daily_and_by_hand_and_only_writes_contents(wf):
    triggers = wf.get("on") or wf.get(True)
    assert triggers["schedule"][0]["cron"] == "0 19 * * *"
    assert "workflow_dispatch" in triggers
    assert wf["permissions"] == {"contents": "write"}
    assert wf["concurrency"] == {"group": "daily-scrape", "cancel-in-progress": False}


def test_never_scrapes_dan_murphys_in_the_cloud(wf):
    assert wf["jobs"]["scrape"]["env"]["BEEROO_SKIP_RETAILERS"] == "dan_murphys"


def test_the_data_is_saved_before_anything_that_can_fail_the_day(steps):
    order = [index(steps, f) for f in ("Restore", "Ingest", "Scrape", "Save the database", "Export", "Deploy")]
    assert order == sorted(order)
    assert steps[index(steps, "Save the database")]["if"].startswith("always()")


def test_secrets_only_reach_the_steps_that_need_them_and_never_the_shell_text(steps):
    for step in steps:
        run = step.get("run", "")
        assert "secrets." not in run and "${{ inputs" not in run
    holders = {s["name"] for s in steps if "secrets." in yaml.dump(s.get("env", {}))}
    assert holders == {"Restore the database from the data branch", "Save the database to the data branch",
                       "Deploy to Cloudflare Pages"}


def test_a_blocked_or_failed_scrape_fails_the_run_after_publishing(steps):
    assert index(steps, "Fail the run") == len(steps) - 1
    assert steps[-1]["if"] == "always()"


def test_summary_title_is_configurable():
    text = '{"results": [{"retailer": "bws", "zone": "NSW", "status": "ok", "collected": 5, "expected": 5}]}'
    md, _ = ci_summary.summarise(text, "0", "Daily cloud scrape")
    assert md.startswith("## Daily cloud scrape")


# ---- daily_run.sh relay mode (Dan Murphy's only, hand-off to the cloud) -------------


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "daily_run.sh", tmp_path / "scripts" / "daily_run.sh")
    log = tmp_path / "calls.log"
    py = tmp_path / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(
        f'#!/bin/sh\necho "py $*" >> "{log}"\n'
        'case "$1" in *scheduled_scrape.py) exit "${FAKE_SCRAPE_CODE:-0}";; '
        '*data_branch.py) exit "${FAKE_PUSH_CODE:-0}";; esac\nexit 0\n'
    )
    py.chmod(0o755)
    return tmp_path, log


def run(repo, **env):
    full = {"PATH": os.environ["PATH"], "HOME": str(repo), "BEEROO_ENV_FILE": str(repo / "none"), **env}
    return subprocess.run([SH, str(repo / "scripts" / "daily_run.sh")], capture_output=True, text=True, env=full)


def calls(log):
    return log.read_text().splitlines()


def test_relay_mode_scrapes_only_dan_murphys_and_pushes_the_pages(repo):
    path, log = repo
    r = run(path, BEEROO_DATA_REMOTE="git@example:x/y.git")
    assert r.returncode == 0, r.stderr
    scrape, push = calls(log)
    assert "--retailer dan_murphys" in scrape and "--emit-dir" in scrape
    assert "data_branch.py push-inbox" in push and "--retailer dan_murphys" in push


def test_relay_mode_does_not_export_or_publish(repo):
    path, log = repo
    run(path, BEEROO_DATA_REMOTE="x", BEEROO_CF_PROJECT="p", BEEROO_PAGES_REMOTE="x")
    text = log.read_text()
    assert "export_static" not in text and "deploy_cloudflare" not in text and "publish" not in text


def test_relay_mode_still_pushes_after_a_block_and_reports_it(repo):
    path, log = repo
    r = run(path, BEEROO_DATA_REMOTE="x", FAKE_SCRAPE_CODE="3")
    assert r.returncode == 3 and any("push-inbox" in c for c in calls(log))


def test_relay_mode_fails_loudly_if_the_push_fails(repo):
    path, _ = repo
    assert run(path, BEEROO_DATA_REMOTE="x", FAKE_PUSH_CODE="1").returncode == 1


def test_without_a_data_remote_everything_still_runs_locally(repo):
    path, log = repo
    run(path)
    text = log.read_text()
    assert "--emit-dir" not in text and "export_static" in text


def test_cloud_scrape_spaces_visits_and_can_clear_a_saved_block(wf, steps):
    scrape = steps[index(steps, "Scrape")]
    assert "--min-spacing 600" in scrape["run"]
    assert scrape["env"]["CLEAR_BACKOFF"] == "${{ inputs.clear_backoff || 'none' }}"
    options = (wf.get("on") or wf.get(True))["workflow_dispatch"]["inputs"]["clear_backoff"]["options"]
    assert options == ["none", "bws", "liquorland", "all"]
