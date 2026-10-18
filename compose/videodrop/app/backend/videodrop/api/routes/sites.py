from importlib import metadata

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ...domain import NotFound
from ...sites import CATEGORIES
from .. import schemas
from ..deps import Ctx

router = APIRouter(prefix="/sites", tags=["Links"])


@router.get("", summary="The sites videodrop downloads from", response_model=schemas.SiteList)
def list_sites(ctx: Ctx):
    """yt-dlp's sites, one entry a site, in categories; known sites first
    in each. Read once for each yt-dlp version (a few seconds the first
    time). Other links are refused (POST /api/v1/links/check says so)."""
    sites = ctx.sites.list()
    counts = {key: 0 for key in CATEGORIES}
    for site in sites:
        counts[site["category"]] += 1
    return {"yt_dlp": metadata.version("yt-dlp"), "sites": sites,
            "categories": [{"id": key, "label": label, "count": counts[key]} for key, label in CATEGORIES.items()]}


@router.get("/{domain}/icon", summary="A site's icon", response_class=FileResponse,
            responses={200: {"content": {"image/x-icon": {}}}, 404: {"model": schemas.Problem}})
async def site_icon(domain: str, ctx: Ctx):
    """Asked of DuckDuckGo's icon service by videodrop (the browser asks
    only videodrop), kept, and kept by the browser a week."""
    found = await ctx.sites.icon(domain)
    if not found:
        raise NotFound("That site has no icon.")
    return FileResponse(found, media_type="image/x-icon", headers={"Cache-Control": "public, max-age=604800"})
