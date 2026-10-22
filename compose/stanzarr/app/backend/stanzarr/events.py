"""What Stanzarr reports (docs/services.md, "Messages"): the events a
notification can subscribe to, and one event as it is sent. The scan and the
queue only say what happened; the notifications (notifications/) decide where
it goes and how it reads there. Names are <thing>.<what happened>."""

import time
from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["info", "success", "warning", "failure"]


@dataclass(frozen=True)
class EventType:
    name: str
    label: str
    description: str
    # Ticked when a notification is made.
    default: bool
    # How it reads where a message has a colour (Apprise's types).
    kind: Kind = "info"


CATALOG: tuple[EventType, ...] = (
    EventType("lyrics.found", "Lyrics found",
              "A source gave a song lyrics it had none of, or synced ones where it had plain (data.song, "
              "data.synced). A library's first scan makes many.", False, "success"),
    EventType("ai.synced", "Synced by AI",
              "The GPU worker gave a song synced lyrics; data.unsure says how many lines to check.", True, "success"),
    EventType("ai.failed", "AI sync failed", "A worker couldn't sync a song (data.error says why).", True, "failure"),
)
NAMES = tuple(item.name for item in CATALOG)
DEFAULTS = tuple(item.name for item in CATALOG if item.default)
KINDS: dict[str, Kind] = {item.name: item.kind for item in CATALOG}


@dataclass(frozen=True)
class Event:
    name: str
    # One line: what happened to what ("Stormy – Hugo: synced by AI").
    subject: str
    # The details, one per line, for people.
    lines: tuple[str, ...] = ()
    # Where to see it on the page, when the page's address is set.
    url: str | None = None
    # For programs: the song as the API gives it, and more.
    data: dict = field(default_factory=dict)
    time: float = field(default_factory=time.time)

    @property
    def kind(self) -> Kind:
        return KINDS.get(self.name, "info")

    def payload(self) -> dict:
        """The JSON a Webhook receives (docs/services.md, "Messages")."""
        return {"source": "stanzarr", "event": self.name, "time": self.time, "subject": self.subject,
                "text": "\n".join(self.lines), "url": self.url, "data": self.data}
