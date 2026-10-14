"""The settings the page and the API may change (GET and PATCH
/api/v1/settings), checked here and kept in state_dir/settings.json (mode 600:
it may hold the Discord webhook). The environment gives the first values;
whatever is saved wins. The webhook is write-only: it is never sent back.
"""

import dataclasses
import json
import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import options as opts
from .config import Settings

FILE = "settings.json"
# Worker threads always running; concurrent_jobs says how many may work.
MAX_CONCURRENT_JOBS = 4
_FOLDER = re.compile(r"^[A-Za-z0-9 _.-]+(?:/[A-Za-z0-9 _.-]+)*$")
_WEBHOOK = re.compile(r"^https://(?:discord\.com|discordapp\.com|ptb\.discord\.com|canary\.discord\.com)/api/webhooks/\d+/[\w-]+$")


class Invalid(ValueError):
    """The message is shown to the user as is."""


class Patch(BaseModel):
    """Every field optional: what is left out keeps its value."""

    model_config = ConfigDict(extra="forbid")

    deliver: bool | None = None
    output_folder: str | None = Field(None, max_length=200)
    retention_hours: int | None = Field(None, ge=1, le=720)
    max_items: int | None = Field(None, ge=1, le=1000)
    concurrent_jobs: int | None = Field(None, ge=1, le=MAX_CONCURRENT_JOBS)
    spotdl_parallel: int | None = Field(None, ge=1, le=8)
    job_timeout_minutes: int | None = Field(None, ge=10, le=1440)
    retry_minutes: list[int] | None = Field(None, max_length=6)
    # "" removes it.
    discord_webhook: str | None = Field(None, max_length=300)
    public_url: str | None = Field(None, max_length=300)
    defaults: dict[str, dict] | None = None

    @field_validator("output_folder")
    @classmethod
    def _folder(cls, value):
        if value is None:
            return value
        value = value.strip().strip("/")
        if not value or not _FOLDER.match(value) or any(part in (".", "..") for part in value.split("/")):
            raise ValueError("folder")
        return value

    @field_validator("retry_minutes")
    @classmethod
    def _retries(cls, value):
        if value is not None and any(not 1 <= minutes <= 10080 for minutes in value):
            raise ValueError("retries")
        return value

    @field_validator("discord_webhook")
    @classmethod
    def _webhook(cls, value):
        if value and not _WEBHOOK.match(value.strip()):
            raise ValueError("webhook")
        return value.strip() if value is not None else None

    @field_validator("public_url")
    @classmethod
    def _public(cls, value):
        if value and not re.match(r"^https?://[^\s/]+(?:/[^\s]*)?$", value.strip()):
            raise ValueError("public_url")
        return value.strip().rstrip("/") if value is not None else None

    @field_validator("defaults")
    @classmethod
    def _defaults(cls, value):
        if value is None:
            return value
        checked = {}
        for engine, chosen in value.items():
            if engine not in opts.MODELS:
                raise ValueError("defaults")
            try:
                checked[engine] = opts.parse(engine, chosen).model_dump()
            except ValidationError:
                raise ValueError("defaults") from None
        return checked


_MESSAGES = {
    "folder": "The folder must be a path inside the output folder, like music/songdrop.",
    "retries": "Each later try must be between 1 minute and 7 days away.",
    "webhook": "That isn't a Discord webhook link (https://discord.com/api/webhooks/…).",
    "public_url": "The page address must start with http:// or https://.",
    "defaults": "One of the default download options isn't valid.",
}


def _explain(exc: ValidationError) -> str:
    for error in exc.errors():
        text = str(error.get("msg", ""))
        for key, message in _MESSAGES.items():
            if text.endswith(key):
                return message
        field = ".".join(str(part) for part in error.get("loc", ()))
        if field:
            return f"{field.replace('_', ' ').capitalize()} isn't valid: {text.removeprefix('Value error, ')}."
    return "One of the settings isn't valid."


def _path(state_dir: Path) -> Path:
    return state_dir / FILE


def stored(state_dir: Path) -> dict:
    try:
        return json.loads(_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def apply(base: Settings, saved: dict) -> Settings:
    """The environment's settings with the saved ones over them."""
    changes = {}
    for key in ("deliver", "output_folder", "retention_hours", "max_items", "concurrent_jobs",
                "spotdl_parallel", "public_url"):
        if key in saved:
            changes[key] = saved[key]
    if "job_timeout_minutes" in saved:
        changes["job_timeout_seconds"] = saved["job_timeout_minutes"] * 60
    if "retry_minutes" in saved:
        changes["retry_minutes"] = tuple(saved["retry_minutes"])
    if "discord_webhook" in saved:
        changes["discord_webhook"] = saved["discord_webhook"] or None
    if "defaults" in saved:
        changes["defaults"] = saved["defaults"]
    if "concurrent_jobs" in changes:
        changes["concurrent_jobs"] = min(changes["concurrent_jobs"], MAX_CONCURRENT_JOBS)
    return dataclasses.replace(base, **changes)


def update(base: Settings, state_dir: Path, raw: dict) -> Settings:
    try:
        patch = Patch.model_validate(raw or {})
    except ValidationError as exc:
        raise Invalid(_explain(exc)) from exc
    saved = stored(state_dir)
    saved.update(patch.model_dump(exclude_none=True))
    target = _path(state_dir)
    partial = target.with_name("." + FILE + ".tmp")
    with open(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as out:
        json.dump(saved, out, indent=1)
    os.replace(partial, target)
    return apply(base, saved)


def public(settings: Settings) -> dict:
    """What GET /api/v1/settings answers: never the webhook itself."""
    return {
        "deliver": settings.deliver,
        "output_root": str(settings.output_root),
        "output_folder": settings.output_folder,
        "output_folder_exists": settings.output_dir.is_dir(),
        "retention_hours": settings.retention_hours,
        "max_items": settings.max_items,
        "concurrent_jobs": settings.concurrent_jobs,
        "spotdl_parallel": settings.spotdl_parallel,
        "job_timeout_minutes": settings.job_timeout_seconds // 60,
        "retry_minutes": list(settings.retry_minutes),
        "discord_webhook_set": bool(settings.discord_webhook),
        "public_url": settings.public_url,
        "defaults": {engine: opts.with_defaults(engine, settings.defaults).model_dump() for engine in opts.MODELS},
        "limits": {
            "retention_hours": [1, 720], "max_items": [1, 1000], "concurrent_jobs": [1, MAX_CONCURRENT_JOBS],
            "spotdl_parallel": [1, 8], "job_timeout_minutes": [10, 1440], "retry_minutes": [1, 10080],
            "retries": 6,
        },
    }
