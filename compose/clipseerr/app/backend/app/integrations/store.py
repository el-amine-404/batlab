"""The saved integrations of one family ("service" or "notification"), in the
database's integrations table. Secrets never leave through public(); an update
that leaves a secret empty keeps the saved one."""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .. import problems
from ..db import Database
from .base import IntegrationType, Invalid

MAX_ITEMS = 30


class NotFound(LookupError):
    """The message is shown to the user as is."""


class IntegrationIn(BaseModel):
    """An integration as the page or an app sends it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    type: str
    enabled: bool = True
    events: list[str] | None = Field(None, description="Notifications only: the events it gets. Left out: all.")
    config: dict[str, str | int | bool | None] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Give it a name.")
        return value


class Integrations:
    def __init__(self, db: Database, family: str, types: dict[str, IntegrationType],
                 events: tuple[str, ...] = (), describe_extra: Callable[[IntegrationType, dict], dict] | None = None):
        self._db = db
        self.family = family
        self.types = types
        self._events = events
        self._extra = describe_extra or (lambda kind, config: {})
        self._lock = threading.Lock()
        # The last use of each one since start: {"at", "ok", "message"}.
        self.last: dict[str, dict] = {}

    # ---- reading --------------------------------------------------------------------

    def _rows(self) -> list[dict]:
        rows = self._db.all("SELECT * FROM integrations WHERE family = ? ORDER BY position, created_at",
                            (self.family,))
        return [{"id": r["id"], "name": r["name"], "type": r["type"], "enabled": bool(r["enabled"]),
                 "position": r["position"], "events": json.loads(r["events"]), "config": json.loads(r["config"])}
                for r in rows]

    def items(self, enabled_only: bool = False) -> list[dict]:
        """Saved items with their secrets, for sending; in order."""
        return [item for item in self._rows() if item["enabled"] or not enabled_only]

    def find(self, item_id: str) -> dict:
        for item in self._rows():
            if item["id"] == item_id:
                return item
        raise NotFound("No such " + ("service." if self.family == "service" else "notification."))

    def public(self, item: dict) -> dict:
        kind = self.types.get(item["type"])
        secret = {spec.key for spec in kind.fields if spec.secret} if kind else set()
        return {
            "id": item["id"],
            "name": item["name"],
            "type": item["type"],
            "type_label": kind.label if kind else item["type"],
            "enabled": item["enabled"],
            "position": item["position"],
            "events": item["events"],
            "config": {k: v for k, v in item["config"].items() if k not in secret},
            "secrets_set": sorted(k for k in item["config"] if k in secret and item["config"][k]),
            "summary": kind.summary(item["config"]) if kind else "This type is no longer available.",
            "last": self.last.get(item["id"]),
            **(self._extra(kind, item["config"]) if kind else {}),
        }

    def list(self) -> list[dict]:
        return [self.public(item) for item in self._rows()]

    def get(self, item_id: str) -> dict:
        return self.public(self.find(item_id))

    # ---- writing ----------------------------------------------------------------------

    def parse(self, raw: dict, item_id: str | None = None) -> tuple[IntegrationIn, IntegrationType, dict]:
        """The body, its type and its checked config; secrets left empty come
        from the saved item_id."""
        try:
            body = IntegrationIn.model_validate(raw or {})
        except ValidationError as exc:
            raise Invalid(problems.explain(exc.errors())) from exc
        kind = self.types.get(body.type)
        if kind is None:
            raise Invalid(f"clipseerr has no {self.family} type {body.type!r}.")
        if body.events is not None and any(name not in self._events for name in body.events):
            raise Invalid("One of the events isn't one clipseerr sends.")
        config = dict(body.config)
        if item_id:
            old = self.find(item_id)
            if old["type"] == body.type:
                for spec in kind.fields:
                    if spec.secret and not str(config.get(spec.key) or "").strip() and old["config"].get(spec.key):
                        config[spec.key] = old["config"][spec.key]
        return body, kind, kind.check(config)

    def _events_of(self, body: IntegrationIn) -> list[str]:
        if not self._events:
            return []
        chosen = body.events if body.events is not None else list(self._events)
        return [name for name in self._events if name in chosen]

    def create(self, raw: dict) -> dict:
        body, _kind, config = self.parse(raw)
        with self._lock:
            items = self._rows()
            if len(items) >= MAX_ITEMS:
                raise Invalid(f"clipseerr keeps at most {MAX_ITEMS} of these.")
            item_id = uuid.uuid4().hex[:12]
            position = max((i["position"] for i in items), default=-1) + 1
            self._db.run("INSERT INTO integrations (id, family, name, type, enabled, position, events, config, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (item_id, self.family, body.name, body.type, int(body.enabled), position,
                          json.dumps(self._events_of(body)), json.dumps(config), time.time()))
        return self.get(item_id)

    def update(self, item_id: str, raw: dict) -> dict:
        body, _kind, config = self.parse(raw, item_id)
        with self._lock:
            self.find(item_id)
            self._db.run("UPDATE integrations SET name = ?, type = ?, enabled = ?, events = ?, config = ? "
                         "WHERE id = ? AND family = ?",
                         (body.name, body.type, int(body.enabled), json.dumps(self._events_of(body)),
                          json.dumps(config), item_id, self.family))
        self.last.pop(item_id, None)
        return self.get(item_id)

    def delete(self, item_id: str) -> None:
        with self._lock:
            self.find(item_id)
            self._db.run("DELETE FROM integrations WHERE id = ? AND family = ?", (item_id, self.family))
        self.last.pop(item_id, None)

    def reorder(self, ids: list[str]) -> list[dict]:
        """Puts the items in this order; ids must name every item once."""
        with self._lock:
            current = {item["id"] for item in self._rows()}
            if set(ids) != current or len(ids) != len(current):
                raise Invalid("Give every item's id once, in the order wanted.")
            self._db.many([("UPDATE integrations SET position = ? WHERE id = ? AND family = ?",
                            (index, item_id, self.family)) for index, item_id in enumerate(ids)])
        return self.list()

    def seed(self, name: str, type_key: str, config: dict, events: list[str] | None = None) -> dict | None:
        """Adds an item made from the environment; raises Invalid."""
        return self.create({"name": name, "type": type_key, "config": config, "events": events})

    def note(self, item_id: str, ok: bool, message: str) -> None:
        self.last[item_id] = {"at": time.time(), "ok": ok, "message": message[:500]}
