#!/usr/bin/env python3
"""Name photo and video archives by local capture time, reversibly.

The tool is plan-driven:

  1. ``plan`` reads metadata, resolves each file's capture time and time zone,
     and writes an immutable manifest plus reviewable reports.
  2. ``apply`` performs collision-free, two-phase renames, writes XMP sidecars
     where software reading only the file would show a different local time, and
     records unverified dates in ``<root>/.organize/unverified.tsv``.
  3. ``rollback`` removes the sidecars it wrote and restores every original name.
  4. ``status`` reconciles the manifest with the filesystem.

Media bytes and embedded metadata are never modified.

Capture time resolution, most reliable first:

  exact    local time with its UTC offset (iPhone photos and videos), or a local
           time whose offset is proven by the GPS clock in the same file, or a UTC
           time converted with the zone at the file's GPS position
  assumed  a time without zone, read in the zone configured for the folder or the
           default zone; or a full date and time in the file name
  weak     a day from the file name or a dated folder, or the file's modified time

Dates that cannot be real (QuickTime and Unix epoch placeholders, camera reset
dates, anything before --min-year or in the future) are rejected and the next
source is used, which also repairs files already named with such dates.
"""

from __future__ import annotations

import argparse
import ctypes
import csv
import datetime as dt
import errno
import fcntl
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from timezonefinder import TimezoneFinder
except ImportError:
    TimezoneFinder = None


SCHEMA_VERSION = 2
TOOL_VERSION = "2.0.0"
GENERATOR = "batlab organize-media"
DEFAULT_ZONE = "Africa/Casablanca"
DEFAULT_MIN_YEAR = 1990
VIDEO_EXTENSIONS = {"3gp", "avi", "m4v", "mkv", "mov", "mp4", "webm"}
DEFAULT_EXTENSIONS = tuple(sorted({"bmp", "dng", "gif", "heic", "heif", "jpeg", "jpg", "png"} | VIDEO_EXTENSIONS))
IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
IGNORED_DIRECTORIES = {".organize", ".rsync-partial", "@eadir", ".trashes", ".spotlight-v100", "$recycle.bin"}
CONFIDENCE_RANK = {"exact": 0, "assumed": 1, "weak": 2}
REVIEW_TAG = "review/date-unverified"

TIMESTAMP_RE = re.compile(
    r"(?P<year>\d{4})[:\-](?P<month>\d{2})[:\-](?P<day>\d{2})"
    r"[ T](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\.\d+)?(?P<offset>Z|[+\-]\d{2}:?\d{2})?"
)
GPS_DATE_RE = re.compile(r"(?P<year>\d{4}):(?P<month>\d{2}):(?P<day>\d{2})")
GPS_TIME_RE = re.compile(r"(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})")
OWN_NAME_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_{1,2}"
    r"(?P<hour>\d{2})(?:h-|-|h)?(?P<minute>\d{2})(?:m-|-|m)?(?P<second>\d{2})s?"
    r"(?:[_\-.]|$)",
    re.IGNORECASE,
)
OWN_DATE_ONLY_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})_date-only(?:_|\.|$)", re.IGNORECASE)
FILENAME_DATETIME_PATTERNS = (
    re.compile(
        r"(?:^|[^0-9])(?P<year>(?:19|20)\d{2})(?P<month>\d{2})(?P<day>\d{2})[_\-T ]"
        r"(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})(?:[^0-9]|\d{1,3}(?:[^0-9]|$)|$)"
    ),
    re.compile(
        r"(?:^|[^0-9])(?P<year>(?:19|20)\d{2})-(?P<month>\d{2})-(?P<day>\d{2})[ _]"
        r"(?P<hour>\d{2})[.\-](?P<minute>\d{2})[.\-](?P<second>\d{2})(?:[^0-9]|$)"
    ),
)
FILENAME_EPOCH_RE = re.compile(r"^(?P<epoch>1\d{12})(?:[^0-9]|$)")
FILENAME_DATE_RE = re.compile(r"(?:^|[^0-9])(?P<year>(?:19|20)\d{2})(?P<month>\d{2})(?P<day>\d{2})(?:[^0-9]|$)")
MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
FOLDER_DATE_RES = (
    re.compile(r"(?:^|[^0-9])(?P<year>(?:19|20)\d{2})-(?P<month>\d{2})-(?P<day>\d{2})(?:[^0-9]|$)"),
    re.compile(r"(?:^|[^0-9A-Za-z])(?P<year>(?:19|20)\d{2})-(?P<mon>[A-Za-z]{3})-(?P<day>\d{2})(?:[^0-9]|$)"),
)
PAIR_ID_KEYS = ("Apple:MediaGroupUUID", "Keys:ContentIdentifier", "QuickTime:ContentIdentifier", "XMP:ContentIdentifier")
STILL_OFFSET_SOURCES = (
    ("Composite:SubSecDateTimeOriginal", None),
    ("ExifIFD:DateTimeOriginal", "ExifIFD:OffsetTimeOriginal"),
    ("XMP-exif:DateTimeOriginal", None),
    ("IFD0:DateTimeOriginal", "ExifIFD:OffsetTimeOriginal"),
    ("ExifIFD:CreateDate", "ExifIFD:OffsetTimeDigitized"),
    ("XMP-photoshop:DateCreated", None),
    ("XMP-xmp:CreateDate", None),
)
VIDEO_LOCAL_SOURCES = ("Keys:CreationDate", "UserData:DateTimeOriginal", "ItemList:ContentCreateDate")
VIDEO_UTC_SOURCES = ("QuickTime:CreateDate", "Track1:MediaCreateDate", "Track1:TrackCreateDate", "QuickTime:ModifyDate")


class RenameError(RuntimeError):
    """A controlled operational or validation failure."""


@dataclass
class Resolution:
    local: str
    precision: str
    confidence: str
    evidence: str
    offset_minutes: int | None = None
    zone: str | None = None
    rejected: list[str] = field(default_factory=list)

    @property
    def local_datetime(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.local)


@dataclass
class FolderRule:
    prefix: str
    zone: str | None = None
    shift_minutes: int = 0
    date: dt.date | None = None
    clock_local: bool = False


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


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def zone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise argparse.ArgumentTypeError(f"unknown time zone: {value}") from error
    return value


def is_ignored(path: Path, root: Path) -> bool:
    relative_parts = path.relative_to(root).parts
    if path.name.startswith("._") or path.name.lower() in IGNORED_NAMES:
        return True
    return any(part.lower() in IGNORED_DIRECTORIES for part in relative_parts[:-1])


