import logging

from fastapi import APIRouter, Response

from ...feeds import FeedIn, FeedPatch
from .. import schemas
from ..auth import need
from ..deps import Ctx

router = APIRouter(tags=["Feeds"])
log = logging.getLogger("subfeed")
_PROBLEMS = {403: {"model": schemas.Problem}, 404: {"model": schemas.Problem}, 409: {"model": schemas.Problem},
             422: {"model": schemas.Problem}}


@router.get("/sources", summary="Where posts can come from", response_model=schemas.SourceList)
def list_sources(ctx: Ctx):
    return {"sources": [source.describe() for source in ctx.sources.values()]}


@router.get("/feeds", summary="Every feed, oldest first", response_model=schemas.FeedList)
def list_feeds(ctx: Ctx):
    counts = ctx.posts.counts()
    return {"feeds": [ctx.feeds.public(row, counts) for row in ctx.feeds.rows()]}


@router.post("/feeds", status_code=201, summary="Make a feed", response_model=schemas.Feed, responses=_PROBLEMS)
def create_feed(body: FeedIn, ctx: Ctx, who=need("feeds")):
    """It is read at once: a new feed first takes the community's history
    (Settings, backfill_posts), then its newest posts."""
    feed = ctx.feeds.public(ctx.feeds.create(body, ctx.settings().default_poll_minutes))
    log.info("%s made feed %s (%s %s)", who.name, feed["slug"], feed["source"], feed["target"])
    return feed


@router.get("/feeds/{slug}", summary="One feed", response_model=schemas.Feed, responses=_PROBLEMS)
def get_feed(slug: str, ctx: Ctx):
    return ctx.feeds.public(ctx.feeds.row(slug), ctx.posts.counts())


@router.patch("/feeds/{slug}", summary="Change a feed; what is left out keeps its value",
              response_model=schemas.Feed, responses=_PROBLEMS)
def update_feed(slug: str, body: FeedPatch, ctx: Ctx, who=need("feeds")):
    """Another target is another community: the posts found so far go, and
    its history is read again."""
    feed = ctx.feeds.public(ctx.feeds.update(slug, body), ctx.posts.counts())
    log.info("%s changed feed %s: %s", who.name, slug, body.model_dump(exclude_unset=True))
    return feed


@router.delete("/feeds/{slug}", status_code=204, summary="Remove a feed and its posts", responses=_PROBLEMS)
def delete_feed(slug: str, ctx: Ctx, who=need("feeds")):
    ctx.feeds.delete(slug)
    log.info("%s removed feed %s", who.name, slug)
    return Response(status_code=204)


@router.post("/feeds/{slug}/refresh", summary="Read it now", response_model=schemas.Feed, responses=_PROBLEMS)
def refresh_feed(slug: str, ctx: Ctx, _who=need("feeds")):
    """Returns at once; the read follows within seconds (its last says how it went)."""
    return ctx.feeds.public(ctx.feeds.refresh(slug), ctx.posts.counts())
