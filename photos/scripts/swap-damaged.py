#!/usr/bin/env python3
"""Replace damaged files in the photo library with repaired copies, and keep every damaged original
outside the library, listed in one manifest.

Immich imports only the library folder, so a damaged file kept beside its replacement would be indexed
as a second asset and flagged as a duplicate. Instead the damaged original goes to a sibling folder
that nothing indexes, under DAMAGED_ROOT/BATCH/<same relative path>, and DAMAGED_ROOT/manifest.json
records each file: what was wrong, its size, date and SHA-256, its sidecar, and what replaced it.

A replacement keeps the exact path and name of the file it replaces, so Immich keeps the asset (its
albums and faces) instead of seeing a deletion plus a new file. Before a file is touched, the damaged
original is copied out and its copy is read back and compared by SHA-256. The replacement is then written
beside the original under a name Immich ignores (NAME.part) and moved into place in one step. An
unrecoverable file (no replacement) is moved out with its sidecar.

The plan is a JSON file that names your media, so it lives outside the repository:

  {
    "library_root": "/mnt/photos/library",
    "damaged_root": "/mnt/photos/damaged",
    "batch": "2026-09-20_bad-copy-sessions",
    "mtime": "now",
    "entries": [
      {"path": "THEME/2019/clip.mp4", "action": "replace", "replacement": "repaired/clip.mp4",
       "reason": "damaged during a copy; 4 s of picture data removed",
       "expect": {"size": 1234567, "mtime_ns": 1700000000000000000},
       "details": {"anything": "you want recorded"}},
      {"path": "THEME/2015/other.mp4", "action": "quarantine", "reason": "unrecoverable"}
    ]
  }

"mtime" is "now" (default) or "keep". With "now" a replaced file gets a new modification date, which is
how Immich notices that an external file changed; with "keep" it keeps the replacement's own date, and
Immich then needs "Scan all library files". "expect" is optional: a file whose size or date no longer
matches was changed since it was examined and is left alone. Relative replacement paths are read
relative to the plan file. A file's sidecar (NAME.xmp or STEM.xmp) is copied out for a replacement and
moved for a quarantine.

  swap-damaged.py PLAN               show what would happen
  swap-damaged.py PLAN --apply       do it (the library must be mounted read-write)
  swap-damaged.py PLAN --verify      re-read everything and compare with the manifest
  swap-damaged.py PLAN --rollback    show how the batch would be undone; add --apply to undo it

Safe to interrupt and run again: finished files are skipped and half-finished ones resume.

Exit status: 0 done, 1 bad plan or arguments, 2 some files were left unchanged or failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
MANIFEST_NAME = "manifest.json"
PART_SUFFIX = ".part"
BATCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHUNK = 1 << 20
ACTIONS = ("replace", "quarantine")
DONE_STATUSES = ("replaced", "quarantined")


class PlanError(ValueError):
    """The plan cannot be used at all."""


class SwapError(RuntimeError):
    """One file could not be handled; the others carry on."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def iso(nanoseconds: int) -> str:
    return datetime.fromtimestamp(nanoseconds / 1e9, timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: object) -> None:
    """Replace a JSON file in one step, so an interruption never leaves half a manifest."""
    temporary = path.with_name(path.name + PART_SUFFIX)
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def resolved(value: object) -> Path:
    """A configured folder as an absolute path with symbolic links resolved; empty when it is not set."""
    return Path(os.path.realpath(value)) if isinstance(value, str) and value.strip() else Path()


def inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def same_device(source: Path, destination_directory: Path) -> bool:
    return os.stat(source).st_dev == os.stat(destination_directory).st_dev


def find_sidecars(path: Path) -> list[Path]:
    """The .xmp files that belong to a photo or video: NAME.ext.xmp and NAME.xmp."""
    found: list[Path] = []
    for candidate in (path.with_name(path.name + ".xmp"), path.with_suffix(".xmp"),
                      path.with_name(path.name + ".XMP"), path.with_suffix(".XMP")):
        if not candidate.is_file() or candidate.is_symlink():
            continue
        if any(os.path.samefile(candidate, known) for known in found):  # case-insensitive shares list one file twice
            continue
        found.append(candidate)
    return found


@dataclass
class Entry:
    path: str
    action: str
    reason: str
    replacement: Path | None = None
    expect: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)


