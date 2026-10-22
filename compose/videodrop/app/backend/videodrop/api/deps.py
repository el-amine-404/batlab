"""What the routes are handed: the one Services (services.py), and the
address the caller reaches videodrop at (for the links in answers)."""

from typing import Annotated

from fastapi import Depends, Path, Request

from ..services import Services


def services(request: Request) -> Services:
    return request.app.state.services


def base_url(request: Request) -> str:
    """http://videodrop:8840 for an app on the home server's network,
    http://videodrop.homelab.lan through Caddy: links that work for whoever
    asked."""
    return str(request.base_url).rstrip("/")


Ctx = Annotated[Services, Depends(services)]
Base = Annotated[str, Depends(base_url)]

# A number in an address or a question: SQLite's integers end at 2^63, and a
# larger one asked for was a 500, not a refusal.
MAX_NUMBER = 2**62
# A job's file, or a file's frame or subtitle track: by its place, from 0.
FileIndex = Annotated[int, Path(ge=0, le=100_000)]
