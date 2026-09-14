#!/usr/bin/env python3
"""Group the dated photos of one folder into one subfolder per visit.

Files named by organize-media.py (YYYY-MM-DD_HHh-MMm-SSs.ext or
YYYY-MM-DD_date-only_NN.ext) lying directly in FOLDER move to YYYY-MM-DD for a
one-day visit or YYYY-MM-DD_TO_YYYY-MM-DD for consecutive days, each together
with its .xmp sidecar. A day inside an existing dated subfolder joins it. Nothing
is ever overwritten: a file whose name, or whose sidecar's name, already exists
in the target stays where it is and is reported.

Works on the server's disk, on a Samba share mounted on Linux or macOS, and on a
Windows share path or mapped drive.

  group-by-date.py FOLDER            show what would move
  group-by-date.py FOLDER --apply    move

Exit status: 0 done, 1 bad arguments, 2 some files were left in place.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ORGANIZED_NAME_RE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})_(?:\d{2}h-\d{2}m-\d{2}s|date-only_\d{2})(?:_\d{2})?(?:_v\d{2})?\.[A-Za-z0-9]+$")
VISIT_FOLDER_RE = re.compile(
    r"^(?P<first>\d{4}-\d{2}-\d{2})(?:[_-]TO[_-](?P<last>\d{4}-\d{2}-\d{2}))?(?:_.*)?$", re.IGNORECASE)


@dataclass
class Unit:
    """A photo or video with the sidecars that must travel with it."""
    day: dt.date
    files: list[Path] = field(default_factory=list)


def parse_day(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def existing_visits(folder: Path) -> list[tuple[dt.date, dt.date, Path]]:
    visits = []
    for child in sorted(folder.iterdir()):
        match = VISIT_FOLDER_RE.match(child.name)
        if not match or child.is_symlink() or not child.is_dir():
            continue
        first = parse_day(match["first"])
        last = parse_day(match["last"]) or first
        if first and last and first <= last:
            visits.append((first, last, child))
    return visits


def collect_units(folder: Path) -> tuple[list[Unit], list[str]]:
    entries = sorted(child for child in folder.iterdir() if not child.is_symlink() and child.is_file())
    sidecars: dict[str, list[Path]] = defaultdict(list)
    for entry in entries:
        if entry.name.lower().endswith(".xmp"):
            sidecars[entry.name[:-4].casefold()].append(entry)
    units, left = [], []
    for entry in entries:
        if entry.name.lower().endswith(".xmp") or entry.name.startswith("."):
            continue
        match = ORGANIZED_NAME_RE.match(entry.name)
        day = parse_day(match["day"]) if match else None
        if day:
            units.append(Unit(day, [entry, *sidecars.get(entry.name.casefold(), [])]))
        else:
            left.append(entry.name)
    return units, left


def plan_targets(folder: Path, days: list[dt.date], gap_days: int) -> dict[dt.date, Path]:
    visits = existing_visits(folder)
    targets: dict[dt.date, Path] = {}
    loose: list[dt.date] = []
    for day in days:
        covering = next((path for first, last, path in visits if first <= day <= last), None)
        if covering:
            targets[day] = covering
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


_WARNED_CHECKED_RENAME = False


def move_noreplace(source: Path, target: Path) -> None:
    """Moves source to target, failing with FileExistsError rather than replacing."""
    global _WARNED_CHECKED_RENAME
    if os.name == "nt":
        # Windows refuses to rename onto an existing name, shares included.
        os.rename(source, target)
        return
    if sys.platform.startswith("linux"):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            result = renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
            if result == 0:
                return
            number = ctypes.get_errno()
            if number == errno.EEXIST:
                raise FileExistsError(number, os.strerror(number), str(target))
            if number not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
                raise OSError(number, os.strerror(number), str(target))
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS, errno.EACCES):
            raise
    else:
        try:
            os.unlink(source)
        except OSError:
            os.unlink(target)
            raise
        return
    # Some network file systems offer neither of the atomic methods above.
    if not _WARNED_CHECKED_RENAME:
        print("  warning: this file system has no atomic no-replace move; do not add files to this folder "
              "from elsewhere while it runs", file=sys.stderr)
        _WARNED_CHECKED_RENAME = True
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(target))
    os.rename(source, target)


def move_unit(unit: Unit, target_dir: Path) -> str | None:
    """Moves a photo and its sidecars together; returns why it stayed, or None."""
    clash = next((file.name for file in unit.files if os.path.lexists(target_dir / file.name)), None)
    if clash:
        return f"{clash} already exists in {target_dir.name}/"
    moved: list[Path] = []
    try:
        for file in unit.files:
            move_noreplace(file, target_dir / file.name)
            moved.append(file)
    except OSError as error:
        for file in reversed(moved):
            try:
                move_noreplace(target_dir / file.name, file)
            except OSError:
                return f"{error.strerror or error}; {file.name} could not be moved back from {target_dir.name}/"
        return f"{error.strerror or error} ({unit.files[0].name})"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--apply", action="store_true", help="move the files; without it nothing changes")
    parser.add_argument("--gap-days", type=int, default=1,
                        help="most days between two photos of the same visit (default 1: consecutive days)")
    args = parser.parse_args(argv)
    if args.gap_days < 1:
        parser.error("--gap-days must be at least 1")

    folder = Path(args.folder).expanduser().absolute()
    if not folder.is_dir():
        print(f"not a folder: {folder}", file=sys.stderr)
        return 1

    units, left = collect_units(folder)
    targets = plan_targets(folder, sorted({unit.day for unit in units}), args.gap_days)
    by_target: dict[Path, list[Unit]] = defaultdict(list)
    for unit in units:
        by_target[targets[unit.day]].append(unit)

    print(("Moving" if args.apply else "Would move") + f" in {folder}:")
    if not by_target:
        print("  no files named by capture time lie directly in this folder")
    problems: list[str] = []
    moved = 0
    for target, target_units in sorted(by_target.items()):
        files = sum(len(unit.files) for unit in target_units)
        if os.path.lexists(target) and not target.is_dir():
            problems.append(f"{target.name} exists but is not a folder; its {files} files stay")
            continue
        state = "existing folder" if target.exists() else "new folder"
        print(f"  {target.name + '/':36} {files:5} files  ({state})")
        if not args.apply:
            problems += [f"{clash}" for unit in target_units if target.is_dir()
                         for clash in [next((f.name for f in unit.files if os.path.lexists(target / f.name)), None)]
                         if clash]
            continue
        target.mkdir(exist_ok=True)
        for unit in target_units:
            reason = move_unit(unit, target)
            if reason:
                problems.append(reason)
            else:
                moved += len(unit.files)

    if left:
        print(f"  {len(left)} files without a capture-time name stay in place, e.g. {left[0]}")
    if args.apply:
        print(f"\nMoved {moved} files.")
    for problem in problems:
        print(("  not moved: " if args.apply else "  would clash, stays: ") + problem)
    if not args.apply:
        print("\nNothing changed. Add --apply to move.")
    return 2 if args.apply and problems else 0


if __name__ == "__main__":
    sys.exit(main())
