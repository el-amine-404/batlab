"""clipfeed's API (/api/v1) and its reference (/api/docs). LAN-only behind
Caddy, without a login, like clipseerr."""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from xml.sax.saxutils import escape

import httpx
from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import VERSION
from .config import load
from .db import Database
from .feeds import Conflict, FeedIn, Feeds, Filters, NotFound
from .sources import KINDS, Reddit

log = logging.getLogger("clipfeed")

conf = load()
db = Database(conf.db_path)
http = httpx.AsyncClient()
feeds = Feeds(db, Reddit(http, conf.user_agent))

Verdict = Literal["official", "likely", "unofficial", "unavailable", "unchecked"]
LinkKind = Literal[KINDS]

DESCRIPTION = """\
What communities post, as feeds a program can read: every post of a
subreddit, newest first, with what it links to; a YouTube video is checked
with YouTube Music (the artist's video, the song, or an upload of a released
song?).

Everything comes out unless asked otherwise: narrow with the parameters, e.g.
`GET /api/v1/feeds/{slug}/items?link=youtube&verdict=official&verdict=likely`
(JSON) or the same on `/atom` (any feed reader). Make and change feeds with
the `Feeds` routes. No account and no key: Reddit's public feed, PullPush's
archive, YouTube Music. Errors answer `{"detail": "<sentence>"}`. Times are
Unix seconds; days are YYYY-MM-DD.
"""


# ---- what it answers ------------------------------------------------------------------

class LastPoll(BaseModel):
    at: float
    ok: bool
    message: str | None


class Feed(BaseModel):
    slug: str
    title: str
    source: str
    target: str = Field(description="The subreddit.")
    enabled: bool
    poll_minutes: int
    created_at: float
    link: str
    last: LastPoll | None
    next_at: float | None
    items: int = Field(description="Posts it found.")
    videos: int = Field(description="Of which carry a YouTube video.")
    official: int = Field(description="Of which official (real songs).")
    likely: int = Field(description="Of which likely: official videos of songs YouTube Music doesn't have.")


class FeedList(BaseModel):
    feeds: list[Feed]


class Link(BaseModel):
    url: str | None = Field(description="What the post links to; none for a text post.")
    kind: LinkKind = Field(description="youtube (it carries a YouTube video), reddit_video, image, gallery, "
                                       "text (no link), other.")
    domain: str | None


class Video(BaseModel):
    youtube_id: str
    url: str
    verdict: Verdict = Field(description="official: the artist's video, the song, or an upload of a released "
                                         "song; likely: an upload YouTube Music doesn't know as a song whose title "
                                         "says it is the official video; unofficial: nothing says it is a released "
                                         "song; unavailable: gone; unchecked: not asked yet.")
    kind: Literal["music_video", "song", "upload"] | None = Field(description="What YouTube Music calls it.")
    title: str | None = Field(description="The song's title, as far as YouTube says.")
    artist: str | None
    duration: int | None = Field(description="Seconds.")
    published: str | None = Field(description="The day YouTube published it.")
    song_id: str | None = Field(description="An upload's song on YouTube Music, when one matched.")
    checked_at: float | None


class Item(BaseModel):
    id: str = Field(description='"<feed>:<post id>".')
    feed: str
    post_id: str
    title: str = Field(description="The post's title.")
    url: str = Field(description="The post on Reddit.")
    author: str | None
    posted_at: float
    link: Link
    youtube_ids: list[str] = Field(description="Every YouTube video it carries; video is the first.")
    video: Video | None = Field(description="Its YouTube video, checked; none when it has none.")


class ItemList(BaseModel):
    items: list[Item]
    total: int = Field(description="Matching items in all; items holds one page.")


class Problem(BaseModel):
    detail: str


class Health(BaseModel):
    ok: bool
    version: str


# ---- the app ----------------------------------------------------------------------------

def _seed() -> None:
    for slug, source, target in conf.seed:
        if not db.one("SELECT 1 FROM feeds WHERE slug = ?", (slug,)):
            feeds.create(FeedIn(slug=slug, title=f"r/{target}", source=source, target=target))
            log.info("made feed %s (r/%s)", slug, target)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _seed()
    feeds.start()
    yield
    await feeds.stop()
    await http.aclose()
    db.close()


app = FastAPI(title="clipfeed", version=VERSION, description=DESCRIPTION, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/v1/openapi.json", lifespan=lifespan,
              openapi_tags=[{"name": "Items", "description": "What the feeds found, newest first."},
                            {"name": "Feeds", "description": "The communities read, and how often."},
                            {"name": "System", "description": "Health."}])
api = APIRouter(prefix="/api/v1")
_errors = {404: {"model": Problem}, 409: {"model": Problem}, 422: {"model": Problem}}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


app.add_exception_handler(NotFound, lambda _r, exc: _error(404, str(exc)))
app.add_exception_handler(Conflict, lambda _r, exc: _error(409, str(exc)))


@app.exception_handler(RequestValidationError)
async def invalid(_request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(part) for part in first.get("loc", [])[1:])
    return _error(422, f"{where}: {first.get('msg', 'invalid')}" if where else first.get("msg", "Invalid request."))


