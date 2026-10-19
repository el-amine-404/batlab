import logging

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool

from ...accounts import PERMISSIONS, SESSION_COOKIE, SESSION_MAX, Principal
from ...domain import Invalid
from .. import schemas
from ..auth import Who, address, principal
from ..deps import Ctx

log = logging.getLogger("videodrop.auth")

# Public: signing in, and whether there is anyone to sign in as.
public = APIRouter(prefix="/auth", tags=["Account"])
router = APIRouter(prefix="/auth", tags=["Account"])
_PROBLEMS = {401: {"model": schemas.Problem}, 409: {"model": schemas.Problem}, 422: {"model": schemas.Problem},
             429: {"model": schemas.Problem}}


class TooSoon(Invalid):
    """Too many failed tries: wait (429)."""

    def __init__(self, seconds: int):
        super().__init__(f"Too many wrong tries: try again in {seconds} s.")
        self.seconds = seconds


def _permissions() -> list[dict]:
    return [{"id": name, "label": label, "description": text} for name, (label, text) in PERMISSIONS.items()]


def _signed_in(response: Response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX, httponly=True, samesite="lax", secure=secure,
                        path="/")


@public.get("/state", summary="Whether videodrop is set up, and who this is", response_model=schemas.AuthState)
def auth_state(request: Request, ctx: Ctx):
    me = None
    try:
        me = principal(request, request.headers.get("x-api-key"), request.cookies.get(SESSION_COOKIE)).public()
    except PermissionError:
        pass
    return {"setup": ctx.accounts.needs_setup(), "me": me, "permissions": _permissions()}


@public.post("/setup", summary="Make the first account, an admin, with the setup code",
             response_model=schemas.Me, responses=_PROBLEMS)
async def setup(body: schemas.SetupIn, request: Request, response: Response, ctx: Ctx):
    """Only while nobody exists. The code is in videodrop's log, and given by
    `docker exec videodrop python -m videodrop.admin setup-code`. Signs the
    new admin in."""
    where = address(request)
    wait = ctx.accounts.throttle.wait(f"setup:{where}")
    if wait:
        raise TooSoon(wait)
    try:
        user = await run_in_threadpool(ctx.accounts.setup, body.code, body.username, body.name, body.password)
    except Invalid as exc:
        if "setup code" in str(exc):
            ctx.accounts.throttle.failed(f"setup:{where}")
            log.warning("a wrong setup code from %s", where)
        raise
    log.warning("set up: %s (%s) is the first admin, from %s", user["username"], user["name"], where)
    token, _ = await run_in_threadpool(ctx.accounts.sign_in, body.username, body.password, where,
                                       request.headers.get("user-agent", ""))
    _signed_in(response, request, token)
    return {"kind": "user", "id": user["id"], "name": user["name"], "permissions": user["permissions"]}


@public.post("/login", summary="Sign in: the session cookie", response_model=schemas.Me, responses=_PROBLEMS)
async def login(body: schemas.LoginIn, request: Request, response: Response, ctx: Ctx):
    """Sets the HttpOnly session cookie the page uses (30 days unused at
    most). Apps use an API key instead. After 5 wrong tries in 15 minutes,
    from one address or for one name, each try waits longer (429)."""
    where, name = address(request), body.username.strip().casefold()
    throttle = ctx.accounts.throttle
    wait = throttle.wait(f"ip:{where}", f"name:{name}")
    if wait:
        log.warning("sign-in as %r from %s refused: too many wrong tries, %s s to wait", body.username, where, wait)
        raise TooSoon(wait)
    try:
        token, user = await run_in_threadpool(ctx.accounts.sign_in, body.username, body.password, where,
                                              request.headers.get("user-agent", ""))
    except Invalid:
        throttle.failed(f"ip:{where}", f"name:{name}")
        log.warning("a failed sign-in as %r from %s", body.username, where)
        raise
    throttle.succeeded(f"name:{name}")
    log.info("%s signed in from %s", user["username"], where)
    _signed_in(response, request, token)
    return {"kind": "user", "id": user["id"], "name": user["name"], "permissions": user["permissions"]}


@public.post("/logout", status_code=204, summary="Sign out: this session ends")
def logout(request: Request, response: Response, ctx: Ctx):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        found = ctx.accounts.session(token)
        if found:
            ctx.accounts.sign_out(found.session)
            log.info("%s signed out", found.name)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204


def _account(ctx, who: Principal) -> dict:
    if who.kind != "user":
        return {"me": who.public(), "username": who.name, "sessions": []}
    user = ctx.accounts.user(who.id)
    sessions = ctx.accounts.sessions(who.id)
    for session in sessions:
        session["current"] = bool(who.session and who.session.startswith(session["id"]))
    return {"me": who.public(), "username": user["username"], "sessions": sessions}


@router.get("/me", summary="Who this is, and their sessions", response_model=schemas.Account)
def me(who: Who, ctx: Ctx):
    return _account(ctx, who)


@router.put("/password", status_code=204, summary="Change your password; your other sessions end",
            responses=_PROBLEMS)
async def change_password(body: schemas.PasswordIn, who: Who, ctx: Ctx):
    if who.kind != "user":
        raise Invalid("An API key has no password.")
    await run_in_threadpool(ctx.accounts.change_own_password, who, body.current, body.new)
    log.info("%s changed their password", who.name)


@router.delete("/sessions", summary="Sign out everywhere else", response_model=schemas.Account)
def sign_out_elsewhere(who: Who, ctx: Ctx):
    if who.kind == "user":
        ended = ctx.accounts.sign_out_everywhere(who.id, keep=who.session)
        log.info("%s signed out of %s other sessions", who.name, ended)
    return _account(ctx, who)
