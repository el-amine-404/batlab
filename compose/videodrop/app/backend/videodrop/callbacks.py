"""A job's callback: its caller told as the job goes (JobIn.callback_url),
POSTed {"event", "time", "job"}. Signed when the caller gave a secret, so it
can tell the call came from videodrop:

    X-Videodrop-Timestamp: <unix seconds>
    X-Videodrop-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<body>", the secret as key>

A failed call is tried again (DELAYS) in the background; the job's
callback.last says how the last one went. Calls are not kept across a
restart: a caller that must not miss the end also reads the job."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Callable

import httpx

from . import outbound

log = logging.getLogger("videodrop.callbacks")

DELAYS = (5, 30, 120, 600)
AT_ONCE = 8


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class Callbacks:
    def __init__(self, http: httpx.AsyncClient, record: Callable[[str, dict], None]):
        """record(job_id, last): saves how a call went."""
        self._http = http
        self._record = record
        self._slots = asyncio.Semaphore(AT_ONCE)
        self._tasks: set[asyncio.Task] = set()

    def send(self, url: str, secret: str | None, event: str, job: dict) -> None:
        """In the background; final events are retried, progress isn't."""
        body = json.dumps({"event": f"job.{event}", "time": time.time(), "job": job},
                          ensure_ascii=False, separators=(",", ":")).encode()
        task = asyncio.get_running_loop().create_task(
            self._deliver(url, secret, event, job["id"], body, retry=event != "progress"))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, url: str, secret: str | None, event: str, job_id: str, body: bytes,
                       retry: bool) -> None:
        delivery = uuid.uuid4().hex
        for attempt, delay in enumerate((0, *DELAYS) if retry else (0,)):
            if delay:
                await asyncio.sleep(delay)
            timestamp = str(int(time.time()))
            headers = {"Content-Type": "application/json", "X-Videodrop-Event": f"job.{event}",
                       "X-Videodrop-Delivery": delivery, "X-Videodrop-Timestamp": timestamp}
            if secret:
                headers["X-Videodrop-Signature"] = sign(secret, timestamp, body)
            try:
                async with self._slots:
                    await outbound.call(self._http, "POST", url, content=body, headers=headers)
            except outbound.SendFailed as exc:
                log.warning("callback job.%s of %s, try %d: %s", event, job_id, attempt + 1, exc)
                self._record(job_id, {"at": time.time(), "event": f"job.{event}", "ok": False,
                                      "message": str(exc)[:300], "tries": attempt + 1})
                continue
            if event != "progress":
                self._record(job_id, {"at": time.time(), "event": f"job.{event}", "ok": True,
                                      "message": "Delivered.", "tries": attempt + 1})
            return

    async def drain(self) -> None:
        """At shutdown: what is being sent now, a few seconds at most."""
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=5)
            for task in self._tasks:
                task.cancel()
