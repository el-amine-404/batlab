"""Settings from the environment. Everything has a default except the library.
What the page or the API changes (settings.py) is kept in the database and
wins over these."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    return value if value != "" else default


def _flag(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    # Folders holding music, separated by ":". Lyrics are written next to the songs.
    library: list[str] = field(default_factory=lambda: [p for p in _env("STANZARR_LIBRARY", "/music").split(":") if p])
    # Database, cache, backups and AI results.
    config_dir: str = field(default_factory=lambda: _env("STANZARR_CONFIG_DIR", "/config"))
    port: int = field(default_factory=lambda: int(_env("STANZARR_PORT", "6868")))
    # The first admin, made at start while nobody exists (instead of the setup code).
    username: str = field(default_factory=lambda: _env("STANZARR_USERNAME"))
    password: str = field(default_factory=lambda: _env("STANZARR_PASSWORD"))
    # Never write into the library: everything else works.
    read_only: bool = field(default_factory=lambda: _flag("STANZARR_READ_ONLY", False))
    # LRCLIB as the first source, on the first start (Settings, Sources after that).
    lrclib: bool = field(default_factory=lambda: _flag("STANZARR_LRCLIB", True))
    # First values of the settings (Settings, General).
    retry_days: int = field(default_factory=lambda: int(_env("STANZARR_RETRY_DAYS", "30")))
    scan_minutes: int = field(default_factory=lambda: max(1, int(_env("STANZARR_SCAN_INTERVAL", "600")) // 60))
    public_url: str = field(default_factory=lambda: _env("STANZARR_PUBLIC_URL").rstrip("/"))
    # Workers authenticate with this key; generated and kept in the database when empty.
    worker_key: str = field(default_factory=lambda: _env("STANZARR_WORKER_KEY"))
    # The first notification, made on the first start (Settings, Notifications after that).
    apprise_url: str = field(default_factory=lambda: _env("STANZARR_APPRISE_URL"))
    apprise_tag: str = field(default_factory=lambda: _env("STANZARR_APPRISE_TAG"))
    # Built page; served at /.
    static_dir: str = field(default_factory=lambda: _env(
        "STANZARR_STATIC", os.path.join(os.path.dirname(__file__), "static")))

    @property
    def db_path(self) -> str:
        return os.path.join(self.config_dir, "stanzarr.db")

    @property
    def cache_dir(self) -> str:
        return os.path.join(self.config_dir, "cache")

    @property
    def artifacts_dir(self) -> str:
        return os.path.join(self.config_dir, "artifacts")

    @property
    def backups_dir(self) -> str:
        return os.path.join(self.config_dir, "backups")

    def ensure_dirs(self) -> None:
        for d in (self.config_dir, self.cache_dir, self.artifacts_dir, self.backups_dir):
            os.makedirs(d, exist_ok=True)
