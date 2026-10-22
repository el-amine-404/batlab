"""What songdrop reports (docs/services.md, "Events"): the catalog of event
names a connection can subscribe to, and one event as it is sent.

Jobs only say what happened (emit an Event); the connections (connections/)
decide where it goes and how it is worded there.
"""

import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class EventType:
    name: str
    label: str
    description: str
    # Ticked when a new connection is made.
    default: bool
    # How it reads where a message has a colour or an icon (Apprise's types).
    kind: str = "info"


CATALOG: tuple[EventType, ...] = (
    EventType("job.incomplete", "Songs missing after a download",
              "A download ended without some of its songs. Says which, why, and when they are tried again.", True,
              "warning"),
    EventType("song.recovered", "A missing song came back",
              "A later try downloaded a song that was missing.", True, "success"),
    EventType("song.gave_up", "songdrop gave up on a song",
              "The last later try of a song failed; only Retry now tries again.", True, "failure"),
    EventType("job.finished", "Every finished download",
              "Any download that ended, complete or not.", False, "success"),
)
NAMES = frozenset(item.name for item in CATALOG)
DEFAULTS = tuple(item.name for item in CATALOG if item.default)
KINDS = {item.name: item.kind for item in CATALOG}


@dataclass(frozen=True)
class Event:
    name: str
    # One line: what happened to what ("Album X: 9 of 10 songs").
    subject: str
    # The details, one per line, for people.
    lines: tuple[str, ...] = ()
    # Where to see it on the page, when the page address is set.
    url: str = ""
    # For programs: the job as GET /api/v1/jobs/{id} gives it, and more.
    data: dict = field(default_factory=dict)
    time: float = field(default_factory=time.time)

    def __post_init__(self):
        if self.name not in NAMES and self.name != "test":
            raise ValueError(f"unknown event {self.name}")

    def as_payload(self) -> dict:
        """The JSON a Webhook connection receives (docs/services.md, "Messages")."""
        return {
            "source": "songdrop",
            "event": self.name,
            "time": self.time,
            "subject": self.subject,
            "text": "\n".join(self.lines),
            "url": self.url or None,
            "data": self.data,
        }
