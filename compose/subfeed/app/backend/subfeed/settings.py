"""The settings (GET and PATCH /api/v1/settings), kept in the database. The
environment gives the first values (config.py); what is saved wins."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Config
from .db import Database

KEY = "settings"
POLL_MINUTES = (15, 1440)


class Settings(BaseModel):
    default_poll_minutes: int = Field(description="How often a new feed is read, unless it says otherwise.")
    keep_days: int = Field(description="Posts older than this many days are forgotten; 0 keeps them all.")
    failing_after: int = Field(description="Failed reads in a row before a feed is said to be failing "
                                           "(the feed.failing event).")
    backfill_posts: int = Field(description="Posts a new feed takes from the community's history; 0 starts it "
                                            "with what is new.")
    public_url: str = Field(description="The page's address, for links in messages; empty leaves them out.")


class SettingsPatch(BaseModel):
    """Only the fields to change; the others keep their value."""

    model_config = ConfigDict(extra="forbid")

    default_poll_minutes: int | None = Field(None, ge=POLL_MINUTES[0], le=POLL_MINUTES[1])
    keep_days: int | None = Field(None, ge=0, le=3650)
    failing_after: int | None = Field(None, ge=1, le=50)
    backfill_posts: int | None = Field(None, ge=0, le=1000)
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
        # Posts are kept, as clipfeed kept them: a few thousand a year are a few MB.
        self._defaults = Settings(default_poll_minutes=30, keep_days=0, failing_after=3, backfill_posts=500,
                                  public_url=config.public_url)
        self._value = self._load()

    def _load(self) -> Settings:
        saved = self._db.get(KEY, {}) or {}
        return self._defaults.model_copy(update={k: v for k, v in saved.items() if k in Settings.model_fields})

    def __call__(self) -> Settings:
        return self._value

    def update(self, patch: SettingsPatch) -> Settings:
        saved = self._db.get(KEY, {}) or {}
        saved.update(patch.model_dump(exclude_none=True))
        self._db.put(KEY, saved)
        self._value = self._load()
        return self._value
