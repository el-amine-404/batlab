"""What every connection type is: a form (its fields) and a way to send an
event. A new type (ntfy, Gotify…) is one module with one ConnectionType
subclass, listed in connections/__init__.py; the page draws its form from
describe(), so the page needs no change."""

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import ClassVar, Literal

from ..events import Event

_URL = re.compile(r"^https?://[^\s/]+(?:/\S*)?$")


class Invalid(ValueError):
    """The message is shown to the user as is."""


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    # secret: write-only, like a password; the API says only whether it is set.
    kind: Literal["text", "url", "secret_url"]
    help: str = ""
    placeholder: str = ""
    required: bool = True

    @property
    def secret(self) -> bool:
        return self.kind.startswith("secret")


class ConnectionType(ABC):
    key: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    fields: ClassVar[tuple[Field, ...]]

    def check(self, config: dict) -> dict:
        """The config with only known fields, trimmed; raises Invalid."""
        checked = {}
        for spec in self.fields:
            value = str(config.get(spec.key) or "").strip()
            if not value:
                if spec.required:
                    raise Invalid(f"{spec.label} is needed.")
                continue
            if spec.kind in ("url", "secret_url") and not _URL.match(value):
                raise Invalid(f"{spec.label} must be a link starting with http:// or https://.")
            checked[spec.key] = value
        self.validate(checked)
        return checked

    def validate(self, config: dict) -> None:
        """Checks of the type's own, on a config with every required field."""

    @abstractmethod
    def send(self, event: Event, config: dict) -> None:
        """Delivers the event now; raises outbound.SendFailed."""

    @abstractmethod
    def summary(self, config: dict) -> str:
        """Where it sends, without giving a secret away."""

    def describe(self) -> dict:
        return {
            "type": self.key,
            "label": self.label,
            "description": self.description,
            "fields": [dict(asdict(spec), secret=spec.secret) for spec in self.fields],
        }
