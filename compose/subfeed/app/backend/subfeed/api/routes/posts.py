"""The posts, three ways: the API's own JSON (/posts), JSON Feed for
programs and Atom for feed readers. All take the same filters, and answer
304 to a reader that has the answer already (If-None-Match). The feeds also
take an API key in their address (?key=…), for a feed reader that can't send
a header; the addresses they write into themselves leave it out."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from ... import formats
from ...posts import PostQuery
from .. import schemas
from ..auth import gate, without_key
from ..deps import Ctx, etag, unchanged, validated

router = APIRouter(tags=["Posts"], dependencies=[Depends(gate.principal)])
# What feed readers read: the key may be in the address.
feeds = APIRouter(tags=["Posts"], dependencies=[Depends(gate.principal_or_address_key)])
_PROBLEMS = {404: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}
_NOT_MODIFIED = {304: {"description": "The reader's copy (If-None-Match) is current."}}


def _filters(
    link: list[schemas.LinkKind] = Query([], description="Only posts linking to these; repeat for several."),
    video: list[schemas.Provider] = Query([], description="Only posts carrying a video of these providers."),
    domain: list[str] = Query([], description='Only links to these sites ("youtu.be", "v.redd.it").'),
    q: str = Query("", max_length=200, description="Words of the post's title, all of them."),
    author: str = Query("", max_length=40, description="Only this author's posts."),
    since: float | None = Query(None, description="Posted at or after (Unix seconds)."),
    until: float | None = Query(None, description="Posted before (Unix seconds)."),
    unique: bool = Query(False, description="One post a video: the first in its feed that carried it."),
    offset: int = Query(0, ge=0, le=100_000),
) -> PostQuery:
    """Every filter; left out, everything comes out."""
    return PostQuery(kinds=list(link), providers=list(video), domains=list(domain), words=q, author=author,
                     since=since, until=until, unique=unique, offset=offset)


Filters = Annotated[PostQuery, Depends(_filters)]


@router.get("/posts", summary="Posts of every feed, newest first", response_model=schemas.PostPage,
            responses={**_PROBLEMS, **_NOT_MODIFIED})
def all_posts(request: Request, response: Response, query: Filters, ctx: Ctx,
              feed: list[str] = Query([], description="Only these feeds (slugs)."),
              limit: int = Query(100, ge=1, le=500)):
    query.feeds, query.limit = list(feed), limit
    for slug in query.feeds:
        ctx.feeds.row(slug)
    return _page(request, response, query, ctx)


@router.get("/feeds/{slug}/posts", summary="One feed's posts, newest first", response_model=schemas.PostPage,
            responses={**_PROBLEMS, **_NOT_MODIFIED})
def feed_posts(slug: str, request: Request, response: Response, query: Filters, ctx: Ctx,
               limit: int = Query(100, ge=1, le=500)):
    ctx.feeds.row(slug)
    query.feeds, query.limit = [slug], limit
    return _page(request, response, query, ctx)


def _page(request: Request, response: Response, query: PostQuery, ctx: Ctx):
    tag = etag("posts", ctx.feeds.version(query.feeds), request.url.query)
    if (same := unchanged(request, tag)) is not None:
        return same
    validated(response, tag)
    posts, total = ctx.posts.query(query)
    return {"posts": posts, "total": total, "limit": query.limit, "offset": query.offset}


@feeds.get("/feeds/{slug}/feed.json", summary="One feed as JSON Feed 1.1, for programs",
            response_model=schemas.JsonFeed, response_model_exclude_none=True,
            responses={200: {"content": {"application/feed+json": {}}}, **_PROBLEMS, **_NOT_MODIFIED})
def feed_json(slug: str, request: Request, query: Filters, ctx: Ctx, limit: int = Query(100, ge=1, le=500)):
    """The same filters as …/posts. A post's videos and what it links to are
    in its "_subfeed"; next_url is the next page, when there is one."""
    feed = ctx.feeds.public(ctx.feeds.row(slug))
    tag = etag("json", feed["changed_at"], request.url.query)
    if (same := unchanged(request, tag)) is not None:
        return same
    query.feeds, query.limit = [slug], limit
    posts, total = ctx.posts.query(query)
    following = query.offset + len(posts)
    next_url = without_key(str(request.url.include_query_params(offset=following))) if following < total else None
    body = schemas.JsonFeed.model_validate(formats.json_feed(feed, posts, feed_url=without_key(str(request.url)),
                                                             next_url=next_url))
    response = Response(body.model_dump_json(by_alias=True, exclude_none=True), media_type="application/feed+json")
    validated(response, tag)
    return response


@feeds.get("/feeds/{slug}/feed.atom", summary="One feed as Atom, for any feed reader",
            response_class=Response,
            responses={200: {"content": {"application/atom+xml": {}}}, **_PROBLEMS, **_NOT_MODIFIED})
def feed_atom(slug: str, request: Request, query: Filters, ctx: Ctx, limit: int = Query(50, ge=1, le=200)):
    """The same filters as …/posts; an entry links to the post's video, else
    to what it links to, else to the post."""
    feed = ctx.feeds.public(ctx.feeds.row(slug))
    tag = etag("atom", feed["changed_at"], request.url.query)
    if (same := unchanged(request, tag)) is not None:
        return same
    query.feeds, query.limit = [slug], limit
    posts, _total = ctx.posts.query(query)
    response = Response(formats.atom(feed, posts, self_url=without_key(str(request.url))), media_type="application/atom+xml")
    validated(response, tag)
    return response
