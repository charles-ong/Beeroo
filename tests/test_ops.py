import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import backup_db  # noqa: E402
import check_freshness  # noqa: E402

from common import db  # noqa: E402

ROOT = Path(__file__).parent.parent
SH = shutil.which("sh")


# ---- backup -----------------------------------------------------------------


def make_db(path):
    conn = db.connect(str(path))
    db.set_meta(conn, "marker", "hello")
    conn.close()


def test_backup_is_a_consistent_openable_copy(tmp_path):
    src = tmp_path / "beeroo.sqlite3"
    make_db(src)
    out = backup_db.backup(src, tmp_path / "b", now=datetime(2026, 10, 1, 1, 2, 3, tzinfo=timezone.utc))
    assert out.name == "beeroo-20261001-010203.sqlite3"
    copy = db.connect(str(out))
    assert db.get_meta(copy, "marker") == "hello"
    assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_works_while_the_database_is_being_written(tmp_path):
    src = tmp_path / "beeroo.sqlite3"
    make_db(src)
    writer = db.connect(str(src))
    writer.execute("BEGIN")
    writer.execute("INSERT INTO meta VALUES ('uncommitted', 'x')")   # open write transaction
    out = backup_db.backup(src, tmp_path / "b")
    assert db.get_meta(db.connect(str(out)), "uncommitted") is None   # only committed data
    writer.rollback()


def test_backup_keeps_only_the_newest_n(tmp_path):
    src = tmp_path / "beeroo.sqlite3"
    make_db(src)
    for sec in range(5):
        backup_db.backup(src, tmp_path / "b", keep=3, now=datetime(2026, 10, 1, 1, 0, sec, tzinfo=timezone.utc))
    kept = sorted(p.name for p in (tmp_path / "b").glob("*.sqlite3"))
    assert kept == ["beeroo-20261001-010002.sqlite3", "beeroo-20261001-010003.sqlite3", "beeroo-20261001-010004.sqlite3"]


def test_backup_of_missing_database_fails_cleanly(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        backup_db.backup(tmp_path / "nope.sqlite3", tmp_path / "b")


# ---- launchd ----------------------------------------------------------------


def render(*args):
    return subprocess.run([SH, str(ROOT / "scripts" / "install_launchd.sh"), "--print", *args],
                          capture_output=True, text=True)


def test_launchd_plist_renders_and_parses():
    r = render("--hour", "7", "--minute", "5")
    assert r.returncode == 0
    plist = plistlib.loads(r.stdout.encode())
    assert plist["Label"] == "com.beeroo.scrape"
    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 5}
    assert plist["RunAtLoad"] is False
    args = plist["ProgramArguments"]
    assert args[1].endswith("scripts/daily_run.sh")
    assert str(ROOT) in args[1] and "@" not in r.stdout.replace("com.apple", "")


def test_launchd_plist_contains_no_secrets():
    text = render().stdout.lower()
    for word in ("token", "password", "secret", "salt"):
        assert word not in text


@pytest.mark.parametrize("bad", [["--hour", "24"], ["--minute", "60"], ["--hour", "x"], ["--nope"]])
def test_install_script_rejects_bad_input(bad):
    assert render(*bad).returncode != 0


@pytest.mark.skipif(shutil.which("plutil") is None, reason="macOS only")
def test_plist_passes_plutil_lint(tmp_path):
    p = tmp_path / "x.plist"
    p.write_text(render().stdout)
    assert subprocess.run(["plutil", "-lint", str(p)], capture_output=True).returncode == 0


# ---- container + deployment files ------------------------------------------


