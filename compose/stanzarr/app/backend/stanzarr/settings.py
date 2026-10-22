"""The settings (GET and PATCH /api/v1/settings), kept in the database. The
environment gives the first values (config.py); what is saved wins."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Config
from .db import Database

KEY = "general"


class Settings(BaseModel):
    scan_minutes: int = Field(description="How often the library is read again for new and changed songs.")
    retry_days: int = Field(description="A song without lyrics synced by a person is asked about again after "
                                        "this many days.")
    public_url: str = Field(description="The page's address, for links in messages; empty leaves them out.")


class SettingsPatch(BaseModel):
    """Only the fields to change; the others keep their value."""

    # strict: a number is a number (false isn't 0).
    model_config = ConfigDict(extra="forbid", strict=True)

    scan_minutes: int | None = Field(None, ge=1, le=1440)
    retry_days: int | None = Field(None, ge=1, le=365)
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
        self._defaults = Settings(scan_minutes=config.scan_minutes, retry_days=config.retry_days,
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
