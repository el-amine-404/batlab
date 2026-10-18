"""What the routes are handed: the one Services (services.py), and the
address the caller reaches videodrop at (for the links in answers)."""

from typing import Annotated

from fastapi import Depends, Request

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
