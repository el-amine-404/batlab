"""Settings from the environment. Everything has a default except the library."""

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
    host: str = field(default_factory=lambda: _env("STANZARR_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("STANZARR_PORT", "6868")))
    # Create (or reset the password of) this account at start, instead of the setup page.
    username: str = field(default_factory=lambda: _env("STANZARR_USERNAME"))
    password: str = field(default_factory=lambda: _env("STANZARR_PASSWORD"))
    # Never write into the library: everything else works.
    read_only: bool = field(default_factory=lambda: _flag("STANZARR_READ_ONLY", False))
    # Ask LRCLIB for missing lyrics, and again every retry_days for songs
    # without lyrics synced by a person.
    lrclib: bool = field(default_factory=lambda: _flag("STANZARR_LRCLIB", True))
    retry_days: int = field(default_factory=lambda: int(_env("STANZARR_RETRY_DAYS", "30")))
    scan_interval: int = field(default_factory=lambda: int(_env("STANZARR_SCAN_INTERVAL", "600")))
    # Workers authenticate with this key; generated and kept in the database when empty.
    worker_key: str = field(default_factory=lambda: _env("STANZARR_WORKER_KEY"))
    # Session cookies only over HTTPS. Off by default: most homelabs serve plain HTTP on the LAN.
    cookie_secure: bool = field(default_factory=lambda: _flag("STANZARR_COOKIE_SECURE", False))
    # Reverse proxies whose X-Forwarded-For is believed (comma-separated
    # addresses, or * for any): the login rate limit counts per real client.
    trusted_proxies: str = field(default_factory=lambda: _env("STANZARR_TRUSTED_PROXIES", "127.0.0.1"))
    # Built frontend; served at /.
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
