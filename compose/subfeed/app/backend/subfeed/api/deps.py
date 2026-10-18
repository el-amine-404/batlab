"""What the routes are handed: the one Services (services.py), and the
conditional answers that let a reader skip what it has already (ETag)."""

import hashlib
from typing import Annotated

from fastapi import Depends, Request, Response

from .. import __version__
from ..services import Services


def services(request: Request) -> Services:
    return request.app.state.services


Ctx = Annotated[Services, Depends(services)]


def etag(*parts) -> str:
    """A weak validator of an answer made from parts (a feed's version, the
    query string…): the same parts, the same answer."""
    digest = hashlib.blake2b("|".join(map(str, (__version__, *parts))).encode(), digest_size=12).hexdigest()
    return f'W/"{digest}"'


def unchanged(request: Request, tag: str) -> Response | None:
    """304 Not Modified when the reader has this answer already, else None.
    Either way the answer says to check again each time (no-cache)."""
    asked = request.headers.get("if-none-match", "")
    if asked.strip() == "*" or tag in (t.strip() for t in asked.split(",")):
        return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-cache"})
    return None


def validated(response: Response, tag: str) -> None:
    response.headers["ETag"] = tag
    response.headers["Cache-Control"] = "no-cache"
