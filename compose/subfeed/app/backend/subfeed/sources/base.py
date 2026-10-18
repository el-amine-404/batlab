"""What every source is: where a community's posts come from, without an
account or a key. A new source (Lemmy, a forum…) is one module with one
Source subclass, listed in sources/__init__.py; feeds name it by its key,
and the page draws its form from describe()."""

import re
from abc import ABC, abstractmethod
from typing import ClassVar

from ..domain import Invalid, Post


class SourceError(Exception):
    """Reading failed; the message says why, as a sentence."""


class Refused(SourceError):
    """The source said no (a limit, a block); it may say when to come back."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class Unreachable(SourceError):
    """No answer: the network, a timeout, the source down."""


class BadAnswer(SourceError):
    """An answer that isn't what the source gives: changed, or not it."""


class Source(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    # What a feed of this source reads, as the form asks for it.
    target_label: ClassVar[str]
    target_help: ClassVar[str]
    target_pattern: ClassVar[str]
    # Where the history comes from, for messages ("PullPush").
    archive_label: ClassVar[str]
    # Posts a page of history holds.
    page_size: ClassVar[int] = 100

    def normalize(self, raw: str) -> str:
        """The target as the form or the API gave it, cleaned; raises Invalid."""
        value = str(raw or "").strip()
        if not re.fullmatch(self.target_pattern, value):
            raise Invalid(f"That isn't a {self.target_label.lower()} name: {self.target_help}")
        return value

    @abstractmethod
    def home(self, target: str) -> str:
        """The community's own page."""

    @abstractmethod
    def title(self, target: str) -> str:
        """How the community is named ("r/Name")."""

    @abstractmethod
    async def newest(self, target: str) -> list[Post]:
        """The latest posts, fresh; raises SourceError."""

    @abstractmethod
    async def history(self, target: str, before: float | None = None) -> list[Post]:
        """A page of older posts, newest first, posted before before (all when
        None); raises SourceError."""

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "target_label": self.target_label,
                "target_help": self.target_help, "target_pattern": self.target_pattern}
