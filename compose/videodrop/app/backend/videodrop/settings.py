"""The settings (GET and PATCH /api/v1/settings), kept in the database. The
environment gives the first values (config.py); what is saved wins."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Config
from .db import Database
from .domain import Invalid
from .options import DEFAULTS, OptionsPatch, VideoOptions

KEY = "settings"


class Settings(BaseModel):
    max_parallel: int = Field(description="Downloads running at once, every site together.")
    per_site_parallel: int = Field(description="Downloads from one site at once. Every app here shares one "
                                               "address; a site that sees too many at once asks it to sign in.")
    retries: int = Field(description="Tries again after a failure that may pass (the network, the site busy), "
                                     "waiting longer each time; 0: never.")
    rate_limit_mib: float = Field(description="MiB/s each download takes at most; 0: as fast as it goes.")
    max_filesize_gb: float = Field(description="Bigger videos aren't downloaded; 0: no limit.")
    min_free_gb: float = Field(description="Downloads wait while the media disk has less free (disk.low, once).")
    hold_hours: int = Field(description="How long the files of a download without a destination are kept for "
                                        "their caller to fetch.")
    keep_days: int = Field(description="Ended downloads are forgotten after this many days (their files stay); "
                                       "0 keeps them all.")
    defaults: VideoOptions = Field(description="Options a download takes when neither it nor its destination "
                                               "says.")
    default_destination: str = Field(description="Where a download that names no destination goes; empty: "
                                                 "kept for its caller (hold_hours).")
    public_url: str = Field(description="The page's address, for links in messages; empty leaves them out.")


class SettingsPatch(BaseModel):
    """Only the fields to change; the others keep their value."""

    model_config = ConfigDict(extra="forbid")

    max_parallel: int | None = Field(None, ge=1, le=16)
    per_site_parallel: int | None = Field(None, ge=1, le=16)
    retries: int | None = Field(None, ge=0, le=10)
    rate_limit_mib: float | None = Field(None, ge=0, le=10_000)
    max_filesize_gb: float | None = Field(None, ge=0, le=1_000)
    min_free_gb: float | None = Field(None, ge=0, le=10_000)
    hold_hours: int | None = Field(None, ge=1, le=24 * 30)
    keep_days: int | None = Field(None, ge=0, le=3650)
    defaults: OptionsPatch | None = None
    default_destination: str | None = Field(None, max_length=40)
    public_url: str | None = Field(None, max_length=300)

    @field_validator("public_url")
    @classmethod
    def _public_url(cls, value):
        if value is None:
            return value
        value = value.strip().rstrip("/")
        if value and not re.fullmatch(r"https?://[^\s/]+(?:/\S*)?", value):
            raise ValueError("The page's address starts with http:// or https://.")
        return value


class SettingsStore:
    def __init__(self, db: Database, config: Config):
        self._db = db
        self._defaults = Settings(max_parallel=2, per_site_parallel=2, retries=2, rate_limit_mib=0,
                                  max_filesize_gb=0, min_free_gb=20, hold_hours=24, keep_days=90,
                                  defaults=DEFAULTS, default_destination="", public_url=config.public_url)
        self._value = self._load()

    def _load(self) -> Settings:
        saved = self._db.get(KEY, {}) or {}
        merged = self._defaults.model_dump()
        merged.update({k: v for k, v in saved.items() if k in Settings.model_fields and k != "defaults"})
        merged["defaults"] = {**merged["defaults"], **(saved.get("defaults") or {})}
        return Settings.model_validate(merged)

    def __call__(self) -> Settings:
        return self._value

    def update(self, patch: SettingsPatch, destination_exists=lambda slug: True) -> Settings:
        """destination_exists(slug): whether default_destination names one."""
        if patch.default_destination and not destination_exists(patch.default_destination):
            raise Invalid(f"There's no destination {patch.default_destination!r}.")
        saved = self._db.get(KEY, {}) or {}
        changes = patch.model_dump(exclude_none=True)
        if "defaults" in changes:
            changes["defaults"] = {**(saved.get("defaults") or {}), **changes["defaults"]}
        saved.update(changes)
        self._db.put(KEY, saved)
        self._value = self._load()
        return self._value

    def forget_destination(self, slug: str) -> None:
        """A removed destination stops being the default."""
        if self._value.default_destination == slug:
            self.update(SettingsPatch(default_destination=""))