def test_web_app_imports_without_scraper_only_dependencies():
    code = ("import sys; sys.modules['playwright']=None; "
            "import app.main; print('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.stdout.strip() == "ok", r.stderr


def test_web_requirements_are_pinned_and_match_the_main_pins():
    web = dict(l.split("==") for l in (ROOT / "requirements-web.txt").read_text().split())
    main = dict(l.split("==") for l in (ROOT / "requirements.txt").read_text().split())
    assert set(web) == {"fastapi", "uvicorn", "pydantic"}
    assert all(main[k] == v for k, v in web.items())


def test_dockerfile_is_locked_down():
    text = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER beeroo$", text, re.M)
    assert "--no-access-log" in text and "BEEROO_ENV=production" in text
    assert "HEALTHCHECK" in text and "/healthz" in text
    assert "EXPOSE 8080" in text
    for secret in ("BEEROO_SALT", "BEEROO_ADMIN_TOKEN", "playwright"):
        assert secret not in text
    assert "COPY scripts/backup_db.py scripts/" in text and "promote" not in text


def test_dockerignore_keeps_data_and_secrets_out_of_the_image():
    lines = (ROOT / ".dockerignore").read_text().split()
    for must in (".venv", ".git", "data", "*.har", "*.sqlite3"):
        assert must in lines


def test_fly_config():
    cfg = tomllib.loads((ROOT / "fly.toml").read_text())
    assert cfg["http_service"]["internal_port"] == 8080 and cfg["http_service"]["force_https"] is True
    assert cfg["mounts"][0]["destination"] == "/data" and cfg["env"]["BEEROO_DB"].startswith("/data/")
    assert "BEEROO_CONTRIB_ENABLED" not in cfg["env"]
    assert cfg["http_service"]["min_machines_running"] == 1
    assert cfg["http_service"]["checks"][0]["path"] == "/healthz"
    flat = (ROOT / "fly.toml").read_text()
    assert "TOKEN" not in flat.replace("BEEROO_ADMIN_TOKEN", "") and "SALT" not in flat


def test_workflows_never_inline_secrets():
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = wf.read_text()
        assert not re.search(r"token\s*[:=]\s*['\"]?[A-Za-z0-9]{20,}", text, re.I), wf.name
    assert not (ROOT / ".github" / "workflows" / "promote.yml").exists()


# ---- publish_pages.sh (against a real local git remote) ----------------------


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def fake_site(tmp_path):
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    (site / "index.html").write_text("<html>v1</html>")
    (site / "data" / "manifest.json").write_text('{"states":["ACT"]}')
    return site


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_publish_pushes_a_single_orphan_commit_each_time(tmp_path, fake_site):
    remote = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(remote))
    env = {**os.environ, "BEEROO_PAGES_REMOTE": str(remote)}
    script = str(ROOT / "scripts" / "publish_pages.sh")

    def publish():
        return subprocess.run([SH, script, str(fake_site)], capture_output=True, text=True, env=env)

    r1 = publish()
    assert r1.returncode == 0, r1.stderr
    (fake_site / "index.html").write_text("<html>v2</html>")
    assert publish().returncode == 0

    assert git("rev-list", "--count", "gh-pages", cwd=remote) == "1"             # history never grows
    assert git("show", "gh-pages:index.html", cwd=remote) == "<html>v2</html>"
    assert "data/manifest.json" in git("ls-tree", "-r", "--name-only", "gh-pages", cwd=remote)
    assert ".git/" not in git("ls-tree", "-r", "--name-only", "gh-pages", cwd=remote)


def test_publish_refuses_without_a_remote_or_a_real_site(tmp_path, fake_site):
    script = str(ROOT / "scripts" / "publish_pages.sh")
    env = {k: v for k, v in os.environ.items() if k != "BEEROO_PAGES_REMOTE"}
    r = subprocess.run([SH, script, str(fake_site)], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "BEEROO_PAGES_REMOTE" in r.stderr
    env["BEEROO_PAGES_REMOTE"] = str(tmp_path / "x.git")
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run([SH, script, str(empty)], capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "not an exported site" in r.stderr


# ---- daily_run.sh control flow (stub programs) -------------------------------


@pytest.fixture()
def daily_repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "daily_run.sh", tmp_path / "scripts" / "daily_run.sh")
    log = tmp_path / "calls.log"
    py = tmp_path / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\necho "py $*" >> "{log}"\ncase "$1" in *scheduled_scrape.py) exit "${{FAKE_SCRAPE_CODE:-0}}";; esac\nexit 0\n')
    py.chmod(0o755)
    pub = tmp_path / "scripts" / "publish_pages.sh"
    pub.write_text(f'#!/bin/sh\necho "publish $*" >> "{log}"\n')
    pub.chmod(0o755)
    return tmp_path, log


