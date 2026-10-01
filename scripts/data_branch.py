"""Keep Beeroo's database and hand-offs on a plain git branch called `data`.

Why: a GitHub Actions runner starts empty every day, so the price history has to
live somewhere free between runs. A branch in the same repo is free and needs no
extra service. The branch holds:

    beeroo.sqlite3              the price history (single commit, history squashed)
    scrape_state_cloud.json     the cloud scraper's backoff/progress state
    inbox/<retailer>/*.json     pages scraped elsewhere (your Mac, for Dan Murphy's),
                                waiting to be ingested by the next cloud run

Commands (BEEROO_DATA_REMOTE is a git URL; it is never printed):

    data_branch.py restore --clone data-branch --into data
        clone the branch and copy the DB + state into ./data (empty start if none yet)
    data_branch.py save --clone data-branch --db data/beeroo.sqlite3 --state data/scrape_state_cloud.json
        replace the branch with one commit holding the DB, state and any still-unprocessed inbox files
    data_branch.py push-inbox --from data/outbox --retailer dan_murphys
        add scraped pages to the inbox (what the Mac does); deletes them locally once pushed

Seed the branch once with your existing local history:
    BEEROO_DATA_REMOTE=git@github.com:you/beeroo.git python scripts/data_branch.py seed --db data/beeroo.sqlite3
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BRANCH = "data"
DB_NAME = "beeroo.sqlite3"
STATE_NAME = "scrape_state_cloud.json"
IDENTITY = ["-c", "user.name=beeroo-bot", "-c", "user.email=beeroo-bot@users.noreply.github.com",
            "-c", "commit.gpgsign=false"]


class DataBranchError(RuntimeError):
    pass


def git(args, cwd, remote=None, check=True):
    proc = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode:
        err = (proc.stderr or proc.stdout).strip()
        if remote:
            err = err.replace(remote, "<remote>")
        raise DataBranchError(f"git {args[0]} failed: {err}")
    return proc


def remote_from_env():
    remote = os.environ.get("BEEROO_DATA_REMOTE", "").strip()
    if not remote:
        raise DataBranchError("set BEEROO_DATA_REMOTE to the git URL of your repo")
    return remote


def clone(remote, dest):
    """Shallow clone of the data branch. Returns True if the branch existed;
    otherwise leaves an empty repo ready for a first (orphan) commit."""
    dest = Path(dest)
    shutil.rmtree(dest, ignore_errors=True)
    proc = git(["clone", "--quiet", "--depth", "1", "--branch", BRANCH, "--single-branch", remote, str(dest)],
               cwd=dest.parent, remote=remote, check=False)
    if proc.returncode == 0:
        return True
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    git(["init", "--quiet", "-b", BRANCH], cwd=dest)
    git(["remote", "add", "origin", remote], cwd=dest)
    return False


def head_files(repo, prefix):
    proc = git(["ls-tree", "-r", "--name-only", "HEAD", prefix], cwd=repo, check=False)
    return set(proc.stdout.split()) if proc.returncode == 0 else set()


def snapshot_db(src, dest):
    """Consistent copy (also folds in the WAL), then verify it."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    a = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    b = sqlite3.connect(dest)
    try:
        a.backup(b)
        ok = b.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        b.close()
        a.close()
    if ok != "ok":
        dest.unlink(missing_ok=True)
        raise DataBranchError(f"database failed its integrity check: {ok}")
    # a rollback journal in WAL mode would only bloat the commit
    c = sqlite3.connect(dest)
    try:
        c.execute("PRAGMA journal_mode=DELETE")
    finally:
        c.close()


def restore(remote, clone_dir, into):
    existed = clone(remote, clone_dir)
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    for name in (DB_NAME, STATE_NAME):
        src = Path(clone_dir) / name
        if src.exists():
            shutil.copy2(src, into / name)
    return existed


def _commit_all(repo, message, remote, force_lease=None):
    git(["checkout", "--quiet", "--orphan", "new-data"], cwd=repo)
    git(["add", "-A"], cwd=repo)
    git(["commit", "--quiet", "-m", message], cwd=repo)
    lease = f"--force-with-lease={BRANCH}:{force_lease}" if force_lease else "--force"
    return git(["push", "--quiet", lease, "origin", f"new-data:{BRANCH}"], cwd=repo, remote=remote, check=False)


