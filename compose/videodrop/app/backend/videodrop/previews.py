"""What the page shows of a finished video without playing it: its details
as ffprobe reads them (codecs, bitrates, tracks, chapters), pictures taken
across it (a gallery), a storyboard (a small picture every few seconds, all
in one image, for the player's seek bar) and its subtitle tracks as WebVTT,
which a browser can show (an mp4's own subtitles it can't).

ffmpeg makes them the first time they are asked for, or right after the
download (prepare). They are kept in previews_dir/<key>, the key being the
file's identity (device, inode, size, time): a file reused by a later job
shares them, a changed file gets new ones, and sweep() drops those of files
gone. Pictures are taken at keyframes only, without decoding what lies
between: a 4K video gives its storyboard in seconds, not minutes."""

import hashlib
import json
import logging
import math
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


log = logging.getLogger("videodrop")

# The gallery: a picture at each chapter's start when there are this many
# chapters; else pictures evenly across the video, more for a longer one.
CHAPTER_FRAMES = (3, 24)
FRAMES_BY_LENGTH = ((60, 4), (10 * 60, 8), (40 * 60, 16))
MOST_FRAMES = 24
FRAME_WIDTH = 640
TILE = (160, 90)
COLUMNS = 10
MAX_TILES = 100
# Seconds between two storyboard pictures, at least.
MIN_EVERY = 2.0
# ffmpeg processes taking one video's pictures at once.
AT_ONCE = 4
TIMEOUT = 60
# Subtitles a browser can be given as text; the others are pictures (DVD, Blu-ray).
TEXT_SUBTITLES = {"mov_text", "subrip", "srt", "webvtt", "ass", "ssa", "text"}
# A preview folder no file has any more is dropped after this long (one being made has no file yet).
UNUSED_FOR = 3600
SAVED = "details.json"
# details.json made by an older videodrop (other pictures) is made again.
VERSION = 2


def _ratio(value) -> float | None:
    """ffprobe's "30000/1001" as 29.97."""
    try:
        top, _, bottom = str(value).partition("/")
        result = float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(result, 3) if result > 0 else None


def _kbps(stream: dict) -> float | None:
    raw = stream.get("bit_rate") or (stream.get("tags") or {}).get("BPS")
    try:
        return round(int(raw) / 1000, 1)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# What mp4 names a track with no name of its own.
_GENERIC_HANDLERS = {"", "SubtitleHandler", "VideoHandler", "SoundHandler", "Core Media Text"}


def _title(stream: dict) -> str | None:
    """A track's name: mkv says title; mp4 keeps it as the handler's name."""
    tags = stream.get("tags") or {}
    handler = tags.get("handler_name") or ""
    return tags.get("title") or (handler if handler not in _GENERIC_HANDLERS else None)


def gallery(duration: float, chapters: list[dict]) -> list[dict]:
    """When the gallery's pictures are taken: {time, chapter}."""
    if duration <= 0:
        return []
    marked = [c for c in chapters if c["end"] > c["start"]]
    if CHAPTER_FRAMES[0] <= len(marked) <= CHAPTER_FRAMES[1]:
        # A little into each chapter: its first frame is often a fade from black.
        return [{"time": round(min(c["start"] + min(2.0, (c["end"] - c["start"]) / 4), duration), 2),
                 "chapter": c["title"]} for c in marked]
    count = next((n for longest, n in FRAMES_BY_LENGTH if duration < longest), MOST_FRAMES)
    return [{"time": round(duration * (n + 0.5) / count, 2), "chapter": None} for n in range(count)]


def _range(stream: dict) -> str:
    transfer = stream.get("color_transfer")
    return "HDR10 (PQ)" if transfer == "smpte2084" else "HLG" if transfer == "arib-std-b67" else "SDR"


