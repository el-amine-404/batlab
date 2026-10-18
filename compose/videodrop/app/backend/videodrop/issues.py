"""What needs a person's eye (GET /api/v1/issues, the page's Issues): each
issue says what is wrong, why it matters and where to fix it. Read from
the state every time, so an issue goes away by itself once fixed.

The newest yt-dlp is asked of PyPI once a day (ReleaseWatch): yt-dlp
follows the sites' changes, and Renovate and the nightly updater bring each
release here within a day or two; one older than that means the chain
stopped."""

# The class has a list() method: annotations are read later, as the built-in's.
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib import metadata
from typing import Literal

import httpx

from . import cookies, outbound
from .config import Config
from .db import Database

log = logging.getLogger("videodrop.issues")

Level = Literal["error", "warning", "info"]
DAY = 86400
# A release older than this and not running here: the update chain stopped.
UPDATE_GRACE = 2 * DAY
LATEST_KEY = "yt_dlp_latest"
PYPI = "https://pypi.org/pypi/yt-dlp/json"
ITEMS = 5
BOT_CHECK = re.compile(r"confirm you.re not a bot|sign in to confirm", re.I)


@dataclass(frozen=True)
class Issue:
    id: str
    level: Level
    title: str
    detail: str
    # Where on the page it is fixed: {"label", "path"}.
    action: dict | None = None
    since: float | None = None
    # What it is about, each with its page: [{"label", "path", "detail"}], ITEMS at most.
    items: tuple[dict, ...] = ()


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


class ReleaseWatch:
    """yt-dlp's newest release on PyPI, asked once a day."""

    def __init__(self, db: Database, http: httpx.AsyncClient):
        self._db = db
        self._http = http

    @property
    def latest(self) -> dict | None:
        return self._db.get(LATEST_KEY)

    async def check(self) -> None:
        try:
            answer = (await outbound.call(self._http, "GET", PYPI)).json()
        except (outbound.SendFailed, ValueError) as exc:
            log.warning("couldn't ask PyPI for yt-dlp's newest release: %s", exc)
            return
        version = answer["info"]["version"]
        files = answer.get("releases", {}).get(version) or [{}]
        released = files[0].get("upload_time_iso_8601")
        self._db.put(LATEST_KEY, {"version": version, "released_at": released, "checked_at": time.time()})

    async def run(self) -> None:
        while True:
            await self.check()
            await asyncio.sleep(DAY)


