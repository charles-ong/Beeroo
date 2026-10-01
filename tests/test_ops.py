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


# ---- wrapper script ---------------------------------------------------------


@pytest.fixture()
def fake_repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "run_scrape.sh", tmp_path / "scripts" / "run_scrape.sh")
    py = tmp_path / ".venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text('#!/bin/sh\necho "args=$* token=${BEEROO_ADMIN_TOKEN:-unset}"\n')
    py.chmod(0o755)
    return tmp_path


def run_wrapper(repo, env_file, *args):
    env = {"PATH": os.environ["PATH"], "HOME": str(repo), "BEEROO_ENV_FILE": str(env_file)}
    return subprocess.run([SH, str(repo / "scripts" / "run_scrape.sh"), *args], capture_output=True, text=True, env=env)


def test_wrapper_loads_secrets_from_a_private_env_file(fake_repo):
    env_file = fake_repo / "env"
    env_file.write_text("BEEROO_ADMIN_TOKEN=s3cr3t\n")
    env_file.chmod(0o600)
    r = run_wrapper(fake_repo, env_file, "--push")
    assert r.returncode == 0 and "args=" in r.stdout
    assert r.stdout.strip().endswith("--push token=s3cr3t") or "token=s3cr3t" in r.stdout


def test_wrapper_refuses_a_group_or_world_readable_env_file(fake_repo):
    env_file = fake_repo / "env"
    env_file.write_text("BEEROO_ADMIN_TOKEN=s3cr3t\n")
    env_file.chmod(0o644)
    r = run_wrapper(fake_repo, env_file)
    assert r.returncode != 0 and "chmod 600" in r.stderr
    assert "s3cr3t" not in r.stdout + r.stderr


def test_wrapper_runs_without_an_env_file(fake_repo):
    r = run_wrapper(fake_repo, fake_repo / "missing")
    assert r.returncode == 0 and "token=unset" in r.stdout


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
    assert args[1].endswith("scripts/run_scrape.sh") and "--push" in args and "--jitter" in args
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
    code = ("import sys; sys.modules['playwright']=None; sys.modules['bs4']=None; "
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
    assert "COPY scripts/promote_contributions.py scripts/backup_db.py scripts/" in text


def test_dockerignore_keeps_data_and_secrets_out_of_the_image():
    lines = (ROOT / ".dockerignore").read_text().split()
    for must in (".venv", ".git", "data", "*.har", "*.sqlite3"):
        assert must in lines


def test_fly_config():
    cfg = tomllib.loads((ROOT / "fly.toml").read_text())
    assert cfg["http_service"]["internal_port"] == 8080 and cfg["http_service"]["force_https"] is True
    assert cfg["mounts"][0]["destination"] == "/data" and cfg["env"]["BEEROO_DB"].startswith("/data/")
    assert cfg["env"]["BEEROO_CONTRIB_ENABLED"] == "0"
    assert cfg["http_service"]["min_machines_running"] == 1
    assert cfg["http_service"]["checks"][0]["path"] == "/healthz"
    flat = (ROOT / "fly.toml").read_text()
    assert "TOKEN" not in flat.replace("BEEROO_ADMIN_TOKEN", "") and "SALT" not in flat


def test_workflows_never_inline_secrets():
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        text = wf.read_text()
        assert not re.search(r"token\s*[:=]\s*['\"]?[A-Za-z0-9]{20,}", text, re.I), wf.name
    assert "${{ secrets.BEEROO_ADMIN_TOKEN }}" in (ROOT / ".github" / "workflows" / "promote.yml").read_text()


# ---- push_captures ----------------------------------------------------------


def test_push_captures_builds_bodies_with_capture_time_and_location(tmp_path, capsys):
    import push_captures
    f = ROOT / "tests" / "fixtures" / "bws_2606_products.json"
    sent = []
    push_captures.main(
        ["bws", "--set-pickup", str(ROOT / "tests/fixtures/bws_2606_set_pickup.json"), str(f)],
        push=lambda b: sent.append(b) or {"products": 14, "new_observations": 50, "new_listings": 14},
    )
    (body,) = sent
    assert body["kind"] == "bws_products" and body["location"]["store_id"] == "6723"
    assert datetime.fromisoformat(body["observed_at"]).timestamp() == pytest.approx(f.stat().st_mtime, abs=1)
    assert "'new_listings': 14" in capsys.readouterr().out


def test_push_captures_validates_required_location_flags(tmp_path):
    import push_captures
    f = str(ROOT / "tests/fixtures/bws_2606_products.json")
    with pytest.raises(SystemExit, match="set-pickup"):
        push_captures.main(["bws", f], push=lambda b: {})
    with pytest.raises(SystemExit, match="store-id"):
        push_captures.main(["dan_murphys", f], push=lambda b: {})
    sent = []
    push_captures.main(["liquorland", str(ROOT / "tests/fixtures/liquorland_act_products.json")],
                       push=lambda b: sent.append(b) or {})
    assert sent[0]["location"] is None and sent[0]["kind"] == "liquorland_products"


def test_push_captures_needs_server_settings(monkeypatch):
    import push_captures
    monkeypatch.delenv("BEEROO_SERVER", raising=False)
    monkeypatch.delenv("BEEROO_ADMIN_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="BEEROO_SERVER"):
        push_captures.main(["liquorland", str(ROOT / "tests/fixtures/liquorland_act_products.json")])