def run_daily(repo, extra_env=None, *args):
    env = {"PATH": os.environ["PATH"], "HOME": str(repo), "BEEROO_ENV_FILE": str(repo / "none"), **(extra_env or {})}
    return subprocess.run([SH, str(repo / "scripts" / "daily_run.sh"), *args], capture_output=True, text=True, env=env)


def test_daily_run_scrapes_exports_checks_then_publishes(daily_repo):
    repo, log = daily_repo
    r = run_daily(repo, {"BEEROO_PAGES_REMOTE": "git@example:x/y.git", "BEEROO_ZONES_PER_RUN": "2"})
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert [c.split()[1].split("/")[-1] for c in calls if c.startswith("py")] == ["scheduled_scrape.py", "export_static.py", "check_freshness.py"]
    assert "--zones-per-run 2" in calls[0] and calls[-1] == "publish site"


def test_daily_run_without_a_remote_exports_but_does_not_publish(daily_repo):
    repo, log = daily_repo
    r = run_daily(repo)
    assert r.returncode == 0 and "not published" in r.stdout
    assert not any(c.startswith("publish") for c in log.read_text().splitlines())


def test_daily_run_still_exports_after_a_block_and_reports_it(daily_repo):
    repo, log = daily_repo
    r = run_daily(repo, {"FAKE_SCRAPE_CODE": "3", "BEEROO_PAGES_REMOTE": "x"})
    calls = log.read_text()
    assert r.returncode == 3                                     # blocked is still reported to launchd
    assert "export_static.py" in calls and "publish site" in calls   # but yesterday's data is still published


def test_daily_run_refuses_a_world_readable_env_file(daily_repo):
    repo, _ = daily_repo
    env_file = repo / "env"
    env_file.write_text("BEEROO_PAGES_REMOTE=x\n")
    env_file.chmod(0o644)
    r = run_daily(repo, {"BEEROO_ENV_FILE": str(env_file)})
    assert r.returncode == 1 and "chmod 600" in r.stderr


# ---- freshness --------------------------------------------------------------


def test_freshness_report(tmp_path):
    import check_freshness
    conn = db.connect(str(tmp_path / "f.sqlite3"))
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    rows = [("bws", "bws:6723", "ACT", "2026-10-09T00:00:00+00:00"),
            ("bws", "bws:9", "WA", "2026-10-01T00:00:00+00:00"),
            ("liquorland", "liquorland:ll_act", "ACT", "2026-10-02T00:00:00+00:00")]
    for i, (retailer, key, state, ts) in enumerate(rows):
        conn.execute("INSERT INTO locations (location_key, retailer, state) VALUES (?, ?, ?)", (key, retailer, state))
        conn.execute("INSERT INTO listings (retailer, retailer_sku, url, name, first_seen, last_seen) VALUES (?, ?, 'u', 'n', 'x', 'x')", (retailer, str(i)))
        conn.execute("INSERT INTO price_observations (listing_id, location_key, pack_type, units, member_only, price, observed_at) VALUES (?, ?, 'single', 1, 0, 5, ?)", (i + 1, key, ts))
    conn.commit()
    got = {(c, r): (s, a) for c, r, s, a in check_freshness.freshness(conn, now=now, max_age_days=3)}
    assert got[("Australian Capital Territory (ACT)", "bws")] == ("ok", 1.0)
    assert got[("Western Australia (WA)", "bws")][0] == "STALE"
    assert got[("Australian Capital Territory (ACT)", "liquorland")][0] == "STALE"
    assert got[("New South Wales (NSW)", "dan_murphys")] == ("MISSING", None)
    assert len(got) == 24                                                      # 8 states and territories x 3 retailers
    assert got[("Northern Territory (NT)", "dan_murphys")] == ("n/a", None)    # Dan Murphy's has no NT stores
    assert got[("Northern Territory (NT)", "bws")][0] == "MISSING"
