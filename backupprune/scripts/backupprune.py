#!/usr/bin/env python3
"""Remove dated backup folders once they are older than their retention.

Before a migration or an upgrade, a copy of the databases goes into a folder
named with the day: ~/pre-upgrade-20261017, ~/pre-subfeed-20261018,
/mnt/docker-volumes/jellyfin-backup-10.11.3-20260915. Once the new version
has run for a while nobody needs them, and nobody remembers to delete them.

Every night: each folder matching a pattern of conf/backups.txt whose name
dates it more than that pattern's days ago is removed. A folder without a
date in its name is never touched. One card to #updates per run that removed
something or could not.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib/batlab-common"))
from batlab_common import notify as apprise  # noqa: E402

DATE = re.compile(r"(20\d\d)(\d\d)(\d\d)(?!.*20\d{6})")   # the last YYYYMMDD in a name


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def rules(config: Path, variables: dict[str, str]) -> list[tuple[str, int]]:
    """(pattern, days) of each line, ~ and ${NAME} filled in."""
    found = []
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        pattern, days = line.rsplit(None, 1)
        pattern = re.sub(r"\$\{(\w+)\}", lambda match: variables[match.group(1)], pattern)
        found.append((os.path.expanduser(pattern), int(days)))
    return found


def dated(name: str) -> datetime.date | None:
    match = DATE.search(name)
    if not match:
        return None
    try:
        return datetime.date(*map(int, match.groups()))
    except ValueError:
        return None


def expired(paths: list[str], days: int, today: datetime.date) -> list[tuple[Path, datetime.date]]:
    """Folders among paths whose name dates them more than days before today."""
    found = []
    for path in map(Path, paths):
        day = dated(path.name)
        if day and (today - day).days > days and path.is_dir() and not path.is_symlink() \
                and not os.path.ismount(path):
            found.append((path, day))
    return found


def size(path: Path) -> int:
    result = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True)
    return int(result.stdout.split()[0]) if result.stdout else 0


def human(size_bytes: float) -> str:
    return f"{size_bytes / 1e9:.1f} GB" if size_bytes >= 1e9 else f"{size_bytes / 1e6:.0f} MB"


def card(removed: list[tuple[Path, datetime.date, int]], failed: list[str]) -> tuple[str, str]:
    total = sum(item[2] for item in removed)
    title = (f"Removed {len(removed)} old backup folder{'s' if len(removed) != 1 else ''} ({human(total)})"
             if removed else "Old backup folders could not be removed")
    fields = []
    if removed:
        fields.append(("Removed", "\n".join(f"{path}  {human(bytes_)}, from {day}" for path, day, bytes_ in removed)))
    if failed:
        fields.append(("Not removed", "\n".join(failed)))
    return title, apprise.fields_text(fields)


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="list what would go; remove and post nothing")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--config", default=str(repo / "backupprune/conf/backups.txt"))
    args = parser.parse_args(argv)
    env = Path(args.env_file)

    today = datetime.date.today()
    removed, failed = [], []
    for pattern, days in rules(Path(args.config), {"VOLUMES_ROOT": env_value(env, "VOLUMES_ROOT")}):
        for path, day in expired(sorted(glob.glob(pattern)), days, today):
            bytes_ = size(path)
            print(f"{path}: from {day}, kept {days} days, {human(bytes_)}")
            if args.dry_run:
                continue
            try:
                shutil.rmtree(path)
                removed.append((path, day, bytes_))
            except OSError as error:
                failed.append(f"{path}: {error.strerror or error}")

    if removed or failed:
        title, body = card(removed, failed)
        print(title + "\n" + body)
        webhook = env_value(env, "DISCORD_WEBHOOK_UPDATES")
        try:
            apprise.deliver("backupprune", "updates", title, body, "failure" if failed else "success",
                            fallback=webhook if webhook.startswith("http") else None)
        except apprise.NotSent as error:
            print(f"message not sent: {error}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
