"""The lyrics sources (Settings, Sources): the add-ons Stanzarr asks for the
lyrics a song lacks, first to last, each with its settings, a switch and a
Test. TYPES lists the kinds there are; the store keeps the ones made."""

from __future__ import annotations

import json
import threading
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .. import problems
from ..db import Database
from ..domain import Invalid, NotFound
from .base import NOTHING, Found, LyricsSource, SongQuery
from .lrclib import LRCLIB

TYPES: dict[str, LyricsSource] = {kind.key: kind for kind in (LRCLIB(),)}
MAX_ITEMS = 10


def _types_enum(schema: dict) -> None:
    schema["enum"] = sorted(TYPES)


class SourceIn(BaseModel):
    """A source as the page or an app sends it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    type: str = Field(description="One of GET /api/v1/sources/types.", json_schema_extra=_types_enum)
    enabled: bool = True
    config: dict[str, str | None] = Field(default_factory=dict, description="Its type's fields. A secret left "
                                                                            "empty keeps the saved one.")

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Give it a name.")
        return value


class SourceStore:
    def __init__(self, db: Database, types: dict[str, LyricsSource] = TYPES):
        self._db = db
        self.types = types
        self._lock = threading.Lock()
        # The last question to each one since start: {"at", "ok", "message"}.
        self._last: dict[str, dict] = {}

    # ---- reading --------------------------------------------------------------------

    def _rows(self) -> list[dict]:
        return [{"id": r["id"], "name": r["name"], "type": r["type"], "enabled": bool(r["enabled"]),
                 "config": json.loads(r["config"])}
                for r in self._db.all("SELECT * FROM sources ORDER BY position, created_at")]

    def items(self, enabled_only: bool = False) -> list[dict]:
        """Saved items with their secrets, first to ask first."""
        return [item for item in self._rows() if item["enabled"] or not enabled_only]

    def asked(self) -> list[tuple[dict, LyricsSource]]:
        """The enabled ones there is a kind for, in order."""
        return [(item, self.types[item["type"]]) for item in self.items(enabled_only=True) if item["type"] in self.types]

    def find(self, item_id: str) -> dict:
        for item in self._rows():
            if item["id"] == item_id:
                return item
        raise NotFound("There's no such source.")

    def public(self, item: dict) -> dict:
        kind = self.types.get(item["type"])
        secret = {spec.key for spec in kind.fields if spec.secret} if kind else set()
        return {
            "id": item["id"],
            "name": item["name"],
            "type": item["type"],
            "type_label": kind.label if kind else item["type"],
            "enabled": item["enabled"],
            "config": {k: v for k, v in item["config"].items() if k not in secret},
            "secrets_set": sorted(k for k in item["config"] if k in secret and item["config"][k]),
            "last": self._last.get(item["id"]),
        }

    def list(self) -> list[dict]:
        return [self.public(item) for item in self._rows()]

    def get(self, item_id: str) -> dict:
        return self.public(self.find(item_id))

    # ---- writing ----------------------------------------------------------------------

    def parse(self, raw: dict, item_id: str | None = None) -> tuple[SourceIn, LyricsSource, dict]:
        try:
            body = SourceIn.model_validate(raw or {})
        except ValidationError as exc:
            raise Invalid(problems.explain(exc.errors())) from exc
        kind = self.types.get(body.type)
        if kind is None:
            raise Invalid(f"stanzarr has no source type {body.type!r}.")
        config = dict(body.config)
        if item_id:
            old = self.find(item_id)
            if old["type"] == body.type:
                for spec in kind.fields:
                    if spec.secret and not str(config.get(spec.key) or "").strip() and old["config"].get(spec.key):
                        config[spec.key] = old["config"][spec.key]
        return body, kind, kind.check(config)

    def create(self, raw: dict) -> dict:
        body, _kind, config = self.parse(raw)
        with self._lock:
            items = self._rows()
            if len(items) >= MAX_ITEMS:
                raise Invalid(f"stanzarr keeps at most {MAX_ITEMS} sources.")
            item_id = uuid.uuid4().hex[:12]
            self._db.run("INSERT INTO sources (id, name, type, enabled, position, config, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (item_id, body.name, body.type, int(body.enabled), len(items), json.dumps(config),
                          time.time()))
        return self.get(item_id)

    def update(self, item_id: str, raw: dict) -> dict:
        body, _kind, config = self.parse(raw, item_id)
        with self._lock:
            self.find(item_id)
            self._db.run("UPDATE sources SET name = ?, type = ?, enabled = ?, config = ? WHERE id = ?",
                         (body.name, body.type, int(body.enabled), json.dumps(config), item_id))
        self._last.pop(item_id, None)
        return self.get(item_id)

    def delete(self, item_id: str) -> None:
        with self._lock:
            self.find(item_id)
            self._db.run("DELETE FROM sources WHERE id = ?", (item_id,))
        self._last.pop(item_id, None)

    def reorder(self, ids: list[str]) -> list[dict]:
        with self._lock:
            known = [item["id"] for item in self._rows()]
            if sorted(ids) != sorted(known):
                raise Invalid("Give every source's id once, in the order wanted.")
            for position, item_id in enumerate(ids):
                self._db.run("UPDATE sources SET position = ? WHERE id = ?", (position, item_id))
        return self.list()

    def note(self, item_id: str, ok: bool, message: str) -> None:
        self._last[item_id] = {"at": time.time(), "ok": ok, "message": message[:500]}


__all__ = ["NOTHING", "TYPES", "Found", "LyricsSource", "SongQuery", "SourceIn", "SourceStore"]
