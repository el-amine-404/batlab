"""Errors as the API answers them: {"detail": "<a sentence a person can
read>"} (docs/services.md, "The API contract"). Validators raise ValueError
with that sentence; pydantic's other errors are worded here."""

from collections.abc import Iterable

_BODY = ("body", "query", "path")


def explain(errors: Iterable[dict]) -> str:
    """The first of pydantic's errors as one sentence."""
    for error in errors:
        message = str(error.get("msg", ""))
        if message.startswith("Value error, "):
            return message.removeprefix("Value error, ")
        where = [str(part) for part in error.get("loc", ()) if part not in _BODY]
        name = " ".join(where).replace("_", " ").capitalize()
        if not name:
            break
        if error.get("type") == "extra_forbidden":
            return f"{name} isn't something subfeed knows."
        return f"{name}: {message[:1].lower()}{message[1:]}."
    return "That request wasn't understood."