def save(remote, clone_dir, db_path, state_path=None, attempts=4):
    """Replace the branch with one commit: DB, state and the inbox files that
    were not ingested (anything the Mac pushed since we cloned is kept too)."""
    clone_dir = Path(clone_dir)
    tip = git(["rev-parse", "HEAD"], cwd=clone_dir, check=False)
    tip = tip.stdout.strip() if tip.returncode == 0 else None
    seen = head_files(clone_dir, "inbox") if tip else set()

    for attempt in range(attempts):
        work = Path(tempfile.mkdtemp(prefix="beeroo-data-"))
        try:
            tree = work / "tree"
            if attempt == 0:
                shutil.copytree(clone_dir, tree, ignore=shutil.ignore_patterns(".git"))
            else:  # lost a race with a push-inbox: start from the new tip, keep its new inbox files
                fresh = work / "fresh"
                clone(remote, fresh)
                tip = git(["rev-parse", "HEAD"], cwd=fresh, check=False).stdout.strip() or None
                shutil.copytree(clone_dir, tree, ignore=shutil.ignore_patterns(".git"))
                for rel in head_files(fresh, "inbox") - seen:
                    target = tree / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(fresh / rel, target)
                seen = seen | head_files(fresh, "inbox")

            snapshot_db(db_path, tree / DB_NAME)
            if state_path and Path(state_path).exists():
                shutil.copy2(state_path, tree / STATE_NAME)

            repo = work / "repo"
            repo.mkdir()
            git(["init", "--quiet", "-b", "tmp"], cwd=repo)
            git(["remote", "add", "origin", remote], cwd=repo)
            shutil.copytree(tree, repo, dirs_exist_ok=True)
            proc = _commit_all(repo, "Update Beeroo data", remote, force_lease=tip)
            if proc.returncode == 0:
                return True
        finally:
            shutil.rmtree(work, ignore_errors=True)

    raise DataBranchError("could not update the data branch (kept losing a race); try again")


def seed(remote, db_path, replace=False):
    work = Path(tempfile.mkdtemp(prefix="beeroo-seed-"))
    try:
        existed = clone(remote, work / "clone")
        if existed and (work / "clone" / DB_NAME).exists() and not replace:
            raise DataBranchError("the data branch already has a database; refusing to overwrite it "
                                  "(use --replace to replace it with this one)")
        save(remote, work / "clone", db_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def push_inbox(remote, source, retailer, attempts=4):
    """Add every *.json in `source` to inbox/<retailer>/ and push a normal
    commit. Local files are deleted only after the push succeeds."""
    source = Path(source)
    files = sorted(source.glob("*.json"))
    if not files:
        return 0

    for attempt in range(attempts):
        work = Path(tempfile.mkdtemp(prefix="beeroo-inbox-"))
        try:
            repo = work / "clone"
            existed = clone(remote, repo)
            target = repo / "inbox" / retailer
            target.mkdir(parents=True, exist_ok=True)
            for f in files:
                shutil.copy2(f, target / f.name)
            git(["add", "-A"], cwd=repo)
            git(["commit", "--quiet", "-m", f"Add {len(files)} {retailer} page(s)"], cwd=repo)
            proc = git(["push", "--quiet", "origin", f"HEAD:{BRANCH}"], cwd=repo, remote=remote, check=False)
            if proc.returncode == 0:
                for f in files:
                    f.unlink()
                return len(files)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    raise DataBranchError("could not push to the data branch; the files are kept in " + str(source))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--clone", default="data-branch")
    r.add_argument("--into", default="data")
    s = sub.add_parser("save")
    s.add_argument("--clone", default="data-branch")
    s.add_argument("--db", default="data/beeroo.sqlite3")
    s.add_argument("--state", default="data/scrape_state_cloud.json")
    d = sub.add_parser("seed")
    d.add_argument("--db", default="data/beeroo.sqlite3")
    d.add_argument("--replace", action="store_true", help="overwrite the database already on the branch")
    p = sub.add_parser("push-inbox")
    p.add_argument("--from", dest="source", default="data/outbox")
    p.add_argument("--retailer", default="dan_murphys")
    args = ap.parse_args(argv)

    try:
        remote = remote_from_env()
        if args.cmd == "restore":
            existed = restore(remote, args.clone, args.into)
            print("restored the data branch" if existed else "no data branch yet: starting empty")
        elif args.cmd == "save":
            save(remote, args.clone, args.db, args.state)
            print("data branch updated")
        elif args.cmd == "seed":
            seed(remote, args.db, args.replace)
            print("data branch created from", args.db)
        else:
            n = push_inbox(remote, args.source, args.retailer)
            print(f"pushed {n} file(s) to the inbox")
    except DataBranchError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