def read(path: Path) -> dict:
    """The file as ffprobe reads it; raises ValueError when it can't."""
    try:
        answer = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-show_chapters",
                                 "-of", "json", str(path)], capture_output=True, text=True, timeout=TIMEOUT,
                                check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"ffprobe couldn't read it ({exc.__class__.__name__})") from exc
    if answer.returncode != 0:
        raise ValueError(f"ffprobe couldn't read it: {answer.stderr.strip()[:200]}")
    found = json.loads(answer.stdout or "{}")
    fmt = found.get("format") or {}
    streams = found.get("streams") or []
    pictures = [s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")]
    video = None
    if pictures:
        v = pictures[0]
        video = {"codec": v.get("codec_name"), "profile": v.get("profile"), "width": _int(v.get("width")),
                 "height": _int(v.get("height")), "fps": _ratio(v.get("avg_frame_rate") or v.get("r_frame_rate")),
                 "kbps": _kbps(v), "pixel_format": v.get("pix_fmt"),
                 "bit_depth": _int(v.get("bits_per_raw_sample")) or (10 if "10" in (v.get("pix_fmt") or "") else 8),
                 "dynamic_range": _range(v)}
    audio = [{"codec": a.get("codec_name"), "profile": a.get("profile"), "channels": _int(a.get("channels")),
              "layout": a.get("channel_layout"), "sample_rate": _int(a.get("sample_rate")), "kbps": _kbps(a),
              "language": (a.get("tags") or {}).get("language")}
             for a in streams if a.get("codec_type") == "audio"]
    subtitles = [{"index": n, "codec": s.get("codec_name"), "language": (s.get("tags") or {}).get("language"),
                  "title": _title(s), "text": s.get("codec_name") in TEXT_SUBTITLES}
                 for n, s in enumerate(x for x in streams if x.get("codec_type") == "subtitle")]
    chapters = [{"start": float(c.get("start_time") or 0), "end": float(c.get("end_time") or 0),
                 "title": (c.get("tags") or {}).get("title")} for c in found.get("chapters") or []]
    duration = None
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        pass
    return {"container": fmt.get("format_name"), "duration": duration, "kbps": _kbps(fmt), "video": video,
            "audio": audio, "subtitles": subtitles, "chapters": chapters}


# JPEG takes full-range colours only; most videos are limited-range.
_JPEG = "scale=out_range=full,format=yuvj420p"


def _picture(path: Path, at: float, out: Path, scale: str) -> bool:
    """The video near at, scaled, as a JPEG; False when there is none. The
    keyframe at or before at, decoded alone: fast. A video with keyframes
    too far apart gives none that way: then the frame at, decoded from its
    keyframe (slower)."""
    for fast in (["-skip_frame", "nokey", "-noaccurate_seek"], []):
        command = ["ffmpeg", "-v", "error", "-nostdin", "-y", *fast, "-ss", f"{at:.3f}", "-i", str(path), "-an",
                   "-sn", "-dn", "-frames:v", "1", "-vf", f"{scale},{_JPEG}", "-q:v", "4", str(out)]
        try:
            subprocess.run(command, capture_output=True, timeout=TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if out.is_file() and out.stat().st_size > 0:
            return True
    return False


class Previews:
    def __init__(self, folder: Path):
        self._folder = folder
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        # Videos worked on at once (each with AT_ONCE ffmpeg); the others wait their turn.
        self._busy = threading.Semaphore(2)
        self._background = ThreadPoolExecutor(1, thread_name_prefix="previews")

    def key(self, path: Path) -> str:
        found = path.stat()
        raw = f"{found.st_dev}:{found.st_ino}:{found.st_size}:{found.st_mtime_ns}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _home(self, path: Path) -> Path:
        return self._folder / self.key(path)

    # ---- what the API asks for ---------------------------------------------------------------

    def details(self, path: Path) -> dict:
        """read(path), with when its pictures are taken: frames (times) and
        storyboard (count, every…). Raises ValueError when ffprobe can't read it."""
        home = self._home(path)
        saved = home / SAVED
        if (found := self._saved(saved)) is not None:
            return found
        with self._lock(f"{home.name}:details"):
            if (found := self._saved(saved)) is not None:
                return found
            # What an older videodrop made is made again.
            shutil.rmtree(home, ignore_errors=True)
            details = read(path)
            duration = details["duration"] or 0
            pictures = details["video"] is not None and duration > 0
            details["version"] = VERSION
            details["frames"] = gallery(duration, details["chapters"]) if pictures else []
            details["storyboard"] = None
            if pictures:
                count = max(1, min(MAX_TILES, int(duration // MIN_EVERY)))
                details["storyboard"] = {"count": count, "columns": min(COLUMNS, count), "width": TILE[0],
                                         "height": TILE[1], "every": duration / count}
            home.mkdir(parents=True, exist_ok=True)
            partial = home / f".{SAVED}"
            partial.write_text(json.dumps(details))
            partial.replace(saved)
            return details

    @staticmethod
    def _saved(saved: Path) -> dict | None:
        try:
            found = json.loads(saved.read_text())
        except (OSError, ValueError):
            return None
        return found if found.get("version") == VERSION else None

    def frame(self, path: Path, n: int) -> Path | None:
        """The gallery's picture n; None when there's none."""
        details = self.details(path)
        if not 0 <= n < len(details["frames"]):
            return None
        home = self._home(path)
        self._make_frames(path, home, details)
        found = home / f"frame-{n}.jpg"
        return found if found.is_file() else None

    def storyboard(self, path: Path) -> Path | None:
        details = self.details(path)
        if not details["storyboard"]:
            return None
        home = self._home(path)
        self._make_storyboard(path, home, details["storyboard"])
        found = home / "storyboard.jpg"
        return found if found.is_file() else None

    def subtitles(self, path: Path, n: int) -> Path | None:
        """Subtitle track n as WebVTT; None when it isn't a text track."""
        details = self.details(path)
        track = next((s for s in details["subtitles"] if s["index"] == n and s["text"]), None)
        if not track:
            return None
        home = self._home(path)
        out = home / f"subtitles-{n}.vtt"
        with self._lock(f"{home.name}:subtitles-{n}"):
            if not out.is_file():
                partial = home / f".subtitles-{n}.vtt"
                try:
                    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(path), "-map", f"0:s:{n}",
                                    "-f", "webvtt", str(partial)], capture_output=True, timeout=TIMEOUT, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    return None
                if partial.is_file():
                    partial.replace(out)
        return out if out.is_file() else None

    # ---- making the pictures -------------------------------------------------------------------

    def _make_frames(self, path: Path, home: Path, details: dict) -> None:
        if (home / "frames.done").is_file():
            return
        with self._lock(f"{home.name}:frames"), self._busy:
            if (home / "frames.done").is_file():
                return
            started = time.monotonic()
            scale = f"scale={FRAME_WIDTH}:-2"
            with ThreadPoolExecutor(AT_ONCE) as pool:
                list(pool.map(lambda job: _picture(path, job[1]["time"], home / f"frame-{job[0]}.jpg", scale),
                              enumerate(details["frames"])))
            (home / "frames.done").touch()
            log.info("gallery of %s in %.1f s", path.name, time.monotonic() - started)

    def _make_storyboard(self, path: Path, home: Path, board: dict) -> None:
        out = home / "storyboard.jpg"
        if out.is_file():
            return
        with self._lock(f"{home.name}:storyboard"), self._busy:
            if out.is_file():
                return
            started = time.monotonic()
            tiles = home / ".tiles"
            shutil.rmtree(tiles, ignore_errors=True)
            tiles.mkdir(parents=True)
            width, height = board["width"], board["height"]
            scale = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                     f"pad={width}:{height}:-1:-1:color=black")
            times = [n * board["every"] for n in range(board["count"])]
            try:
                with ThreadPoolExecutor(AT_ONCE) as pool:
                    made = list(pool.map(lambda job: _picture(path, job[1], tiles / f"{job[0]:03d}.jpg", scale),
                                         enumerate(times)))
                if not made[0]:
                    return
                # ffmpeg reads the pictures in order and stops at a gap: a missing one is its neighbour.
                for n in range(1, len(made)):
                    if not made[n]:
                        shutil.copyfile(tiles / f"{n - 1:03d}.jpg", tiles / f"{n:03d}.jpg")
                rows = math.ceil(board["count"] / board["columns"])
                partial = home / ".storyboard.jpg"
                subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(tiles / "%03d.jpg"), "-vf",
                                f"tile={board['columns']}x{rows}", "-frames:v", "1", "-q:v", "5", str(partial)],
                               capture_output=True, timeout=TIMEOUT, check=False)
                if partial.is_file():
                    partial.replace(out)
            except (OSError, subprocess.TimeoutExpired):
                log.warning("no storyboard for %s", path.name)
            finally:
                shutil.rmtree(tiles, ignore_errors=True)
            log.info("storyboard of %s in %.1f s", path.name, time.monotonic() - started)

    # ---- in the background --------------------------------------------------------------------

    def prepare(self, paths: list[Path]) -> None:
        """Makes everything for these videos, one after the other, in the
        background: ready by the time someone looks."""
        for path in paths:
            self._background.submit(self._prepare, path)

    def _prepare(self, path: Path) -> None:
        try:
            details = self.details(path)
            home = self._home(path)
            self._make_frames(path, home, details)
            if details["storyboard"]:
                self._make_storyboard(path, home, details["storyboard"])
        except (OSError, ValueError) as exc:
            log.warning("no previews for %s: %s", path.name, exc)

    def sweep(self, files: list[Path]) -> int:
        """Drops the previews no file has any more (files: every file there
        is); how many."""
        keep = set()
        for path in files:
            try:
                keep.add(self.key(path))
            except OSError:
                pass
        dropped = 0
        if not self._folder.is_dir():
            return 0
        for home in self._folder.iterdir():
            if home.name in keep:
                continue
            try:
                if time.time() - home.stat().st_mtime < UNUSED_FOR:
                    continue
            except OSError:
                continue
            shutil.rmtree(home, ignore_errors=True)
            dropped += 1
        with self._guard:
            self._locks = {name: lock for name, lock in self._locks.items() if name.split(":")[0] in keep}
        return dropped

    def close(self) -> None:
        self._background.shutdown(wait=False, cancel_futures=True)
