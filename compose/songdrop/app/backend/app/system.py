"""System status (GET /api/v1/system/status): the tools songdrop runs and
their versions, the space left where it writes, and its jobs at a glance."""

import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import get_args

from .config import Settings
from .schemas import JobStatus

STARTED_AT = time.time()
_versions: list[dict] | None = None
_versions_lock = threading.Lock()


def _first_line(command: list[str]) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (done.stdout or done.stderr).strip()
    return text.splitlines()[0] if done.returncode == 0 and text else None


def _ffmpeg() -> str | None:
    line = _first_line(["ffmpeg", "-version"])  # "ffmpeg version 7.1.1-… Copyright …"
    return line.split()[2] if line and len(line.split()) > 2 else None


def versions(settings: Settings) -> list[dict]:
    """Asked once: they change only with the image."""
    global _versions
    with _versions_lock:
        if _versions is None:
            from yt_dlp.version import __version__ as ytdlp

            spotdl = _first_line([settings.spotdl_python, "-c",
                                  "from spotdl._version import __version__; print(__version__)"])
            deno = _first_line([settings.deno, "--version"]) if settings.deno else None
            _versions = [
                {"name": "yt-dlp", "version": ytdlp, "role": "Downloads from YouTube and the other sites."},
                {"name": "spotDL", "version": spotdl, "role": "Finds Spotify songs on YouTube Music."},
                {"name": "ffmpeg", "version": _ffmpeg(), "role": "Converts and tags the audio."},
                {"name": "Deno", "version": deno.split()[1] if deno and len(deno.split()) > 1 else None,
                 "role": "Solves YouTube's challenge scripts for yt-dlp."},
                {"name": "Python", "version": platform.python_version(), "role": "Runs songdrop."},
            ]
        return _versions


def _disk(label: str, path: Path) -> dict:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"label": label, "path": str(path), "total_bytes": None, "free_bytes": None}
    return {"label": label, "path": str(path), "total_bytes": usage.total, "free_bytes": usage.free}


def status(settings: Settings, jobs: list[dict]) -> dict:
    # Every status, 0 included: a reader (Homepage's card) finds each one.
    counts: dict[str, int] = dict.fromkeys(get_args(JobStatus), 0)
    for job in jobs:
        counts[job["status"]] = counts.get(job["status"], 0) + 1
    waiting = sum(1 for job in jobs for error in job["errors"] if error["state"] in ("waiting", "queued", "retrying"))
    return {
        "started_at": STARTED_AT,
        "tools": versions(settings),
        "disks": [_disk("Output", settings.output_root), _disk("songdrop's own files", settings.state_dir)],
        "jobs": {"by_status": counts, "songs_waiting": waiting},
    }
