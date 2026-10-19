"""Who asks, for every route but the public ones (health, signing in): a
person by the page's session cookie, or an app by its API key (X-Api-Key,
or Authorization: Bearer). What they may do is checked by need().

The cookie is SameSite=Lax and HttpOnly. A change asked with it (POST, PUT,
PATCH, DELETE) must also carry X-Videodrop-Page: 1, which another site's page
can't send without the browser asking videodrop first (and it allows no other
origin): a page elsewhere, even on another homelab.lan name, can't act as
whoever is signed in here.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie, APIKeyHeader

from ..accounts import PERMISSIONS, SESSION_COOKIE, Principal
from ..domain import Forbidden, Unauthorized

PAGE_HEADER = "X-Videodrop-Page"
SAFE = {"GET", "HEAD", "OPTIONS"}

_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False, scheme_name="ApiKey",
                           description="An API key, made in Settings → API keys.")
_cookie = APIKeyCookie(name=SESSION_COOKIE, auto_error=False, scheme_name="Session",
                       description="The page's session, once signed in.")


def address(request: Request) -> str:
    """The asker's address: the last one Caddy forwarded for, else the peer's."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()[:64]
    return request.client.host if request.client else "?"


def principal(request: Request, key: Annotated[str | None, Depends(_key_header)],
              cookie: Annotated[str | None, Depends(_cookie)]) -> Principal:
    accounts = request.app.state.services.accounts
    bearer = request.headers.get("authorization", "")
    if not key and bearer[:7].lower() == "bearer ":
        key = bearer[7:].strip()
    if key:
        found = accounts.key(key, address(request))
        if not found:
            raise Unauthorized("That API key isn't known: it was removed, or mistyped.")
    elif cookie:
        found = accounts.session(cookie)
        if not found:
            raise Unauthorized("Your session ended: sign in again.")
        if request.method not in SAFE and request.headers.get(PAGE_HEADER) != "1":
            raise Forbidden(f"A change asked with the page's cookie carries {PAGE_HEADER}: 1.")
    elif accounts.needs_setup():
        raise Unauthorized("videodrop isn't set up yet: open its page to make the first account.")
    else:
        raise Unauthorized("Sign in, or give an API key (X-Api-Key).")
    request.state.principal = found
    return found


Who = Annotated[Principal, Depends(principal)]


def need(permission: str):
    """A route's dependency: the asker may do permission (an admin, anything)."""
    label = "manage people and API keys" if permission == "admin" else PERMISSIONS[permission][0].lower()

    def check(who: Who) -> Principal:
        if not who.can(permission):
            raise Forbidden(f"{who.name} may not {label}: an admin gives that permission.")
        return who

    return Depends(check)
