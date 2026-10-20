import logging

from fastapi import APIRouter

from ... import __version__
from ...settings import Settings, SettingsPatch
from .. import schemas
from ..auth import Who, need
from ..deps import Ctx

# Anyone may: whether subfeed is up.
public = APIRouter()
router = APIRouter()
log = logging.getLogger("subfeed")


@public.get("/health", tags=["System"], summary="Whether subfeed can do its job", response_model=schemas.Health)
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
              response_model=Settings, responses={403: {"model": schemas.Problem}, 422: {"model": schemas.Problem}},
              dependencies=[need("settings")])
def patch_settings(body: SettingsPatch, ctx: Ctx, who: Who):
    """Kept in subfeed's database; they win over the environment. 422 with
    the reason when a value isn't valid, and nothing is changed then."""
    saved = ctx.settings.update(body)
    log.info("%s changed the settings: %s", who.name, body.model_dump(exclude_unset=True))
    return saved
