"""Settings, all from the environment (compose/music-links/docker-compose.yml)."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a whole number, not {raw!r}") from exc
    if value < minimum:
        raise SystemExit(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    port: int
    # Each job's folder: its job.json and the files offered for download.
    state_dir: Path
    # musicguard's inbox folder for songdrop: every finished song is copied here.
    inbox_dir: Path
    static_dir: Path
    retention_hours: int
    max_items: int
    concurrent_jobs: int
    spotdl_parallel: int
    job_timeout_seconds: int
    # spotDL runs in its own virtualenv (the base image's); yt-dlp in ours.
    spotdl_python: str
    app_python: str
    # spotDL's Deno, which yt-dlp needs for YouTube's challenge scripts.
    deno: str | None
    # Discord: songs still missing after a job, and what later tries bring back.
    discord_webhook: str | None = None
    # The page's address, for links in those messages.
    public_url: str = ""

    @property
    def jobs_dir(self) -> Path:
        return self.state_dir / "jobs"


def load() -> Settings:
    deno = os.environ.get("SONGDROP_DENO", str(Path.home() / ".config/spotdl/deno"))
    return Settings(
        port=_int("SONGDROP_PORT", 8810),
        state_dir=Path(os.environ.get("SONGDROP_STATE", "/state")),
        inbox_dir=Path(os.environ.get("SONGDROP_INBOX", "/inbox")),
        static_dir=Path(os.environ.get("SONGDROP_STATIC", "/opt/songdrop/static")),
        retention_hours=_int("SONGDROP_RETENTION_HOURS", 24),
        max_items=_int("SONGDROP_MAX_ITEMS", 200),
        concurrent_jobs=_int("SONGDROP_CONCURRENT_JOBS", 1),
        spotdl_parallel=_int("SONGDROP_SPOTDL_PARALLEL", 3),
        job_timeout_seconds=_int("SONGDROP_JOB_TIMEOUT", 3 * 3600, minimum=60),
        spotdl_python=os.environ.get("SONGDROP_SPOTDL_PYTHON", "/app/.venv/bin/python"),
        app_python=os.environ.get("SONGDROP_APP_PYTHON", sys.executable),
        deno=deno if os.access(deno, os.X_OK) else None,
        discord_webhook=os.environ.get("SONGDROP_DISCORD_WEBHOOK", "").strip() or None,
        public_url=os.environ.get("SONGDROP_PUBLIC_URL", "").strip().rstrip("/"),
    )
