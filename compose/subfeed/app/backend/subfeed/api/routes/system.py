from fastapi import APIRouter

from ... import __version__
from ...settings import Settings, SettingsPatch
from .. import schemas
from ..deps import Ctx

router = APIRouter()


@router.get("/health", tags=["System"], summary="Whether subfeed can do its job", response_model=schemas.Health)
def health(ctx: Ctx):
    """200 when it answers and its database is readable."""
    ctx.db.one("SELECT 1")
    return {"ok": True, "version": __version__}


@router.get("/status", tags=["System"], summary="Version, feeds, posts and the database",
            response_model=schemas.SystemStatus)
def status(ctx: Ctx):
    rows = ctx.feeds.rows()
    return {
        "version": __version__,
        "started_at": ctx.started_at,
        "feeds": {"total": len(rows), "enabled": sum(r["enabled"] for r in rows),
                  "failing": sum(1 for r in rows if r["failures"])},
        "posts": ctx.posts.total(),
        "database_bytes": ctx.db.size(),
        "last_round": ctx.poller.last_round,
        "sources": list(ctx.sources),
    }


@router.get("/settings", tags=["Settings"], summary="The settings", response_model=Settings)
def get_settings(ctx: Ctx):
    return ctx.settings()


@router.patch("/settings", tags=["Settings"], summary="Change some settings; the others keep their value",
              response_model=Settings, responses={422: {"model": schemas.Problem}})
def patch_settings(body: SettingsPatch, ctx: Ctx):
    """Kept in subfeed's database; they win over the environment. 422 with
    the reason when a value isn't valid, and nothing is changed then."""
    return ctx.settings.update(body)
