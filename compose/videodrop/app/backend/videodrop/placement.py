"""A finished download's files, moved from its work folder to where they
belong: its destination's folder, or the held folder for its caller to
fetch. A file never replaces one already there: a video downloaded twice
(its id is in its name) is the one there already, and the new copy goes."""

import errno
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

VIDEO = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".ts"}
SUBTITLES = {".srt", ".vtt", ".ass", ".ssa", ".ttml"}


@dataclass(frozen=True)
class Target:
    """Where files go: base/prefix/<the file's path in the work folder>.
    root: "destination" (base is the media root) or "held"."""

    root: str
    base: Path
    prefix: str


def kind_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return "video" if suffix in VIDEO else "subtitles" if suffix in SUBTITLES else "other"


def _move(source: Path, target: Path) -> bool:
    """source to target, never over an existing file; False when target was
    there already (source is removed either way)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # One step that fails if target exists: a link, then the source goes.
        os.link(source, target)
    except FileExistsError:
        source.unlink()
        return False
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK):
            raise
        # Another disk, or no links there: a copy beside the target, then renamed.
        if target.exists():
            source.unlink()
            return False
        partial = target.with_name(f".{target.name}.videodrop-part")
        shutil.copyfile(source, partial)
        if target.exists():
            partial.unlink()
            source.unlink()
            return False
        os.replace(partial, target)
    source.unlink()
    return True


def place(work_dir: Path, made: list[str], target: Target) -> list[dict]:
    """Moves each made file (relative to work_dir); what each one became,
    for JobStore.add_files."""
    placed = []
    for relative in made:
        source = (work_dir / relative).resolve()
        if work_dir.resolve() not in source.parents or not source.is_file():
            continue  # reported, but not in the work folder: never moved
        path = f"{target.prefix}/{relative}" if target.prefix else relative
        destination = target.base / path
        created = _move(source, destination)
        placed.append({"root": target.root, "path": path, "name": destination.name,
                       "size": destination.stat().st_size, "kind": kind_of(destination.name),
                       "existed": not created})
    return placed


def clear(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)
