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

ROOT = Path(__file__).parent.parent
SH = shutil.which("sh")
WORKFLOW = ROOT / ".github" / "workflows" / "daily-scrape.yml"


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(WORKFLOW.read_text())


def steps_of(wf, job):
    return wf["jobs"][job]["steps"]


def find(steps, fragment):
    return next(s for s in steps if fragment in s.get("name", ""))


def index(steps, fragment):
    return next(i for i, s in enumerate(steps) if fragment in s.get("name", ""))


def triggers(wf):
    return wf.get("on") or wf.get(True)


def test_four_schedules_a_day_and_manual_runs_with_inputs(wf):
    assert [c["cron"] for c in triggers(wf)["schedule"]] == ["0 19 * * *", "0 1 * * *", "0 7 * * *", "0 13 * * *"]
    inputs = triggers(wf)["workflow_dispatch"]["inputs"]
    assert inputs["retailers"]["options"] == ["all", "bws", "dan_murphys", "liquorland"]
    assert inputs["clear_backoff"]["options"] == ["none", "bws", "liquorland", "all"]
    assert wf["permissions"] == {"contents": "write"}
    assert wf["concurrency"] == {"group": "daily-scrape", "cancel-in-progress": False}


def test_each_retailer_is_its_own_job_leg_so_one_can_be_re_run_alone(wf):
    scrape = wf["jobs"]["scrape"]
    assert scrape["strategy"]["fail-fast"] is False
    assert scrape["strategy"]["matrix"] == "${{ fromJSON(needs.plan.outputs.matrix) }}"
    assert wf["jobs"]["publish"]["needs"] == ["plan", "scrape"]


def run_plan(wf, **env):
    script = steps_of(wf, "plan")[0]["run"]
    out = Path(env.pop("OUT"))
    full = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(out), "SCHEDULE": "", "RETAILERS": "", "ZONES": "", **env}
    proc = subprocess.run([SH, "-c", script], capture_output=True, text=True, env=full)
    matrix = json.loads(out.read_text().split("matrix=", 1)[1]) if out.exists() and out.read_text() else None
    return proc, matrix


def legs(matrix):
    return [(i["retailer"], i["zones"]) for i in matrix["include"]]


def test_plan_evening_run_does_bws_fully_and_one_liquorland_state(wf, tmp_path):
    proc, m = run_plan(wf, OUT=tmp_path / "o", SCHEDULE="0 19 * * *")
    assert proc.returncode == 0 and legs(m) == [("bws", "8"), ("dan_murphys", "7"), ("liquorland", "1")]


@pytest.mark.parametrize("cron", ["0 1 * * *", "0 7 * * *", "0 13 * * *"])
def test_plan_other_runs_do_one_liquorland_state_only(wf, tmp_path, cron):
    _, m = run_plan(wf, OUT=tmp_path / "o", SCHEDULE=cron)
    assert legs(m) == [("liquorland", "1")]


@pytest.mark.parametrize("choice,zones,expected", [
    ("all", "", [("bws", "8"), ("dan_murphys", "7"), ("liquorland", "1")]),
    ("bws", "", [("bws", "8")]),
    ("dan_murphys", "", [("dan_murphys", "7")]),
    ("dan_murphys", "3", [("dan_murphys", "3")]),
    ("liquorland", "3", [("liquorland", "3")]),
    ("all", "2", [("bws", "2"), ("dan_murphys", "2"), ("liquorland", "2")]),
])
def test_plan_manual_runs_follow_the_inputs(wf, tmp_path, choice, zones, expected):
    _, m = run_plan(wf, OUT=tmp_path / "o", RETAILERS=choice, ZONES=zones)
    assert legs(m) == expected


@pytest.mark.parametrize("bad", ["0", "9", "x", "1; rm -rf /"])
def test_plan_rejects_a_bad_zone_count(wf, tmp_path, bad):
    proc, _ = run_plan(wf, OUT=tmp_path / "o", RETAILERS="all", ZONES=bad)
    assert proc.returncode != 0


def test_scrape_jobs_never_write_the_database_or_the_branch(wf):
    scrape = steps_of(wf, "scrape")
    text = yaml.dump(scrape)
    assert "--emit-dir data/outbox" in text and "--min-spacing 600" in text
    assert "data_branch.py save" not in text and "ingest_inbox" not in text


