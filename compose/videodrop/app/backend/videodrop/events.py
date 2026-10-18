"""What videodrop reports to people (docs/services.md, "Messages"): the
events a notification can subscribe to, and one event as it is sent. The
scheduler only says what happened; the notifications (notifications/) decide
where it goes and how it reads there. Names are <thing>.<what happened>.

The program that asked for a download follows it otherwise: its callback,
the live stream or GET /api/v1/jobs/{id} (api/routes/jobs.py)."""

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
    EventType("job.failed", "A download fails",
              "After its last try (Settings, retries). data.job: the job, as the API gives it.", True, "failure"),
    EventType("job.finished", "A download is done", "data.job: the job, with its files. For programs, or for "
                                                    "people who want to know each time.", False, "success"),
    EventType("disk.low", "The disk is nearly full",
              "Less free than Settings, min_free_gb: downloads wait. Once, until there is room again.", True,
              "warning"),
)
NAMES = tuple(item.name for item in CATALOG)
DEFAULTS = tuple(item.name for item in CATALOG if item.default)
KINDS: dict[str, Kind] = {item.name: item.kind for item in CATALOG}


@dataclass(frozen=True)
class Event:
    name: str
    # One line: what happened to what ("Housewife (Official Video) failed").
    subject: str
    # The details, one per line, for people.
    lines: tuple[str, ...] = ()
    # Where to see it on the page, when the page's address is set.
    url: str | None = None
    # For programs: the job as GET /api/v1/jobs/{id} gives it, and more.
    data: dict = field(default_factory=dict)
    time: float = field(default_factory=time.time)

    @property
    def kind(self) -> Kind:
        return KINDS.get(self.name, "info")

    def payload(self) -> dict:
        """The JSON a Webhook receives (docs/services.md, "Messages")."""
        return {"source": "videodrop", "event": self.name, "time": self.time, "subject": self.subject,
                "text": "\n".join(self.lines), "url": self.url, "data": self.data}
