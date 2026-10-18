"""The YouTube cookies videodrop may hold: a cookies.txt exported from a
browser where the account is signed in, for videos YouTube shows only to a
signed-in viewer (age-restricted ones). One file for every download, kept in
the state folder, never sent back to the page; each job gets its own copy,
because yt-dlp writes the cookies back when it is done.
"""

import os
import time
from pathlib import Path

from .domain import Invalid

MAX_BYTES = 1_000_000
NAME = "cookies.txt"
HEADERS = ("# Netscape HTTP Cookie File", "# HTTP Cookie File")


class BadCookies(Invalid):
    """The message is shown to the user as is."""


def path(state_dir: Path) -> Path:
    return state_dir / NAME


def _rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            raise BadCookies("That isn't a cookies.txt file (Netscape format): a line doesn't have 7 fields.")
        rows.append(fields)
    return rows


def save(state_dir: Path, data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise BadCookies("That file is too big for a cookies.txt.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadCookies("That isn't a cookies.txt file (it isn't text).") from exc
    if not text.lstrip().startswith(HEADERS):
        raise BadCookies("That isn't a cookies.txt file: it should start with \"# Netscape HTTP Cookie File\".")
    if not any("youtube.com" in row[0] for row in _rows(text)):
        raise BadCookies("This file has no YouTube cookies. Export it from youtube.com while signed in.")
    target = path(state_dir)
    temporary = target.with_name("." + NAME + ".tmp")
    with open(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as out:
        out.write(text)
    os.replace(temporary, target)
    return status(state_dir)


def delete(state_dir: Path) -> None:
    path(state_dir).unlink(missing_ok=True)


def status(state_dir: Path) -> dict:
    """What the page shows: whether there are cookies, since when, and when the
    YouTube ones run out. Never the cookies themselves."""
    target = path(state_dir)
    try:
        text = target.read_text(encoding="utf-8")
        saved = target.stat().st_mtime
    except OSError:
        return {"present": False}
    expiries = []
    for row in _rows(text):
        if "youtube.com" in row[0] and row[4].isdigit() and int(row[4]) > 0:
            expiries.append(int(row[4]))
    expires = min((e for e in expiries if e > time.time()), default=None)
    return {
        "present": True,
        "saved_at": saved,
        "expires_at": expires,
        "expired": bool(expiries) and expires is None,
    }


def copy_for(state_dir: Path, job_dir: Path) -> str | None:
    """A private copy for one job, or None without cookies."""
    try:
        text = path(state_dir).read_text(encoding="utf-8")
    except OSError:
        return None
    copy = job_dir / ".cookies.txt"
    with open(os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as out:
        out.write(text)
    return str(copy)
