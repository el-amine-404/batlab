"""The live stream of jobs (Server-Sent Events, api/routes/jobs.py): each
stream keeps the ids of the jobs that changed since it last sent; a job that
changes ten times before the stream sends is sent once, as it is then. So a
slow reader costs nothing, and nothing piles up."""

import asyncio
from collections.abc import AsyncIterator

from .loops import call_on

MAX_SUBSCRIBERS = 64
KEEP_ALIVE = 15


class TooMany(Exception):
    """The message is shown as is."""


class Subscription:
    def __init__(self, broker: "Broker", jobs: frozenset[str] | None):
        self._broker = broker
        self.jobs = jobs
        self._pending: dict[str, None] = {}
        self._ready = asyncio.Event()

    def _add(self, job_id: str) -> None:
        if self.jobs is None or job_id in self.jobs:
            self._pending[job_id] = None
            self._ready.set()

    async def messages(self) -> AsyncIterator[str | None]:
        """The id of each job that changed, in the order they did; None when
        KEEP_ALIVE seconds pass without one, the cue for a keep-alive."""
        try:
            while True:
                try:
                    await asyncio.wait_for(self._ready.wait(), KEEP_ALIVE)
                except TimeoutError:
                    yield None
                    continue
                self._ready.clear()
                pending, self._pending = self._pending, {}
                for job_id in pending:
                    yield job_id
        finally:
            self.close()

    def close(self) -> None:
        self._broker._subscriptions.discard(self)


class Broker:
    def __init__(self):
        self._subscriptions: set[Subscription] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """The loop the streams run on (the server's)."""
        self._loop = loop

    def publish(self, job_id: str) -> None:
        """The job changed (or was removed). Each stream reads the job itself
        when it sends, as its caller sees it. Safe from any thread (a route
        runs in a worker thread)."""
        if not self._subscriptions:
            return
        call_on(self._loop, self._publish, job_id)

    def _publish(self, job_id: str) -> None:
        for subscription in list(self._subscriptions):
            subscription._add(job_id)

    @property
    def subscribers(self) -> int:
        return len(self._subscriptions)

    def subscribe(self, jobs: frozenset[str] | None = None) -> Subscription:
        """A stream of these jobs (None: every job); raises TooMany."""
        if len(self._subscriptions) >= MAX_SUBSCRIBERS:
            raise TooMany("Too many live streams are open; try again in a moment.")
        subscription = Subscription(self, jobs)
        self._subscriptions.add(subscription)
        return subscription
