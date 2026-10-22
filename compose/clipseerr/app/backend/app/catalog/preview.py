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
import secrets
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yt_dlp

log = logging.getLogger("clipseerr.preview")
# YouTube's answer once an address asks too much: "Sign in to confirm you're not a bot".
BOT_CHECK = re.compile(r"confirm you.re not a bot|sign in to confirm", re.I)

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


# YouTube serves a read of 1 MB at full speed (about 200 Mbit/s here), an
# open or larger one at 1 Mbit/s, past a few MB not at all (403): ffmpeg's
# reads, always open, go through the relay below, which asks in pieces.
PIECE = 1024 * 1024
_RANGE = re.compile(r"bytes=(\d+)-(\d*)")


class _Relay(BaseHTTPRequestHandler):
    """ffmpeg's reads of a stream, asked of YouTube PIECE by PIECE. Only on
    127.0.0.1, and only for the streams of the cuts in progress, under names
    made for them (STREAMS)."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _piece(self, url: str, headers: dict, start: int, end: int):
        request = urllib.request.Request(url, headers={**headers, "Range": f"bytes={start}-{end}"})
        for attempt in range(2):
            try:
                return urllib.request.urlopen(request, timeout=20)
            except OSError:
                if attempt:
                    raise
                time.sleep(0.3)

    def do_GET(self):
        stream = STREAMS.get(self.path.lstrip("/"))
        if stream is None:
            self.send_error(404)
            return
        url, headers = stream
        asked = _RANGE.match(self.headers.get("Range") or "")
        start = int(asked[1]) if asked else 0
        try:
            first = self._piece(url, headers, start, start + PIECE - 1)
        except OSError:
            self.send_error(502)
            return
        total = int((first.headers.get("Content-Range") or "/0").rsplit("/", 1)[1] or 0)
        if not total:
            first.close()
            self.send_error(502)
            return
        end = total - 1
        if asked and asked[2]:
            end = min(end, int(asked[2]))
        self.send_response(206 if asked else 200)
        self.send_header("Content-Type", first.headers.get("Content-Type", "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if asked:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.end_headers()
        position, answer = start, first
        try:
            while position <= end:
                while chunk := answer.read(min(65536, end - position + 1)):
                    self.wfile.write(chunk)
                    position += len(chunk)
                    if position > end:
                        break
                answer.close()
                if position <= end:
                    answer = self._piece(url, headers, position, min(position + PIECE - 1, end))
        except OSError:
            # ffmpeg has what it wanted and hung up (it does, after a seek), or YouTube failed.
            self.close_connection = True
        finally:
            answer.close()


STREAMS: dict[str, tuple[str, dict]] = {}
_relay: ThreadingHTTPServer | None = None
_relay_lock = threading.Lock()


def _relay_port() -> int:
    global _relay
    with _relay_lock:
        if _relay is None:
            _relay = ThreadingHTTPServer(("127.0.0.1", 0), _Relay)
            _relay.daemon_threads = True
            threading.Thread(target=_relay.serve_forever, name="preview-relay", daemon=True).start()
        return _relay.server_address[1]


def _headers(fmt: dict) -> dict:
    """The headers to ask for a stream as yt-dlp would."""
    return {k: v for k, v in (fmt.get("http_headers") or {}).items() if k.lower() != "accept"}


def start_of(duration: float | None) -> float:
    """Where the clip starts: a third in, but never so late that it would run short."""
    if not duration or duration <= LENGTH + 5:
        return 0.0
    return round(min(duration * START_AT, duration - LENGTH - 1), 1)


def cut(youtube_id: str, out: Path) -> Path:
    """Makes out, the video's clip; raises when YouTube or ffmpeg won't, twice."""
    try:
        return _cut_once(youtube_id, out)
    except Exception as error:
        if BOT_CHECK.search(str(error)):
            raise
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
    port = _relay_port()
    names = []
    for stream in streams:
        if not stream.get("url"):
            raise LookupError(f"no stream address for {youtube_id}")
        name = secrets.token_urlsafe(16)
        STREAMS[name] = (stream["url"], _headers(stream))
        names.append(name)
        # Seeking each input before reading it: ffmpeg asks for the bytes of the clip's stretch only.
        command += ["-ss", str(start), "-t", str(LENGTH), "-i", f"http://127.0.0.1:{port}/{name}"]
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
        for name in names:
            STREAMS.pop(name, None)
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


class Busy(Exception):
    """No new clip for now: the budget is spent, or YouTube asked to prove we aren't a bot."""

    def __init__(self, seconds: int):
        super().__init__(f"previews paused for {seconds} s")
        self.seconds = seconds


