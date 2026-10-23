"""A video's moments: a few frames along it, from YouTube's storyboard (the
small pictures its seek bar shows), so a glance tells what the video is like.

Nothing of the video is downloaded: yt-dlp reads its page once, and only the
storyboard sheets the chosen frames are on are fetched (small JPEGs of 3x3
frames, from YouTube's image servers), kept on disk; the page cuts each frame
out of its sheet. A frame every ~15 s, between MIN and MAX of them."""

import asyncio
import json
import logging
import os
import shutil
import time
import urllib.request
from pathlib import Path

import yt_dlp

log = logging.getLogger("clipseerr.frames")

EVERY = 15
MIN, MAX = 6, 16
# Two readings of YouTube at once at most; the rest wait.
_gate = asyncio.Semaphore(2)
OPTIONS = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}


def count(duration: float) -> int:
    return max(MIN, min(MAX, round(duration / EVERY)))


def _storyboard(info: dict) -> dict | None:
    """The sharpest storyboard: sb0 first (frames of 262x180 or so)."""
    boards = [f for f in info.get("formats") or [] if f.get("format_note") == "storyboard" and f.get("fragments")]
    return min(boards, key=lambda f: f.get("format_id") or "sb9") if boards else None


def plan(duration: float, board: dict) -> tuple[list[dict], set[int]]:
    """The frames to show ({"t", "sheet", "col", "row"}) and the sheets they are on."""
    columns, rows, fps = board["columns"], board["rows"], board.get("fps") or 0.5
    per_sheet = columns * rows
    sheets = len(board["fragments"])
    frames, needed = [], set()
    n = count(duration)
    for k in range(n):
        # The middle of each stretch: no black first frame, no end card.
        t = duration * (k + 0.5) / n
        index = min(int(t * fps), sheets * per_sheet - 1)
        sheet, tile = divmod(index, per_sheet)
        frames.append({"t": round(t, 1), "sheet": sheet, "col": tile % columns, "row": tile // columns})
        needed.add(sheet)
    return frames, needed


def _fetch(url: str, out: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(2 * 1024 * 1024)
    tmp = out.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, out)


def _make(youtube_id: str, folder: Path) -> dict:
    with yt_dlp.YoutubeDL(OPTIONS) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=False)
    duration = float(info.get("duration") or 0)
    board = _storyboard(info)
    if not board or duration <= 0:
        return {"frames": [], "tile": None}
    frames, needed = plan(duration, board)
    folder.mkdir(parents=True, exist_ok=True)
    for sheet in sorted(needed):
        _fetch(board["fragments"][sheet]["url"], folder / f"{sheet}.jpg")
    return {"frames": frames, "tile": {"width": board["width"], "height": board["height"],
                                       "columns": board["columns"], "rows": board["rows"]}}


class Frames:
    def __init__(self, folder: Path):
        self.folder = folder

    def sheet(self, youtube_id: str, sheet: int) -> Path:
        return self.folder / youtube_id / f"{sheet}.jpg"

    async def get(self, youtube_id: str) -> dict:
        """The video's moments, made the first time it is asked, then kept."""
        folder = self.folder / youtube_id
        manifest = folder / "frames.json"
        if manifest.exists():
            os.utime(manifest)
            return json.loads(manifest.read_text())
        async with _gate:
            if manifest.exists():
                return json.loads(manifest.read_text())
            made = await asyncio.to_thread(_make, youtube_id, folder)
            folder.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps(made))
            return made

    def prune(self, keep: int = 2000) -> None:
        """The videos looked at least recently go first, past keep of them."""
        if not self.folder.exists():
            return
        found = sorted((p for p in self.folder.iterdir() if (p / "frames.json").exists()),
                       key=lambda p: (p / "frames.json").stat().st_mtime, reverse=True)
        for stale in found[keep:]:
            shutil.rmtree(stale, ignore_errors=True)
        # A making that died half way: its sheets without a manifest, an hour on.
        for part in self.folder.iterdir():
            if part.is_dir() and not (part / "frames.json").exists() and time.time() - part.stat().st_mtime > 3600:
                shutil.rmtree(part, ignore_errors=True)

