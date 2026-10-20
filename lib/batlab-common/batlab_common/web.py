"""The web side of accounts (accounts.py), for the apps built on FastAPI
(videodrop, songdrop, clipseerr, subfeed). Needs FastAPI and pydantic: only
their servers import it, never spotDL's environment.

One Gate per app:

    gate = Gate("songdrop", PERMISSIONS, user_default=["download"], key_default=["download"])
    gate.install(app, accounts)                    # its refusals answered, app.state.accounts
    api.include_router(gate.public)                # /auth/state, /auth/setup, /auth/login, /auth/logout
    api.include_router(gate.router, dependencies=[Depends(gate.principal)])   # /auth/me…, /users, /keys
    api.include_router(jobs, dependencies=[Depends(gate.principal)])

gate.principal says who asks: a person by the page's session cookie, or an
app by its API key (X-Api-Key, or Authorization: Bearer). gate.need(name) is
a route's dependency on a permission. The cookie is SameSite=Lax and
HttpOnly; a change asked with it (POST, PUT, PATCH, DELETE) must also carry
X-<App>-Page: 1, which another site's page can't send without the browser
asking the app first (and it allows no other origin): a page elsewhere, even
on another homelab.lan name, can't act as whoever is signed in here.
"""

import logging
import secrets
import time
import urllib.parse
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie, APIKeyHeader, APIKeyQuery
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders

from .accounts import SESSION_MAX, Accounts, Forbidden, Invalid, Principal, Refusal, TooSoon, Unauthorized

SAFE = {"GET", "HEAD", "OPTIONS"}


