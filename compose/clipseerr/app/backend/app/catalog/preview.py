"""A video's preview for its card: a clip of a few seconds with its sound,
cut here and kept on disk, played by the page's own <video>, so no YouTube
player shows its title, logo or "Watch on YouTube" over it.

YouTube serves its one-file 360p stream (format 18) only from its start to a
client without a proof-of-origin token; its separate streams, the 360p H.264
picture and the AAC sound, it serves all through to yt-dlp's default
clients, which answer its challenge with Deno. ffmpeg reads just the clip's
stretch of each (HTTP ranges, from a third in) and joins them, copied, not
re-encoded: about a megabyte, made in a few seconds, then instant."""

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path

import yt_dlp

log = logging.getLogger("clipseerr.preview")

# Seconds a clip plays, and where it starts: a third in, past the intro.
LENGTH = 15
START_AT = 1 / 3

OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    # The 360p H.264 picture and the AAC sound, both plain HTTPS (ranges); a
    # smaller picture, or the one-file stream, when a video lacks it.
    "format": ("bv*[height<=360][vcodec^=avc1][protocol=https]+ba[ext=m4a][protocol=https]"
               "/bv*[height<=480][vcodec^=avc1][protocol=https]+ba[protocol=https]"
               "/b[height<=480][protocol=https]"),
}


def _headers(fmt: dict) -> list[str]:
    """ffmpeg's options to ask for a stream as yt-dlp would."""
    found = fmt.get("http_headers") or {}
    options = []
    if found.get("User-Agent"):
        options += ["-user_agent", found["User-Agent"]]
    extra = "".join(f"{k}: {v}\r\n" for k, v in found.items() if k not in ("User-Agent", "Accept"))
    if extra:
        options += ["-headers", extra]
    return options


def start_of(duration: float | None) -> float:
    """Where the clip starts: a third in, but never so late that it would run short."""
    if not duration or duration <= LENGTH + 5:
        return 0.0
    return round(min(duration * START_AT, duration - LENGTH - 1), 1)


def cut(youtube_id: str, out: Path) -> Path:
    """Makes out, the video's clip; raises when YouTube or ffmpeg won't, twice."""
    try:
        return _cut_once(youtube_id, out)
    except Exception:
        # YouTube refuses a stream now and then: once more, with new addresses.
        time.sleep(1)
        return _cut_once(youtube_id, out)


def _quiet(stderr: bytes) -> str:
    """ffmpeg's complaint, without the streams' long addresses."""
    text = re.sub(r"https?://\S+", "<stream>", stderr.decode(errors="replace"))
    return " / ".join(line.strip() for line in text.splitlines() if line.strip())[-300:]


def _cut_once(youtube_id: str, out: Path, timeout: int = 60) -> Path:
    with yt_dlp.YoutubeDL(OPTIONS) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={youtube_id}", download=False)
    streams = info.get("requested_formats") or [info]
    start = start_of(info.get("duration"))
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for stream in streams:
        if not stream.get("url"):
            raise LookupError(f"no stream address for {youtube_id}")
        # Seeking each input before reading it: ffmpeg asks for the bytes of the clip's stretch only.
        command += [*_headers(stream), "-ss", str(start), "-t", str(LENGTH), "-i", stream["url"]]
    command += ["-map", "0:v:0", "-map", f"{len(streams) - 1}:a:0", "-c", "copy",
                # The index first: the page plays it before it has it all.
                "-movflags", "+faststart", "-f", "mp4"]
    partial = out.with_suffix(".part")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run([*command, str(partial)], capture_output=True, timeout=timeout, check=False)
        if done.returncode or not partial.exists() or partial.stat().st_size < 10_000:
            raise RuntimeError(f"ffmpeg couldn't cut {youtube_id}: {_quiet(done.stderr) or 'no clip'}")
        os.replace(partial, out)
    finally:
        partial.unlink(missing_ok=True)
    return out


def prune(folder: Path, keep_bytes: int) -> None:
    """The clips least recently played go first, until the folder is under keep_bytes."""
    clips = sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in clips)
    for clip in clips:
        if total <= keep_bytes:
            break
        total -= clip.stat().st_size
        clip.unlink(missing_ok=True)


class Clips:
    """The clips kept on disk (state/previews), made on demand: one cut per
    video at a time, a few at once, a failure not tried again for a while,
    and the folder kept under KEEP_BYTES (about 1 MB a clip)."""

    KEEP_BYTES = 500 * 1024 * 1024
    AT_ONCE = 3
    RETRY_AFTER = 30 * 60

    def __init__(self, folder: Path):
        self.folder = folder
        self._cutting: dict[str, "asyncio.Task[Path]"] = {}
        self._failed: dict[str, float] = {}
        self._gate: asyncio.Semaphore | None = None

    def path(self, youtube_id: str) -> Path:
        return self.folder / f"{youtube_id}.mp4"

    async def get(self, youtube_id: str) -> Path:
        """The video's clip, cut first if needed; raises LookupError when it can't be."""
        out = self.path(youtube_id)
        if out.exists():
            # Recently played: the last pruned.
            os.utime(out)
            return out
        if time.monotonic() - self._failed.get(youtube_id, -1e9) < self.RETRY_AFTER:
            raise LookupError(f"no preview for {youtube_id} for now")
        task = self._cutting.get(youtube_id)
        if task is None:
            task = asyncio.create_task(self._cut(youtube_id, out))
            self._cutting[youtube_id] = task
            task.add_done_callback(lambda _: self._cutting.pop(youtube_id, None))
        # Shielded: a page that stops waiting doesn't stop the cut, the next hover gets it.
        return await asyncio.shield(task)

    async def _cut(self, youtube_id: str, out: Path) -> Path:
        if self._gate is None:
            self._gate = asyncio.Semaphore(self.AT_ONCE)
        async with self._gate:
            try:
                await asyncio.to_thread(cut, youtube_id, out)
            except Exception as error:
                self._failed[youtube_id] = time.monotonic()
                log.warning("preview of %s: %s", youtube_id, str(error)[:300])
                raise LookupError(f"no preview for {youtube_id}") from error
        await asyncio.to_thread(prune, self.folder, self.KEEP_BYTES)
        return out
