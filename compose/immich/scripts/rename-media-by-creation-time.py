#!/usr/bin/env python3
"""Safely rename photo/video archives from embedded creation timestamps.

The tool is deliberately plan-driven:

  1. ``plan`` reads metadata and writes an immutable manifest.
  2. ``apply`` performs collision-free, two-phase renames.
  3. ``rollback`` restores every original name from the manifest.
  4. ``status`` reconciles the manifest with the filesystem.

Planning never changes media. Applying and rolling back only rename directory
entries; media bytes and embedded metadata are not modified.
"""

from __future__ import annotations

import argparse
import ctypes
import csv
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


SCHEMA_VERSION = 1
TOOL_VERSION = "1.0.0"
DEFAULT_EXTENSIONS = ("dng", "heic", "jpeg", "jpg", "mov", "mp4", "png")
VIDEO_EXTENSIONS = {"mov", "mp4"}
TIMESTAMP_RE = re.compile(
    r"(?P<year>\d{4})[:\-](?P<month>\d{2})[:\-](?P<day>\d{2})"
    r"[ T](?P<hour>\d{2}):?(?P<minute>\d{2}):?(?P<second>\d{2})"
)
CURRENT_NAME_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_"
    r"(?P<hour>\d{2})(?:h-|-|h)?(?P<minute>\d{2})(?:m-|-|m)?(?P<second>\d{2})s?"
    r"(?:_|\.|$)",
    re.IGNORECASE,
)
STILL_TIMESTAMP_KEYS = (
    "Composite:SubSecDateTimeOriginal",
    "ExifIFD:DateTimeOriginal",
    "XMP-exif:DateTimeOriginal",
    "XMP-photoshop:DateCreated",
    "XMP-xmp:CreateDate",
    "ExifIFD:CreateDate",
)
VIDEO_TIMESTAMP_KEYS = (
    "Keys:CreationDate",
    "UserData:DateTimeOriginal",
    "QuickTime:CreateDate",
    "Track1:MediaCreateDate",
)
PAIR_ID_KEYS = (
    "Apple:MediaGroupUUID",
    "Keys:ContentIdentifier",
    "QuickTime:ContentIdentifier",
    "XMP:ContentIdentifier",
)


class RenameError(RuntimeError):
    """A controlled operational or validation failure."""


