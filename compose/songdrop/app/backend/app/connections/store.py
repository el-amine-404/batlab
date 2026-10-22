"""The saved connections (state_dir/connections.json, mode 600: it holds
webhook URLs) and sending events to them. Secrets never leave through
public(); an update that leaves a secret out keeps the saved one."""

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .. import events, outbound, problems
from ..events import Event
from .base import ConnectionType, Invalid

log = logging.getLogger("songdrop.connections")

FILE = "connections.json"
MAX_CONNECTIONS = 20
TEST_EVENT = Event("test", "songdrop · test message", ("If you can read this, this connection works.",))


@dataclass(frozen=True)
class Seed:
    """The connection made on the first start, from the environment."""

    type: str
    name: str
    config: dict


def _types_enum(schema: dict) -> None:
    """The types, written into the description when it is made (the package's
    registry imports this module, so not before)."""
    from . import TYPES

    schema["enum"] = sorted(TYPES)


def _events_enum(schema: dict) -> None:
    for shape in schema.get("anyOf", [schema]):
        if shape.get("type") == "array":
            shape["items"]["enum"] = list(events.NAMES)


class ConnectionIn(BaseModel):
    """A connection as the page or an app sends it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=60)
    type: str = Field(description="One of GET /api/v1/connections/types.", json_schema_extra=_types_enum)
    enabled: bool = True
    events: list[str] = Field(default_factory=lambda: list(events.DEFAULTS), json_schema_extra=_events_enum)
    config: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Give the connection a name.")
        return value

    @field_validator("events")
    @classmethod
    def _events(cls, value):
        if any(name not in events.NAMES for name in value):
            raise ValueError("One of the events isn't one songdrop sends.")
        return sorted(set(value), key=[item.name for item in events.CATALOG].index)


class Connections:
    def __init__(self, state_dir: Path, types: dict[str, ConnectionType], seed: "Seed | None" = None):
        self._path = state_dir / FILE
        self._types = types
        self._lock = threading.Lock()
        # The last send of each connection, since songdrop started.
        self._last: dict[str, dict] = {}
        self._items: list[dict] = self._read(seed)

    # ---- reading and writing -------------------------------------------------

    def _read(self, seed: "Seed | None") -> list[dict]:
        try:
            items = json.loads(self._path.read_text(encoding="utf-8"))["connections"]
            return self._renamed(items, seed)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError):
            log.exception("ignoring unreadable %s", self._path)
            return []
        # First start: the connection the environment names (context.py).
        if not seed:
            return []
        try:
            config = self._types[seed.type].check(seed.config)
        except Invalid as exc:
            log.warning("no %s connection made from the environment: %s", seed.type, exc)
            return []
        items = [{"id": uuid.uuid4().hex[:12], "name": seed.name, "type": seed.type, "enabled": True,
                  "events": list(events.DEFAULTS), "config": config}]
        self._write(items)
        return items

    def _renamed(self, items: list[dict], seed: "Seed | None") -> list[dict]:
        """The first start's connection was called Apprise ("Apprise · download-issues"), which says how,
        not what: the one the environment still names is called as the seed is now, once."""
        if not seed:
            return items
        changed = False
        for item in items:
            if (item.get("type") == seed.type and str(item.get("name", "")).startswith("Apprise")
                    and item.get("config", {}).get("url") == seed.config.get("url") and item["name"] != seed.name):
                item["name"] = seed.name
                changed = True
        if changed:
            self._write(items)
        return items

    def _write(self, items: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._path.with_name("." + FILE + ".tmp")
        with open(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as out:
            json.dump({"connections": items}, out, indent=1)
        os.replace(partial, self._path)

    # ---- what the API offers -------------------------------------------------

    def public(self, item: dict) -> dict:
        kind = self._types.get(item["type"])
        secret = {spec.key for spec in kind.fields if spec.secret} if kind else set()
        return {
            "id": item["id"],
            "name": item["name"],
            "type": item["type"],
            "type_label": kind.label if kind else item["type"],
            "enabled": item["enabled"],
            "events": item["events"],
            "config": {key: value for key, value in item["config"].items() if key not in secret},
            "secrets_set": sorted(key for key in item["config"] if key in secret),
            "summary": kind.summary(item["config"]) if kind else "This type is no longer available.",
            "last": self._last.get(item["id"]),
        }

    def list(self) -> list[dict]:
        with self._lock:
            return [self.public(item) for item in self._items]

    def get(self, connection_id: str) -> dict:
        with self._lock:
            return self.public(self._find(connection_id))

    def create(self, raw: dict) -> dict:
        body, kind = self._parse(raw)
        item = {"id": uuid.uuid4().hex[:12], "name": body.name, "type": body.type, "enabled": body.enabled,
                "events": body.events, "config": kind.check(body.config)}
        with self._lock:
            if len(self._items) >= MAX_CONNECTIONS:
                raise Invalid(f"songdrop keeps at most {MAX_CONNECTIONS} connections.")
            self._write([*self._items, item])
            self._items.append(item)
            return self.public(item)

    def update(self, connection_id: str, raw: dict) -> dict:
        body, kind = self._parse(raw)
        with self._lock:
            old = self._find(connection_id)
            config = self._with_saved_secrets(kind, body, old)
            item = {**old, "name": body.name, "type": body.type, "enabled": body.enabled,
                    "events": body.events, "config": kind.check(config)}
            items = [item if entry["id"] == connection_id else entry for entry in self._items]
            self._write(items)
            self._items = items
            return self.public(item)

    def delete(self, connection_id: str) -> None:
        with self._lock:
            self._find(connection_id)
            items = [entry for entry in self._items if entry["id"] != connection_id]
            self._write(items)
            self._items = items
            self._last.pop(connection_id, None)

    def test(self, raw: dict, connection_id: str | None = None) -> dict:
        """Sends a test message with a connection as the form has it now;
        secrets left out come from the saved connection_id."""
        body, kind = self._parse(raw)
        with self._lock:
            old = self._find(connection_id) if connection_id else None
            config = kind.check(self._with_saved_secrets(kind, body, old))
        try:
            kind.send(TEST_EVENT, config)
        except outbound.SendFailed as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "Sent. Check that it arrived."}

    # ---- sending events -----------------------------------------------------------

    def emit(self, event: Event) -> None:
        """Sends the event to every enabled connection that wants it, each on
        its own thread."""
        with self._lock:
            targets = [dict(item) for item in self._items if item["enabled"] and event.name in item["events"]]
        for item in targets:
            kind = self._types.get(item["type"])
            if kind:
                outbound.in_background(f"connection {item['name']}", self._send, kind, item, event)

    def _send(self, kind: ConnectionType, item: dict, event: Event) -> None:
        try:
            kind.send(event, item["config"])
        except outbound.SendFailed as exc:
            self._last[item["id"]] = {"at": time.time(), "event": event.name, "ok": False, "error": str(exc)}
            raise
        self._last[item["id"]] = {"at": time.time(), "event": event.name, "ok": True, "error": None}

    # ---- helpers ---------------------------------------------------------------

    def _parse(self, raw: dict) -> tuple[ConnectionIn, ConnectionType]:
        try:
            body = ConnectionIn.model_validate(raw or {})
        except ValidationError as exc:
            raise Invalid(problems.explain(exc.errors())) from exc
        kind = self._types.get(body.type)
        if kind is None:
            raise Invalid(f"songdrop has no connection type {body.type!r}.")
        return body, kind

    @staticmethod
    def _with_saved_secrets(kind: ConnectionType, body: ConnectionIn, old: dict | None) -> dict:
        config = dict(body.config)
        if old and old["type"] == body.type:
            for spec in kind.fields:
                if spec.secret and not config.get(spec.key, "").strip() and spec.key in old["config"]:
                    config[spec.key] = old["config"][spec.key]
        return config

    def _find(self, connection_id: str) -> dict:
        for item in self._items:
            if item["id"] == connection_id:
                return item
        raise NotFound("No such connection.")


class NotFound(LookupError):
    """The message is shown to the user as is."""
