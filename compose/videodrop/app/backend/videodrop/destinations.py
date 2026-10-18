"""Destinations: named places a download's files go (a folder under the media
root), how they are named there, and the options downloads to it take unless
they say otherwise. Callers name a destination; they never see a path."""

import json
import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import folders
from .db import Database
from .domain import Conflict, Invalid, NotFound
from .options import DEFAULT_TEMPLATE, OptionsPatch, check_template

MAX_DESTINATIONS = 50
SLUG = r"^[a-z0-9-]{1,40}$"


class DestinationIn(BaseModel):
    """A new destination."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60, examples=["Music videos"])
    slug: str | None = Field(None, pattern=SLUG, description="Its name in the API (a job's destination); made "
                                                             "from the name when left out. Fixed once made.")
    folder: str = Field(min_length=1, max_length=200, description="A folder inside the media folder; made when "
                                                                 "missing.", examples=["music-videos"])
    template: str = Field(DEFAULT_TEMPLATE, max_length=200,
                          description="Each file's path inside the folder, without the extension, from the fields "
                                      "{id} {title} {artist} {uploader} {channel} {date} {year} {playlist} {index} "
                                      "{site}. Must hold {id}.")
    options: OptionsPatch = Field(default_factory=OptionsPatch,
                                  description="Options downloads to it take, unless they set their own.")

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Give it a name.")
        return value

    @field_validator("template")
    @classmethod
    def _template(cls, value):
        return value if value is None else check_template(value)


class DestinationPatch(BaseModel):
    """Only the fields to change. A new folder or template applies to the
    downloads after; files already there stay where they are."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=60)
    folder: str | None = Field(None, min_length=1, max_length=200)
    template: str | None = Field(None, max_length=200)
    options: OptionsPatch | None = Field(None, description="Replaces its options as a whole.")

    @field_validator("template")
    @classmethod
    def _template(cls, value):
        return value if value is None else check_template(value)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:40] or "destination"


class DestinationStore:
    def __init__(self, db: Database, media_root: Path):
        self._db = db
        self._root = media_root
        self._removed: list[Callable[[str], None]] = []

    def on_remove(self, listener: Callable[[str], None]) -> None:
        self._removed.append(listener)

    def public(self, row: sqlite3.Row) -> dict:
        return {"slug": row["slug"], "name": row["name"], "folder": row["folder"], "template": row["template"],
                "options": json.loads(row["options"]), "created_at": row["created_at"],
                "updated_at": row["updated_at"]}

    def row(self, slug: str) -> sqlite3.Row:
        found = self._db.one("SELECT * FROM destinations WHERE slug = ?", (slug,))
        if not found:
            raise NotFound(f"There's no destination {slug!r}.")
        return found

    def exists(self, slug: str) -> bool:
        return self._db.one("SELECT 1 FROM destinations WHERE slug = ?", (slug,)) is not None

    def rows(self) -> list[sqlite3.Row]:
        return self._db.all("SELECT * FROM destinations ORDER BY name COLLATE NOCASE")

    def _folder(self, folder: str) -> str:
        checked = folders.check(self._root, folder)
        if not checked["ok"]:
            raise Invalid(checked["message"])
        return checked["path"]

    def create(self, body: DestinationIn) -> sqlite3.Row:
        slug = body.slug or _slugify(body.name)
        if self.exists(slug):
            raise Conflict(f"There's a destination {slug!r} already: give this one another slug.")
        if self._db.one("SELECT COUNT(*) AS n FROM destinations")["n"] >= MAX_DESTINATIONS:
            raise Conflict(f"videodrop keeps at most {MAX_DESTINATIONS} destinations.")
        folder = self._folder(body.folder)
        now = time.time()
        self._db.run("INSERT INTO destinations (slug, name, folder, template, options, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (slug, body.name, folder, body.template, json.dumps(body.options.model_dump(exclude_none=True)),
                      now, now))
        return self.row(slug)

    def update(self, slug: str, patch: DestinationPatch) -> sqlite3.Row:
        self.row(slug)
        changes = patch.model_dump(exclude_none=True, exclude={"options"})
        if "name" in changes:
            changes["name"] = changes["name"].strip() or self.row(slug)["name"]
        if "folder" in changes:
            changes["folder"] = self._folder(changes["folder"])
        if patch.options is not None:
            changes["options"] = json.dumps(patch.options.model_dump(exclude_none=True))
        changes["updated_at"] = time.time()
        assignments = ", ".join(f"{column} = ?" for column in changes)
        self._db.run(f"UPDATE destinations SET {assignments} WHERE slug = ?", (*changes.values(), slug))
        return self.row(slug)

    def delete(self, slug: str) -> None:
        """Its files stay; its jobs keep their files' places."""
        self.row(slug)
        self._db.run("DELETE FROM destinations WHERE slug = ?", (slug,))
        for listener in self._removed:
            listener(slug)

    def path(self, row: sqlite3.Row) -> Path:
        """Its folder, checked again: the disk may have changed since."""
        return folders.inside(self._root, folders.normalise(row["folder"]))
