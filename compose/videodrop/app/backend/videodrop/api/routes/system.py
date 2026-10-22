import logging
from importlib import metadata
from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from ... import __version__, cookies, logs
from ...domain import Invalid
from ...settings import Settings, SettingsPatch
from .. import schemas
from ..auth import Who, need
from ..deps import MAX_NUMBER, Ctx

public = APIRouter()
router = APIRouter()
log = logging.getLogger("videodrop.settings")


@public.get("/health", tags=["System"], summary="Whether videodrop can do its job", response_model=schemas.Health)
def health(ctx: Ctx):
    """200 when it answers and its database is readable."""
    ctx.db.one("SELECT 1")
    return {"ok": True, "version": __version__}


@router.get("/status", tags=["System"], summary="Version, downloads, the disk and the cookies",
            response_model=schemas.SystemStatus)
def status(ctx: Ctx, who: Who):
    settings = ctx.settings()
    return {
        "version": __version__,
        "yt_dlp": metadata.version("yt-dlp"),
        "deno": bool(ctx.config.deno),
        "started_at": ctx.started_at,
        "jobs": ctx.jobs.counts(None if who.can("everyone") else who.owner),
        "running": ctx.scheduler.running,
        "max_parallel": settings.max_parallel,
        "disk": {"free_bytes": ctx.scheduler.free_bytes, "low": ctx.scheduler.disk_low,
                 "min_free_bytes": int(settings.min_free_gb * 1024 ** 3)},
        "cookies": cookies.status(ctx.config.state_dir),
        "streams": ctx.broker.subscribers,
        "database_bytes": ctx.db.size(),
        "clients": ctx.jobs.clients(),
        "egress": {"connections": ctx.egress.connections, "refused": ctx.egress.refused},
    }


@router.get("/log", tags=["System"], summary="videodrop's own log, its newest lines", response_model=schemas.ServerLog,
            dependencies=[need("settings")])
def server_log(after: int = Query(0, ge=0, le=MAX_NUMBER, description="Only the lines after this one (its seq): to follow it."),
               level: Literal["debug", "info", "warning", "error"] = Query("info", description="This one and worse."),
               limit: int = Query(500, ge=1, le=2000)):
    """Kept in memory since the start, the newest 2000; the container's log
    has all of it. Without secrets. A download's own log is
    /jobs/{id}/log."""
    return {"lines": logs.RECENT.lines(after, level, limit)}


@router.get("/issues", tags=["System"], summary="What needs a person's eye", response_model=schemas.IssueList,
            dependencies=[need("settings")])
def list_issues(ctx: Ctx):
    """Errors first. Each says what is wrong, why it matters and, when it is
    fixed on the page, where. An issue goes once fixed."""
    return ctx.issues.public()


@router.get("/settings", tags=["Settings"], summary="The settings", response_model=Settings)
def get_settings(ctx: Ctx):
    return ctx.settings()


@router.patch("/settings", tags=["Settings"], summary="Change some settings; the others keep their value",
              response_model=Settings, responses={422: {"model": schemas.Problem}}, dependencies=[need("settings")])
def patch_settings(body: SettingsPatch, ctx: Ctx, who: Who):
    """Kept in videodrop's database; they win over the environment. 422 with
    the reason when a value isn't valid, and nothing is changed then. More
    downloads at once start within seconds; fewer let the running ones end."""
    updated = ctx.settings.update(body, ctx.destinations.exists)
    log.info("%s changed the settings: %s", who.name, ", ".join(body.model_dump(exclude_none=True)) or "nothing")
    ctx.scheduler.wake()
    return updated


@router.get("/cookies", tags=["Settings"], summary="Whether YouTube cookies are saved, and until when",
            response_model=schemas.CookieStatus, response_model_exclude_none=True, dependencies=[need("settings")])
def cookie_status(ctx: Ctx):
    return cookies.status(ctx.config.state_dir)


@router.put("/cookies", tags=["Settings"], summary="Save a cookies.txt (the request body is the file)",
            response_model=schemas.CookieStatus, response_model_exclude_none=True,
            responses={422: {"model": schemas.Problem}}, dependencies=[need("settings")])
async def put_cookies(request: Request, ctx: Ctx, who: Who):
    """The body is the cookies.txt itself (Netscape format, at most 1 MB, with
    youtube.com cookies): `curl -X PUT --data-binary @cookies.txt …`. Every
    download after uses them; they are never answered back."""
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > cookies.MAX_BYTES:
            raise Invalid("That file is too big for a cookies.txt.")
    saved = cookies.save(ctx.config.state_dir, bytes(data))
    log.info("%s saved new YouTube cookies", who.name)
    return saved


@router.delete("/cookies", tags=["Settings"], status_code=204, summary="Remove the saved cookies",
               dependencies=[need("settings")])
def delete_cookies(ctx: Ctx):
    cookies.delete(ctx.config.state_dir)
    return Response(status_code=204)