def discover_files(root: Path, recursive: bool, extensions: Sequence[str]) -> list[Path]:
    iterator: Iterable[Path] = root.rglob("*") if recursive else root.iterdir()
    discovered: list[Path] = []
    for path in iterator:
        if path.is_symlink() or not path.is_file() or is_ignored(path, root):
            continue
        if path.suffix.lower().lstrip(".") in extensions:
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
    for extension in sorted(groups, key=lambda key: (len(groups[key]), key)):
        if len(selected) >= count:
            break
        group = groups[extension]
        selected.add(group[len(group) // 2])

    remaining = [path for path in files if path not in selected]
    remaining.sort(key=lambda path: hashlib.sha256(os.fsencode(str(path.relative_to(root)))).digest())
    selected.update(remaining[: count - len(selected)])
    return sorted(selected, key=lambda item: os.fsencode(str(item.relative_to(root))))


def chunked(items: Sequence[Path], size: int) -> Iterable[Sequence[Path]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def cpu_temperature(sensor: str) -> float | None:
    for hwmon in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            if (hwmon / "name").read_text(encoding="utf-8").strip() != sensor:
                continue
            return int((hwmon / "temp1_input").read_text(encoding="utf-8").strip()) / 1000
        except (OSError, ValueError):
            continue
    return None


def wait_for_cool_cpu(max_temp: float, sensor: str) -> None:
    if not max_temp:
        return
    reading = cpu_temperature(sensor)
    if reading is None:
        raise RenameError(f"No readable hwmon sensor named {sensor!r}; refusing to run without the temperature guard")
    if reading <= max_temp:
        return
    print(f"\nCPU at {reading:.0f} C, above {max_temp:g} C; pausing until it cools.", file=sys.stderr, flush=True)
    while (reading := cpu_temperature(sensor)) is not None and reading > max_temp - 8:
        time.sleep(15)
    print(f"CPU back to {reading:.0f} C; resuming.", file=sys.stderr, flush=True)


def extract_metadata(
    files: Sequence[Path], exiftool: str, batch_size: int, max_temp: float = 0, sensor: str = ""
) -> dict[Path, dict[str, Any]]:
    metadata: dict[Path, dict[str, Any]] = {}
    progress = Progress("Reading metadata", len(files))
    completed = 0
    tags = (
        "-DateTimeOriginal", "-SubSecDateTimeOriginal", "-OffsetTimeOriginal", "-OffsetTimeDigitized",
        "-CreateDate", "-CreationDate", "-DateCreated", "-ContentCreateDate", "-ModifyDate",
        "-MediaCreateDate", "-TrackCreateDate",
        "-GPSDateStamp", "-GPSTimeStamp", "-GPSLatitude#", "-GPSLongitude#", "-GPSCoordinates#",
        "-ContentIdentifier", "-MediaGroupUUID", "-Make", "-Model",
    )
    for batch in chunked(files, batch_size):
        wait_for_cool_cpu(max_temp, sensor)
        # QuickTimeUTC is deliberately not set: ExifTool would otherwise convert
        # the UTC QuickTime dates into the time zone of the machine running it.
        command = [exiftool, "-j", "-G1", "-a", "-charset", "filename=UTF8", *tags, "--", *(str(path) for path in batch)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if result.returncode not in (0, 1):
            raise RenameError(f"ExifTool failed with exit code {result.returncode}:\n{result.stderr.strip()}")
        try:
            records = json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError as error:
            raise RenameError(f"ExifTool returned invalid JSON: {error}") from error
        for record in records:
            source_value = record.get("SourceFile")
            if source_value:
                metadata[Path(source_value).resolve()] = record
        completed += len(batch)
        progress.update(completed, Path(batch[-1]).name, force=completed == len(files))
    return metadata


def parse_offset(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if value == "Z":
        return 0
    match = re.fullmatch(r"([+\-])(\d{2}):?(\d{2})", value)
    if not match:
        return None
    minutes = int(match.group(2)) * 60 + int(match.group(3))
    if minutes > 14 * 60:
        return None
    return -minutes if match.group(1) == "-" else minutes


def parse_metadata_timestamp(value: Any) -> tuple[dt.datetime, int | None] | None:
    if not isinstance(value, str):
        return None
    match = TIMESTAMP_RE.search(value)
    if not match:
        return None
    parts = {key: int(number) for key, number in match.groupdict().items() if key != "offset"}
    try:
        parsed = dt.datetime(**parts)
    except ValueError:
        return None
    return parsed, parse_offset(match.group("offset"))


def implausible(local: dt.datetime, min_year: int, now: dt.datetime) -> str | None:
    if local.year < min_year:
        return f"before {min_year}"
    if local > now + dt.timedelta(days=2):
        return "in the future"
    if local.month == 1 and local.day == 1 and local.hour == 0 and local.minute == 0 and local.second == 0:
        return "a camera or container reset date"
    return None


def zone_offset(zone: str, local: dt.datetime) -> int:
    offset = local.replace(tzinfo=ZoneInfo(zone)).utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def utc_to_zone(utc: dt.datetime, zone: str) -> tuple[dt.datetime, int]:
    converted = utc.replace(tzinfo=dt.timezone.utc).astimezone(ZoneInfo(zone))
    offset = converted.utcoffset()
    return converted.replace(tzinfo=None), int(offset.total_seconds() // 60) if offset is not None else 0


def gps_utc(record: dict[str, Any]) -> dt.datetime | None:
    date_value = next((value for key, value in record.items() if key.endswith(":GPSDateStamp")), None)
    time_value = next((value for key, value in record.items() if key.endswith(":GPSTimeStamp")), None)
    if not isinstance(date_value, str) or not isinstance(time_value, str):
        return None
    date_match, time_match = GPS_DATE_RE.search(date_value), GPS_TIME_RE.search(time_value)
    if not date_match or not time_match:
        return None
    try:
        return dt.datetime(
            **{key: int(number) for key, number in date_match.groupdict().items()},
            **{key: int(number) for key, number in time_match.groupdict().items()},
        )
    except ValueError:
        return None


def gps_position(record: dict[str, Any]) -> tuple[float, float] | None:
    latitude = next((value for key, value in record.items() if key.endswith(":GPSLatitude")), None)
    longitude = next((value for key, value in record.items() if key.endswith(":GPSLongitude")), None)
    if latitude is None or longitude is None:
        coordinates = next((value for key, value in record.items() if key.endswith(":GPSCoordinates")), None)
        if isinstance(coordinates, str):
            numbers = re.findall(r"-?\d+(?:\.\d+)?", coordinates)
            if len(numbers) >= 2:
                latitude, longitude = numbers[0], numbers[1]
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if latitude == 0 and longitude == 0:
        return None
    return latitude, longitude


_ZONE_FINDER: Any = None


def zone_at(position: tuple[float, float] | None) -> str | None:
    global _ZONE_FINDER
    if position is None or TimezoneFinder is None:
        return None
    if _ZONE_FINDER is None:
        _ZONE_FINDER = TimezoneFinder()
    return _ZONE_FINDER.timezone_at(lat=position[0], lng=position[1])


def parse_rules(path: Path | None) -> list[FolderRule]:
    if path is None:
        return []
    rules: dict[str, FolderRule] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3:
            raise RenameError(f"{path}:{number}: expected '<folder> <zone|shift|date|clock> <value>'")
        prefix = PurePosixPath(parts[0].strip("/")).as_posix()
        rule = rules.setdefault(prefix, FolderRule(prefix=prefix))
        kind, value = parts[1].lower(), parts[2]
        if kind == "zone":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as error:
                raise RenameError(f"{path}:{number}: unknown time zone {value!r}") from error
            rule.zone = value
        elif kind == "shift":
            match = re.fullmatch(r"([+\-])(\d+)h(?:(\d+)m)?", value)
            if not match:
                raise RenameError(f"{path}:{number}: shift must look like +1h or -0h30m")
            minutes = int(match.group(2)) * 60 + int(match.group(3) or 0)
            rule.shift_minutes = -minutes if match.group(1) == "-" else minutes
        elif kind == "date":
            try:
                rule.date = dt.date.fromisoformat(value)
            except ValueError as error:
                raise RenameError(f"{path}:{number}: date must be YYYY-MM-DD") from error
        elif kind == "clock" and value.lower() == "local":
            rule.clock_local = True
        else:
            raise RenameError(f"{path}:{number}: unknown rule {parts[1]!r}")
    return sorted(rules.values(), key=lambda rule: len(rule.prefix))


def effective_rule(rules: Sequence[FolderRule], relative: str) -> FolderRule:
    merged = FolderRule(prefix="")
    for rule in rules:
        if rule.prefix in ("", ".") or relative == rule.prefix or relative.startswith(rule.prefix + "/"):
            merged.zone = rule.zone or merged.zone
            merged.shift_minutes = rule.shift_minutes or merged.shift_minutes
            merged.date = rule.date or merged.date
            merged.clock_local = rule.clock_local or merged.clock_local
    return merged


def resolve_capture_time(
    relative: str,
    record: dict[str, Any],
    mtime: float,
    rules: Sequence[FolderRule],
    default_zone: str,
    min_year: int,
    now: dt.datetime | None = None,
) -> Resolution:
    now = now or dt.datetime.now()
    path = PurePosixPath(relative)
    is_video = path.suffix.lower().lstrip(".") in VIDEO_EXTENSIONS
    rule = effective_rule(rules, relative)
    zone = rule.zone or default_zone
    shift = dt.timedelta(minutes=rule.shift_minutes)
    rejected: list[str] = []

    def accept(local: dt.datetime, label: str) -> bool:
        reason = implausible(local, min_year, now)
        if reason:
            rejected.append(f"{label}={local.isoformat(sep=' ')} ({reason})")
            return False
        return True

    def finish(local: dt.datetime, confidence: str, evidence: str, offset: int | None, used_zone: str | None,
               precision: str = "second") -> Resolution:
        if rule.shift_minutes and confidence != "exact":
            local = local + shift
            evidence += f" shifted {rule.shift_minutes:+d}m"
            if used_zone:
                offset = zone_offset(used_zone, local)
        return Resolution(local.isoformat(timespec="seconds"), precision, confidence, evidence, offset, used_zone, rejected)

    local_sources = VIDEO_LOCAL_SOURCES if is_video else tuple(key for key, _ in STILL_OFFSET_SOURCES)
    offsets = dict(STILL_OFFSET_SOURCES)
    local_without_offset: tuple[dt.datetime, str] | None = None
    for key in local_sources:
        parsed = parse_metadata_timestamp(record.get(key))
        if not parsed:
            if isinstance(record.get(key), str) and record[key].startswith("0000"):
                rejected.append(f"{key}={record[key]} (empty date)")
            continue
        local, offset = parsed
        if offset is None and offsets.get(key):
            offset = parse_offset(record.get(offsets[key]))
        if not accept(local, key):
            continue
        if offset is not None:
            return Resolution(local.isoformat(timespec="seconds"), "second", "exact", key, offset, None, rejected)
        local_without_offset = local_without_offset or (local, key)

    position = gps_position(record)
    if local_without_offset:
        local, key = local_without_offset
        gps_time = gps_utc(record)
        if gps_time and not rule.shift_minutes:
            difference = (local - gps_time).total_seconds() / 60
            quarter = round(difference / 15) * 15
            if abs(quarter) <= 14 * 60 and abs(difference - quarter) <= 5:
                return Resolution(local.isoformat(timespec="seconds"), "second", "exact",
                                  f"{key} + GPS clock offset", quarter, None, rejected)
        gps_zone = None if rule.zone else zone_at(position)
        if gps_zone:
            return Resolution(local.isoformat(timespec="seconds"), "second", "exact",
                              f"{key} in GPS zone {gps_zone}", zone_offset(gps_zone, local), gps_zone, rejected)
        return finish(local, "assumed", f"{key} read as {zone}", zone_offset(zone, local), zone)

    utc_sources = VIDEO_UTC_SOURCES if is_video else ()
    for key in utc_sources:
        parsed = parse_metadata_timestamp(record.get(key))
        if not parsed:
            if isinstance(record.get(key), str) and record[key].startswith("0000"):
                rejected.append(f"{key}={record[key]} (empty date)")
            continue
        recorded, offset = parsed
        if rule.clock_local:
            if accept(recorded, key):
                return finish(recorded, "assumed", f"{key} as local clock in {zone}", zone_offset(zone, recorded), zone)
            continue
        if offset not in (None, 0):
            recorded = recorded - dt.timedelta(minutes=offset)
        gps_zone = None if rule.zone else zone_at(position)
        used_zone = gps_zone or zone
        local, converted_offset = utc_to_zone(recorded, used_zone)
        if not accept(local, key):
            continue
        if gps_zone:
            return Resolution(local.isoformat(timespec="seconds"), "second", "exact",
                              f"{key} UTC in GPS zone {gps_zone}", converted_offset, gps_zone, rejected)
        return finish(local, "assumed", f"{key} UTC converted to {zone}", converted_offset, zone)

    name = path.name
    for label, match in (("own name", OWN_NAME_RE.match(name)),
                         *(("name date-time", pattern.search(name)) for pattern in FILENAME_DATETIME_PATTERNS)):
        if not match:
            continue
        try:
            local = dt.datetime(**{key: int(number) for key, number in match.groupdict().items()})
        except ValueError:
            continue
        if accept(local, label):
            return finish(local, "assumed", f"file name ({label}) read as {zone}", zone_offset(zone, local), zone)

    epoch = FILENAME_EPOCH_RE.match(name)
    if epoch:
        digits = epoch.group("epoch")
        seconds = int(digits) / 1000
        local, offset = utc_to_zone(dt.datetime(1970, 1, 1) + dt.timedelta(seconds=seconds), zone)
        if accept(local, "name epoch"):
            return finish(local, "assumed", f"file name (Unix time) converted to {zone}", offset, zone)

    if rule.date:
        return Resolution(rule.date.isoformat() + "T00:00:00", "day", "assumed", "configured folder date",
                          None, zone, rejected)

    modified_local, modified_offset = utc_to_zone(dt.datetime(1970, 1, 1) + dt.timedelta(seconds=mtime), zone)
    day_candidates: list[tuple[dt.date, str]] = []
    own_day = OWN_DATE_ONLY_RE.match(name)
    for label, match in (("own date-only name", own_day), ("name date", FILENAME_DATE_RE.search(name))):
        if match:
            try:
                day_candidates.append((dt.date(int(match["year"]), int(match["month"]), int(match["day"])), label))
            except ValueError:
                pass
    for folder in reversed(path.parent.parts):
        for pattern in FOLDER_DATE_RES:
            match = pattern.search(folder)
            if not match:
                continue
            month = int(match["month"]) if "month" in match.groupdict() else MONTHS.get(match["mon"].lower())
            try:
                day_candidates.append((dt.date(int(match["year"]), month or 0, int(match["day"])), f"folder {folder}"))
            except (TypeError, ValueError):
                pass
            break
    for day, label in day_candidates:
        noon = dt.datetime.combine(day, dt.time(12))
        if not accept(noon, label):
            continue
        if modified_local.date() == day:
            return Resolution(modified_local.isoformat(timespec="seconds"), "second", "weak",
                              f"{label}, time from modified date", modified_offset, zone, rejected)
        return Resolution(noon.replace(hour=0).isoformat(timespec="seconds"), "day", "weak", label, None, zone, rejected)

    if accept(modified_local, "modified date"):
        return Resolution(modified_local.isoformat(timespec="seconds"), "second", "weak",
                          "file modified date", modified_offset, zone, rejected)
    return Resolution("", "none", "none", "no plausible date", None, None, rejected)


def embedded_is_authoritative(resolution: Resolution) -> bool:
    """True when the media file itself already carries this exact local time and offset."""
    return resolution.confidence == "exact" and "+" not in resolution.evidence and " in GPS zone" not in resolution.evidence \
        and " UTC " not in resolution.evidence


def sidecar_needed(resolution: Resolution) -> bool:
    """True when software reading only the media file would show a different local time."""
    evidence = resolution.evidence
    if resolution.confidence == "weak" or "shifted" in evidence:
        return True
    # A UTC container date, a date taken from the file name or a folder rule is
    # not something Immich can recover from the file's own metadata.
    return " UTC " in evidence or evidence.startswith(("file name", "configured")) or " as local clock " in evidence


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


def target_name(local: dt.datetime, precision: str, extension: str, collision: int = 1, variant: int = 1) -> str:
    if precision == "day":
        stem = local.strftime("%Y-%m-%d_date-only") + f"_{collision:02d}"
    else:
        stem = local.strftime("%Y-%m-%d_%Hh-%Mm-%Ss")
        if collision > 1:
            stem += f"_{collision:02d}"
    if variant > 1:
        stem += f"_v{variant:02d}"
    return f"{stem}.{extension.lower()}"


def best_unit(items: list[dict[str, Any]]) -> dict[str, Any]:
    stills = [item for item in items if item["extension"] not in VIDEO_EXTENSIONS]
    candidates = stills or items
    return sorted(candidates, key=lambda item: (CONFIDENCE_RANK[item["resolution"]["confidence"]], item["source"]))[0]


def xmp_date(resolution: Resolution) -> str:
    local = resolution.local_datetime
    if resolution.precision == "day":
        return local.date().isoformat()
    text = local.isoformat(timespec="seconds")
    if resolution.offset_minutes is not None:
        sign = "-" if resolution.offset_minutes < 0 else "+"
        hours, minutes = divmod(abs(resolution.offset_minutes), 60)
        text += f"{sign}{hours:02d}:{minutes:02d}"
    return text


def render_sidecar(resolution: Resolution, original_name: str, flag: bool) -> str:
    date = html.escape(xmp_date(resolution), quote=True)
    original = html.escape(original_name, quote=True)
    tags = ""
    if flag:
        tags = (
            f"   <dc:subject><rdf:Bag><rdf:li>{REVIEW_TAG}</rdf:li></rdf:Bag></dc:subject>\n"
            f"   <lr:hierarchicalSubject><rdf:Bag><rdf:li>{REVIEW_TAG.replace('/', '|')}</rdf:li></rdf:Bag></lr:hierarchicalSubject>\n"
            f"   <digiKam:TagsList><rdf:Seq><rdf:li>{REVIEW_TAG}</rdf:li></rdf:Seq></digiKam:TagsList>\n"
        )
    return (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        f'<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{GENERATOR} {TOOL_VERSION}">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        '    xmlns:exif="http://ns.adobe.com/exif/1.0/"\n'
        '    xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"\n'
        '    xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
        '    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"\n'
        '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
        '    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"\n'
        '    xmlns:digiKam="http://www.digikam.org/ns/1.0/"\n'
        f'    exif:DateTimeOriginal="{date}"\n'
        f'    photoshop:DateCreated="{date}"\n'
        f'    xmp:CreateDate="{date}"\n'
        f'    xmpMM:PreservedFileName="{original}">\n'
        f"{tags}"
        "  </rdf:Description>\n"
        " </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )


def is_own_sidecar(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return GENERATOR in handle.read(600)
    except OSError:
        return False


def sidecar_candidates(path: Path) -> list[Path]:
    return [path.with_name(path.name + ".xmp"), path.with_name(path.name + ".XMP"),
            path.with_suffix(".xmp"), path.with_suffix(".XMP")]


def make_plan_entries(
    root: Path,
    files: Sequence[Path],
    metadata: dict[Path, dict[str, Any]],
    run_id: str,
    rules: Sequence[FolderRule] = (),
    default_zone: str = DEFAULT_ZONE,
    min_year: int = DEFAULT_MIN_YEAR,
    sidecar_mode: str = "needed",
    now: dt.datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    provisional: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    progress = Progress("Resolving dates", len(files))
    for index, path in enumerate(files, start=1):
        relative = str(path.relative_to(root))
        record = metadata.get(path, {})
        stat_result = path.stat()
        resolution = resolve_capture_time(relative, record, stat_result.st_mtime, rules, default_zone, min_year, now)
        foreign = [candidate for candidate in sidecar_candidates(path) if candidate.exists() and not is_own_sidecar(candidate)]
        if resolution.confidence == "none":
            skipped.append({"source": relative, "reason": "; ".join(resolution.rejected) or "no plausible date"})
        elif foreign:
            skipped.append({"source": relative, "reason": f"has a sidecar from another tool: {foreign[0].name}"})
        else:
            provisional.append(
                {
                    "source": relative,
                    "parent": str(path.parent.relative_to(root)),
                    "extension": path.suffix.lower().lstrip("."),
                    "resolution": asdict(resolution),
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
        if path.is_file() and path not in source_paths and not path.name.lower().endswith(".xmp"):
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

    def unit_key(pair: tuple[tuple[str, str], list[dict[str, Any]]]) -> tuple[str, int, str, str]:
        chosen = best_unit(pair[1])
        resolution = Resolution(**chosen["resolution"])
        first_choice = target_name(resolution.local_datetime, resolution.precision, chosen["extension"])
        # Files already carrying their exact name claim it before same-second neighbours.
        keeps_name = PurePosixPath(chosen["source"]).name.casefold() == first_choice.casefold()
        return resolution.local, 0 if keeps_name else 1, pair[0][0], pair[0][1]

    for (_, _), items in sorted(units.items(), key=unit_key):
        chosen = best_unit(items)
        resolution = Resolution(**chosen["resolution"])
        local = resolution.local_datetime
        parent = root / items[0]["parent"]
        parent_key = str(parent.relative_to(root)).casefold()
        ordered = sorted(items, key=lambda value: value["source"])
        collision = 1
        while True:
            extension_seen: Counter[str] = Counter()
            proposed = []
            for item in ordered:
                extension_seen[item["extension"]] += 1
                proposed.append(target_name(local, resolution.precision, item["extension"], collision,
                                            extension_seen[item["extension"]]))
            proposed_keys = {(parent_key, name.casefold()) for name in proposed}
            stem = target_name(local, resolution.precision, "placeholder", collision).rsplit(".", 1)[0]
            stem_key = (parent_key, stem.casefold())
            if (
                len(proposed_keys) == len(proposed)
                and not any(key in assigned for key in proposed_keys)
                and stem_key not in assigned_stems
            ):
                break
            collision += 1

        extension_seen = Counter()
        for item, name in zip(ordered, proposed):
            extension_seen[item["extension"]] += 1
            target = parent / name
            assigned.add((parent_key, name.casefold()))
            item_resolution = Resolution(**item["resolution"])
            unit_resolution = resolution
            flagged = unit_resolution.confidence == "weak"
            wants_sidecar = sidecar_mode == "all" or (
                sidecar_mode == "needed" and (sidecar_needed(unit_resolution) or item_resolution.local != unit_resolution.local)
            )
            item["target"] = str(target.relative_to(root))
            number = len(finalized) + 1
            item["temp"] = str((parent / f".archive-rename-{run_id}-{number:06d}.tmp").relative_to(root))
            item["rollback_temp"] = str((parent / f".archive-rollback-{run_id}-{number:06d}.tmp").relative_to(root))
            item["collision_index"] = collision
            item["variant_index"] = extension_seen[item["extension"]]
            item["unit_resolution"] = asdict(unit_resolution)
            item["flagged"] = flagged
            item["sidecar"] = str(target.with_name(target.name + ".xmp").relative_to(root)) if wants_sidecar else None
            item["sidecar_content"] = (
                render_sidecar(unit_resolution, PurePosixPath(item["source"]).name, flagged) if wants_sidecar else None
            )
            item["action"] = "unchanged" if item["source"] == item["target"] else "rename"
            finalized.append(item)
        assigned_stems.add((parent_key, target_name(local, resolution.precision, "placeholder", collision)
                            .rsplit(".", 1)[0].casefold()))

    return sorted(finalized, key=lambda item: item["source"]), skipped


def default_state_base() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    base = Path(configured).expanduser() if configured else Path.home() / ".local" / "state"
    return base / "batlab-organize-media"


def write_reports(run_dir: Path, entries: Sequence[dict[str, Any]], skipped: Sequence[dict[str, str]]) -> None:
    with (run_dir / "plan.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, dialect="excel-tab")
        writer.writerow(("action", "original_path", "new_path", "local_time", "utc_offset", "precision",
                         "confidence", "evidence", "sidecar", "flagged", "rejected_dates", "size_bytes"))
        for item in entries:
            resolution = item["unit_resolution"]
            offset = resolution["offset_minutes"]
            writer.writerow((
                item["action"], item["source"], item["target"], resolution["local"],
                "" if offset is None else f"{offset:+d}m", resolution["precision"], resolution["confidence"],
                resolution["evidence"], "yes" if item["sidecar"] else "", "yes" if item["flagged"] else "",
                " | ".join(item["resolution"]["rejected"]), item["size"],
            ))
        for item in skipped:
            writer.writerow(("SKIPPED", item["source"], "", "", "", "", "", item["reason"], "", "", "", ""))

    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for item in entries:
        resolution = item["unit_resolution"]
        if resolution["confidence"] != "exact":
            evidence = re.sub(r" read as .*| converted to .*| as local clock .*", "", resolution["evidence"])
            groups[(item["parent"], resolution["confidence"], evidence)].append(resolution["local"])
    with (run_dir / "review.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, dialect="excel-tab")
        writer.writerow(("folder", "confidence", "evidence", "files", "earliest", "latest"))
        for (folder, confidence, evidence), times in sorted(groups.items(), key=lambda pair: (pair[0][1] != "weak", pair[0][0])):
            writer.writerow((folder, confidence, evidence, len(times), min(times)[:10], max(times)[:10]))


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
    rules = parse_rules(Path(args.rules)) if args.rules else []
    discovered_files = discover_files(root, args.recursive, args.extensions)
    if not discovered_files:
        raise RenameError(f"No supported media files found under {root}")
    sample_mode = args.sample is not None
    files = representative_sample(root, discovered_files, args.sample) if sample_mode else discovered_files

    run_id = ("sample-" if sample_mode else "") + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    run_dir = Path(args.state_base).expanduser().resolve() / run_id
    if run_dir.exists():
        raise RenameError(f"State directory already exists: {run_dir}")
    run_dir.mkdir(parents=True, mode=0o700)

    print(f"Archive root : {root}")
    print(f"Media files  : {len(discovered_files):,} discovered; {len(files):,} selected")
    print(f"Default zone : {args.default_zone}  (GPS zone lookup: {'available' if TimezoneFinder else 'not installed'})")
    print(f"Folder rules : {len(rules)}")
    print(f"State bundle : {run_dir}")
    print("Mode         : " + ("SAMPLE VALIDATION (cannot be applied)" if sample_mode else "PLAN ONLY (nothing is changed)"))
    metadata = extract_metadata(files, exiftool, args.batch_size, args.max_cpu_temp, args.cpu_temp_sensor)
    entries, skipped = make_plan_entries(root, files, metadata, run_id, rules, args.default_zone, args.min_year, args.sidecars)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "created_at": utc_now(),
        "run_id": run_id,
        "root": str(root),
        "root_device": root.stat().st_dev,
        "sampled": sample_mode,
        "discovered_count": len(discovered_files),
        "selected_count": len(files),
        "recursive": args.recursive,
        "extensions": list(args.extensions),
        "default_zone": args.default_zone,
        "min_year": args.min_year,
        "sidecar_mode": args.sidecars,
        "rules": [asdict(rule) | {"date": rule.date.isoformat() if rule.date else None} for rule in rules],
        "filename_format": "YYYY-MM-DD_HHh-MMm-SSs[_NN][_vNN].ext | YYYY-MM-DD_date-only_NN[_vNN].ext",
        "entries": entries,
        "skipped": skipped,
    }
    manifest_path = run_dir / "manifest.json"
    atomic_write(manifest_path, json_text(manifest))
    atomic_write(run_dir / "manifest.sha256", f"{sha256_file(manifest_path)}  manifest.json\n")
    write_reports(run_dir, entries, skipped)
    shutil.copy2(Path(__file__).resolve(), run_dir / "tool.py")
    os.chmod(run_dir / "tool.py", 0o700)
    if sample_mode:
        atomic_write(run_dir / "SAMPLE-ONLY.txt", "This validation bundle cannot be applied. Plan again without --sample.\n")
    else:
        create_wrapper(run_dir / "apply.sh", "apply")
        create_wrapper(run_dir / "rollback.sh", "rollback")

    confidence = Counter(item["unit_resolution"]["confidence"] for item in entries)
    rejected = [item for item in entries if item["resolution"]["rejected"]]
    print("\nPlan summary")
    print(f"  Files inspected           : {len(files):,}")
    print(f"  To rename                 : {sum(item['action'] == 'rename' for item in entries):,}")
    print(f"  Already correctly named   : {sum(item['action'] == 'unchanged' for item in entries):,}")
    print(f"  Exact / assumed / weak    : {confidence['exact']:,} / {confidence['assumed']:,} / {confidence['weak']:,}")
    print(f"  Day-only names            : {sum(item['unit_resolution']['precision'] == 'day' for item in entries):,}")
    print(f"  Sidecars to write         : {sum(bool(item['sidecar']) for item in entries):,}")
    print(f"  Flagged for review        : {sum(item['flagged'] for item in entries):,}")
    print(f"  Fake dates rejected       : {len(rejected):,}")
    print(f"  Collision-suffixed        : {sum(item['collision_index'] > 1 for item in entries):,}")
    print(f"  Apple media groups        : {len({item['pair_id'] for item in entries if item['pair_id']}):,}")
    print(f"  Skipped                   : {len(skipped):,}")
    for item in rejected[:5]:
        print(f"    rejected: {item['source']}: {' | '.join(item['resolution']['rejected'])}")
    print(f"\nEvery file : {run_dir / 'plan.tsv'}")
    print(f"By folder  : {run_dir / 'review.tsv'}  (assumed and weak dates only)")
    if sample_mode:
        print("Apply      : disabled for sample bundles")
    else:
        print(f"Apply      : {run_dir / 'apply.sh'}" + ("  (add --accept-weak to include flagged files)" if confidence["weak"] else ""))
        print(f"Rollback   : {run_dir / 'rollback.sh'}")
    return 0


def load_bundle(run_dir_value: str) -> tuple[Path, dict[str, Any], Path]:
    run_dir = Path(run_dir_value).expanduser().resolve(strict=True)
    manifest_path, checksum_path = run_dir / "manifest.json", run_dir / "manifest.sha256"
    if not manifest_path.is_file() or not checksum_path.is_file():
        raise RenameError(f"Incomplete state bundle: {run_dir}")
    if sha256_file(manifest_path) != checksum_path.read_text(encoding="utf-8").split()[0]:
        raise RenameError("Manifest checksum mismatch; refusing to change anything")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RenameError(f"Unsupported manifest schema: {manifest.get('schema_version')}")
    root = validate_root(Path(manifest["root"]))
    if root.stat().st_dev != manifest["root_device"]:
        raise RenameError("Archive root is now on a different filesystem/device")
    return run_dir, manifest, root


def item_paths(root: Path, item: dict[str, Any]) -> dict[str, Path]:
    return {key: canonical_relative(root, item[key]) for key in ("source", "target", "temp", "rollback_temp")}


def path_matches(path: Path, item: dict[str, Any]) -> bool:
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return False
    return stat_result.st_dev == item["device"] and stat_result.st_ino == item["inode"]


def locate_item(root: Path, item: dict[str, Any]) -> tuple[str, Path]:
    matches = [(label, path) for label, path in item_paths(root, item).items() if path_matches(path, item)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RenameError(f"Cannot find expected inode for {item['source']}; the file may have been moved or replaced")
    raise RenameError(f"Expected inode appears at multiple managed paths for {item['source']}")


def rename_noreplace(source: Path, target: Path) -> None:
    if source == target:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            result = renameat2(ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(source)),
                               ctypes.c_int(-100), ctypes.c_char_p(os.fsencode(target)), ctypes.c_uint(1))
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number not in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise OSError(error_number, os.strerror(error_number), str(target))
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP):
            raise
        rename_if_absent(source, target)
        return
    try:
        os.unlink(source)
    except Exception:
        os.unlink(target)
        raise


_WARNED_CHECKED_RENAME = False


# Last resort on file systems with neither RENAME_NOREPLACE nor hard links (some
# network shares): another process creating the target between the check and the
# rename would be overwritten, which the other two methods rule out.
def rename_if_absent(source: Path, target: Path) -> None:
    global _WARNED_CHECKED_RENAME
    if not _WARNED_CHECKED_RENAME:
        print(f"warning: {target.parent} supports neither atomic no-replace renames nor hard links; "
              "renaming after an existence check, so do not modify this folder while it runs", file=sys.stderr)
        _WARNED_CHECKED_RENAME = True
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(target))
    os.rename(source, target)


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
            target = item_paths(root, item)["target"]
            if target.exists() and label != "target" and not path_matches(target, item):
                target_stat = target.stat()
                if (target_stat.st_dev, target_stat.st_ino) not in planned_inodes:
                    raise RenameError(f"Target was created after planning: {target}")
        if item["sidecar"]:
            sidecar = canonical_relative(root, item["sidecar"])
            if sidecar.exists() and not is_own_sidecar(sidecar):
                raise RenameError(f"A sidecar from another tool appeared after planning: {sidecar}")
        progress.update(index, item["source"], force=index == len(entries))


UNVERIFIED_COLUMNS = ("path", "local_time", "precision", "evidence", "plan")


def read_unverified(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, dialect="excel-tab"))


def write_unverified(path: Path, rows: Sequence[dict[str, str]]) -> None:
    lines = ["\t".join(UNVERIFIED_COLUMNS)]
    for row in sorted(rows, key=lambda value: value["path"]):
        lines.append("\t".join(row.get(column, "").replace("\t", " ") for column in UNVERIFIED_COLUMNS))
    atomic_write(path, "\n".join(lines) + "\n", 0o644)


def command_apply(args: argparse.Namespace) -> int:
    run_dir, manifest, root = load_bundle(args.state_dir)
    if manifest.get("sampled"):
        raise RenameError("Sample bundles cannot be applied; plan again without --sample")
    if manifest["skipped"] and not args.allow_skipped:
        raise RenameError(f"{len(manifest['skipped'])} files were skipped; review plan.tsv, then pass --allow-skipped")
    weak = [item for item in manifest["entries"] if item["flagged"]]
    if weak and not args.accept_weak:
        raise RenameError(
            f"{len(weak)} files only have a weak date (file name, folder or modified time). Review review.tsv, "
            "then pass --accept-weak to rename and flag them, or add folder rules and plan again."
        )
    renames = [item for item in manifest["entries"] if item["action"] == "rename"]
    sidecars = [item for item in manifest["entries"] if item["sidecar"]]
    if not renames and not sidecars:
        print("Nothing to do; every file already has its planned name and needs no sidecar.")
        return 0
    lock = acquire_lock(run_dir)
    try:
        preflight_apply(root, manifest["entries"])
        confirm("apply", len(renames) + len(sidecars), args.yes)

        print(f"Phase 1/4 moves {len(renames):,} files to unique temporary names.")
        progress = Progress("Staging renames", len(renames))
        for index, item in enumerate(renames, start=1):
            label, current = locate_item(root, item)
            if label == "source":
                paths = item_paths(root, item)
                rename_noreplace(current, paths["temp"])
                append_log(run_dir, "stage", current, paths["temp"])
            progress.update(index, item["source"], force=index == len(renames))

        print("Phase 2/4 installs the final names without overwriting anything.")
        progress = Progress("Installing names", len(renames))
        for index, item in enumerate(renames, start=1):
            label, current = locate_item(root, item)
            paths = item_paths(root, item)
            if label == "temp":
                rename_noreplace(current, paths["target"])
                append_log(run_dir, "install", current, paths["target"])
            elif label != "target":
                raise RenameError(f"Unexpected apply state {label} for {item['source']}")
            progress.update(index, item["target"], force=index == len(renames))

        print(f"Phase 3/4 writes {len(sidecars):,} XMP sidecars.")
        backups = run_dir / "sidecar-backups"
        progress = Progress("Writing sidecars", len(sidecars))
        for index, item in enumerate(sidecars, start=1):
            sidecar = canonical_relative(root, item["sidecar"])
            if sidecar.exists():
                if not is_own_sidecar(sidecar):
                    raise RenameError(f"Refusing to overwrite a sidecar from another tool: {sidecar}")
                backup = backups / (item["sidecar"] + ".previous")
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sidecar, backup)
            atomic_write(sidecar, item["sidecar_content"], 0o644)
            append_log(run_dir, "sidecar", sidecar, sidecar)
            progress.update(index, item["sidecar"], force=index == len(sidecars))

        print("Phase 4/4 updates the unverified-dates manifest.")
        unverified_path = root / ".organize" / "unverified.tsv"
        snapshot = run_dir / "unverified.before.tsv"
        if unverified_path.exists() and not snapshot.exists():
            shutil.copy2(unverified_path, snapshot)
        touched = {item["source"] for item in manifest["entries"]} | {item["target"] for item in manifest["entries"]}
        rows = [row for row in read_unverified(unverified_path) if row["path"] not in touched]
        for item in weak:
            resolution = item["unit_resolution"]
            rows.append({"path": item["target"], "local_time": resolution["local"], "precision": resolution["precision"],
                         "evidence": resolution["evidence"], "plan": manifest["run_id"]})
        write_unverified(unverified_path, rows)

        atomic_write(run_dir / "completed.json", json_text({"action": "apply", "completed_at": utc_now()}))
        print(f"\nDone. Flagged files: {len(weak):,} (tag {REVIEW_TAG}, listed in {unverified_path})")
        print(f"Roll back with: {run_dir / 'rollback.sh'}")
        return 0
    finally:
        lock.close()


def command_rollback(args: argparse.Namespace) -> int:
    run_dir, manifest, root = load_bundle(args.state_dir)
    renames = [item for item in manifest["entries"] if item["action"] == "rename"]
    sidecars = [item for item in manifest["entries"] if item["sidecar"]]
    lock = acquire_lock(run_dir)
    try:
        for item in renames:
            original = item_paths(root, item)["source"]
            if original.exists() and not path_matches(original, item) and not any(
                path_matches(original, other) for other in renames if other is not item
            ):
                raise RenameError(f"Original path is occupied by an unrelated file: {original}")
        confirm("rollback", len(renames) + len(sidecars), args.yes)

        kept = 0
        backups = run_dir / "sidecar-backups"
        for item in sidecars:
            sidecar = canonical_relative(root, item["sidecar"])
            backup = backups / (item["sidecar"] + ".previous")
            if sidecar.exists():
                if sha256_file(sidecar) == sha256_text(item["sidecar_content"]):
                    if backup.exists():
                        shutil.copy2(backup, sidecar)
                    else:
                        sidecar.unlink()
                    append_log(run_dir, "sidecar-remove", sidecar, sidecar)
                else:
                    kept += 1
                    print(f"Keeping edited sidecar: {sidecar}", file=sys.stderr)

        progress = Progress("Staging rollback", len(renames))
        for index, item in enumerate(renames, start=1):
            label, current = locate_item(root, item)
            if label not in ("source", "rollback_temp"):
                rename_noreplace(current, item_paths(root, item)["rollback_temp"])
                append_log(run_dir, "rollback-stage", current, item_paths(root, item)["rollback_temp"])
            progress.update(index, item["source"], force=index == len(renames))
        progress = Progress("Restoring names", len(renames))
        for index, item in enumerate(renames, start=1):
            label, current = locate_item(root, item)
            if label == "rollback_temp":
                rename_noreplace(current, item_paths(root, item)["source"])
                append_log(run_dir, "restore", current, item_paths(root, item)["source"])
            elif label != "source":
                raise RenameError(f"Unexpected rollback state {label} for {item['source']}")
            progress.update(index, item["source"], force=index == len(renames))

        unverified_path = root / ".organize" / "unverified.tsv"
        snapshot = run_dir / "unverified.before.tsv"
        if snapshot.exists():
            shutil.copy2(snapshot, unverified_path)
        elif unverified_path.exists():
            targets = {item["target"] for item in manifest["entries"]}
            write_unverified(unverified_path, [row for row in read_unverified(unverified_path) if row["path"] not in targets])

        atomic_write(run_dir / "rolled-back.json", json_text({"action": "rollback", "completed_at": utc_now()}))
        print("\nRollback complete: original names restored and generated sidecars removed"
              + (f" ({kept} edited sidecars kept)." if kept else "."))
        return 0
    finally:
        lock.close()


def command_status(args: argparse.Namespace) -> int:
    _, manifest, root = load_bundle(args.state_dir)
    counts: Counter[str] = Counter()
    errors: list[str] = []
    for item in manifest["entries"]:
        try:
            if item["action"] == "unchanged":
                if not path_matches(canonical_relative(root, item["source"]), item):
                    raise RenameError(f"Unchanged file no longer matches the plan: {item['source']}")
                label = "source"
            else:
                label, _ = locate_item(root, item)
            counts[label] += 1
            if item["sidecar"]:
                counts["sidecar present" if canonical_relative(root, item["sidecar"]).exists() else "sidecar absent"] += 1
        except RenameError as error:
            counts["error"] += 1
            errors.append(str(error))
    print("Filesystem state")
    for label in ("source", "target", "temp", "rollback_temp", "sidecar present", "sidecar absent", "error"):
        print(f"  {label:16}: {counts[label]:,}")
    print(f"  skipped         : {len(manifest['skipped']):,}")
    for error in errors[:20]:
        print(f"  - {error}", file=sys.stderr)
    return 2 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="resolve capture times and write a no-change plan",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    plan.add_argument("root", help="archive directory to scan")
    plan.add_argument("--recursive", action="store_true", help="include nested directories")
    plan.add_argument("--sample", type=positive_integer, help="inspect a deterministic subset; cannot be applied")
    plan.add_argument("--extensions", type=parse_extensions, default=DEFAULT_EXTENSIONS, help="comma-separated media extensions")
    plan.add_argument("--rules", help="folder rules file: '<folder> zone <Area/City>' | 'shift +1h' | 'date YYYY-MM-DD' | 'clock local'")
    plan.add_argument("--default-zone", type=zone_name, default=DEFAULT_ZONE, help="zone for times that carry none")
    plan.add_argument("--min-year", type=int, default=DEFAULT_MIN_YEAR, help="reject capture dates before this year")
    plan.add_argument("--sidecars", choices=("needed", "all", "none"), default="needed",
                      help="needed: only where the file's own metadata lacks the local time and offset")
    plan.add_argument("--exiftool", default="exiftool", help="ExifTool executable")
    plan.add_argument("--batch-size", type=int, default=100, choices=range(1, 501), metavar="1..500")
    plan.add_argument("--max-cpu-temp", type=float, default=0, help="pause metadata reading above this many C (0 is off)")
    plan.add_argument("--cpu-temp-sensor", default="", help="hwmon name for --max-cpu-temp, e.g. k10temp")
    plan.add_argument("--state-base", default=str(default_state_base()), help="where plan/rollback bundles are kept")
    plan.set_defaults(func=command_plan)

    apply = subparsers.add_parser("apply", help="apply a plan")
    apply.add_argument("state_dir")
    apply.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    apply.add_argument("--accept-weak", action="store_true", help="rename and flag files that only have a weak date")
    apply.add_argument("--allow-skipped", action="store_true", help="leave skipped files untouched and apply the rest")
    apply.set_defaults(func=command_apply)

    rollback = subparsers.add_parser("rollback", help="undo an applied plan")
    rollback.add_argument("state_dir")
    rollback.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    rollback.set_defaults(func=command_rollback)

    status = subparsers.add_parser("status", help="reconcile a plan with the filesystem")
    status.add_argument("state_dir")
    status.set_defaults(func=command_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
