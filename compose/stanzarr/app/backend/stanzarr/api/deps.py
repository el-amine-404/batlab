"""What the routes are handed: the one Services (services.py)."""

from typing import Annotated

from fastapi import Depends, Request

from ..services import Services


def services(request: Request) -> Services:
    return request.app.state.services


Ctx = Annotated[Services, Depends(services)]
