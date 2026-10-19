#!/usr/bin/env python3
"""Consistent copies of every SQLite database in the Docker volumes, for the
snapshot (backup.sh runs it before restic).

restic reads a live database, its -wal and its -shm at different moments: a
write between them makes a copy that may not open. SQLite's backup API copies
a database as one transaction, so the copy is whole, whatever the app does
meanwhile. Each lands in database-dumps/sqlite/<its path in the volumes>.

A database is copied by a process running as its file's owner: a root process
opening it could leave a root-owned -wal or -shm behind, which the app (not
root) then can't open. Each copy is checked (quick_check) before it replaces
the last one; a failed copy leaves the last good one in place and fails the run.

    dump-sqlite.py [--volumes /mnt/docker-volumes] [--out <dumps>/sqlite]
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HEADER = b"SQLite format 3\x00"
NAMES = (".db", ".sqlite", ".sqlite3")
# What restic leaves out anyway (conf/excludes.txt): caches and logs.
SKIP_DIRS = {"cache", "Cache", "logs", "log", "database-dumps", ".sqlite-staging"}
SKIP_FILES = {"logs.db", "cache.db"}


def databases(volumes: Path):
    for folder, dirs, files in os.walk(volumes):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not os.path.islink(os.path.join(folder, d)))
        for name in sorted(files):
            path = Path(folder, name)
            if not name.endswith(NAMES) or name in SKIP_FILES or path.is_symlink():
                continue
            try:
                with open(path, "rb") as file:
                    if file.read(16) == HEADER:
                        yield path
            except OSError as exc:
                print(f"skipped {path}: {exc}", file=sys.stderr)


def copy(source: Path, target: Path) -> None:
    """The one transaction: run as the database's owner (see main). The copy
    is a plain file, not in WAL mode: it opens on its own."""
    db = sqlite3.connect(f"file:{source}?mode=rw", uri=True, timeout=60)
    out = sqlite3.connect(target)
    try:
        db.backup(out, pages=4096, sleep=0.05)
        out.execute("PRAGMA journal_mode=DELETE")
    finally:
        out.close()
        db.close()


def check(path: Path) -> None:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        found = db.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        db.close()
    if found != "ok":
        raise RuntimeError(f"quick_check: {found}")


def as_owner(source: Path, target: Path) -> None:
    owner = source.stat()
    command = [sys.executable, "-I", __file__, "--copy", str(source), str(target)]
    if os.geteuid() == 0 and owner.st_uid != 0:
        command = ["setpriv", f"--reuid={owner.st_uid}", f"--regid={owner.st_gid}", "--clear-groups", *command]
    elif os.geteuid() not in (0, owner.st_uid):
        raise PermissionError(f"owned by uid {owner.st_uid}: run as root")
    done = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    if done.returncode:
        raise RuntimeError((done.stderr.strip().splitlines() or ["the copy failed"])[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--volumes", type=Path, default=Path("/mnt/docker-volumes"))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--copy", nargs=2, type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.copy:
        copy(*args.copy)
        return 0

    volumes = args.volumes.resolve()
    out = (args.out or volumes / "database-dumps" / "sqlite").resolve()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(out, 0o700)
    # The copies are made here, on the same disk, then moved: the dumps' folder is root's only, and an
    # owner's process can go through this one (711) to its own folder in it, not list it.
    staging = volumes / ".sqlite-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o711)
    os.chmod(staging, 0o711)
    failed, made, kept = [], 0, set()
    for source in databases(volumes):
        relative = source.relative_to(volumes)
        target = out / relative
        kept.add(target)
        started = time.monotonic()
        # A folder only the owner's process and root can write: it holds the copy until it is checked.
        work = Path(tempfile.mkdtemp(dir=staging))
        try:
            if os.geteuid() == 0:
                os.chown(work, source.stat().st_uid, source.stat().st_gid)
            fresh = work / source.name
            as_owner(source, fresh)
            check(fresh)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(fresh, 0o600)
            os.replace(fresh, target)
            made += 1
            print(f"{relative}: {target.stat().st_size / 1e6:.1f} MB in {time.monotonic() - started:.1f} s")
        except Exception as exc:  # one database's failure doesn't stop the others
            failed.append(str(relative))
            print(f"{relative}: FAILED, the last copy stays: {exc}", file=sys.stderr)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    shutil.rmtree(staging, ignore_errors=True)

    # Copies of databases that are gone go too (the snapshots before keep them).
    for old in out.rglob("*"):
        if old.is_file() and old not in kept:
            old.unlink()
            print(f"{old.relative_to(out)}: removed, its database is gone")
    for folder in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()

    print(f"{made} SQLite databases copied into {out}" + (f"; {len(failed)} failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
