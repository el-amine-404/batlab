import asyncio
from importlib.metadata import version

from fastapi import APIRouter

from .. import schemas
from ..context import cache, catalog, engine, requests, settings, started_at
from ..settings import REGIONS, STOREFRONTS, SettingsPatch

router = APIRouter(tags=["System"])
VERSION = "1"


def _settings_out() -> dict:
    return {**settings(), "regions": [{"value": k, "label": v} for k, v in sorted(REGIONS.items(), key=lambda i: i[1])],
            "storefronts": [{"value": k, "label": v} for k, v in sorted(STOREFRONTS.items(), key=lambda i: i[1])]}


@router.get("/health", summary="Whether clipseerr runs", response_model=schemas.Health)
def health():
    return {"ok": True}


@router.get("/settings", tags=["Settings"], summary="The settings, and the choices for each",
            response_model=schemas.SettingsOut)
def get_settings():
    return _settings_out()


@router.patch("/settings", tags=["Settings"], summary="Change some settings", response_model=schemas.SettingsOut,
              responses={422: {"model": schemas.Problem}})
async def patch_settings(body: SettingsPatch):
    before = settings()
    settings.update(body)
    after = settings()
    if (before["region"], before["storefront"]) != (after["region"], after["storefront"]):
        # Another country's charts: have them ready before the page asks.
        asyncio.get_running_loop().create_task(catalog.warm())
    if before["poll_seconds"] != after["poll_seconds"]:
        engine.wake()
    return _settings_out()


@router.get("/system/status", summary="Versions, the sources' health, the cache and the queue",
            response_model=schemas.SystemStatus)
def status():
    return {
        "version": VERSION,
        "ytmusicapi": version("ytmusicapi"),
        "started_at": started_at,
        "cache": cache.stats(),
        "sources": catalog.health,
        "requests": requests.counts(),
        "chain": {kind: [s["name"] for s in engine.chain(kind)] for kind in ("audio", "video")},
        "last_round": engine.last_round,
    }


@router.post("/system/cache/clear", summary="Forget what the sources answered (fetched again when asked)",
             response_model=schemas.Health)
async def clear_cache():
    cache.forget("")
    return {"ok": True}