def test_scrape_state_is_per_retailer_and_a_block_can_be_cleared(wf):
    step = find(steps_of(wf, "scrape"), "Scrape (headed")
    assert 'scrape_state_$RETAILER.json' in step["run"] and "--retailer" in step["run"]
    assert step["env"]["CLEAR_BACKOFF"] == "${{ inputs.clear_backoff || 'none' }}"


def test_every_scrape_leg_hands_over_pages_state_and_log_even_when_it_fails(wf):
    steps = steps_of(wf, "scrape")
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")]
    assert {u["with"]["name"] for u in uploads} == {"pages-${{ matrix.retailer }}", "state-${{ matrix.retailer }}", "log-${{ matrix.retailer }}"}
    assert all(u["if"] == "always()" for u in uploads)
    assert steps[-1]["if"] == "always()" and "Fail this job" in steps[-1]["name"]


def test_publish_saves_the_data_before_anything_that_can_fail_the_day(wf):
    steps = steps_of(wf, "publish")
    order = [index(steps, f) for f in ("Restore", "Ingest Dan", "Ingest this run", "Save the database", "Export", "Deploy")]
    assert order == sorted(order)
    assert find(steps, "Save the database")["if"].startswith("always()")


def test_publish_runs_even_if_a_scrape_failed_and_then_fails_so_a_rerun_includes_it(wf):
    publish = wf["jobs"]["publish"]
    assert publish["if"].startswith("always()")
    last = publish["steps"][-1]
    assert last["if"] == "always()" and last["env"]["SCRAPE_RESULT"] == "${{ needs.scrape.result }}"


def test_secrets_only_reach_the_steps_that_need_them_and_never_the_shell_text(wf):
    holders = set()
    for job in wf["jobs"].values():
        for step in job["steps"]:
            assert "secrets." not in step.get("run", "") and "${{ inputs" not in step.get("run", "")
            if "secrets." in yaml.dump(step.get("env", {})):
                holders.add(step["name"])
    assert holders == {"Restore back-off state from the data branch", "Restore the database from the data branch",
                       "Save the database to the data branch", "Deploy to Cloudflare Pages"}


def test_dan_murphys_runs_once_a_day_in_the_cloud_in_the_evening_slot_only(wf, tmp_path):
    _, evening = run_plan(wf, OUT=tmp_path / "a", SCHEDULE="0 19 * * *")
    assert ("dan_murphys", "7") in legs(evening)
    for cron in ("0 1 * * *", "0 7 * * *", "0 13 * * *"):
        _, other = run_plan(wf, OUT=tmp_path / cron.replace(" ", "_").replace("*", "x"), SCHEDULE=cron)
        assert "dan_murphys" not in [r for r, _ in legs(other)]


def test_summary_title_is_configurable():
    text = '{"results": [{"retailer": "bws", "zone": "NSW", "status": "ok", "collected": 5, "expected": 5}]}'
    md, _ = ci_summary.summarise(text, "0", "Scrape: bws")
    assert md.startswith("## Scrape: bws")


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


# ---- the deploy-site workflow (text and style changes go live without a scrape) ---------------------------------


DEPLOY = ROOT / ".github" / "workflows" / "deploy-site.yml"


def test_deploy_site_runs_on_pushes_that_change_the_site_and_by_hand():
    wf = yaml.safe_load(DEPLOY.read_text())
    triggers = wf.get("on") or wf.get(True)
    assert triggers["push"]["branches"] == ["main"] and "app/static/**" in triggers["push"]["paths"]
    assert "workflow_dispatch" in triggers and wf["permissions"] == {"contents": "read"}


def test_deploy_site_never_writes_the_data_branch_or_scrapes():
    text = DEPLOY.read_text()
    assert "data_branch.py restore" in text and "data_branch.py save" not in text
    assert "scheduled_scrape" not in text and "playwright" not in text.lower()


def test_deploy_site_and_the_daily_publish_share_a_lock_so_old_builds_never_overwrite_new_ones():
    deploy = yaml.safe_load(DEPLOY.read_text())
    daily = yaml.safe_load(WORKFLOW.read_text())
    assert deploy["concurrency"]["group"] == daily["jobs"]["publish"]["concurrency"]["group"] == "site-deploy"
    assert deploy["concurrency"]["cancel-in-progress"] is False is daily["jobs"]["publish"]["concurrency"]["cancel-in-progress"]


def test_deploy_site_secrets_stay_in_env_blocks():
    wf = yaml.safe_load(DEPLOY.read_text())
    for step in wf["jobs"]["deploy"]["steps"]:
        assert "secrets." not in step.get("run", "") and "${{ inputs" not in step.get("run", "")
