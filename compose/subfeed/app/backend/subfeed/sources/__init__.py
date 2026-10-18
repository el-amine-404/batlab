"""Where posts come from. A source is one module (base.py says what it is),
listed here."""

import httpx

from .base import BadAnswer, Refused, Source, SourceError, Unreachable
from .reddit import Reddit


def registry(http: httpx.AsyncClient, user_agent: str) -> dict[str, Source]:
    return {source.key: source for source in (Reddit(http, user_agent),)}


__all__ = ["BadAnswer", "Refused", "Source", "SourceError", "Unreachable", "registry"]