class Issues:
    def __init__(self, config: Config, db: Database, settings, scheduler, notifications, releases: ReleaseWatch):
        self._config = config
        self._db = db
        self._settings = settings
        self._scheduler = scheduler
        self._notifications = notifications
        self._releases = releases

    def list(self, now: float | None = None) -> list[Issue]:
        now = now or time.time()
        found: list[Issue] = []
        if not self._config.deno:
            found.append(Issue("deno", "error", "No Deno: YouTube may refuse downloads",
                               "yt-dlp answers YouTube's challenge with Deno, which isn't in this image. Rebuild "
                               "videodrop (make up STACK=videodrop)."))
        if self._scheduler.disk_low:
            free = (self._scheduler.free_bytes or 0) / 1024 ** 3
            found.append(Issue("disk", "error", "The media disk is nearly full: downloads wait",
                               f"{free:.1f} GB free, under the {self._settings().min_free_gb:g} GB asked for.",
                               {"label": "Storage settings", "path": "/settings/storage"}))
        found += self._yt_dlp(now)
        found += self._failures(now)
        found += self._cookies(now)
        for item in self._notifications.list():
            if item["enabled"] and item["last"] and not item["last"]["ok"]:
                found.append(Issue(f"notification:{item['id']}", "warning", f"Notification {item['name']} fails",
                                   item["last"]["message"], {"label": "Notifications", "path": "/settings/notifications"},
                                   item["last"]["at"]))
        callbacks = self._db.all("SELECT id, title, url, client, callback_url, callback_last, updated_at FROM jobs "
                                 "WHERE updated_at > ? AND json_extract(callback_last, '$.ok') = 0 "
                                 "ORDER BY updated_at DESC", (now - DAY,))
        if callbacks:
            count = len(callbacks)
            found.append(Issue("callbacks", "warning", f"{count} callback{'s' if count > 1 else ''} failed in the last day",
                               "videodrop couldn't tell the apps that asked how their downloads went (the address "
                               "they gave doesn't answer). They still see their downloads when they ask.",
                               None, callbacks[0]["updated_at"],
                               tuple({"label": row["title"] or row["url"], "path": f"/jobs/{row['id']}",
                                      "detail": f"{row['client'] or 'An app'}: "
                                                f"{json.loads(row['callback_last'])['message']}"}
                                     for row in callbacks[:ITEMS])))
        order = {"error": 0, "warning": 1, "info": 2}
        return sorted(found, key=lambda issue: order[issue.level])

    def public(self) -> dict:
        issues = [asdict(issue) for issue in self.list()]
        return {"issues": issues, "counts": {level: sum(1 for i in issues if i["level"] == level)
                                             for level in ("error", "warning", "info")}}

    def _yt_dlp(self, now: float) -> list[Issue]:
        latest = self._releases.latest
        running = metadata.version("yt-dlp")
        if not latest or _version_tuple(latest["version"]) <= _version_tuple(running):
            return []
        released = _parse_time(latest.get("released_at"))
        late = released is not None and now - released > UPDATE_GRACE
        return [Issue("yt-dlp", "warning" if late else "info",
                      f"yt-dlp {latest['version']} is out; this runs {running}",
                      "The nightly updater should have rebuilt videodrop with it by now: check #updates and #alerts, "
                      "or run make up STACK=videodrop." if late else
                      "Renovate and the nightly updater bring it here within a day or two, nothing to do.",
                      None, released)]

    def _failures(self, now: float) -> list[Issue]:
        rows = self._db.all("SELECT id, title, url, error, finished_at FROM jobs WHERE status = 'failed' AND "
                            "finished_at > ? ORDER BY finished_at DESC", (now - DAY,))

        def items(chosen):
            return tuple({"label": row["title"] or row["url"], "path": f"/jobs/{row['id']}", "detail": row["error"]}
                         for row in chosen[:ITEMS])
        bot = [r for r in rows if BOT_CHECK.search(r["error"] or "")]
        found = []
        if bot:
            found.append(Issue("bot-check", "error", "YouTube asks this address to sign in",
                               f"{len(bot)} download{'s' if len(bot) > 1 else ''} in the last day failed with "
                               "\"confirm you're not a bot\". It passes after a while; cookies of a signed-in "
                               "account get through now. Every app here shares this address.",
                               {"label": "Cookies", "path": "/settings/cookies"}, bot[0]["finished_at"], items(bot)))
        failed = [r for r in rows if not BOT_CHECK.search(r["error"] or "")]
        if failed:
            others = len(failed)
            found.append(Issue("failed", "warning", f"{others} download{'s' if others > 1 else ''} failed in the last day",
                               "Each one's page says why, and its log what happened.",
                               {"label": "Failed downloads", "path": "/?tab=failed"}, failed[0]["finished_at"],
                               items(failed)))
        return found

    def _cookies(self, now: float) -> list[Issue]:
        status = cookies.status(self._config.state_dir)
        action = {"label": "Cookies", "path": "/settings/cookies"}
        if not status["present"]:
            return []
        if status.get("expired"):
            return [Issue("cookies", "warning", "The YouTube cookies have run out",
                          "Age-restricted and sign-in-only videos fail until new ones are uploaded.", action)]
        expires = status.get("expires_at")
        if expires and expires - now < 7 * DAY:
            return [Issue("cookies", "info", "The YouTube cookies run out within a week",
                          time.strftime("They run out on %d %b.", time.localtime(expires)), action)]
        return []


def _parse_time(text: str | None) -> float | None:
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
