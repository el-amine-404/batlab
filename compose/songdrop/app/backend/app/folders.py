"""The folders under the output root (/output), for the Settings page's
folder browser and its check of the output folder. Nothing outside the root
is ever listed: paths are relative to it, and one that leaves it (.., a link
pointing out) is refused."""

import os
import re
import shutil
from pathlib import Path

# Letters, digits, space, _ . - in each part; no part "." or "..".
_PATH = re.compile(r"^[A-Za-z0-9 _.-]+(?:/[A-Za-z0-9 _.-]+)*$")
MAX_LISTED = 500


class Outside(ValueError):
    """The message is shown to the user as is."""


def normalise(rel: str) -> str:
    """"/music/songdrop/" → "music/songdrop"; "" is the root. Raises Outside."""
    rel = (rel or "").strip().strip("/")
    if rel and (not _PATH.match(rel) or any(part in (".", "..") for part in rel.split("/"))):
        raise Outside("Use a folder inside the output folder, with letters, digits, spaces, _ . and - only.")
    return rel


def _inside(root: Path, rel: str) -> Path:
    path = (root / rel).resolve() if rel else root.resolve()
    if path != root.resolve() and root.resolve() not in path.parents:
        raise Outside("That folder is outside the output folder.")
    return path


def listing(root: Path, rel: str) -> dict:
    """The folders inside root/rel, hidden ones left out."""
    rel = normalise(rel)
    path = _inside(root, rel)
    if not path.is_dir():
        raise Outside("That folder doesn't exist.")
    names = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_dir() or not _PATH.match(entry.name):
                continue
            if entry.is_symlink():
                try:
                    _inside(root, f"{rel}/{entry.name}" if rel else entry.name)
                except Outside:
                    continue
            names.append(entry.name)
    names.sort(key=str.casefold)
    return {
        "root": str(root),
        "path": rel,
        "parent": rel.rpartition("/")[0] if rel else None,
        "folders": [{"name": name, "path": f"{rel}/{name}" if rel else name} for name in names[:MAX_LISTED]],
        "truncated": len(names) > MAX_LISTED,
    }


def check(root: Path, rel: str) -> dict:
    """Whether songs can go to root/rel: it is writable, or the nearest
    folder above it that exists is (songdrop makes the rest)."""
    result = {"path": rel, "ok": False, "exists": False, "message": "", "free_bytes": None}
    try:
        rel = result["path"] = normalise(rel)
        if not rel:
            raise Outside("Choose a folder inside the output folder, not the output folder itself.")
        path = _inside(root, rel)
    except Outside as exc:
        result["message"] = str(exc)
        return result
    existing = path
    while not existing.exists() and existing != root:
        existing = existing.parent
    if existing.exists() and not existing.is_dir():
        result["message"] = f"{existing.relative_to(root)} is a file, not a folder."
        return result
    if not existing.exists():
        result["message"] = "The output folder isn't mounted."
        return result
    result["exists"] = existing == path
    result["free_bytes"] = shutil.disk_usage(existing).free
    if not os.access(existing, os.W_OK | os.X_OK):
        result["message"] = "songdrop can't write there."
        return result
    result["ok"] = True
    result["message"] = "Songs can be written here." if result["exists"] else "It will be made with the first song."
    return result
