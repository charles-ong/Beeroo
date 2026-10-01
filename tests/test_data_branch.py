import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import data_branch as dbr  # noqa: E402
import ingest_inbox  # noqa: E402
import scheduled_scrape  # noqa: E402

from common import db  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    return str(bare)


def make_db(path, rows=1):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (n)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()


def branch_files(remote, tmp_path):
    out = tmp_path / "peek"
    assert dbr.clone(remote, out)
    return {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_first_restore_is_empty_then_save_and_restore_roundtrip(remote, tmp_path):
    assert dbr.restore(remote, tmp_path / "c1", tmp_path / "d1") is False
    assert not (tmp_path / "d1" / dbr.DB_NAME).exists()

    local = tmp_path / "local.sqlite3"
    make_db(local, rows=3)
    (tmp_path / "state.json").write_text('{"a": 1}')
    dbr.save(remote, tmp_path / "c1", local, tmp_path / "state.json")

    assert dbr.restore(remote, tmp_path / "c2", tmp_path / "d2") is True
    restored = sqlite3.connect(tmp_path / "d2" / dbr.DB_NAME)
    assert restored.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3
    assert json.loads((tmp_path / "d2" / dbr.STATE_NAME).read_text()) == {"a": 1}


def test_history_is_squashed_to_one_commit(remote, tmp_path):
    local = tmp_path / "l.sqlite3"
    make_db(local)
    for _ in range(3):
        dbr.restore(remote, tmp_path / "c", tmp_path / "d")
        dbr.save(remote, tmp_path / "c", local)
    count = subprocess.run(["git", "--git-dir", remote, "rev-list", "--count", dbr.BRANCH],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert count == "1"


def test_inbox_pages_pushed_while_a_run_is_in_flight_survive_its_save(remote, tmp_path):
    local = tmp_path / "l.sqlite3"
    make_db(local)
    dbr.restore(remote, tmp_path / "cloud", tmp_path / "d")
    dbr.save(remote, tmp_path / "cloud", local)

    dbr.restore(remote, tmp_path / "cloud", tmp_path / "d")          # the cloud run starts
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "20261001T030000-dan_murphys-001.json").write_text("{}")
    assert dbr.push_inbox(remote, outbox, "dan_murphys") == 1       # the Mac pushes meanwhile
    assert list(outbox.glob("*.json")) == []                         # deleted only after a successful push

    dbr.save(remote, tmp_path / "cloud", local)                      # first attempt loses the lease
    assert "inbox/dan_murphys/20261001T030000-dan_murphys-001.json" in branch_files(remote, tmp_path)


def test_ingested_inbox_pages_are_removed_from_the_branch(remote, tmp_path):
    local = tmp_path / "l.sqlite3"
    make_db(local)
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "a.json").write_text("{}")
    dbr.push_inbox(remote, outbox, "dan_murphys")
    dbr.restore(remote, tmp_path / "cloud", tmp_path / "d")
    (tmp_path / "cloud" / "inbox" / "dan_murphys" / "a.json").unlink()      # what ingest_inbox does
    dbr.save(remote, tmp_path / "cloud", local)
    assert not any(f.startswith("inbox/") for f in branch_files(remote, tmp_path))


def test_seed_refuses_to_overwrite_an_existing_database(remote, tmp_path):
    local = tmp_path / "l.sqlite3"
    make_db(local)
    dbr.seed(remote, local)
    with pytest.raises(dbr.DataBranchError):
        dbr.seed(remote, local)


def test_seed_replace_overwrites_on_request(remote, tmp_path):
    first, second = tmp_path / "a.sqlite3", tmp_path / "b.sqlite3"
    make_db(first, rows=1)
    make_db(second, rows=5)
    dbr.seed(remote, first)
    dbr.seed(remote, second, replace=True)
    dbr.restore(remote, tmp_path / "c", tmp_path / "d")
    assert sqlite3.connect(tmp_path / "d" / dbr.DB_NAME).execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5


def test_push_failure_keeps_local_files(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "a.json").write_text("{}")
    with pytest.raises(dbr.DataBranchError):
        dbr.push_inbox(str(tmp_path / "does-not-exist.git"), outbox, "dan_murphys", attempts=1)
    assert (outbox / "a.json").exists()


def test_errors_never_show_the_remote(tmp_path):
    secret = "https://x-access-token:SECRETTOKEN@invalid.invalid/none.git"
    with pytest.raises(dbr.DataBranchError) as e:
        dbr.git(["ls-remote", secret], cwd=tmp_path, remote=secret)
    assert "SECRETTOKEN" not in str(e.value)


def test_main_needs_the_remote(monkeypatch, capsys):
    monkeypatch.delenv("BEEROO_DATA_REMOTE", raising=False)
    assert dbr.main(["restore"]) == 1
    assert "BEEROO_DATA_REMOTE" in capsys.readouterr().err


def test_corrupt_database_is_not_saved(remote, tmp_path):
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"not a database" * 100)
    dbr.restore(remote, tmp_path / "c", tmp_path / "d")
    with pytest.raises(Exception):
        dbr.save(remote, tmp_path / "c", bad)


# ---- emit + ingest_inbox ------------------------------------------------------

DM_BODY = {
    "kind": "dan_murphys_browse",
    "location": {"store_id": "1546", "store_name": "Thornleigh", "suburb": "Thornleigh",
                 "state": "NSW", "postcode": "2120"},
    "payload": json.loads((FIXTURES / "dan_murphys_browse_page1.json").read_text()),
    "observed_at": datetime.now(timezone.utc).isoformat(),
}


def test_emit_push_writes_validated_bodies(tmp_path):
    push = scheduled_scrape.emit_push(tmp_path / "out")
    assert push(DM_BODY)["new_observations"] == 0
    [written] = list((tmp_path / "out").glob("*.json"))
    assert json.loads(written.read_text())["kind"] == "dan_murphys_browse"
    assert not list((tmp_path / "out").glob("*.tmp"))

    with pytest.raises(Exception):
        push({"kind": "nonsense", "payload": {}})


def test_ingest_inbox_rejects_bad_pages_without_stopping(tmp_path):
    inbox = tmp_path / "inbox" / "dan_murphys"
    inbox.mkdir(parents=True)
    (inbox / "1.json").write_text("not json")
    (inbox / "2.json").write_text(json.dumps({"kind": "dan_murphys_browse", "payload": {}}))
    dbfile = tmp_path / "x.sqlite3"
    db.connect(str(dbfile)).close()

    done, rejected = ingest_inbox.ingest_inbox(tmp_path / "inbox", dbfile)
    assert done == 0 and len(rejected) == 2
    assert len(list((tmp_path / "inbox").rglob("rejected/*.json"))) == 2


def test_ingest_inbox_ingests_a_real_page_and_deletes_it(tmp_path):
    inbox = tmp_path / "inbox"
    scheduled_scrape.emit_push(inbox / "dan_murphys")(DM_BODY)
    dbfile = tmp_path / "x.sqlite3"
    db.connect(str(dbfile)).close()

    done, rejected = ingest_inbox.ingest_inbox(inbox, dbfile)
    assert (done, rejected) == (1, [])
    assert list(inbox.rglob("*.json")) == []
    conn = db.connect(str(dbfile))
    assert conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 24
    assert conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] > 0


def test_an_unreachable_remote_is_an_error_not_an_empty_branch(tmp_path):
    with pytest.raises(dbr.DataBranchError):
        dbr.clone(str(tmp_path / "nothing-here.git"), tmp_path / "c")
