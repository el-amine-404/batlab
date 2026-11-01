"""One listener on PostgreSQL per API process; each browser's stream is a queue
fed from it. Nobody polls the database, so idle streams cost nothing, and more
API processes or workers need no other change."""
from __future__ import annotations

import asyncio
import json
import logging
import select
import threading

logger = logging.getLogger("scanloom.events")
CHANNEL = "scanloom_events"


class Broker:
    def __init__(self, connect):
        self._connect = connect
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._listen, name="scanloom-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, message: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A stream that stopped reading: it refetches everything instead.
                queue.get_nowait()
                queue.put_nowait({"event": "resync"})

    def _listen(self) -> None:
        wait = 1.0
        first = True
        while not self._stopping.is_set():
            connection = None
            try:
                connection = self._connect()
                with connection.cursor() as cursor:
                    cursor.execute(f"LISTEN {CHANNEL}")
                if not first:
                    # Anything may have happened while the database was away.
                    self._loop.call_soon_threadsafe(self._publish, {"event": "resync"})
                first, wait = False, 1.0
                while not self._stopping.is_set():
                    if not select.select([connection], [], [], 5)[0]:
                        continue
                    connection.poll()
                    while connection.notifies:
                        payload = json.loads(connection.notifies.pop(0).payload)
                        self._loop.call_soon_threadsafe(self._publish, payload)
            except Exception as error:
                logger.warning("Event listener lost the database: %s", error)
                self._stopping.wait(wait)
                wait = min(30.0, wait * 2)
            finally:
                if connection is not None:
                    connection.close()
