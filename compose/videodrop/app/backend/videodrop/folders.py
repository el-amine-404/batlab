"""Folders under the media root (/media): where destinations point, and the
page's folder browser. Nothing outside the root is ever listed or written:
paths are relative to it, and one that leaves it (.., a link pointing out)
or enters videodrop's own folder is refused."""

import os
import re
import shutil
from pathlib import Path

from .config import OWN_FOLDER
from .domain import Invalid

# Letters, digits, space, _ . - in each part; no part "." or "..".
_PATH = re.compile(r"^[A-Za-z0-9 _.-]+(?:/[A-Za-z0-9 _.-]+)*$")
MAX_LISTED = 500


def normalise(rel: str) -> str:
    """"/music-videos/" → "music-videos"; "" is the root. Raises Invalid."""
    rel = (rel or "").strip().strip("/")
    if rel and (not _PATH.match(rel) or any(part in (".", "..") for part in rel.split("/"))):
        raise Invalid("Use a folder inside the media folder, with letters, digits, spaces, _ . and - only.")
    if any(len(part.encode()) > 255 for part in rel.split("/")):
        # The disk's limit for one name: past it, the system refuses (it was a 500).
        raise Invalid("A folder's name is 255 characters at most.")
    if rel.split("/")[0] == OWN_FOLDER:
        raise Invalid(f"{OWN_FOLDER} is videodrop's own folder.")
    return rel


def inside(root: Path, rel: str) -> Path:
    """root/rel, resolved; raises Invalid when it leaves the root."""
    path = (root / rel).resolve() if rel else root.resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise Invalid("That folder is outside the media folder.")
    return path


def listing(root: Path, rel: str) -> dict:
    """The folders inside root/rel, hidden ones left out."""
    rel = normalise(rel)
    path = inside(root, rel)
    if not path.is_dir():
        raise Invalid("That folder doesn't exist.")
    names = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_dir() or not _PATH.match(entry.name):
                continue
            if entry.is_symlink():
                try:
                    inside(root, f"{rel}/{entry.name}" if rel else entry.name)
                except Invalid:
                    continue
            names.append(entry.name)
    names.sort(key=str.casefold)
    return {
        "path": rel,
        "parent": rel.rpartition("/")[0] if rel else None,
        "folders": [{"name": name, "path": f"{rel}/{name}" if rel else name} for name in names[:MAX_LISTED]],
        "truncated": len(names) > MAX_LISTED,
    }


def check(root: Path, rel: str) -> dict:
    """Whether videos can go to root/rel: it is writable, or the nearest
    folder above it that exists is (videodrop makes the rest)."""
    result = {"path": rel, "ok": False, "exists": False, "message": "", "free_bytes": None}
    try:
        rel = result["path"] = normalise(rel)
        if not rel:
            raise Invalid("Choose a folder inside the media folder, not the media folder itself.")
        path = inside(root, rel)
    except Invalid as exc:
        result["message"] = str(exc)
        return result
    existing = path
    while not existing.exists() and existing != root:
        existing = existing.parent
    if existing.exists() and not existing.is_dir():
        result["message"] = f"{existing.relative_to(root)} is a file, not a folder."
        return result
    if not existing.exists():
        result["message"] = "The media folder isn't mounted."
        return result
    result["exists"] = existing == path
    result["free_bytes"] = shutil.disk_usage(existing).free
    if not os.access(existing, os.W_OK | os.X_OK):
        result["message"] = "videodrop can't write there."
        return result
    result["ok"] = True
    result["message"] = "Videos can be written here." if result["exists"] else "It will be made with the first video."
    return result


def free_bytes(root: Path) -> int | None:
    try:
        return shutil.disk_usage(root).free
    except OSError:
        return None
