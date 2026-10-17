"""What every integration type is, a service (an app that downloads) or a
notification (somewhere messages go): a form, described by its fields, and
checks of what was filled in. A new type is one module with one subclass,
listed in services/__init__.py or notifications/__init__.py; the page draws
its form from describe(), so the page needs no change."""

import re
from dataclasses import asdict, dataclass
from typing import ClassVar, Literal

_URL = re.compile(r"^https?://[^\s/]+(?:/\S*)?$")


class Invalid(ValueError):
    """The message is shown to the user as is."""


@dataclass(frozen=True)
class Choice:
    value: str
    label: str


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    # secret: write-only, like a password; the API says only whether it is set.
    kind: Literal["text", "url", "secret", "secret_url", "select", "number", "switch"]
    help: str = ""
    placeholder: str = ""
    required: bool = True
    default: str | int | bool | None = None
    choices: tuple[Choice, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    @property
    def secret(self) -> bool:
        return self.kind.startswith("secret")


class IntegrationType:
    key: ClassVar[str]
    label: ClassVar[str]
    description: ClassVar[str]
    fields: ClassVar[tuple[Field, ...]]
    # A link to the app's own page, for the form.
    homepage: ClassVar[str | None] = None

    def check(self, config: dict) -> dict:
        """The config with only known fields, typed and trimmed, defaults
        filled in; raises Invalid."""
        checked: dict = {}
        for spec in self.fields:
            raw = config.get(spec.key)
            if spec.kind == "switch":
                checked[spec.key] = bool(raw) if raw is not None else bool(spec.default)
                continue
            if spec.kind == "number":
                if raw in (None, ""):
                    if spec.default is None:
                        if spec.required:
                            raise Invalid(f"{spec.label} is needed.")
                        continue
                    raw = spec.default
                try:
                    value = int(raw)
                except (TypeError, ValueError) as exc:
                    raise Invalid(f"{spec.label} must be a whole number.") from exc
                if spec.minimum is not None and value < spec.minimum:
                    raise Invalid(f"{spec.label} must be at least {spec.minimum}.")
                if spec.maximum is not None and value > spec.maximum:
                    raise Invalid(f"{spec.label} must be at most {spec.maximum}.")
                checked[spec.key] = value
                continue
            value = str(raw if raw is not None else "").strip()
            if not value and spec.default is not None:
                value = str(spec.default)
            if not value:
                if spec.required:
                    raise Invalid(f"{spec.label} is needed.")
                continue
            if spec.kind in ("url", "secret_url"):
                if not _URL.match(value):
                    raise Invalid(f"{spec.label} must be a link starting with http:// or https://.")
                value = value.rstrip("/")
            if spec.kind == "select" and value not in {c.value for c in spec.choices}:
                raise Invalid(f"{spec.label}: choose one of {', '.join(c.label for c in spec.choices)}.")
            checked[spec.key] = value
        self.validate(checked)
        return checked

    def validate(self, config: dict) -> None:
        """Checks of the type's own, on a config with every required field."""

    def summary(self, config: dict) -> str:
        """Where it goes, without giving a secret away."""
        url = config.get("url") or config.get("base_url") or ""
        return re.sub(r"^https?://", "", url) or self.label

    def describe(self) -> dict:
        return {
            "type": self.key,
            "label": self.label,
            "description": self.description,
            "homepage": self.homepage,
            "fields": [dict(asdict(spec), secret=spec.secret) for spec in self.fields],
        }