class Progress:
    """TTY progress bar with a useful periodic non-TTY fallback."""

    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = max(total, 1)
        self.start = time.monotonic()
        self.last_print = 0.0
        self.tty = sys.stderr.isatty()

    def update(self, completed: int, detail: str = "", *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and not self.tty and completed != self.total and now - self.last_print < 5:
            return
        elapsed = max(now - self.start, 0.001)
        rate = completed / elapsed
        remaining = max(self.total - completed, 0)
        eta = remaining / rate if rate else 0
        percent = min(completed / self.total, 1.0) * 100
        suffix = f" | {detail}" if detail else ""
        if self.tty:
            width = 28
            filled = int(width * min(completed / self.total, 1.0))
            bar = "#" * filled + "-" * (width - filled)
            message = (
                f"\r{self.label:<20} [{bar}] {percent:6.2f}% "
                f"{completed:,}/{self.total:,} {rate:7.1f}/s ETA {format_duration(eta)}{suffix}"
            )
            sys.stderr.write(message[: max(shutil.get_terminal_size((120, 20)).columns - 1, 40)])
            sys.stderr.flush()
            if completed >= self.total:
                sys.stderr.write("\n")
        else:
            print(
                f"{self.label}: {percent:6.2f}% ({completed:,}/{self.total:,}) "
                f"rate={rate:.1f}/s eta={format_duration(eta)}{suffix}",
                file=sys.stderr,
                flush=True,
            )
        self.last_print = now


def format_duration(seconds: float) -> str:
    seconds_int = max(int(seconds), 0)
    hours, remainder = divmod(seconds_int, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_relative(root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RenameError(f"Unsafe relative path in manifest: {value!r}")
    resolved = (root / relative).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RenameError(f"Manifest path escapes archive root: {value!r}") from error
    return resolved


def validate_root(root: Path) -> Path:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise RenameError(f"Archive root is not a directory: {root}")
    if root == Path("/"):
        raise RenameError("Refusing to operate on the filesystem root")
    lowered = tuple(part.lower() for part in root.parts)
    forbidden = (("immich", "library"), ("immich", "upload"))
    for first, second in forbidden:
        if any(lowered[index : index + 2] == (first, second) for index in range(len(lowered) - 1)):
            raise RenameError(
                f"Refusing to rename an Immich-managed directory: {root}. "
                "Operate only on the independent canonical archive."
            )
    if not os.access(root, os.R_OK | os.X_OK):
        raise RenameError(f"Archive root is not readable/searchable: {root}")
    return root


def parse_extensions(value: str) -> tuple[str, ...]:
    extensions = tuple(sorted({item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()}))
    if not extensions or any(not re.fullmatch(r"[a-z0-9]+", item) for item in extensions):
        raise argparse.ArgumentTypeError("Extensions must be a comma-separated alphanumeric list")
    return extensions


def positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def discover_files(root: Path, recursive: bool, extensions: Sequence[str]) -> list[Path]:
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    discovered: list[Path] = []
    for path in iterator:
        if path.is_symlink():
            continue
        if path.is_file() and path.suffix.lower().lstrip(".") in extensions:
            discovered.append(path.resolve())
    return sorted(discovered, key=lambda item: os.fsencode(str(item.relative_to(root))))


def representative_sample(root: Path, files: Sequence[Path], count: int) -> list[Path]:
    """Return a deterministic sample that includes every extension when possible."""
    if count >= len(files):
        return list(files)
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        groups[path.suffix.lower()].append(path)

    selected: set[Path] = set()
    # Rare formats are valuable during validation, so seed with one midpoint
    # from each extension before filling the remainder deterministically.
    for extension in sorted(groups, key=lambda key: (len(groups[key]), key)):
        if len(selected) >= count:
            break
        group = groups[extension]
        selected.add(group[len(group) // 2])

    remaining = [path for path in files if path not in selected]
    remaining.sort(
        key=lambda path: hashlib.sha256(os.fsencode(str(path.relative_to(root)))).digest()
    )
    selected.update(remaining[: count - len(selected)])
    return sorted(selected, key=lambda item: os.fsencode(str(item.relative_to(root))))


def chunked(items: Sequence[Path], size: int) -> Iterable[Sequence[Path]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def extract_metadata(files: Sequence[Path], exiftool: str, batch_size: int) -> dict[Path, dict[str, Any]]:
    metadata: dict[Path, dict[str, Any]] = {}
    progress = Progress("Reading metadata", len(files))
    completed = 0
    tags = (
        "-DateTimeOriginal",
        "-CreateDate",
        "-CreationDate",
        "-DateCreated",
        "-ModifyDate",
        "-SubSecDateTimeOriginal",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-OffsetTimeOriginal",
        "-ContentIdentifier",
        "-MediaGroupUUID",
    )
    for batch in chunked(files, batch_size):
        command = [
            exiftool,
            "-j",
            "-G1",
            "-charset",
            "filename=UTF8",
            "-api",
            "QuickTimeUTC=1",
            *tags,
            "--",
            *(str(path) for path in batch),
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if result.returncode not in (0, 1):
            raise RenameError(
                f"ExifTool failed with exit code {result.returncode}:\n{result.stderr.strip()}"
            )
        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RenameError(f"ExifTool returned invalid JSON: {error}") from error
        for record in records:
            source_value = record.get("SourceFile")
            if source_value:
                metadata[Path(source_value).resolve()] = record
        completed += len(batch)
        progress.update(completed, Path(batch[-1]).name, force=completed == len(files))
    return metadata


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    match = TIMESTAMP_RE.search(value)
    if not match:
        return None
    try:
        parsed = dt.datetime(**{key: int(number) for key, number in match.groupdict().items()})
    except ValueError:
        return None
    if not 1900 <= parsed.year <= 2200:
        return None
    return parsed


def timestamp_from_filename(name: str) -> dt.datetime | None:
    match = CURRENT_NAME_RE.match(name)
    if not match:
        return None
    try:
        return dt.datetime(**{key: int(number) for key, number in match.groupdict().items()})
    except ValueError:
        return None


def choose_timestamp(path: Path, record: dict[str, Any], allow_mtime: bool) -> tuple[dt.datetime | None, str]:
    extension = path.suffix.lower().lstrip(".")
    preferred = VIDEO_TIMESTAMP_KEYS if extension in VIDEO_EXTENSIONS else STILL_TIMESTAMP_KEYS
    for key in preferred:
        parsed = parse_timestamp(record.get(key))
        if parsed:
            return parsed, key

    # Accommodate ExifTool group variations without accidentally preferring a
    # track timestamp over the explicitly ordered keys above.
    generic_names = ("DateTimeOriginal", "CreationDate", "CreateDate", "DateCreated")
    for tag_name in generic_names:
        for key in sorted(record):
            if key.split(":")[-1] == tag_name:
                parsed = parse_timestamp(record.get(key))
                if parsed:
                    return parsed, key

    existing = timestamp_from_filename(path.name)
    if existing:
        return existing, "FileName:existing-timestamp"

    if allow_mtime:
        modified = dt.datetime.fromtimestamp(path.stat().st_mtime)
        return modified, "System:FileModifyDate-fallback"
    return None, "missing"


def choose_pair_id(record: dict[str, Any]) -> str | None:
    for key in PAIR_ID_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    for key in sorted(record):
        if key.split(":")[-1] in {"ContentIdentifier", "MediaGroupUUID"}:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
    return None


def target_name(timestamp: dt.datetime, extension: str, collision: int = 1, variant: int = 1) -> str:
    stem = timestamp.strftime("%Y-%m-%d_%Hh-%Mm-%Ss")
    if collision > 1:
        stem += f"_{collision:02d}"
    if variant > 1:
        stem += f"_v{variant:02d}"
    return f"{stem}.{extension.lower()}"


def best_unit_timestamp(items: list[dict[str, Any]]) -> tuple[dt.datetime, str]:
    still_items = [item for item in items if item["extension"] not in VIDEO_EXTENSIONS]
    candidates = still_items or items
    chosen = sorted(candidates, key=lambda item: item["source"])[0]
    return dt.datetime.fromisoformat(chosen["timestamp"]), chosen["timestamp_source"]


def make_plan_entries(
    root: Path,
    files: Sequence[Path],
    metadata: dict[Path, dict[str, Any]],
    allow_mtime: bool,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    provisional: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    progress = Progress("Building plan", len(files))
    for index, path in enumerate(files, start=1):
        record = metadata.get(path, {})
        timestamp, source = choose_timestamp(path, record, allow_mtime)
        if timestamp is None:
            skipped.append({"source": str(path.relative_to(root)), "reason": "no trustworthy creation time"})
            progress.update(index, path.name, force=index == len(files))
            continue
        stat_result = path.stat()
        provisional.append(
            {
                "source": str(path.relative_to(root)),
                "parent": str(path.parent.relative_to(root)),
                "extension": path.suffix.lower().lstrip("."),
                "timestamp": timestamp.isoformat(timespec="seconds"),
                "timestamp_source": source,
                "pair_id": choose_pair_id(record),
                "size": stat_result.st_size,
                "device": stat_result.st_dev,
                "inode": stat_result.st_ino,
                "mtime_ns": stat_result.st_mtime_ns,
            }
        )
        progress.update(index, path.name, force=index == len(files))

    source_paths = {root / item["source"] for item in provisional}
    occupied: set[tuple[str, str]] = set()
    occupied_stems: set[tuple[str, str]] = set()
    for path in root.rglob("*"):
        if path.is_file() and path not in source_paths:
            parent_key = str(path.parent.relative_to(root)).casefold()
            occupied.add((parent_key, path.name.casefold()))
            occupied_stems.add((parent_key, path.stem.casefold()))

    units: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in provisional:
        identity = item["pair_id"] or f"single:{item['source']}"
        units[(item["parent"], identity)].append(item)

    assigned: set[tuple[str, str]] = set(occupied)
    assigned_stems: set[tuple[str, str]] = set(occupied_stems)
    finalized: list[dict[str, Any]] = []
    ordered_units = sorted(
        units.items(),
        key=lambda pair: (best_unit_timestamp(pair[1])[0], pair[0][0], pair[0][1]),
    )
    for (_, _), items in ordered_units:
        unit_timestamp, unit_source = best_unit_timestamp(items)
        parent = root / items[0]["parent"]
        collision = 1
        while True:
            proposed: list[Path] = []
            extension_seen: Counter[str] = Counter()
            for item in sorted(items, key=lambda value: value["source"]):
                extension_seen[item["extension"]] += 1
                proposed.append(
                    parent
                    / target_name(
                        unit_timestamp,
                        item["extension"],
                        collision,
                        extension_seen[item["extension"]],
                    )
                )
            parent_key = str(parent.relative_to(root)).casefold()
            proposed_keys = {(parent_key, path.name.casefold()) for path in proposed}
            proposed_stem = target_name(unit_timestamp, "placeholder", collision).rsplit(".", 1)[0]
            stem_key = (parent_key, proposed_stem.casefold())
            if (
                len(proposed_keys) == len(proposed)
                and not any(key in assigned for key in proposed_keys)
                and stem_key not in assigned_stems
            ):
                break
            collision += 1

        extension_seen = Counter()
        for item in sorted(items, key=lambda value: value["source"]):
            extension_seen[item["extension"]] += 1
            target = parent / target_name(
                unit_timestamp,
                item["extension"],
                collision,
                extension_seen[item["extension"]],
            )
            assigned.add((str(parent.relative_to(root)).casefold(), target.name.casefold()))
            item["target"] = str(target.relative_to(root))
            item["temp"] = str(
                (parent / f".archive-rename-{run_id}-{len(finalized) + 1:06d}.tmp").relative_to(root)
            )
            item["rollback_temp"] = str(
                (parent / f".archive-rollback-{run_id}-{len(finalized) + 1:06d}.tmp").relative_to(root)
            )
            item["collision_index"] = collision
            item["variant_index"] = extension_seen[item["extension"]]
            item["effective_timestamp"] = unit_timestamp.isoformat(timespec="seconds")
            item["pair_timestamp_source"] = unit_source
            item["action"] = "unchanged" if item["source"] == item["target"] else "rename"
            finalized.append(item)
        assigned_stems.add(
            (
                str(parent.relative_to(root)).casefold(),
                target_name(unit_timestamp, "placeholder", collision).rsplit(".", 1)[0].casefold(),
            )
        )

    return sorted(finalized, key=lambda item: item["source"]), skipped


def default_state_base() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        return Path(configured).expanduser() / "batlab-media-renamer"
    return Path.home() / ".local" / "state" / "batlab-media-renamer"


def write_report(path: Path, entries: Sequence[dict[str, Any]], skipped: Sequence[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, dialect="excel-tab")
        writer.writerow(
            (
                "action",
                "original_path",
                "new_path",
                "capture_time",
                "timestamp_source",
                "effective_capture_time",
                "effective_timestamp_source",
                "pair_id",
                "collision_index",
                "size_bytes",
            )
        )
        for item in entries:
            writer.writerow(
                (
                    item["action"],
                    item["source"],
                    item["target"],
                    item["timestamp"],
                    item["timestamp_source"],
                    item["effective_timestamp"],
                    item["pair_timestamp_source"],
                    item["pair_id"] or "",
                    item["collision_index"],
                    item["size"],
                )
            )
        for item in skipped:
            writer.writerow(
                ("SKIPPED", item["source"], "", "", item["reason"], "", "", "", "", "")
            )


def create_wrapper(path: Path, action: str) -> None:
    content = f"""#!/bin/sh
set -eu
STATE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$STATE_DIR/tool.py" {action} "$STATE_DIR" "$@"
"""
    atomic_write(path, content, 0o700)


def command_plan(args: argparse.Namespace) -> int:
    root = validate_root(Path(args.root))
    exiftool = shutil.which(args.exiftool)
    if not exiftool:
        raise RenameError(f"ExifTool executable not found: {args.exiftool}")
    discovered_files = discover_files(root, args.recursive, args.extensions)
    if not discovered_files:
        raise RenameError(f"No supported media files found under {root}")
    sample_mode = args.sample is not None
    files = (
        representative_sample(root, discovered_files, args.sample)
        if sample_mode
        else discovered_files
    )

    run_prefix = "sample-" if sample_mode else ""
    run_id = run_prefix + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    state_base = Path(args.state_base).expanduser().resolve()
    run_dir = state_base / run_id
    if run_dir.exists():
        raise RenameError(f"State directory already exists: {run_dir}")
    run_dir.mkdir(parents=True, mode=0o700)

    print(f"Archive root : {root}")
    print(f"Media files  : {len(discovered_files):,} discovered; {len(files):,} selected")
    print(f"State bundle : {run_dir}")
    if sample_mode:
        print("Mode         : SAMPLE VALIDATION (this bundle cannot be applied)")
    else:
        print("Mode         : FULL PLAN ONLY (no files will be renamed)")
    metadata = extract_metadata(files, exiftool, args.batch_size)
    entries, skipped = make_plan_entries(root, files, metadata, args.allow_mtime_fallback, run_id)

    root_stat = root.stat()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "created_at": utc_now(),
        "run_id": run_id,
        "root": str(root),
        "root_device": root_stat.st_dev,
        "sampled": sample_mode,
        "discovered_count": len(discovered_files),
        "selected_count": len(files),
        "recursive": args.recursive,
        "extensions": list(args.extensions),
        "filename_format": "YYYY-MM-DD_HHh-MMm-SSs[_NN][_vNN].ext",
        "entries": entries,
        "skipped": skipped,
    }
    manifest_path = run_dir / "manifest.json"
    atomic_write(manifest_path, json_text(manifest))
    atomic_write(run_dir / "manifest.sha256", f"{sha256_file(manifest_path)}  manifest.json\n")
    write_report(run_dir / "plan.tsv", entries, skipped)
    shutil.copy2(Path(__file__).resolve(), run_dir / "tool.py")
    os.chmod(run_dir / "tool.py", 0o700)
    if sample_mode:
        atomic_write(
            run_dir / "SAMPLE-ONLY.txt",
            "This validation bundle intentionally cannot be applied. Run a full plan without --sample.\n",
        )
    else:
        create_wrapper(run_dir / "apply.sh", "apply")
        create_wrapper(run_dir / "rollback.sh", "rollback")

    renamed = sum(item["action"] == "rename" for item in entries)
    unchanged = len(entries) - renamed
    collisions = sum(item["collision_index"] > 1 for item in entries)
    pair_groups = len({item["pair_id"] for item in entries if item["pair_id"]})
    source_counts = Counter(item["timestamp_source"] for item in entries)
    print("\nPlan summary")
    print(f"  Files discovered       : {len(discovered_files):,}")
    print(f"  Files inspected        : {len(files):,}")
    print(f"  Files to rename        : {renamed:,}")
    print(f"  Already correctly named: {unchanged:,}")
    print(f"  Skipped                : {len(skipped):,}")
    print(f"  Collision-suffixed     : {collisions:,}")
    print(f"  Apple media groups     : {pair_groups:,}")
    print("  Timestamp sources:")
    for source, count in source_counts.most_common():
        print(f"    {count:6,}  {source}")
    print(f"\nReview mapping : {run_dir / 'plan.tsv'}")
    if sample_mode:
        print("Apply           : DISABLED for sample bundles")
        print("Next step       : run plan again without --sample")
    else:
        print(f"Apply safely   : {run_dir / 'apply.sh'}")
        print(f"Rollback       : {run_dir / 'rollback.sh'}")
    if skipped:
        print(
            "\nWARNING: Some files lack a trustworthy creation time. Apply will refuse them "
            "unless --allow-skipped is supplied.",
            file=sys.stderr,
        )
        return 2
    return 0


def load_bundle(run_dir_value: str) -> tuple[Path, dict[str, Any], Path]:
    run_dir = Path(run_dir_value).expanduser().resolve(strict=True)
    manifest_path = run_dir / "manifest.json"
    checksum_path = run_dir / "manifest.sha256"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise RenameError(f"Incomplete state bundle: {run_dir}")
    expected = checksum_path.read_text(encoding="utf-8").split()[0]
    actual = sha256_file(manifest_path)
    if actual != expected:
        raise RenameError("Manifest checksum mismatch; refusing to rename anything")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RenameError(f"Unsupported manifest schema: {manifest.get('schema_version')}")
    root = validate_root(Path(manifest["root"]))
    if root.stat().st_dev != manifest["root_device"]:
        raise RenameError("Archive root is now on a different filesystem/device")
    return run_dir, manifest, root


def item_paths(root: Path, item: dict[str, Any]) -> dict[str, Path]:
    return {
        key: canonical_relative(root, item[key])
        for key in ("source", "target", "temp", "rollback_temp")
    }


def path_matches(path: Path, item: dict[str, Any]) -> bool:
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return False
    return stat_result.st_dev == item["device"] and stat_result.st_ino == item["inode"]


def locate_item(root: Path, item: dict[str, Any]) -> tuple[str, Path]:
    matches: list[tuple[str, Path]] = []
    for label, path in item_paths(root, item).items():
        if path_matches(path, item):
            matches.append((label, path))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RenameError(
            f"Cannot find expected inode for {item['source']}; the file may have been moved or replaced"
        )
    labels = ", ".join(label for label, _ in matches)
    raise RenameError(f"Expected inode appears at multiple managed paths for {item['source']}: {labels}")


def rename_noreplace(source: Path, target: Path) -> None:
    if source == target:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is not None:
        result = renameat2(
            ctypes.c_int(-100),
            ctypes.c_char_p(os.fsencode(source)),
            ctypes.c_int(-100),
            ctypes.c_char_p(os.fsencode(target)),
            ctypes.c_uint(1),
        )
        if result == 0:
            return
        error_number = ctypes.get_errno()
        if error_number not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
            raise OSError(error_number, os.strerror(error_number), str(target))
    # Safe same-filesystem fallback: linking is no-clobber and retains inode,
    # followed by removal of the old directory entry.
    os.link(source, target, follow_symlinks=False)
    try:
        os.unlink(source)
    except Exception:
        os.unlink(target)
        raise


def append_log(run_dir: Path, action: str, source: Path, target: Path) -> None:
    with (run_dir / "operations.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()}\t{action}\t{source}\t{target}\n")
        handle.flush()
        os.fsync(handle.fileno())


def acquire_lock(run_dir: Path):
    handle = (run_dir / "operation.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise RenameError("Another apply/rollback operation is using this state bundle") from error
    return handle


def confirm(action: str, count: int, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise RenameError("Interactive confirmation is unavailable; rerun with --yes after reviewing plan.tsv")
    expected = f"{action.upper()} {count}"
    print(f"Type {expected!r} to continue: ", end="", flush=True)
    if input().strip() != expected:
        raise RenameError("Confirmation did not match; no files were changed")


def preflight_apply(root: Path, entries: Sequence[dict[str, Any]]) -> None:
    planned_inodes = {(item["device"], item["inode"]) for item in entries}
    progress = Progress("Apply preflight", len(entries))
    for index, item in enumerate(entries, start=1):
        if item["action"] == "unchanged":
            if not path_matches(canonical_relative(root, item["source"]), item):
                raise RenameError(f"Unchanged file no longer matches the plan: {item['source']}")
        else:
            label, _ = locate_item(root, item)
            paths = item_paths(root, item)
            target = paths["target"]
            if target.exists() and label != "target" and not path_matches(target, item):
                target_stat = target.stat()
                if (target_stat.st_dev, target_stat.st_ino) not in planned_inodes:
                    raise RenameError(f"Target was created after planning: {target}")
        progress.update(index, item["source"], force=index == len(entries))


def command_apply(args: argparse.Namespace) -> int:
    run_dir, manifest, root = load_bundle(args.state_dir)
    if manifest.get("sampled"):
        raise RenameError("Sample validation bundles cannot be applied; create a full plan without --sample")
    if manifest["skipped"] and not args.allow_skipped:
        raise RenameError(
            f"Manifest has {len(manifest['skipped'])} skipped files. Review plan.tsv, then use "
            "--allow-skipped only if intentionally leaving them unchanged."
        )
    entries = [item for item in manifest["entries"] if item["action"] == "rename"]
    if not entries:
        print("Nothing to rename; every file already has its planned name.")
        return 0
    lock = acquire_lock(run_dir)
    try:
        preflight_apply(root, manifest["entries"])
        confirm("rename", len(entries), args.yes)
        print(f"Renaming {len(entries):,} files under {root}")
        print("Phase 1/2 moves every source to a unique temporary name.")
        phase_one = Progress("Staging renames", len(entries))
        for index, item in enumerate(entries, start=1):
            label, current = locate_item(root, item)
            paths = item_paths(root, item)
            if label == "source":
                rename_noreplace(current, paths["temp"])
                append_log(run_dir, "stage", current, paths["temp"])
            phase_one.update(index, item["source"], force=index == len(entries))

        print("Phase 2/2 installs every final name without overwriting existing files.")
        phase_two = Progress("Installing names", len(entries))
        for index, item in enumerate(entries, start=1):
            label, current = locate_item(root, item)
            paths = item_paths(root, item)
            if label == "temp":
                rename_noreplace(current, paths["target"])
                append_log(run_dir, "install", current, paths["target"])
            elif label != "target":
                raise RenameError(f"Unexpected apply state {label} for {item['source']}")
            phase_two.update(index, item["target"], force=index == len(entries))
        atomic_write(run_dir / "completed.json", json_text({"action": "apply", "completed_at": utc_now()}))
        print(f"\nRename complete. Roll back with: {run_dir / 'rollback.sh'}")
        return 0
    finally:
        lock.close()


def preflight_rollback(root: Path, entries: Sequence[dict[str, Any]]) -> None:
    locations = {item["source"]: locate_item(root, item)[0] for item in entries}
    for item in entries:
        paths = item_paths(root, item)
        original = paths["source"]
        if original.exists() and not path_matches(original, item):
            occupying_item = next(
                (
                    other
                    for other in entries
                    if other is not item
                    and path_matches(original, other)
                    and locations[other["source"]] in {"target", "temp", "rollback_temp"}
                ),
                None,
            )
            if occupying_item is None:
                raise RenameError(f"Original path is occupied by an unrelated file: {original}")


def command_rollback(args: argparse.Namespace) -> int:
    run_dir, manifest, root = load_bundle(args.state_dir)
    entries = [item for item in manifest["entries"] if item["action"] == "rename"]
    if not entries:
        print("Nothing to roll back.")
        return 0
    lock = acquire_lock(run_dir)
    try:
        preflight_rollback(root, entries)
        remaining = sum(locate_item(root, item)[0] != "source" for item in entries)
        if remaining == 0:
            print("All files already have their original names.")
            return 0
        confirm("rollback", remaining, args.yes)
        print(f"Restoring {remaining:,} original names under {root}")
        phase_one = Progress("Staging rollback", len(entries))
        for index, item in enumerate(entries, start=1):
            label, current = locate_item(root, item)
            paths = item_paths(root, item)
            if label != "source" and label != "rollback_temp":
                rename_noreplace(current, paths["rollback_temp"])
                append_log(run_dir, "rollback-stage", current, paths["rollback_temp"])
            phase_one.update(index, item["source"], force=index == len(entries))

        phase_two = Progress("Restoring names", len(entries))
        for index, item in enumerate(entries, start=1):
            label, current = locate_item(root, item)
            paths = item_paths(root, item)
            if label == "rollback_temp":
                rename_noreplace(current, paths["source"])
                append_log(run_dir, "restore", current, paths["source"])
            elif label != "source":
                raise RenameError(f"Unexpected rollback state {label} for {item['source']}")
            phase_two.update(index, item["source"], force=index == len(entries))
        atomic_write(run_dir / "rolled-back.json", json_text({"action": "rollback", "completed_at": utc_now()}))
        print("\nRollback complete. Every planned file has its original name.")
        return 0
    finally:
        lock.close()


def command_status(args: argparse.Namespace) -> int:
    _, manifest, root = load_bundle(args.state_dir)
    counts: Counter[str] = Counter()
    errors: list[str] = []
    progress = Progress("Checking status", len(manifest["entries"]))
    for index, item in enumerate(manifest["entries"], start=1):
        try:
            if item["action"] == "unchanged":
                source = canonical_relative(root, item["source"])
                if not path_matches(source, item):
                    raise RenameError(f"Unchanged file no longer matches the plan: {item['source']}")
                label = "source"
            else:
                label, _ = locate_item(root, item)
            counts[label] += 1
        except RenameError as error:
            counts["error"] += 1
            errors.append(str(error))
        progress.update(index, item["source"], force=index == len(manifest["entries"]))
    print("Filesystem state")
    for label in ("source", "target", "temp", "rollback_temp", "error"):
        print(f"  {label:14}: {counts[label]:,}")
    print(f"  unchanged     : {sum(item['action'] == 'unchanged' for item in manifest['entries']):,}")
    print(f"  skipped       : {len(manifest['skipped']):,}")
    if errors:
        print("\nErrors:", file=sys.stderr)
        for error in errors[:20]:
            print(f"  - {error}", file=sys.stderr)
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more", file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan, apply, verify, and roll back metadata-based media renames.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="read metadata and create a no-change rename plan")
    plan.add_argument("root", help="canonical archive directory to scan")
    plan.add_argument("--recursive", action="store_true", help="include nested directories")
    plan.add_argument(
        "--sample",
        type=positive_integer,
        help="inspect a deterministic extension-aware subset; sample bundles cannot be applied",
    )
    plan.add_argument(
        "--extensions",
        type=parse_extensions,
        default=DEFAULT_EXTENSIONS,
        help="comma-separated media extensions",
    )
    plan.add_argument("--exiftool", default="exiftool", help="ExifTool executable")
    plan.add_argument("--batch-size", type=int, default=100, choices=range(1, 501), metavar="1..500")
    plan.add_argument(
        "--state-base",
        default=str(default_state_base()),
        help="directory in which the durable plan/rollback bundle is created",
    )
    plan.add_argument(
        "--allow-mtime-fallback",
        action="store_true",
        help="use filesystem modification time only when metadata and existing filename have no date",
    )
    plan.set_defaults(func=command_plan)

    for command_name, handler in (("apply", command_apply), ("rollback", command_rollback)):
        command = subparsers.add_parser(command_name, help=f"{command_name} a previously generated plan")
        command.add_argument("state_dir", help="state bundle created by the plan command")
        command.add_argument("--yes", action="store_true", help="skip the typed confirmation")
        if command_name == "apply":
            command.add_argument(
                "--allow-skipped",
                action="store_true",
                help="apply the plan while intentionally leaving timestamp-less files unchanged",
            )
        command.set_defaults(func=handler)

    status = subparsers.add_parser("status", help="reconcile a state bundle with the filesystem")
    status.add_argument("state_dir", help="state bundle created by the plan command")
    status.set_defaults(func=command_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RenameError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Rerun apply/rollback or use status; the manifest remains valid.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
