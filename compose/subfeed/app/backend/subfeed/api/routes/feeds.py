from fastapi import APIRouter, Response

from ...feeds import FeedIn, FeedPatch
from .. import schemas
from ..deps import Ctx

router = APIRouter(tags=["Feeds"])
_PROBLEMS = {404: {"model": schemas.Problem}, 409: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}


@router.get("/sources", summary="Where posts can come from", response_model=schemas.SourceList)
def list_sources(ctx: Ctx):
    return {"sources": [source.describe() for source in ctx.sources.values()]}


@router.get("/feeds", summary="Every feed, oldest first", response_model=schemas.FeedList)
def list_feeds(ctx: Ctx):
    counts = ctx.posts.counts()
    return {"feeds": [ctx.feeds.public(row, counts) for row in ctx.feeds.rows()]}


@router.post("/feeds", status_code=201, summary="Make a feed", response_model=schemas.Feed, responses=_PROBLEMS)
def create_feed(body: FeedIn, ctx: Ctx):
    """It is read at once: a new feed first takes the community's history
    (Settings, backfill_posts), then its newest posts."""
    return ctx.feeds.public(ctx.feeds.create(body, ctx.settings().default_poll_minutes))


@router.get("/feeds/{slug}", summary="One feed", response_model=schemas.Feed, responses=_PROBLEMS)
def get_feed(slug: str, ctx: Ctx):
    return ctx.feeds.public(ctx.feeds.row(slug), ctx.posts.counts())


@router.patch("/feeds/{slug}", summary="Change a feed; what is left out keeps its value",
              response_model=schemas.Feed, responses=_PROBLEMS)
def update_feed(slug: str, body: FeedPatch, ctx: Ctx):
    """Another target is another community: the posts found so far go, and
    its history is read again."""
    return ctx.feeds.public(ctx.feeds.update(slug, body), ctx.posts.counts())


@router.delete("/feeds/{slug}", status_code=204, summary="Remove a feed and its posts", responses=_PROBLEMS)
def delete_feed(slug: str, ctx: Ctx):
    ctx.feeds.delete(slug)
    return Response(status_code=204)


@router.post("/feeds/{slug}/refresh", summary="Read it now", response_model=schemas.Feed, responses=_PROBLEMS)
def refresh_feed(slug: str, ctx: Ctx):
    """Returns at once; the read follows within seconds (its last says how it went)."""
    return ctx.feeds.public(ctx.feeds.refresh(slug), ctx.posts.counts())
