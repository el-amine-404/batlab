from fastapi import APIRouter, Query

from ... import links
from ...domain import Invalid
from .. import schemas
from ..deps import Ctx

router = APIRouter(tags=["Links"])


@router.get("/links/check", summary="Whether videodrop takes a link (instant, without asking the site)",
            response_model=schemas.LinkCheck)
def check_link(url: str = Query(..., max_length=2000)):
    """For checking a link as it is typed: the scheme, an address outside the
    home network, a site yt-dlp has an extractor for. Always 200; ok says."""
    try:
        checked = links.check(url)
    except Invalid as exc:
        return {"ok": False, "message": str(exc), "url": url, "site": links.site_of(url) or None, "canonical": None}
    return {"ok": True, "message": None, "url": checked, "site": links.site_of(checked),
            "canonical": links.canonical(checked)}


@router.post("/probe", summary="What a link would download, without downloading it", response_model=schemas.Probe,
             responses={422: {"model": schemas.Problem}})
async def probe_link(body: schemas.ProbeIn, ctx: Ctx):
    """Asks the site (a few seconds): the title, picture and source, the
    format the options pick with its size and bitrate, the subtitles there
    are, and where the file would land. Two at a time at most; an answer is
    kept 10 minutes. 422 with the site's reason when it can't be read."""
    return await ctx.probe(body.url, body.destination, body.options)

