import logging

from fastapi import APIRouter, Query

from ... import __version__, language, library
from ...settings import Settings, SettingsPatch
from .. import schemas
from ..auth import Who, need
from ..deps import Ctx

# Anyone may: whether Stanzarr is up.
public = APIRouter()
router = APIRouter()
log = logging.getLogger("stanzarr")


@public.get("/health", tags=["System"], summary="Whether Stanzarr can do its job", response_model=schemas.Health)
def health(ctx: Ctx):
    """200 when it answers and its database is readable."""
    ctx.db.one("SELECT 1")
    return {"ok": True, "version": __version__}


def _coverage(ctx) -> dict:
    found = {r["status"]: r["n"] for r in ctx.db.all(
        f"SELECT {library.STATUS_SQL} AS status, COUNT(*) AS n FROM songs s GROUP BY status")}
    return {name: found.get(name, 0) for name in library.STATUSES}


@router.get("/system/status", tags=["System"], summary="The library's coverage, the workers, the sources",
            response_model=schemas.SystemStatus)
def status(ctx: Ctx):
    """What Homepage's card reads (with a key that only reads): every count,
    0 included."""
    coverage = _coverage(ctx)
    check = ctx.db.one("SELECT COUNT(*) AS songs, COALESCE(SUM(unsure - checked), 0) AS lines FROM lyrics"
                       " WHERE source='ai' AND ai_state='unchecked' AND unsure > checked")
    online = ctx.workers_online()
    background = ctx.background
    return {"version": __version__, "started_at": ctx.started_at, "songs": sum(coverage.values()),
            "by_status": coverage, "to_check": {"songs": check["songs"], "lines": check["lines"]},
            "background": background.busy if background else "", "last_scan": background.last_scan if background else {},
            "workers_online": len(online), "gpu_online": any(w["gpu"] for w in online),
            "sources": [item["name"] for item, _kind in ctx.sources.asked()], "read_only": ctx.config.read_only,
            "database_bytes": ctx.db.size()}


@router.get("/system/stats", tags=["System"], summary="The library by language and by kind of file")
def stats(ctx: Ctx):
    by_language = ctx.db.all(
        "SELECT language, COUNT(DISTINCT song_id) AS n FROM lyrics GROUP BY language ORDER BY n DESC")
    by_format = ctx.db.all("SELECT format, synced, COUNT(*) AS n FROM lyrics GROUP BY format, synced ORDER BY n DESC")
    return {"by_language": [{"code": r["language"], "name": language.name(r["language"]), "songs": r["n"]}
                            for r in by_language],
            "by_format": [{"format": r["format"], "synced": bool(r["synced"]), "files": r["n"]} for r in by_format]}


@router.get("/events", tags=["System"], summary="What Stanzarr did, newest first")
def events(ctx: Ctx, limit: int = Query(100, ge=1, le=500)):
    return ctx.db.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))


@router.post("/library/scan", tags=["Library"], summary="Read the library now, then ask the sources",
             response_model=schemas.Ok, dependencies=[need("settings")])
def scan(ctx: Ctx, who: Who):
    if ctx.background:
        ctx.background.wake.set()
    log.info("%s asked for a scan", who.name)
    return {"ok": True}


@router.get("/settings", tags=["Settings"], summary="The settings", response_model=Settings)
def get_settings(ctx: Ctx):
    return ctx.settings()


@router.patch("/settings", tags=["Settings"], summary="Change some settings; the others keep their value",
              response_model=Settings, responses={403: {"model": schemas.Problem}, 422: {"model": schemas.Problem}},
              dependencies=[need("settings")])
def patch_settings(body: SettingsPatch, ctx: Ctx, who: Who):
    """Kept in Stanzarr's database; they win over the environment."""
    saved = ctx.settings.update(body)
    log.info("%s changed the settings: %s", who.name, body.model_dump(exclude_unset=True))
    if ctx.background:
        ctx.background.wake.set()
    return saved


@router.get("/system/library", tags=["System"], summary="The library's folders, and whether it writes")
def library_info(ctx: Ctx):
    return {"folders": ctx.config.library, "read_only": ctx.config.read_only}
