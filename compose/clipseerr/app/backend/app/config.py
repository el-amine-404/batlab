"""What the environment says (compose/clipseerr/docker-compose.yml). It gives
the first values only: what the Settings screens change is kept in the
database (settings.py) and wins over them."""

import os
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


def _text(name: str) -> str:
    return os.environ.get(name, "").strip()


@dataclass(frozen=True)
class Config:
    port: int
    # The database: requests, services, notifications, settings and the cache.
    state_dir: Path
    static_dir: Path
    # The page's address, for links in messages.
    public_url: str
    # Where the services that call back (songdrop, videodrop) reach clipseerr.
    internal_url: str
    # First values of the settings (settings.py).
    region: str
    storefront: str
    # The Hot category's feeds: JSON Feed addresses, subfeed's (CLIPSEERR_HOT_FEEDS, comma-separated).
    hot_feeds: tuple[str, ...]
    # The API key they are read with: subfeed asks everyone who reads it for one (a key that only reads).
    hot_feeds_key: str
    # Made on the first start only, when given (seed.py).
    apprise_url: str
    apprise_tag: str
    aurral_url: str
    aurral_api_key: str
    songdrop_url: str
    videodrop_url: str
    videodrop_api_key: str
    songdrop_api_key: str
    # The first libraries (seed.py): Navidrome, and the music videos folder.
    navidrome_url: str
    navidrome_user: str
    navidrome_password: str
    navidrome_token: str
    navidrome_salt: str
    music_videos_dir: str

    @property
    def database(self) -> Path:
        return self.state_dir / "clipseerr.db"


def load() -> Config:
    port = _int("CLIPSEERR_PORT", 8820)
    return Config(
        port=port,
        state_dir=Path(os.environ.get("CLIPSEERR_STATE", "/state")),
        static_dir=Path(os.environ.get("CLIPSEERR_STATIC", "/opt/clipseerr/static")),
        public_url=_text("CLIPSEERR_PUBLIC_URL").rstrip("/"),
        internal_url=(_text("CLIPSEERR_INTERNAL_URL") or f"http://clipseerr:{port}").rstrip("/"),
        region=(_text("CLIPSEERR_REGION") or "US").upper(),
        storefront=(_text("CLIPSEERR_STOREFRONT") or "us").lower(),
        hot_feeds=tuple(u.strip() for u in _text("CLIPSEERR_HOT_FEEDS").split(",") if u.strip()),
        hot_feeds_key=_text("CLIPSEERR_HOT_FEEDS_KEY"),
        apprise_url=_text("CLIPSEERR_APPRISE_URL"),
        apprise_tag=_text("CLIPSEERR_APPRISE_TAG"),
        aurral_url=_text("CLIPSEERR_AURRAL_URL"),
        aurral_api_key=_text("CLIPSEERR_AURRAL_API_KEY"),
        songdrop_url=_text("CLIPSEERR_SONGDROP_URL"),
        videodrop_url=_text("CLIPSEERR_VIDEODROP_URL"),
        videodrop_api_key=_text("CLIPSEERR_VIDEODROP_API_KEY"),
        songdrop_api_key=_text("CLIPSEERR_SONGDROP_API_KEY"),
        navidrome_url=_text("CLIPSEERR_NAVIDROME_URL"),
        navidrome_user=_text("CLIPSEERR_NAVIDROME_USER"),
        navidrome_password=_text("CLIPSEERR_NAVIDROME_PASSWORD"),
        navidrome_token=_text("CLIPSEERR_NAVIDROME_TOKEN"),
        navidrome_salt=_text("CLIPSEERR_NAVIDROME_SALT"),
        music_videos_dir=_text("CLIPSEERR_MUSIC_VIDEOS_DIR"),
    )
