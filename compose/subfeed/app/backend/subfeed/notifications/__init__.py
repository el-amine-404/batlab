"""Notifications: where subfeed's events go (Settings, Notifications)."""

import asyncio
import logging

import httpx

from .. import outbound
from ..events import Event
from .apprise import Apprise
from .base import Field, NotificationType
from .discord import Discord
from .store import NotificationIn, NotificationStore
from .webhook import Webhook

log = logging.getLogger("subfeed.notifications")

TYPES: dict[str, NotificationType] = {kind.key: kind for kind in (Apprise(), Discord(), Webhook())}
TEST_EVENT = Event("test", "subfeed · test message", ("If you can read this, this notification works.",))


class Notifier:
    def __init__(self, store: NotificationStore, http: httpx.AsyncClient):
        self.store = store
        self._http = http
        self._tasks: set[asyncio.Task] = set()

    def emit(self, event: Event) -> None:
        """Sends the event to every enabled notification that wants it, in the
        background: a slow app never holds a read up."""
        for item in self.store.items(enabled_only=True):
            if event.name in item["events"] and item["type"] in self.store.types:
                task = asyncio.get_running_loop().create_task(self._send(item, event))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _send(self, item: dict, event: Event) -> None:
        try:
            await self.store.types[item["type"]].send(self._http, event, item["config"])
        except outbound.SendFailed as exc:
            log.warning("notification %s failed: %s", item["name"], exc)
            self.store.note(item["id"], False, str(exc))
        except Exception as exc:
            log.exception("notification %s failed", item["name"])
            self.store.note(item["id"], False, f"Failed: {exc.__class__.__name__}.")
        else:
            self.store.note(item["id"], True, f"Sent {event.name}.")

    async def test(self, raw: dict, item_id: str | None = None) -> dict:
        _body, kind, config = self.store.parse(raw, item_id)
        try:
            await kind.send(self._http, TEST_EVENT, config)
        except outbound.SendFailed as exc:
            return {"ok": False, "message": str(exc)}
        return {"ok": True, "message": "Sent. Check that it arrived."}

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=10)


__all__ = ["TYPES", "Field", "NotificationIn", "NotificationStore", "NotificationType", "Notifier"]