@app.exception_handler(Exception)
async def unexpected(_request: Request, exc: Exception):
    log.exception("unhandled error", exc_info=exc)
    return _error(500, "Something went wrong on the server.")


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/api/docs")


@api.get("/health", tags=["System"], response_model=Health)
def health():
    return {"ok": True, "version": VERSION}


# ---- items -------------------------------------------------------------------------------

def filters(
    link: list[LinkKind] = Query([], description="Only posts linking to these; repeat for several."),
    verdict: list[Verdict] = Query([], description="Only posts whose video got these (posts without a video "
                                                    "drop out); repeat for several."),
    domain: list[str] = Query([], description='Only links to these sites ("youtu.be", "v.redd.it").'),
    q: str = Query("", max_length=200, description="Words of the post's title, or of its song's title or artist."),
    author: str = Query("", max_length=40, description="Only this Redditor's posts."),
    since: float | None = Query(None, description="Posted at or after (Unix seconds)."),
    until: float | None = Query(None, description="Posted before (Unix seconds)."),
    unique: bool = Query(False, description="One item a video: the first post that linked it."),
) -> Filters:
    """Every filter; left out, everything comes out."""
    return Filters(links=list(link), verdicts=list(verdict), domains=domain, words=q, author=author, since=since,
                   until=until, unique=unique)


_SORT = Query("posted", description="posted: when the post was made; published: when YouTube published its "
                                    "video (posts without one by their own day).")


@api.get("/items", tags=["Items"], summary="Every feed's posts, newest first", response_model=ItemList,
         responses=_errors)
def all_items(found: Filters = Depends(filters), sort: Literal["posted", "published"] = _SORT,
              feed: list[str] = Query([], description="Only these feeds (slugs)."),
              limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    found.feeds = list(feed)
    items, total = feeds.items(found, sort, limit, offset)
    return {"items": items, "total": total}


@api.get("/feeds/{slug}/items", tags=["Items"], summary="One feed's posts, newest first", response_model=ItemList,
         responses=_errors)
def feed_items(slug: str, found: Filters = Depends(filters), sort: Literal["posted", "published"] = _SORT,
               limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    found.feeds = [slug]
    items, total = feeds.items(found, sort, limit, offset)
    return {"items": items, "total": total}


def _when(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


@api.get("/feeds/{slug}/atom", tags=["Items"], summary="One feed as Atom, for any feed reader (same filters)",
         response_class=Response, responses={200: {"content": {"application/atom+xml": {}}}, **_errors})
def feed_atom(slug: str, request: Request, found: Filters = Depends(filters), limit: int = Query(50, ge=1, le=200)):
    feed = feeds.get(slug)
    found.feeds = [slug]
    items, _total = feeds.items(found, "posted", limit, 0)
    updated = max((i["posted_at"] for i in items), default=time.time())
    entries = []
    for item in items:
        video = item["video"] or {}
        name = f"{video['artist']} – {video['title']}" if video.get("artist") and video.get("title") else item["title"]
        href = video.get("url") or item["link"]["url"] or item["url"]
        entries.append(
            f"<entry><id>urn:clipfeed:{escape(item['id'])}</id><title>{escape(name)}</title>"
            f"<link href=\"{escape(href)}\"/><updated>{_when(item['posted_at'])}</updated>"
            f"<summary>{escape(item['title'])} (r/{escape(feed['target'])}, {item['link']['kind']})</summary></entry>")
    body = (f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<id>urn:clipfeed:{escape(slug)}</id><title>{escape(feed['title'])}</title>"
            f'<link rel="self" href="{escape(str(request.url))}"/><updated>{_when(updated)}</updated>'
            f"{''.join(entries)}</feed>")
    return Response(body, media_type="application/atom+xml")


# ---- feeds -------------------------------------------------------------------------------

@api.get("/feeds", tags=["Feeds"], response_model=FeedList)
def list_feeds():
    return {"feeds": feeds.list()}


@api.post("/feeds", tags=["Feeds"], status_code=201, response_model=Feed, responses=_errors)
def create_feed(body: FeedIn):
    return feeds.create(body)


@api.get("/feeds/{slug}", tags=["Feeds"], response_model=Feed, responses=_errors)
def get_feed(slug: str):
    return feeds.get(slug)


@api.put("/feeds/{slug}", tags=["Feeds"], response_model=Feed, responses=_errors)
def update_feed(slug: str, body: FeedIn):
    return feeds.update(slug, body)


@api.delete("/feeds/{slug}", tags=["Feeds"], status_code=204, responses=_errors)
def delete_feed(slug: str):
    feeds.delete(slug)
    return Response(status_code=204)


@api.post("/feeds/{slug}/refresh", tags=["Feeds"], summary="Read it now", response_model=Feed, responses=_errors)
def refresh_feed(slug: str):
    return feeds.refresh(slug)


app.include_router(api)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def not_found(rest: str):
    return _error(404, "Not found. The API is under /api/v1/ (see /api/docs).")
