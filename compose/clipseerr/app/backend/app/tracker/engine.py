"""Following requests (Settings, Services): each one goes to the first
enabled service that takes its kind; while that service works on it, it is
asked where it is every poll_seconds (or at once when it calls back); when it
fails, or doesn't answer within the attempt timeout, the next service gets it.
Every service failed: the request failed.

Only one round runs at a time; a service is asked about its whole queue once
a round (services/base.py, Context.once).
"""

import asyncio
import logging
import time
from collections.abc import Callable

import httpx

from .. import outbound
from ..events import Event
from ..integrations import Integrations
from ..services import TYPES, Context, Update
from .store import ACTIVE, Requests

log = logging.getLogger("clipseerr.engine")

KIND_LABEL = {"audio": "Audio", "video": "Video"}


def credit(media: dict) -> str:
    return f"{media['artist']} – {media['title']}"


class Engine:
    def __init__(self, requests: Requests, services: Integrations, emit: Callable[[Event], None],
                 settings: Callable[[], dict], http: httpx.AsyncClient, callback_base: str, public_url: str):
        self.requests = requests
        self.services = services
        self._emit = emit
        self._settings = settings
        self._http = http
        self._callback_base = callback_base
        self._public_url = public_url
        self._wake = asyncio.Event()
        self._round = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.last_round: float | None = None

    # ---- running -----------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="engine")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_round()
            except Exception:
                log.exception("a round failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._settings()["poll_seconds"])
            except TimeoutError:
                pass

    async def run_round(self) -> None:
        async with self._round:
            ctx = Context(http=self._http, callback_base=self._callback_base)
            for request in self.requests.active():
                try:
                    await self._step(ctx, request)
                except Exception:
                    log.exception("request %s", request["id"])
            self.last_round = time.time()

    # ---- one request ---------------------------------------------------------------------

    def chain(self, kind: str) -> list[dict]:
        """The enabled services that take this kind, in order."""
        chain = []
        for item in self.services.items(enabled_only=True):
            kind_type = TYPES.get(item["type"])
            if kind_type and kind in kind_type.kinds(item["config"]):
                chain.append(item)
        return chain

    async def _step(self, ctx: Context, request: dict) -> None:
        attempt = _current(request)
        if attempt:
            await self._poll(ctx, request, attempt)
            if request["status"] not in ACTIVE or _current(request):
                return
        await self._submit(ctx, request)

    async def _poll(self, ctx: Context, request: dict, attempt: dict) -> None:
        service = _find(self.services.items(), attempt["service_id"])
        kind_type = TYPES.get(service["type"]) if service else None
        if not service or not kind_type:
            self._attempt_failed(request, attempt, "This service was removed.")
            return
        timeout = self._settings()["attempt_timeout_hours"] * 3600
        try:
            update = await kind_type.poll(ctx, request, attempt, service["config"])
        except outbound.SendFailed as exc:
            attempt["message"] = f"Can't ask {service['name']} right now: {exc}"
            update = None
        if update:
            self._apply(request, attempt, update, service)
        if _current(request) is attempt and time.time() - attempt["started_at"] > timeout:
            self._attempt_failed(request, attempt, f"{service['name']} didn't finish within "
                                                   f"{self._settings()['attempt_timeout_hours']} h.")
        self.requests.save(request)

    async def _submit(self, ctx: Context, request: dict) -> None:
        tried = {a["service_id"] for a in request["attempts"] if a.get("round", 0) == request["round"]}
        for service in self.chain(request["kind"]):
            if service["id"] in tried:
                continue
            attempt = {"service_id": service["id"], "service_name": service["name"], "service_type": service["type"],
                       "round": request["round"], "external_id": None, "state": "sent", "message": None,
                       "quality": None, "progress": None, "started_at": time.time(), "updated_at": time.time()}
            request["attempts"].append(attempt)
            try:
                update = await TYPES[service["type"]].submit(ctx, request, service["config"])
            except outbound.SendFailed as exc:
                self.services.note(service["id"], False, str(exc))
                self._attempt_failed(request, attempt, str(exc))
                self.requests.save(request)
                continue
            except Exception as exc:
                log.exception("%s failed to take request %s", service["name"], request["id"])
                self._attempt_failed(request, attempt, f"{service['name']} failed: {exc.__class__.__name__}.")
                self.requests.save(request)
                continue
            self.services.note(service["id"], True, "Took a request.")
            self._apply(request, attempt, update, service)
            self.requests.save(request)
            if request["status"] in ACTIVE and not _current(request):
                continue  # failed at once: the next one
            return
        # Nobody (left) to take it.
        if request["status"] in ACTIVE:
            failures = [a for a in request["attempts"] if a.get("round", 0) == request["round"]]
            if not failures:
                message = (f"No enabled service takes {request['kind']}. Add one in Settings, Services.")
            elif len(failures) == 1:
                message = f"{failures[0]['service_name']}: {failures[0]['message']}"
            else:
                message = "Every service failed. Last: " + f"{failures[-1]['service_name']}: {failures[-1]['message']}"
            request["status"] = "failed"
            request["message"] = message
            self.requests.save(request)
            self._announce(request, "request.failed")

    def _apply(self, request: dict, attempt: dict, update: Update, service: dict) -> None:
        attempt["state"] = update.state
        attempt["updated_at"] = time.time()
        if update.external_id:
            attempt["external_id"] = update.external_id
        if update.message:
            attempt["message"] = update.message
        if update.quality:
            attempt["quality"] = update.quality
        attempt["progress"] = update.progress
        if update.state == "failed":
            self._attempt_failed(request, attempt, update.message or f"{service['name']} couldn't get it.")
            return
        request["message"] = update.message
        if update.state == "completed":
            request["status"] = "completed"
            request["quality"] = update.quality
            attempt["progress"] = 1.0
            self._announce(request, "request.completed", already=update.already)
        else:
            request["status"] = update.state

    def _attempt_failed(self, request: dict, attempt: dict, message: str) -> None:
        attempt["state"] = "failed"
        attempt["message"] = message
        attempt["updated_at"] = time.time()
        request["status"] = "queued"
        request["message"] = f"{attempt['service_name']} failed; trying the next service."

    # ---- what the API asks -----------------------------------------------------------------

    def created(self, request: dict) -> None:
        self._announce(request, "request.created")
        self.wake()

    def retry(self, request: dict) -> dict:
        request["round"] += 1
        request["status"] = "queued"
        request["message"] = "Waiting for a service."
        request["quality"] = None
        self.requests.save(request)
        self.wake()
        return request

    def cancel(self, request: dict) -> dict:
        request["status"] = "cancelled"
        request["message"] = "Cancelled here; a service that already has it may still finish it."
        self.requests.save(request)
        return request

    def report(self, request: dict, update: Update) -> dict:
        """What an app said about its attempt (POST /requests/{id}/report)."""
        attempt = _current(request)
        if not attempt:
            raise LookupError("This request has no attempt under way.")
        service = _find(self.services.items(), attempt["service_id"]) or {"name": attempt["service_name"]}
        self._apply(request, attempt, update, service)
        self.requests.save(request)
        if request["status"] == "queued":
            self.wake()
        return request

    # ---- messages ---------------------------------------------------------------------------

    def _announce(self, request: dict, name: str, already: bool = False) -> None:
        media = request["media"]
        kind = KIND_LABEL[request["kind"]]
        url = f"{self._public_url}/requests/{request['id']}" if self._public_url else None
        watch = media.get("links", {}).get("youtube")
        if name == "request.created":
            route = " → ".join(s["name"] for s in self.chain(request["kind"])) or "no service yet"
            what = "best quality available" if request["kind"] == "audio" else "best quality, as a file"
            subject, kind_of = f"Requested: {credit(media)}", "info"
            lines = [f"**{kind}**, {what}", f"Via {route}"]
        elif name == "request.completed":
            last = _last(request)
            subject = (f"Already in the library: {credit(media)}" if already else f"Got it: {credit(media)}")
            kind_of = "success"
            detail = " · ".join(x for x in (kind, request.get("quality"), last and f"by {last['service_name']}") if x)
            lines = [detail, *([request["message"]] if request.get("message") else [])]
        else:
            subject, kind_of = f"Couldn't get: {credit(media)}", "failure"
            lines = [f"**{kind}**", request.get("message") or "Every service failed."]
        if (request.get("asked_by") or {}).get("name"):
            lines.append(f"Asked by {request['asked_by']['name']}")
        if watch:
            lines.append(f"[Watch on YouTube]({watch})")
        self._emit(Event(name, subject, tuple(lines), kind_of, url, media.get("thumbnail"),
                         {"request": request}))


def _current(request: dict) -> dict | None:
    """The attempt under way, if any."""
    if request["status"] not in ACTIVE:
        return None
    for attempt in reversed(request["attempts"]):
        if attempt.get("round", 0) == request["round"] and attempt["state"] in ("sent", "downloading"):
            return attempt
    return None


def _last(request: dict) -> dict | None:
    return request["attempts"][-1] if request["attempts"] else None


def _find(items: list[dict], item_id: str) -> dict | None:
    return next((item for item in items if item["id"] == item_id), None)
