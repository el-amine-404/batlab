"""The saved notifications, in the database. Secrets never leave through
public(); an update that leaves a secret empty keeps the saved one."""

# The class has a list() method: annotations are read later, as the built-in's.
from __future__ import annotations

import json
import threading
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .. import events, problems
from ..db import Database
from ..domain import Invalid, NotFound
from .base import NotificationType

MAX_ITEMS = 20


def _types_enum(schema: dict) -> None:
    """The types, written into the description when it is made (the package's
    registry imports this module, so not before)."""
    from . import TYPES

    schema["enum"] = sorted(TYPES)


def _events_enum(schema: dict) -> None:
    for shape in schema.get("anyOf", [schema]):
        if shape.get("type") == "array":
            shape["items"]["enum"] = list(events.NAMES)


class NotificationIn(BaseModel):
    """A notification as the page or an app sends it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    type: str = Field(description="One of GET /api/v1/notifications/types.", json_schema_extra=_types_enum)
    enabled: bool = True
    events: list[str] | None = Field(None, description="The events it gets; left out: the usual ones.",
                                     json_schema_extra=_events_enum)
    config: dict[str, str | None] = Field(default_factory=dict, description="Its type's fields. A secret left "
                                                                            "empty keeps the saved one.")

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Give it a name.")
        return value

    @field_validator("events")
    @classmethod
    def _events(cls, value):
        if value is not None and any(name not in events.NAMES for name in value):
            raise ValueError("One of the events isn't one videodrop sends.")
        return value


class NotificationStore:
    def __init__(self, db: Database, types: dict[str, NotificationType]):
        self._db = db
        self.types = types
        self._lock = threading.Lock()
        # The last send of each one since start: {"at", "ok", "message"}.
        self._last: dict[str, dict] = {}

    # ---- reading --------------------------------------------------------------------

    def _rows(self) -> list[dict]:
        return [{"id": r["id"], "name": r["name"], "type": r["type"], "enabled": bool(r["enabled"]),
                 "events": json.loads(r["events"]), "config": json.loads(r["config"])}
                for r in self._db.all("SELECT * FROM notifications ORDER BY position, created_at")]

    def items(self, enabled_only: bool = False) -> list[dict]:
        """Saved items with their secrets, for sending."""
        return [item for item in self._rows() if item["enabled"] or not enabled_only]

    def find(self, item_id: str) -> dict:
        for item in self._rows():
            if item["id"] == item_id:
                return item
        raise NotFound("There's no such notification.")

    def public(self, item: dict) -> dict:
        kind = self.types.get(item["type"])
        secret = {spec.key for spec in kind.fields if spec.secret} if kind else set()
        return {
            "id": item["id"],
            "name": item["name"],
            "type": item["type"],
            "type_label": kind.label if kind else item["type"],
            "enabled": item["enabled"],
            "events": item["events"],
            "config": {k: v for k, v in item["config"].items() if k not in secret},
            "secrets_set": sorted(k for k in item["config"] if k in secret and item["config"][k]),
            "summary": kind.summary(item["config"]) if kind else "This type is no longer available.",
            "last": self._last.get(item["id"]),
        }

    def list(self) -> list[dict]:
        return [self.public(item) for item in self._rows()]

    def get(self, item_id: str) -> dict:
        return self.public(self.find(item_id))

    # ---- writing ----------------------------------------------------------------------

    def parse(self, raw: dict, item_id: str | None = None) -> tuple[NotificationIn, NotificationType, dict]:
        """The body, its type and its checked config; secrets left empty come
        from the saved item_id."""
        try:
            body = NotificationIn.model_validate(raw or {})
        except ValidationError as exc:
            raise Invalid(problems.explain(exc.errors())) from exc
        kind = self.types.get(body.type)
        if kind is None:
            raise Invalid(f"videodrop has no notification type {body.type!r}.")
        config = dict(body.config)
        if item_id:
            old = self.find(item_id)
            if old["type"] == body.type:
                for spec in kind.fields:
                    if spec.secret and not str(config.get(spec.key) or "").strip() and old["config"].get(spec.key):
                        config[spec.key] = old["config"][spec.key]
        return body, kind, kind.check(config)

    @staticmethod
    def _events_of(body: NotificationIn) -> list[str]:
        chosen = body.events if body.events is not None else list(events.DEFAULTS)
        return [name for name in events.NAMES if name in chosen]

    def create(self, raw: dict) -> dict:
        body, _kind, config = self.parse(raw)
        with self._lock:
            items = self._rows()
            if len(items) >= MAX_ITEMS:
                raise Invalid(f"videodrop keeps at most {MAX_ITEMS} notifications.")
            item_id = uuid.uuid4().hex[:12]
            self._db.run("INSERT INTO notifications (id, name, type, enabled, position, events, config, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (item_id, body.name, body.type, int(body.enabled), len(items),
                          json.dumps(self._events_of(body)), json.dumps(config), time.time()))
        return self.get(item_id)

    def update(self, item_id: str, raw: dict) -> dict:
        body, _kind, config = self.parse(raw, item_id)
        with self._lock:
            self.find(item_id)
            self._db.run("UPDATE notifications SET name = ?, type = ?, enabled = ?, events = ?, config = ? "
                         "WHERE id = ?",
                         (body.name, body.type, int(body.enabled), json.dumps(self._events_of(body)),
                          json.dumps(config), item_id))
        self._last.pop(item_id, None)
        return self.get(item_id)

    def delete(self, item_id: str) -> None:
        with self._lock:
            self.find(item_id)
            self._db.run("DELETE FROM notifications WHERE id = ?", (item_id,))
        self._last.pop(item_id, None)

    def note(self, item_id: str, ok: bool, message: str) -> None:
        self._last[item_id] = {"at": time.time(), "ok": ok, "message": message[:500]}