def address(request: Request) -> str:
    """The asker's address: the last one Caddy forwarded for, else the peer's."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()[:64]
    return request.client.host if request.client else "?"


def _accounts(request: Request) -> Accounts:
    return request.app.state.accounts


REQUEST_ID = "x-request-id"
# A caller's own request id is kept when it looks like one.
_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")


class RequestLog:
    """Each request gets an id (X-Request-ID, the caller's or a new one),
    answered back and kept in scope["state"] for the error handler. A
    change (POST, PUT, PATCH, DELETE) and every answer of 400 or more are
    logged with it, and who asked (a person's or a key's name); reads,
    which callers and pages repeat all day, are not. Never the query string:
    a feed reader's key may be in it.

        app.add_middleware(RequestLog, name="subfeed")   # logs as subfeed.http
    """

    def __init__(self, app, name: str):
        self._app = app
        self._log = logging.getLogger(f"{name}.http")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        given = dict(scope.get("headers") or []).get(REQUEST_ID.encode(), b"").decode("latin-1")
        request_id = given if 0 < len(given) <= 64 and set(given) <= _ID_CHARS else secrets.token_hex(6)
        scope.setdefault("state", {})["request_id"] = request_id
        started, status = time.monotonic(), [500]

        async def start(message):
            if message["type"] == "http.response.start":
                status[0] = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self._app(scope, receive, start)
        finally:
            method, path = scope.get("method", ""), scope.get("path", "")
            if method not in SAFE or status[0] >= 400:
                client = (scope.get("client") or ("?",))[0]
                who = scope["state"].get("principal")
                level = logging.WARNING if status[0] >= 500 else logging.INFO
                self._log.log(level, "%s %s %s %.0f ms from %s%s [%s]", method, path, status[0],
                              (time.monotonic() - started) * 1000, client,
                              f" by {who.name} ({who.kind})" if who else "", request_id)


def without_key(url: str) -> str:
    """url without its key=… (a feed reader's key), for an address a feed
    writes into itself (its own, its next page)."""
    parts = urllib.parse.urlsplit(url)
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != "key"]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(kept)))


class Problem(BaseModel):
    detail: str = Field(description="What went wrong, as a sentence a person can read.")


class Gate:
    def __init__(self, app: str, permissions: dict[str, tuple[str, str]], *, user_default: list[str],
                 key_default: list[str], key_for: str = "clipseerr, Homepage, a script"):
        """app: its name, as sentences and the cookie say it. permissions:
        the same as its Accounts'. user_default, key_default: what a new
        person, a new key, may do unless told otherwise."""
        setup_command = f"docker exec {app} admin setup-code"
        self.app = app
        self.permissions = permissions
        self.cookie = f"{app}_session"
        self.page_header = f"X-{app.capitalize()}-Page"
        self.log = logging.getLogger(f"{app}.auth")
        self._key_header = APIKeyHeader(name="X-Api-Key", auto_error=False, scheme_name="ApiKey",
                                        description="An API key, made in Settings → API keys.")
        self._cookie = APIKeyCookie(name=self.cookie, auto_error=False, scheme_name="Session",
                                    description="The page's session, once signed in.")
        self._query_key = APIKeyQuery(name="key", auto_error=False, scheme_name="ApiKeyInAddress",
                                      description="An API key in the address, for a program that can't send "
                                                  "headers (a feed reader). Only where a route says so.")
        Permission = Literal[tuple(permissions)]  # noqa: N806 (a type)

        # ---- the schemas, with this app's permissions ----------------------------------------

        class PermissionInfo(BaseModel):
            id: Permission
            label: str
            description: str

        class Me(BaseModel):
            kind: Literal["user", "key"] = Field(description="A person signed in, or an app's API key.")
            id: str
            name: str
            permissions: list[Permission] = Field(description="What it may do; an admin's lists them all.")

        class AuthState(BaseModel):
            setup: bool = Field(description="Nobody exists yet: the page asks for the setup code and the first admin.")
            me: Me | None = Field(None, description="Who this request is, when signed in.")
            permissions: list[PermissionInfo]

        class SetupIn(BaseModel):
            code: str = Field(min_length=4, max_length=40, description=f"The setup code: {app}'s log, or "
                                                                      f"`{setup_command}`.")
            username: str = Field(min_length=1, max_length=40)
            name: str = Field("", max_length=60, description="How notifications and the page name them.")
            password: str = Field(min_length=1, max_length=200)

        class LoginIn(BaseModel):
            username: str = Field(min_length=1, max_length=40)
            password: str = Field(min_length=1, max_length=200)

        class PasswordIn(BaseModel):
            current: str = Field(min_length=1, max_length=200)
            new: str = Field(min_length=1, max_length=200)

        class Session(BaseModel):
            id: str = Field(description="Its first characters: enough to tell them apart.")
            created_at: float
            last_seen_at: float
            address: str | None
            host: str | None = Field(None, description="What the address is called on the network (a device's "
                                                       "name), when it has a name.")
            agent: str | None
            current: bool = False

        class Account(BaseModel):
            me: Me
            username: str
            sessions: list[Session]

        class User(BaseModel):
            id: str
            username: str
            name: str
            permissions: list[Permission]
            enabled: bool
            created_at: float
            last_login_at: float | None
            password_changed_at: float | None

        class UserList(BaseModel):
            users: list[User]

        class UserIn(BaseModel):
            username: str = Field(min_length=1, max_length=40,
                                  description="What they sign in with: letters, digits, . _ -.")
            name: str = Field("", max_length=60, description="How the page and the notifications name them.")
            password: str = Field(min_length=1, max_length=200, description="At least 10 characters; they can "
                                                                           "change it.")
            permissions: list[Permission] = Field(default_factory=lambda: list(user_default))

        class UserPatch(BaseModel):
            name: str | None = Field(None, max_length=60)
            permissions: list[Permission] | None = None
            enabled: bool | None = Field(None, description="Off: they can't sign in, their sessions end.")

        class NewPassword(BaseModel):
            password: str = Field(min_length=1, max_length=200)

        class ApiKey(BaseModel):
            id: str
            name: str
            prefix: str = Field(description="The key's first characters, to recognise it.")
            permissions: list[Permission]
            created_at: float
            last_used_at: float | None
            last_address: str | None
            last_host: str | None = Field(None, description="What last_address is called on the network: the app's "
                                                            "container (homepage, clipseerr), a device; None "
                                                            "without a name.")

        class ApiKeyList(BaseModel):
            keys: list[ApiKey]

        class ApiKeyIn(BaseModel):
            name: str = Field(min_length=1, max_length=40, description=f"The app it is for: {key_for}…")
            permissions: list[Permission] = Field(default_factory=lambda: list(key_default))

        class ApiKeyMade(ApiKey):
            key: str = Field(description="The key, shown this once: the app sends it as X-Api-Key.")

        class ApiKeyPatch(BaseModel):
            permissions: list[Permission]

        self.Me = Me

        # ---- who asks ------------------------------------------------------------------------

        def who(request: Request, key: str | None, cookie: str | None) -> Principal:
            accounts = _accounts(request)
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
                if request.method not in SAFE and request.headers.get(self.page_header) != "1":
                    raise Forbidden(f"A change asked with the page's cookie carries {self.page_header}: 1.")
            elif accounts.needs_setup():
                raise Unauthorized(f"{app} isn't set up yet: open its page to make the first account.")
            else:
                raise Unauthorized("Sign in, or give an API key (X-Api-Key).")
            request.state.principal = found
            return found

        def principal(request: Request, key: Annotated[str | None, Depends(self._key_header)],
                      cookie: Annotated[str | None, Depends(self._cookie)]) -> Principal:
            return who(request, key, cookie)

        def principal_or_address_key(request: Request, key: Annotated[str | None, Depends(self._key_header)],
                                     cookie: Annotated[str | None, Depends(self._cookie)],
                                     in_address: Annotated[str | None, Depends(self._query_key)]) -> Principal:
            return who(request, key or in_address, cookie)

        self.principal = principal
        # For what a program without headers reads (a feed reader): the key may also be ?key=.
        self.principal_or_address_key = principal_or_address_key
        self.Who = Annotated[Principal, Depends(principal)]
        Who = self.Who  # noqa: N806

        # ---- signing in: public --------------------------------------------------------------

        log = self.log
        problems = {401: {"model": Problem}, 409: {"model": Problem}, 422: {"model": Problem},
                    429: {"model": Problem}}
        public = APIRouter(prefix="/auth", tags=["Account"])
        router = APIRouter(tags=["Account"])

        def permission_list() -> list[dict]:
            return [{"id": name, "label": label, "description": text} for name, (label, text) in permissions.items()]

        def signed_in(response: Response, request: Request, token: str) -> None:
            secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            response.set_cookie(self.cookie, token, max_age=SESSION_MAX, httponly=True, samesite="lax", secure=secure,
                                path="/")

        @public.get("/state", summary=f"Whether {app} is set up, and who this is", response_model=AuthState)
        def auth_state(request: Request):
            me = None
            try:
                me = who(request, request.headers.get("x-api-key"), request.cookies.get(self.cookie)).public()
            except PermissionError:
                pass
            return {"setup": _accounts(request).needs_setup(), "me": me, "permissions": permission_list()}

        @public.post("/setup", summary="Make the first account, an admin, with the setup code",
                     response_model=Me, responses=problems,
                     description=f"Only while nobody exists. The code is in {app}'s log, and given by "
                                 f"`{setup_command}`. Signs the new admin in.")
        async def setup(body: SetupIn, request: Request, response: Response):
            accounts = _accounts(request)
            where = address(request)
            wait = accounts.throttle.wait(f"setup:{where}")
            if wait:
                raise TooSoon(wait)
            try:
                user = await run_in_threadpool(accounts.setup, body.code, body.username, body.name, body.password)
            except Invalid as exc:
                if "setup code" in str(exc):
                    accounts.throttle.failed(f"setup:{where}")
                    log.warning("a wrong setup code from %s", where)
                raise
            log.warning("set up: %s (%s) is the first admin, from %s", user["username"], user["name"], where)
            token, _ = await run_in_threadpool(accounts.sign_in, body.username, body.password, where,
                                               request.headers.get("user-agent", ""))
            signed_in(response, request, token)
            return {"kind": "user", "id": user["id"], "name": user["name"], "permissions": user["permissions"]}

        @public.post("/login", summary="Sign in: the session cookie", response_model=Me, responses=problems)
        async def login(body: LoginIn, request: Request, response: Response):
            """Sets the HttpOnly session cookie the page uses (30 days unused at
            most). Apps use an API key instead. After 5 wrong tries in 15 minutes,
            from one address or for one name, each try waits longer (429)."""
            accounts = _accounts(request)
            where, name = address(request), body.username.strip().casefold()
            throttle = accounts.throttle
            wait = throttle.wait(f"ip:{where}", f"name:{name}")
            if wait:
                log.warning("sign-in as %r from %s refused: too many wrong tries, %s s to wait", body.username, where,
                            wait)
                raise TooSoon(wait)
            try:
                token, user = await run_in_threadpool(accounts.sign_in, body.username, body.password, where,
                                                      request.headers.get("user-agent", ""))
            except Invalid:
                throttle.failed(f"ip:{where}", f"name:{name}")
                log.warning("a failed sign-in as %r from %s", body.username, where)
                raise
            throttle.succeeded(f"name:{name}")
            log.info("%s signed in from %s", user["username"], where)
            signed_in(response, request, token)
            return {"kind": "user", "id": user["id"], "name": user["name"], "permissions": user["permissions"]}

        @public.post("/logout", status_code=204, summary="Sign out: this session ends")
        def logout(request: Request, response: Response):
            token = request.cookies.get(self.cookie)
            if token:
                accounts = _accounts(request)
                found = accounts.session(token)
                if found:
                    accounts.sign_out(found.session)
                    log.info("%s signed out", found.name)
            response.delete_cookie(self.cookie, path="/")
            response.status_code = 204

        # ---- one's own account: signed in ------------------------------------------------------

        def account(accounts: Accounts, me: Principal) -> dict:
            if me.kind != "user":
                return {"me": me.public(), "username": me.name, "sessions": []}
            user = accounts.user(me.id)
            sessions = accounts.sessions(me.id)
            for session in sessions:
                session["current"] = bool(me.session and me.session.startswith(session["id"]))
            return {"me": me.public(), "username": user["username"], "sessions": sessions}

        @router.get("/auth/me", summary="Who this is, and their sessions", response_model=Account)
        def get_me(request: Request, me: Who):
            return account(_accounts(request), me)

        @router.put("/auth/password", status_code=204, summary="Change your password; your other sessions end",
                    responses=problems)
        async def change_password(body: PasswordIn, request: Request, me: Who):
            if me.kind != "user":
                raise Invalid("An API key has no password.")
            await run_in_threadpool(_accounts(request).change_own_password, me, body.current, body.new)
            log.info("%s changed their password", me.name)

        @router.delete("/auth/sessions", summary="Sign out everywhere else", response_model=Account)
        def sign_out_elsewhere(request: Request, me: Who):
            accounts = _accounts(request)
            if me.kind == "user":
                ended = accounts.sign_out_everywhere(me.id, keep=me.session)
                log.info("%s signed out of %s other sessions", me.name, ended)
            return account(accounts, me)

        # ---- people and keys: the admins' ------------------------------------------------------

        admin = self.need("admin")
        people = APIRouter(tags=["People and keys"], dependencies=[admin])
        admin_problems = {403: {"model": Problem}, 404: {"model": Problem}, 409: {"model": Problem},
                          422: {"model": Problem}}

        @people.get("/users", summary="The people who can sign in", response_model=UserList)
        def list_users(request: Request):
            return {"users": _accounts(request).users()}

        @people.post("/users", status_code=201, summary="Add someone", response_model=User, responses=admin_problems)
        async def add_user(body: UserIn, request: Request, me: Who):
            user = await run_in_threadpool(_accounts(request).create_user, body.username, body.name, body.password,
                                           body.permissions)
            log.info("%s added %s (%s), who may: %s", me.name, user["username"], user["name"],
                     ", ".join(user["permissions"]) or "nothing")
            return user

        @people.get("/users/{user_id}", summary="One person", response_model=User, responses=admin_problems)
        def get_user(user_id: str, request: Request):
            return _accounts(request).user(user_id)

        @people.patch("/users/{user_id}", summary="Change someone's name, permissions, or turn them off",
                      response_model=User, responses=admin_problems)
        def change_user(user_id: str, body: UserPatch, request: Request, me: Who):
            user = _accounts(request).update_user(user_id, name=body.name, permissions=body.permissions,
                                                  enabled=body.enabled, acting=me)
            log.info("%s changed %s: %s", me.name, user["username"], body.model_dump(exclude_none=True))
            return user

        @people.put("/users/{user_id}/password", status_code=204,
                    summary="Give someone a new password; their sessions end", responses=admin_problems)
        async def reset_password(user_id: str, body: NewPassword, request: Request, me: Who):
            accounts = _accounts(request)
            await run_in_threadpool(accounts.set_password, user_id, body.password,
                                    keep_session=me.session if me.id == user_id else None)
            log.info("%s gave %s a new password", me.name, accounts.user(user_id)["username"])

        @people.delete("/users/{user_id}", status_code=204, summary="Remove someone; what they asked for stays",
                       responses=admin_problems)
        def remove_user(user_id: str, request: Request, me: Who):
            accounts = _accounts(request)
            username = accounts.user(user_id)["username"]
            accounts.delete_user(user_id, acting=me)
            log.info("%s removed %s", me.name, username)
            return Response(status_code=204)

        @people.get("/keys", summary="The apps' API keys", response_model=ApiKeyList)
        def list_keys(request: Request):
            return {"keys": _accounts(request).keys()}

        @people.post("/keys", status_code=201, summary="Make an API key; it is shown this once",
                     response_model=ApiKeyMade, responses=admin_problems)
        def add_key(body: ApiKeyIn, request: Request, me: Who):
            token, key = _accounts(request).create_key(body.name, body.permissions, me.id)
            log.info("%s made the API key %s (%s…), which may: %s", me.name, key["name"], key["prefix"],
                     ", ".join(key["permissions"]) or "read only")
            return {**key, "key": token}

        @people.patch("/keys/{key_id}", summary="Change what a key may do", response_model=ApiKey,
                      responses=admin_problems)
        def change_key(key_id: str, body: ApiKeyPatch, request: Request, me: Who):
            key = _accounts(request).update_key(key_id, body.permissions)
            log.info("%s changed the API key %s: %s", me.name, key["name"], ", ".join(key["permissions"]) or "read only")
            return key

        @people.delete("/keys/{key_id}", status_code=204,
                       summary="Remove an API key: the app using it is refused from now on", responses=admin_problems)
        def remove_key(key_id: str, request: Request, me: Who):
            accounts = _accounts(request)
            name = next((k["name"] for k in accounts.keys() if k["id"] == key_id), key_id)
            accounts.delete_key(key_id)
            log.info("%s removed the API key %s", me.name, name)
            return Response(status_code=204)

        router.include_router(people)
        # Anyone may: signing in, and whether there is anyone to sign in as.
        self.public = public
        # Signed in: one's own account; the admins: people and keys.
        self.router = router

    def need(self, permission: str):
        """A route's dependency: the asker may do permission (an admin, anything)."""
        label = "manage people and API keys" if permission == "admin" else self.permissions[permission][0].lower()

        def check(who: Annotated[Principal, Depends(self.principal)]) -> Principal:
            if not who.can(permission):
                raise Forbidden(f"{who.name} may not {label}: an admin gives that permission.")
            return who

        return Depends(check)

    def install(self, app: FastAPI, accounts: Accounts) -> None:
        """Its refusals answered as {"detail": "<sentence>"} with their status
        (401 with WWW-Authenticate, 429 with Retry-After); the accounts the
        routes use."""
        app.state.accounts = accounts

        async def refused(_request: Request, exc: Refusal):
            response = JSONResponse({"detail": str(exc)}, status_code=exc.status)
            if isinstance(exc, Unauthorized):
                response.headers["WWW-Authenticate"] = 'ApiKey header="X-Api-Key"'
            if isinstance(exc, TooSoon):
                response.headers["Retry-After"] = str(exc.seconds)
            return response

        app.add_exception_handler(Refusal, refused)
