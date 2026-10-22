"""What every lyrics source is: an add-on, chosen in Settings, Sources, not
written into Stanzarr. A form described by its fields (as a notification's),
and a search for one song. A new source (NetEase, Musixmatch with a token,
Genius for plain text…) is one module with one LyricsSource subclass, listed
in sources/__init__.py; the page draws its form from describe()."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import ClassVar, Literal

from ..notifications.base import Field, NotificationType

Kind = Literal["synced", "plain", "instrumental", "none"]


@dataclass(frozen=True)
class SongQuery:
    artist: str
    title: str
    album: str = ""
    # Seconds; 0 when unknown.
    duration: float = 0.0


@dataclass(frozen=True)
class Found:
    kind: Kind
    # The lyrics as LRC (synced) or plain text; "" when none.
    text: str = ""
    # Where at the source, written into the file: "lrclib id: 123".
    reference: str | None = None


NOTHING = Found("none")
# A song every source should know, for Test.
PROBE = SongQuery("Daft Punk", "One More Time", "Discovery", 320)


class LyricsSource(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    fields: ClassVar[tuple[Field, ...]] = ()
    homepage: ClassVar[str | None] = None
    # True: it can give synced lyrics, not only plain text.
    synced: ClassVar[bool] = True

    # The same checks of a form as a notification's.
    check = NotificationType.check
    validate = NotificationType.validate

    @abstractmethod
    def search(self, song: SongQuery, config: dict) -> Found:
        """The best this source has for the song; raises on a failure to ask
        (the next pass tries again)."""

    def test(self, config: dict) -> str:
        """A sentence saying it works: it is asked for a song it should know."""
        found = self.search(PROBE, config)
        return f"{self.label} answered: {found.kind} lyrics for {PROBE.artist} – {PROBE.title}."

    def describe(self) -> dict:
        return {"type": self.key, "label": self.label, "description": self.description, "homepage": self.homepage,
                "synced": self.synced, "fields": [dict(asdict(spec), secret=spec.secret) for spec in self.fields]}