class Clips:
    """The clips kept on disk (state/previews), made on demand: one cut per
    video at a time, a few at once, a failure not tried again for a while,
    and the folder kept under KEEP_BYTES (about 1 MB a clip).

    Gentle with YouTube, whose bot check would also stop songdrop and videodrop
    (same address): at most BUDGET new clips per WINDOW, and none for PAUSE
    once YouTube asks to sign in. Clips already cut are always served."""

    KEEP_BYTES = 500 * 1024 * 1024
    AT_ONCE = 2
    RETRY_AFTER = 30 * 60
    BUDGET = 12
    WINDOW = 10 * 60
    PAUSE = 60 * 60

    def __init__(self, folder: Path):
        self.folder = folder
        self._cutting: dict[str, "asyncio.Task[Path]"] = {}
        self._failed: dict[str, float] = {}
        self._started: list[float] = []
        self._paused_until = 0.0
        # Bound to the loop when first waited on (Python 3.10+), not here.
        self._gate = asyncio.Semaphore(self.AT_ONCE)

    def path(self, youtube_id: str) -> Path:
        return self.folder / f"{youtube_id}.mp4"

    def _admit(self) -> None:
        """Counts a new cut against the budget; raises Busy when there's none left."""
        now = time.monotonic()
        if now < self._paused_until:
            raise Busy(int(self._paused_until - now))
        self._started = [t for t in self._started if now - t < self.WINDOW]
        if len(self._started) >= self.BUDGET:
            raise Busy(int(self.WINDOW - (now - self._started[0])) + 1)
        self._started.append(now)

    async def get(self, youtube_id: str) -> Path:
        """The video's clip, cut first if needed; raises LookupError when it
        can't be, Busy when not now."""
        out = self.path(youtube_id)
        if out.exists():
            # Recently played: the last pruned.
            os.utime(out)
            return out
        if time.monotonic() - self._failed.get(youtube_id, -1e9) < self.RETRY_AFTER:
            raise LookupError(f"no preview for {youtube_id} for now")
        task = self._cutting.get(youtube_id)
        if task is None:
            self._admit()
            task = asyncio.create_task(self._cut(youtube_id, out))
            self._cutting[youtube_id] = task
            task.add_done_callback(lambda _: self._cutting.pop(youtube_id, None))
        # Shielded: a page that stops waiting doesn't stop the cut, the next hover gets it.
        return await asyncio.shield(task)

    async def _cut(self, youtube_id: str, out: Path) -> Path:
        async with self._gate:
            if time.monotonic() < self._paused_until:
                raise Busy(int(self._paused_until - time.monotonic()))
            try:
                await asyncio.to_thread(cut, youtube_id, out)
            except Exception as error:
                if BOT_CHECK.search(str(error)):
                    self._paused_until = time.monotonic() + self.PAUSE
                    log.warning("YouTube asks to prove clipseerr isn't a bot: no new preview for %d min",
                                self.PAUSE // 60)
                    raise Busy(self.PAUSE) from error
                self._failed[youtube_id] = time.monotonic()
                log.warning("preview of %s: %s", youtube_id, str(error)[:300])
                raise LookupError(f"no preview for {youtube_id}") from error
        await asyncio.to_thread(prune, self.folder, self.KEEP_BYTES)
        return out
