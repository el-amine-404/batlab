#!/usr/bin/env python3
"""Group the dated files of one folder into one subfolder per visit.

Files named by organize-media.py (YYYY-MM-DD_...) directly inside FOLDER move to
YYYY-MM-DD for a one-day visit or YYYY-MM-DD_TO_YYYY-MM-DD for consecutive days,
together with their .xmp sidecars. A day that falls inside an existing dated
subfolder joins it. Nothing is ever overwritten: a name that already exists in
the target stays where it is and is reported.

  group-by-date.py FOLDER            show what would move
  group-by-date.py FOLDER --apply    move
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

DATED_NAME_RE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_")
VISIT_FOLDER_RE = re.compile(r"^(?P<first>\d{4}-\d{2}-\d{2})(?:[_-]TO[_-](?P<last>\d{4}-\d{2}-\d{2}))?(?:_.*)?$", re.IGNORECASE)
SIDECAR_SUFFIXES = (".xmp", ".XMP")


def parse_day(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def existing_visits(folder: Path) -> list[tuple[dt.date, dt.date, Path]]:
    visits = []
    for child in folder.iterdir():
        match = VISIT_FOLDER_RE.match(child.name) if child.is_dir() else None
        if not match:
            continue
        first = parse_day(match["first"])
        last = parse_day(match["last"]) if match["last"] else first
        if first and last and first <= last:
            visits.append((first, last, child))
    return visits


def media_by_day(folder: Path) -> tuple[dict[dt.date, list[Path]], list[Path]]:
    by_day: dict[dt.date, list[Path]] = defaultdict(list)
    undated = []
    for child in sorted(folder.iterdir()):
        if not child.is_file() or child.name.startswith(".") or child.name.endswith(SIDECAR_SUFFIXES):
            continue
        match = DATED_NAME_RE.match(child.name)
        day = parse_day(match["day"]) if match else None
        if day:
            by_day[day].append(child)
        else:
            undated.append(child)
    return by_day, undated


def plan_targets(folder: Path, days: list[dt.date], gap_days: int) -> dict[dt.date, Path]:
    visits = existing_visits(folder)
    targets: dict[dt.date, Path] = {}
    loose = []
    for day in days:
        joined = next((path for first, last, path in visits if first <= day <= last), None)
        if joined:
            targets[day] = joined
        else:
            loose.append(day)
    runs: list[list[dt.date]] = []
    for day in loose:
        if runs and (day - runs[-1][-1]).days <= gap_days:
            runs[-1].append(day)
        else:
            runs.append([day])
    for run in runs:
        name = run[0].isoformat() if len(run) == 1 else f"{run[0].isoformat()}_TO_{run[-1].isoformat()}"
        for day in run:
            targets[day] = folder / name
    return targets


# A hard link fails with EEXIST instead of replacing, which a plain rename would do.
def move_noreplace(source: Path, target: Path) -> None:
    os.link(source, target, follow_symlinks=False)
    os.unlink(source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--apply", action="store_true", help="move the files; without it nothing changes")
    parser.add_argument("--gap-days", type=int, default=1,
                        help="days apart that still count as one visit (default 1: consecutive days)")
    args = parser.parse_args(argv)

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"not a folder: {folder}", file=sys.stderr)
        return 1

    by_day, undated = media_by_day(folder)
    targets = plan_targets(folder, sorted(by_day), max(args.gap_days, 1))
    moves: dict[Path, list[Path]] = defaultdict(list)
    for day, files in by_day.items():
        for file in files:
            moves[targets[day]].append(file)
            moves[targets[day]] += [file.with_name(file.name + suffix) for suffix in SIDECAR_SUFFIXES
                                    if file.with_name(file.name + suffix).exists()]

    if not moves:
        print(f"nothing to group in {folder}")
        return 0
    print(("Moving" if args.apply else "Would move") + f" in {folder}:")
    clashes = []
    moved = 0
    for target, files in sorted(moves.items()):
        state = "existing" if target.exists() else "new"
        print(f"  {target.name + '/':36} {len(files):5} files  ({state} folder)")
        if not args.apply:
            continue
        target.mkdir(exist_ok=True)
        for file in files:
            try:
                move_noreplace(file, target / file.name)
                moved += 1
            except FileExistsError:
                clashes.append(file)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    raise
                clashes.append(file)
    for file in undated:
        print(f"  left in place, name has no date: {file.name}")
    if not args.apply:
        print("\nNothing changed. Add --apply to move.")
        return 0
    print(f"\nMoved {moved} files.")
    for file in clashes:
        print(f"  not moved, {file.name} already exists in the target: compare the two")
    return 2 if clashes else 0


if __name__ == "__main__":
    sys.exit(main())