@dataclass
class Plan:
    file: Path
    library: Path
    damaged: Path
    batch: str
    mtime: str
    entries: list[Entry]

    @property
    def batch_root(self) -> Path:
        return self.damaged / self.batch

    def target(self, entry: Entry) -> Path:
        return self.library / entry.path

    def copy_of(self, entry: Entry) -> Path:
        return self.batch_root / entry.path


def load_plan(file: Path) -> Plan:
    try:
        data = json.loads(Path(file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PlanError(f"cannot read the plan {file}: {error}") from error
    if not isinstance(data, dict):
        raise PlanError("the plan must be a JSON object")
    problems: list[str] = []
    for key in ("library_root", "damaged_root", "batch"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            problems.append(f'"{key}" is missing')
    entries_raw = data.get("entries")
    if not isinstance(entries_raw, list) or not entries_raw:
        problems.append('"entries" must be a non-empty list')
        entries_raw = []
    library, damaged = resolved(data.get("library_root")), resolved(data.get("damaged_root"))
    batch = data["batch"] if isinstance(data.get("batch"), str) else ""
    mtime = data.get("mtime", "now")
    if data.get("library_root") and not library.is_dir():
        problems.append(f"the library folder does not exist: {library}")
    if batch and not BATCH_RE.match(batch):
        problems.append('"batch" may contain only letters, digits, dot, dash and underscore')
    if mtime not in ("now", "keep"):
        problems.append('"mtime" must be "now" or "keep"')
    if data.get("library_root") and data.get("damaged_root") and (inside(damaged, library) or inside(library, damaged)):
        problems.append("the damaged folder must be outside the library, or Immich and the duplicate tools would see "
                        f"the damaged files ({damaged} and {library} overlap)")
    plan_dir = Path(file).resolve().parent
    entries: list[Entry] = []
    seen: set[str] = set()
    for number, raw in enumerate(entries_raw, 1):
        label = f"entry {number}"
        if not isinstance(raw, dict):
            problems.append(f"{label} is not an object")
            continue
        relative = raw.get("path")
        parts = Path(relative).parts if isinstance(relative, str) else ()
        if not relative or Path(relative).is_absolute() or ".." in parts or not parts:
            problems.append(f'{label}: "path" must be a relative path inside the library ({relative!r})')
            continue
        label = f"{relative}"
        if relative in seen:
            problems.append(f"{label}: listed twice")
        seen.add(relative)
        action = raw.get("action")
        if action not in ACTIONS:
            problems.append(f'{label}: "action" must be one of {", ".join(ACTIONS)}')
            continue
        if not isinstance(raw.get("reason"), str) or not raw["reason"].strip():
            problems.append(f'{label}: a "reason" is required, it is what the manifest tells you later')
        replacement = None
        if action == "replace":
            if not isinstance(raw.get("replacement"), str) or not raw["replacement"]:
                problems.append(f'{label}: "replace" needs a "replacement" file')
            else:
                replacement = Path(raw["replacement"])
                if not replacement.is_absolute():
                    replacement = plan_dir / replacement
                replacement = Path(os.path.abspath(replacement))
        elif raw.get("replacement"):
            problems.append(f'{label}: "quarantine" takes no "replacement"')
        expect = raw.get("expect") or {}
        if not isinstance(expect, dict) or any(not isinstance(v, int) for v in expect.values()):
            problems.append(f'{label}: "expect" must hold whole numbers (size, mtime_ns)')
            expect = {}
        details = raw.get("details") or {}
        if not isinstance(details, dict):
            problems.append(f'{label}: "details" must be an object')
            details = {}
        entries.append(Entry(relative, action, str(raw.get("reason", "")).strip(), replacement, expect, details))
    if problems:
        raise PlanError("\n".join(problems))
    return Plan(Path(file), library, damaged, batch, mtime, entries)


# --------------------------------------------------------------------------- manifest

def load_manifest(plan: Plan) -> dict:
    path = plan.damaged / MANIFEST_NAME
    if not path.exists():
        return {"schema": VERSION, "library_root": str(plan.library), "files": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != VERSION or not isinstance(data.get("files"), list):
            raise ValueError("unexpected layout")
    except (OSError, ValueError) as error:
        raise PlanError(f"cannot read {path} ({error}); move it aside only if you are sure it can be rebuilt") from error
    return data


def record_of(manifest: dict, plan: Plan, entry: Entry) -> dict | None:
    for record in manifest["files"]:
        if record.get("batch") == plan.batch and record.get("path") == entry.path:
            return record
    return None


def save_record(manifest: dict, plan: Plan, record: dict) -> None:
    files = [r for r in manifest["files"] if not (r.get("batch") == record["batch"] and r.get("path") == record["path"])]
    files.append(record)
    manifest["files"] = sorted(files, key=lambda r: (r["batch"], r["path"]))
    manifest["updated"] = now_iso()
    manifest["tool"] = f"swap-damaged.py schema {VERSION}"
    plan.damaged.mkdir(parents=True, exist_ok=True)
    write_json(plan.damaged / MANIFEST_NAME, manifest)


# --------------------------------------------------------------------------- file operations

def copy_verified(source: Path, destination: Path, expected: str, *, keep_times: bool) -> None:
    """Copy through NAME.part, compare the copy's SHA-256 with the source's, then put it in place."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + PART_SUFFIX)
    shutil.copyfile(source, part)
    if keep_times:
        shutil.copystat(source, part)
    if sha256(part) != expected:
        part.unlink(missing_ok=True)
        raise SwapError(f"the copy of {source.name} does not match the original")
    os.replace(part, destination)


def move_verified(source: Path, destination: Path, expected: str) -> None:
    """Move a file. On the same disk that is a rename; otherwise copy, verify, then remove the source."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if same_device(source, destination.parent):
        os.replace(source, destination)
        return
    copy_verified(source, destination, expected, keep_times=True)
    source.unlink()


def describe_sidecar(path: Path, where: str) -> dict:
    return {"name": path.name, "size": path.stat().st_size, "sha256": sha256(path), where: True}


# --------------------------------------------------------------------------- preflight

@dataclass
class Item:
    entry: Entry
    target: Path
    copy: Path
    state: str = "pending"          # pending | done | blocked
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def library_problem(plan: Plan, target: Path) -> str | None:
    if target.is_symlink():
        return "the library file is a symbolic link"
    if target.exists() and not target.is_file():
        return "the library path is not a regular file"
    if not inside(Path(os.path.realpath(target)), plan.library):
        return "the file resolves to a place outside the library"
    return None


def preflight(plan: Plan, manifest: dict, ignore_expect: bool) -> list[Item]:
    items = []
    for entry in plan.entries:
        item = Item(entry, plan.target(entry), plan.copy_of(entry))
        items.append(item)
        bad = library_problem(plan, item.target)
        if bad:
            item.state, item.problems = "blocked", [bad]
            continue
        record = record_of(manifest, plan, entry)
        if record and record.get("status") in DONE_STATUSES and item.copy.is_file() and \
                (entry.action == "quarantine") == (record["status"] == "quarantined"):
            done = item.target.is_file() if entry.action == "replace" else not item.target.exists()
            if done:
                item.state = "done"
                item.notes.append(f"already {record['status']} on {record.get('processed_at', '?')}")
                continue
        if entry.action == "replace":
            if entry.replacement is None or not entry.replacement.is_file():
                item.problems.append(f"the replacement file is missing: {entry.replacement}")
            elif entry.replacement.stat().st_size == 0:
                item.problems.append("the replacement file is empty")
        if not item.target.exists():
            if item.copy.is_file() and entry.action == "quarantine":
                item.notes.append("the library file is gone and a damaged copy exists: will be recorded")
            else:
                item.problems.append("the file is not in the library")
        elif entry.expect and not ignore_expect:
            stat = item.target.stat()
            if "size" in entry.expect and entry.expect["size"] != stat.st_size and not _is_replacement(item):
                item.problems.append(f"the size changed since it was examined ({entry.expect['size']} -> {stat.st_size})")
            if "mtime_ns" in entry.expect and entry.expect["mtime_ns"] != stat.st_mtime_ns and not _is_replacement(item):
                item.problems.append("the modification date changed since it was examined")
        if item.copy.exists() and not (record and record.get("status") in DONE_STATUSES):
            item.notes.append("a damaged copy already exists at the destination: it will be compared and reused")
        if item.problems:
            item.state = "blocked"
    return items


def _is_replacement(item: Item) -> bool:
    """A file that already is the replacement (an earlier run was interrupted) has legitimately changed."""
    replacement = item.entry.replacement
    return bool(replacement and replacement.is_file() and item.target.is_file()
                and item.target.stat().st_size == replacement.stat().st_size)


def free_space_problems(plan: Plan, items: list[Item]) -> list[str]:
    pending = [i for i in items if i.state == "pending" and i.target.exists()]
    problems = []
    need_damaged = sum(i.target.stat().st_size for i in pending)
    probe = plan.damaged if plan.damaged.exists() else plan.damaged.parent
    if probe.exists() and need_damaged > shutil.disk_usage(probe).free:
        problems.append(f"not enough free space for the damaged copies: need {human(need_damaged)}, "
                        f"have {human(shutil.disk_usage(probe).free)}")
    biggest = max((i.entry.replacement.stat().st_size for i in pending
                   if i.entry.replacement and i.entry.replacement.is_file()), default=0)
    if biggest > shutil.disk_usage(plan.library).free:
        problems.append(f"not enough free space in the library to stage the largest replacement ({human(biggest)})")
    return problems


def writable_problems(plan: Plan) -> list[str]:
    problems = []
    parent = plan.damaged if plan.damaged.exists() else plan.damaged.parent
    for label, folder in (("library", plan.library), ("damaged folder", parent)):
        if not os.access(folder, os.W_OK):
            problems.append(f"the {label} ({folder}) is read-only or not writable: mount the share read-write for this step")
    return problems


# --------------------------------------------------------------------------- the two actions

def sidecar_records(sources: list[Path], where: str, *, move_to: Path | None, copy_to: Path | None) -> list[dict]:
    records = []
    for sidecar in sources:
        record = describe_sidecar(sidecar, where)
        if move_to is not None:
            move_verified(sidecar, move_to / sidecar.name, record["sha256"])
        elif copy_to is not None:
            copy_verified(sidecar, copy_to / sidecar.name, record["sha256"], keep_times=True)
        records.append(record)
    return records


def do_replace(plan: Plan, item: Item) -> dict:
    entry, target, copy = item.entry, item.target, item.copy
    replacement = entry.replacement
    assert replacement is not None
    replacement_sha = sha256(replacement)
    current_sha = sha256(target)
    if current_sha == replacement_sha:
        # An earlier run swapped the file and stopped before writing the manifest: recover the original from its copy.
        if not copy.is_file():
            raise SwapError("the library file already is the replacement but no damaged copy exists, "
                            "so the original cannot be recorded")
        original_sha, original_stat = sha256(copy), copy.stat()
        sidecars = [describe_sidecar(p, "copied") for p in find_sidecars(copy)]
    else:
        original_sha, original_stat = current_sha, target.stat()
        if copy.exists():
            if sha256(copy) != original_sha:
                raise SwapError("a different file is already at the damaged destination; nothing was changed")
        else:
            copy_verified(target, copy, original_sha, keep_times=True)
        sidecars = sidecar_records(find_sidecars(target), "copied", move_to=None, copy_to=copy.parent)
        staged = target.with_name(target.name + PART_SUFFIX)  # an extension no photo tool indexes
        shutil.copyfile(replacement, staged)
        if plan.mtime == "keep":
            shutil.copystat(replacement, staged)
        if sha256(staged) != replacement_sha:
            staged.unlink(missing_ok=True)
            raise SwapError("the staged replacement does not match its source; the library file is unchanged")
        os.replace(staged, target)
        if sha256(target) != replacement_sha:
            raise SwapError("the replacement changed while being installed; restore with --rollback")
    return {
        "action": "replaced", "status": "replaced",
        "original": {"size": original_stat.st_size, "sha256": original_sha, "mtime": iso(original_stat.st_mtime_ns),
                     "mtime_ns": original_stat.st_mtime_ns},
        "sidecars": sidecars,
        "replacement": {"source": str(replacement), "size": replacement.stat().st_size, "sha256": replacement_sha,
                        "mtime_policy": plan.mtime},
    }


def do_quarantine(plan: Plan, item: Item) -> dict:
    entry, target, copy = item.entry, item.target, item.copy
    if target.exists():
        sha, stat = sha256(target), target.stat()
        if copy.exists() and sha256(copy) != sha:
            raise SwapError("a different file is already at the damaged destination; nothing was moved")
        sidecar_sources = find_sidecars(target)
        if copy.exists():          # an earlier run copied it across disks and stopped before removing the source
            target.unlink()
            sidecars = sidecar_records(sidecar_sources, "moved", move_to=copy.parent, copy_to=None)
        else:
            move_verified(target, copy, sha)
            sidecars = sidecar_records(sidecar_sources, "moved", move_to=copy.parent, copy_to=None)
    else:
        if not copy.is_file():
            raise SwapError("neither the library file nor a damaged copy exists")
        sha, stat = sha256(copy), copy.stat()
        sidecars = [describe_sidecar(p, "moved") for p in find_sidecars(copy)]
    return {
        "action": "quarantined", "status": "quarantined",
        "original": {"size": stat.st_size, "sha256": sha, "mtime": iso(stat.st_mtime_ns), "mtime_ns": stat.st_mtime_ns},
        "sidecars": sidecars, "replacement": None,
    }


# --------------------------------------------------------------------------- commands

def print_plan(plan: Plan, items: list[Item]) -> None:
    print(f"Library:  {plan.library}\nDamaged:  {plan.batch_root}\nManifest: {plan.damaged / MANIFEST_NAME}\n"
          f"Replaced files get a {'new' if plan.mtime == 'now' else 'kept'} modification date.\n")
    for number, item in enumerate(items, 1):
        size = human(item.target.stat().st_size) if item.target.exists() else "-"
        verb = {"replace": "replace   ", "quarantine": "quarantine"}[item.entry.action]
        print(f"{number:>3}. [{item.state:7}] {verb} {size:>9}  {item.entry.path}")
        if item.entry.action == "replace" and item.entry.replacement:
            print(f"                          with {item.entry.replacement}")
        for note in item.notes:
            print(f"                          note: {note}")
        for problem in item.problems:
            print(f"                          PROBLEM: {problem}")


def apply(plan: Plan, items: list[Item], manifest: dict, limit: int | None) -> int:
    pending = [i for i in items if i.state == "pending"]
    if limit:
        pending = pending[:limit]
    failures = 0
    for number, item in enumerate(pending, 1):
        print(f"[{number}/{len(pending)}] {item.entry.action:10} {item.entry.path}", flush=True)
        try:
            result = do_replace(plan, item) if item.entry.action == "replace" else do_quarantine(plan, item)
        except (SwapError, OSError) as error:
            failures += 1
            print(f"    FAILED: {error}", file=sys.stderr, flush=True)
            continue
        record = {"batch": plan.batch, "path": item.entry.path, **result,
                  "damaged_copy": str(item.copy.relative_to(plan.damaged)), "reason": item.entry.reason,
                  "details": item.entry.details, "processed_at": now_iso()}
        save_record(manifest, plan, record)
        print(f"    {result['status']}  (sha256 {result['original']['sha256'][:12]}…)", flush=True)
    return failures


def verify(plan: Plan, manifest: dict) -> int:
    problems = 0
    for entry in plan.entries:
        record = record_of(manifest, plan, entry)
        if record is None or record.get("status") not in DONE_STATUSES:
            print(f"not done      {entry.path}")
            problems += 1
            continue
        copy, target = plan.damaged / record["damaged_copy"], plan.target(entry)
        issues = []
        if not copy.is_file():
            issues.append("damaged copy missing")
        elif sha256(copy) != record["original"]["sha256"]:
            issues.append("damaged copy differs from the manifest")
        if record["status"] == "replaced":
            if not target.is_file():
                issues.append("library file missing")
            elif sha256(target) != record["replacement"]["sha256"]:
                issues.append("library file is not the recorded replacement")
        elif target.exists():
            issues.append("a file is back in the library")
        for sidecar in record.get("sidecars", []):
            path = (copy.parent / sidecar["name"])
            if not path.is_file() or sha256(path) != sidecar["sha256"]:
                issues.append(f"sidecar {sidecar['name']} missing or changed")
        print(f"{'OK          ' if not issues else 'PROBLEM     '} {entry.path}" + (f"  ({'; '.join(issues)})" if issues else ""))
        problems += bool(issues)
    return problems


def rollback(plan: Plan, manifest: dict, do_it: bool) -> int:
    failures = 0
    for entry in plan.entries:
        record = record_of(manifest, plan, entry)
        if record is None or record.get("status") not in DONE_STATUSES:
            print(f"nothing to undo  {entry.path}")
            continue
        copy, target = plan.damaged / record["damaged_copy"], plan.target(entry)
        print(f"{'undo' if do_it else 'would undo'} {record['status']:12} {entry.path}", flush=True)
        try:
            if not copy.is_file() or sha256(copy) != record["original"]["sha256"]:
                raise SwapError("the damaged copy is missing or differs from the manifest")
            if record["status"] == "replaced":
                if target.is_file() and sha256(target) != record["replacement"]["sha256"]:
                    raise SwapError("the library file was changed after the swap; it is not touched")
                if do_it:
                    staged = target.with_name(target.name + PART_SUFFIX)
                    shutil.copyfile(copy, staged)
                    shutil.copystat(copy, staged)
                    if sha256(staged) != record["original"]["sha256"]:
                        staged.unlink(missing_ok=True)
                        raise SwapError("the restored copy does not match; the library file is unchanged")
                    os.replace(staged, target)
            else:
                if target.exists():
                    raise SwapError("something already exists at the original path; nothing was restored")
                if do_it:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(copy, target.with_name(target.name + PART_SUFFIX))
                    shutil.copystat(copy, target.with_name(target.name + PART_SUFFIX))
                    if sha256(target.with_name(target.name + PART_SUFFIX)) != record["original"]["sha256"]:
                        target.with_name(target.name + PART_SUFFIX).unlink(missing_ok=True)
                        raise SwapError("the restored copy does not match; nothing was restored")
                    os.replace(target.with_name(target.name + PART_SUFFIX), target)
                    for sidecar in record.get("sidecars", []):
                        shutil.copy2(copy.parent / sidecar["name"], target.parent / sidecar["name"])
        except (SwapError, OSError) as error:
            failures += 1
            print(f"    FAILED: {error}", file=sys.stderr, flush=True)
            continue
        if do_it:
            record["status"] = "rolled-back"
            record["rolled_back_at"] = now_iso()
            save_record(manifest, plan, record)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("plan")
    parser.add_argument("--apply", action="store_true", help="do it; without this nothing changes")
    parser.add_argument("--verify", action="store_true", help="re-read every file and compare it with the manifest")
    parser.add_argument("--rollback", action="store_true", help="undo the batch (preview unless --apply is given)")
    parser.add_argument("--limit", type=int, help="handle at most this many files now")
    parser.add_argument("--ignore-expect", action="store_true",
                        help="replace files even if their size or date differs from the plan's \"expect\"")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.verify and (args.apply or args.rollback):
        parser.error("--verify cannot be combined with --apply or --rollback")
    try:
        plan = load_plan(Path(args.plan))
        manifest = load_manifest(plan)
    except PlanError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if args.verify:
        problems = verify(plan, manifest)
        print(f"\n{'All files match the manifest.' if not problems else f'{problems} file(s) need attention.'}")
        return 2 if problems else 0
    if args.rollback:
        if args.apply:
            blocked = writable_problems(plan)
            if blocked:
                print("\n".join(f"Error: {b}" for b in blocked), file=sys.stderr)
                return 1
        failures = rollback(plan, manifest, args.apply)
        if not args.apply:
            print("\nNothing changed. Add --apply to undo.")
        return 2 if failures else 0
    items = preflight(plan, manifest, args.ignore_expect)
    print_plan(plan, items)
    space = free_space_problems(plan, items)
    for problem in space:
        print(f"\nPROBLEM: {problem}")
    blocked = writable_problems(plan)
    pending = [i for i in items if i.state == "pending"]
    if not args.apply:
        for problem in blocked:
            print(f"\nNote: --apply will refuse to run: {problem}")
        print(f"\n{len(pending)} to do, {sum(i.state == 'done' for i in items)} already done, "
              f"{sum(i.state == 'blocked' for i in items)} blocked.\nNothing changed. Add --apply to do it.")
        return 2 if any(i.state == "blocked" for i in items) else 0
    if blocked or space:
        print("\n".join(f"Error: {b}" for b in blocked + space), file=sys.stderr)
        return 1
    failures = apply(plan, items, manifest, args.limit)
    left = sum(i.state == "blocked" for i in items)
    print(f"\nManifest: {plan.damaged / MANIFEST_NAME}")
    replaced = any(i.entry.action == "replace" for i in pending)
    if replaced:
        print("In Immich, rescan the external library" + (" (Scan All Library Files: the dates were kept)."
                                                          if plan.mtime == "keep" else "."))
    if left:
        print(f"{left} file(s) were left unchanged because of the problems listed above.")
    return 2 if failures or left else 0


if __name__ == "__main__":
    sys.exit(main())
