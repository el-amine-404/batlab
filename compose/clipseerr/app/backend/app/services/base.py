"""A service: an app that downloads what was requested (Settings, Services).
clipseerr only asks and follows: submit() hands a request over, poll() says
where it is. A request goes to the first enabled service that takes its kind
(audio or video), and to the next one when that one fails (requests/engine.py).

A new app is one module with one ServiceType subclass, listed in
services/__init__.py; its form is drawn from its fields.
"""

from dataclasses import dataclass, field
from typing import Literal

import httpx

from ..integrations import IntegrationType

State = Literal["sent", "downloading", "completed", "failed"]
Kind = Literal["audio", "video"]


@dataclass
class Update:
    """Where an attempt is, as a service says it."""

    state: State
    message: str | None = None
    external_id: str | None = None
    quality: str | None = None
    progress: float | None = None
    # Completed because the library had it already.
    already: bool = False


@dataclass
class Context:
    http: httpx.AsyncClient
    # Where an app that calls back reaches clipseerr about this request.
    callback_base: str
    # Shared by every poll of one round: an app's whole queue is read once.
    memo: dict = field(default_factory=dict)

    async def once(self, key: str, fetch):
        if key not in self.memo:
            self.memo[key] = await fetch()
        return self.memo[key]


class ServiceType(IntegrationType):
    def kinds(self, config: dict) -> tuple[Kind, ...]:
        raise NotImplementedError

    async def submit(self, ctx: Context, request: dict, config: dict) -> Update:
        """Hands the request over; raises outbound.SendFailed."""
        raise NotImplementedError

    async def poll(self, ctx: Context, request: dict, attempt: dict, config: dict) -> Update:
        """Where the attempt is now; raises outbound.SendFailed when the app
        can't be asked (the attempt then waits for the next round)."""
        raise NotImplementedError

    async def test(self, ctx: Context, config: dict) -> str:
        """A sentence saying it works; raises outbound.SendFailed."""
        raise NotImplementedError


def watch_url(youtube_id: str, music: bool = False) -> str:
    host = "music.youtube.com" if music else "www.youtube.com"
    return f"https://{host}/watch?v={youtube_id}"
