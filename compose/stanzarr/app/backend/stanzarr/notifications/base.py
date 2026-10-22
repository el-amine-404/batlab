"""What every notification type is: a form, described by its fields, checks
of what was filled in, and a way to send an event. A new type (ntfy,
Gotify…) is one module with one NotificationType subclass, listed in
notifications/__init__.py; the page draws its form from describe(), so the
page needs no change."""

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import ClassVar, Literal

import httpx

from ..domain import Invalid
from ..events import Event

_URL = re.compile(r"^https?://[^\s/]+(?:/\S*)?$")


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    # secret: write-only, like a password; the API says only whether it is set.
    kind: Literal["text", "url", "secret", "secret_url"]
    help: str = ""
    placeholder: str = ""
    required: bool = True
    default: str | None = None

    @property
    def secret(self) -> bool:
        return self.kind.startswith("secret")


class NotificationType(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    fields: ClassVar[tuple[Field, ...]]
    # A link to the app's own page, for the form.
    homepage: ClassVar[str | None] = None

    def check(self, config: dict) -> dict:
        """The config with only known fields, trimmed, defaults filled in;
        raises Invalid."""
        checked = {}
        for spec in self.fields:
            value = str(config.get(spec.key) if config.get(spec.key) is not None else "").strip()
            if not value and spec.default is not None:
                value = spec.default
            if not value:
                if spec.required:
                    raise Invalid(f"{spec.label} is needed.")
                continue
            if spec.kind in ("url", "secret_url"):
                if not _URL.match(value):
                    raise Invalid(f"{spec.label} must be a link starting with http:// or https://.")
                value = value.rstrip("/")
            checked[spec.key] = value
        self.validate(checked)
        return checked

    def validate(self, config: dict) -> None:
        """Checks of the type's own, on a config with every required field."""

    @abstractmethod
    async def send(self, http: httpx.AsyncClient, event: Event, config: dict) -> None:
        """Delivers the event now; raises outbound.SendFailed."""

    @abstractmethod
    def summary(self, config: dict) -> str:
        """Where it sends, without giving a secret away."""

    def describe(self) -> dict:
        return {"type": self.key, "label": self.label, "description": self.description, "homepage": self.homepage,
                "fields": [dict(asdict(spec), secret=spec.secret) for spec in self.fields]}
