"""What subfeed reports (docs/services.md, "Messages"): the events a
notification can subscribe to, and one event as it is sent. The poller only
says what happened; the notifications (notifications/) decide where it goes
and how it reads there. Names are <thing>.<what happened>."""

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
    EventType("feed.failing", "A feed fails",
              "Its reads failed several times in a row (Settings, General). Once, until it works again.", True,
              "warning"),
    EventType("feed.recovered", "A failing feed works again", "The first good read after feed.failing.", True,
              "success"),
    EventType("posts.new", "New posts", "A read found new posts (data.posts: them, as the API gives them). For "
                                        "programs: a busy community makes one a read.", False),
)
NAMES = tuple(item.name for item in CATALOG)
DEFAULTS = tuple(item.name for item in CATALOG if item.default)
KINDS: dict[str, Kind] = {item.name: item.kind for item in CATALOG}


@dataclass(frozen=True)
class Event:
    name: str
    # One line: what happened to what ("r/SexyMusicVideos fails").
    subject: str
    # The details, one per line, for people.
    lines: tuple[str, ...] = ()
    # Where to see it on the page, when the page's address is set.
    url: str | None = None
    # For programs: the feed as GET /api/v1/feeds/{slug} gives it, and more.
    data: dict = field(default_factory=dict)
    time: float = field(default_factory=time.time)

    @property
    def kind(self) -> Kind:
        return KINDS.get(self.name, "info")

    def payload(self) -> dict:
        """The JSON a Webhook receives (docs/services.md, "Messages")."""
        return {"source": "subfeed", "event": self.name, "time": self.time, "subject": self.subject,
                "text": "\n".join(self.lines), "url": self.url, "data": self.data}
