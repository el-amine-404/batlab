from importlib import metadata

from fastapi import APIRouter, Request, Response

from ... import __version__, cookies
from ...domain import Invalid
from ...settings import Settings, SettingsPatch
from .. import schemas
from ..deps import Ctx

router = APIRouter()


@router.get("/health", tags=["System"], summary="Whether videodrop can do its job", response_model=schemas.Health)
def health(ctx: Ctx):
    """200 when it answers and its database is readable."""
    ctx.db.one("SELECT 1")
    return {"ok": True, "version": __version__}


@router.get("/status", tags=["System"], summary="Version, downloads, the disk and the cookies",
            response_model=schemas.SystemStatus)
def status(ctx: Ctx):
    settings = ctx.settings()
    return {
        "version": __version__,
        "yt_dlp": metadata.version("yt-dlp"),
        "deno": bool(ctx.config.deno),
        "started_at": ctx.started_at,
        "jobs": ctx.jobs.counts(),
        "running": ctx.scheduler.running,
        "max_parallel": settings.max_parallel,
        "disk": {"free_bytes": ctx.scheduler.free_bytes, "low": ctx.scheduler.disk_low,
                 "min_free_bytes": int(settings.min_free_gb * 1024 ** 3)},
        "cookies": cookies.status(ctx.config.state_dir),
        "streams": ctx.broker.subscribers,
        "database_bytes": ctx.db.size(),
        "clients": ctx.jobs.clients(),
    }


@router.get("/settings", tags=["Settings"], summary="The settings", response_model=Settings)
def get_settings(ctx: Ctx):
    return ctx.settings()


@router.patch("/settings", tags=["Settings"], summary="Change some settings; the others keep their value",
              response_model=Settings, responses={422: {"model": schemas.Problem}})
def patch_settings(body: SettingsPatch, ctx: Ctx):
    """Kept in videodrop's database; they win over the environment. 422 with
    the reason when a value isn't valid, and nothing is changed then. More
    downloads at once start within seconds; fewer let the running ones end."""
    updated = ctx.settings.update(body, ctx.destinations.exists)
    ctx.scheduler.wake()
    return updated


@router.get("/cookies", tags=["Settings"], summary="Whether YouTube cookies are saved, and until when",
            response_model=schemas.CookieStatus, response_model_exclude_none=True)
def cookie_status(ctx: Ctx):
    return cookies.status(ctx.config.state_dir)


@router.put("/cookies", tags=["Settings"], summary="Save a cookies.txt (the request body is the file)",
            response_model=schemas.CookieStatus, response_model_exclude_none=True,
            responses={422: {"model": schemas.Problem}})
async def put_cookies(request: Request, ctx: Ctx):
    """The body is the cookies.txt itself (Netscape format, at most 1 MB, with
    youtube.com cookies): `curl -X PUT --data-binary @cookies.txt …`. Every
    download after uses them; they are never answered back."""
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > cookies.MAX_BYTES:
            raise Invalid("That file is too big for a cookies.txt.")
    return cookies.save(ctx.config.state_dir, bytes(data))


@router.delete("/cookies", tags=["Settings"], status_code=204, summary="Remove the saved cookies")
def delete_cookies(ctx: Ctx):
    cookies.delete(ctx.config.state_dir)
    return Response(status_code=204)
