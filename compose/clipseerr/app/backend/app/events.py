"""What clipseerr tells notifications about (Settings, Notifications): a
request made, done, or failed. Names are <thing>.<what happened>, as in
songdrop (docs/services.md)."""

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["info", "success", "warning", "failure"]


@dataclass(frozen=True)
class EventInfo:
    name: str
    label: str
    description: str


CATALOG = (
    EventInfo("request.created", "Requested", "Someone asked for a song or a video."),
    EventInfo("request.completed", "Downloaded", "A service has it: it is in the library, or on its way there."),
    EventInfo("request.failed", "Failed", "No service could get it."),
)
NAMES = tuple(item.name for item in CATALOG)


@dataclass(frozen=True)
class Event:
    name: str
    subject: str
    lines: tuple[str, ...] = ()
    kind: Kind = "info"
    url: str | None = None
    image: str | None = None
    data: dict = field(default_factory=dict)
