"""What the environment says (compose/videodrop/docker-compose.yml). The
destinations and the notification it names are made on the first start only;
after that the API, and the page, own them."""

import os
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_URL = re.compile(r"^https?://[^\s/]+(?:/\S*)?$")
# videodrop's own folder inside the media root: downloads in progress, and
# the files of downloads made without a destination. On the same disk as the
# destinations, so that a finished file is moved by a rename.
OWN_FOLDER = ".videodrop"


@dataclass(frozen=True)
class Seed:
    """A destination to make on the first start."""

    slug: str
    folder: str


@dataclass(frozen=True)
class Config:
    port: int
    # The database and the cookies (the SSD).
    state_dir: Path
    # Every destination is a folder inside it (the media disk).
    media_root: Path
    # The page, built (the image's static folder).
    static_dir: Path
    seed: tuple[Seed, ...]
    # The page's address, for links in messages; a setting after the first start.
    public_url: str
    # The first notification (Apprise), when given.
    apprise_url: str
    apprise_tag: str
    # The JavaScript runtime yt-dlp answers YouTube's challenge with.
    deno: str | None
    # The command a download runs as (runner.py, with this interpreter).
    runner: tuple[str, ...]
    # The command that reads a link without downloading it (probe.py).
    prober: tuple[str, ...]
    # Whether to ask PyPI daily for yt-dlp's newest release (issues.py).
    check_releases: bool

    @property
    def database(self) -> Path:
        return self.state_dir / "videodrop.db"

    @property
    def previews_dir(self) -> Path:
        """Each video's gallery, storyboard and details (previews.py)."""
        return self.state_dir / "previews"

    @property
    def work_dir(self) -> Path:
        """One folder per download in progress; emptied when it ends."""
        return self.media_root / OWN_FOLDER / "work"

    @property
    def held_dir(self) -> Path:
        """The files of downloads made without a destination, kept a while
        (Settings, hold_hours) for their callers to fetch."""
        return self.media_root / OWN_FOLDER / "files"


def _seed(raw: str) -> tuple[Seed, ...]:
    """ "music-videos=music-videos,clips=videos/clips"."""
    seeds = []
    for part in filter(None, (p.strip() for p in raw.split(","))):
        found = re.fullmatch(r"([a-z0-9-]{1,40})=([A-Za-z0-9 _./-]{1,200})", part)
        if not found:
            raise SystemExit(f"VIDEODROP_DESTINATIONS: {part!r} isn't slug=folder")
        seeds.append(Seed(*found.groups()))
    return tuple(seeds)


def _url(name: str, value: str) -> str:
    value = value.strip().rstrip("/")
    if value and not _URL.match(value):
        raise SystemExit(f"{name} must start with http:// or https://, not {value!r}")
    return value


def load(env: Mapping[str, str] = os.environ) -> Config:
    port = env.get("VIDEODROP_PORT", "8840").strip()
    if not port.isdigit():
        raise SystemExit(f"VIDEODROP_PORT must be a port number, not {port!r}")
    deno = env.get("VIDEODROP_DENO", "").strip() or shutil.which("deno")
    return Config(
        port=int(port),
        state_dir=Path(env.get("VIDEODROP_STATE", "/state")),
        media_root=Path(env.get("VIDEODROP_MEDIA", "/media")),
        static_dir=Path(env.get("VIDEODROP_STATIC", "/opt/videodrop/static")),
        seed=_seed(env.get("VIDEODROP_DESTINATIONS", "")),
        public_url=_url("VIDEODROP_PUBLIC_URL", env.get("VIDEODROP_PUBLIC_URL", "")),
        apprise_url=_url("VIDEODROP_APPRISE_URL", env.get("VIDEODROP_APPRISE_URL", "")),
        apprise_tag=env.get("VIDEODROP_APPRISE_TAG", "").strip(),
        deno=deno if deno and os.access(deno, os.X_OK) else None,
        runner=(sys.executable, "-m", "videodrop.runner"),
        prober=(sys.executable, "-m", "videodrop.probe"),
        check_releases=env.get("VIDEODROP_CHECK_RELEASES", "1").strip() not in ("0", "false", "no"),
    )
