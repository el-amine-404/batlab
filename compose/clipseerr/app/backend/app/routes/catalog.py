import re
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from .. import schemas
from ..catalog.models import (AlbumPage, ArtistPage, ArtistSummary, CategoryGroup, Credits, Lyrics, SearchResults, Shelf, Video,
                              VideoDetail, Versions)
from ..cache import tracking
from ..context import catalog, http, known, library, requests, settings
from ..problems import Problem

router = APIRouter(tags=["Catalog"])
_YOUTUBE_ID = re.compile(r"[\w-]{11}")
_404 = {404: {"model": schemas.Problem}, 502: {"model": schemas.Problem}}


def _clean(items: list) -> list:
    """Without explicit videos when the settings say so."""
    return [v for v in items if not v.explicit] if settings()["hide_explicit"] else items


Sort = Literal["default", "newest", "views", "title"]
_SORT = Query("default", description="The list's own order (a chart's rank, YouTube's relevance), newest out, "
                                     "most viewed, or A to Z.")


def _views(views: str | None) -> float:
    """ "1.2M" → 1200000; an unknown count last."""
    found = re.fullmatch(r"([\d.]+)([KMB]?)", (views or "").replace(",", ""))
    return float(found[1]) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[found[2]] if found else -1


async def _sorted(items: list, sort: Sort) -> list:
    """The whole list in the order asked, before it is cut into pages."""
    if sort == "newest":
        await catalog.fill_released(items)
        return sorted(items, key=lambda v: v.released or "", reverse=True)
    if sort == "views":
        catalog.known_views(items)
        return sorted(items, key=lambda v: _views(v.views), reverse=True)
    if sort == "title":
        return sorted(items, key=lambda v: v.title.casefold())
    return items


async def _page(shelf: Shelf, limit: int, offset: int, source: str | None = None, sort: Sort = "default") -> Shelf:
    """One page of a shelf; total counts all of it (after _clean, and of one
    source when asked), sorted first when asked."""
    known.saw(shelf.items, list_id=shelf.id)
    items = await _sorted([v for v in _clean(shelf.items) if not source or v.source == source], sort)
    shelf.total = len(items)
    shelf.items = items[offset:offset + limit]
    library.mark(shelf.items)
    catalog.known_views(shelf.items)
    return shelf


_LIMIT = Query(48, ge=1, le=500, description="Videos a page.")
_OFFSET = Query(0, ge=0, description="Videos to skip: the next page starts at offset + limit, until total.")
_SOURCE = Query(None, description="Only the videos from YouTube, or from Apple Music.")


@router.get("/home", summary="The front page: the newest videos and the charts, each with its first items",
            response_model=schemas.Home, responses=_404)
async def home():
    shelves = await catalog.home()
    for shelf in shelves:
        known.saw(shelf.items, list_id=f"home:{shelf.id}")
        shelf.items = _clean(shelf.items)
        library.mark(shelf.items)
        catalog.known_views(shelf.items)
    return {"shelves": shelves}


@router.get("/shelves/{shelf_id}", summary="One shelf, a page at a time; \"releases\": what came out lately "
            "and what is coming, newest first", response_model=Shelf, responses=_404)
async def shelf(shelf_id: str, limit: int = _LIMIT, offset: int = _OFFSET,
                source: Literal["youtube", "apple"] | None = _SOURCE, sort: Sort = _SORT):
    with tracking() as reads:
        found = await catalog.shelf(shelf_id)
    found.updated_at = min(reads, default=None)
    return await _page(found, limit, offset, source, sort)


@router.get("/categories", summary="Genres to browse, by source", response_model=list[CategoryGroup],
            responses=_404)
async def categories():
    return await catalog.categories()


@router.get("/categories/{category_id}", summary="The videos of a genre, a page at a time", response_model=Shelf,
            responses=_404)
async def category(category_id: str, limit: int = _LIMIT, offset: int = _OFFSET,
                   source: Literal["youtube", "apple"] | None = _SOURCE, sort: Sort = _SORT):
    with tracking() as reads:
        found = await catalog.category(category_id)
    found.updated_at = min(reads, default=None)
    return await _page(found, limit, offset, source, sort)


@router.get("/search", summary="Videos and artists by name", response_model=SearchResults, responses=_404)
async def search(q: str = Query("", max_length=200, description="Words of the title or the artist."),
                 limit: int = _LIMIT, offset: int = _OFFSET, sort: Sort = _SORT,
                 official: bool = Query(False, description="Only the artists' own videos, no fan uploads.")):
    found = await catalog.search(q)
    known.saw(found.videos, found.artists, list_id=f"search:{q.casefold()}")
    found.artists = [a for a in found.artists if not catalog.artist_empty(a.id)]
    found.videos = await _sorted([v for v in _clean(found.videos) if v.official or not official], sort)
    found.total, found.offset = len(found.videos), offset
    found.videos = found.videos[offset:offset + limit]
    library.mark(found.videos)
    catalog.known_views(found.videos)
    return found


@router.get("/search/quick", summary="Videos and artists by name, among those clipseerr has shown: answers at once "
            "(the full search asks YouTube Music)", response_model=SearchResults)
def quick_search(q: str = Query("", max_length=200, description="Words of the title or the artist; each may be cut short.")):
    artists, videos = known.search(q)
    artists = [a for a in artists if not catalog.artist_empty(a.id)]
    videos = _clean(videos)
    library.mark(videos)
    catalog.known_views(videos)
    return {"query": q.strip(), "artists": artists, "videos": videos}


@router.get("/artists/{artist_id}", summary="An artist (YouTube Music channel) and their videos",
            response_model=ArtistPage, responses=_404)
async def artist(artist_id: str):
    found = await catalog.artist(artist_id)
    known.saw(found.videos, [ArtistSummary(id=found.id, name=found.name, thumbnail=found.thumbnail,
                                           subscribers=found.subscribers), *found.related], list_id=f"artist:{found.id}")
    found.videos = _clean(found.videos)
    library.mark(found.videos)
    catalog.known_views(found.videos)
    return found


@router.get("/albums/{album_id}", summary="An album, single or EP on YouTube Music, with its tracks",
            response_model=AlbumPage, responses=_404)
async def album(album_id: str):
    return await catalog.album(album_id)


@router.get("/videos/{video_id}", summary="One video: what it is, where it plays, its official audio",
            response_model=VideoDetail, responses=_404)
async def video(video_id: str):
    found = await catalog.video(video_id)
    # What its page found out (its artists, whether it is the artist's own) is
    # what its tags go by.
    known.saw([Video.model_validate(found.model_dump())])
    library.mark([found])
    return found


@router.get("/videos/{video_id}/versions", summary="Other takes of a video's song: clean or explicit, "
            "remixes, live, its official video", response_model=Versions, responses=_404)
async def versions(video_id: str):
    found = await catalog.versions(video_id)
    found.videos = _clean(found.videos)
    found.songs = _clean(found.songs)
    library.mark([*found.videos, *found.songs])
    catalog.known_views([*found.videos, *found.songs])
    return found


@router.get("/views", summary="How many watched each video, and when it came out: for the lists that don't say",
            response_model=schemas.Views, responses=_404)
async def views(ids: str = Query(..., max_length=48 * 12, description="YouTube video ids, comma-separated (48 at most).")):
    wanted = list(dict.fromkeys(i for i in ids.split(",") if _YOUTUBE_ID.fullmatch(i)))[:48]
    return await catalog.views(wanted)


@router.get("/videos/{video_id}/files", summary="The video's files in the libraries (Settings, Libraries), "
            "to download: its music video file, its song", response_model=schemas.LibraryFiles, responses=_404)
async def video_files(video_id: str):
    found = await catalog.video(video_id)
    files = library.files(found.youtube_id, found.title, found.artist)
    if found.audio:
        files += [f for f in library.files(None, found.audio.title, found.audio.artist) if f not in files]
    return {"files": [{"kind": f.kind, "name": f.name, "size": f.size, "url": f"/api/v1/files/{library.token(f)}"}
                      for f in files]}


_PREVIEW_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges")
# YouTube's file servers refuse an open range ("bytes=0-", what a <video> asks
# first) and serve a bounded one: each answer is this much at most, which
# HTTP allows, and the player asks for the rest as it goes.
_PREVIEW_CHUNK = 2 * 1024 * 1024
_RANGE = re.compile(r"bytes=(\d+)-(\d*)$")


def _bounded(asked: str | None) -> str:
    """The Range to ask YouTube for: the one asked, never open nor larger than _PREVIEW_CHUNK."""
    found = _RANGE.match((asked or "").strip())
    start = int(found[1]) if found else 0
    end = start + _PREVIEW_CHUNK - 1
    if found and found[2]:
        end = min(end, int(found[2]))
    return f"bytes={start}-{end}"


@router.get("/previews/{youtube_id}", summary="A video's preview for its card: its 360p file with sound, "
            "streamed through clipseerr; ranges as asked", responses={404: {"model": schemas.Problem},
                                                                     206: {"content": {"video/mp4": {}}}})
async def preview_file(youtube_id: str, request: Request):
    if not _YOUTUBE_ID.fullmatch(youtube_id):
        raise Problem(404, "No such video.")
    wanted = {"Range": _bounded(request.headers.get("range"))}
    for again in (False, True):
        try:
            found = await catalog.preview_url(youtube_id, again=again)
        except Problem:
            raise Problem(404, "YouTube has no preview file for this video.") from None
        upstream = await http.send(http.build_request("GET", found["url"], headers={**found["headers"], **wanted},
                                                      timeout=httpx.Timeout(30.0, connect=5.0)), stream=True)
        # A kept address YouTube no longer honours: found again, once.
        if upstream.status_code in (403, 410) and not again:
            await upstream.aclose()
            continue
        if upstream.status_code >= 400:
            await upstream.aclose()
            raise Problem(404, "YouTube refused the preview file.")
        headers = {k: upstream.headers[k] for k in _PREVIEW_HEADERS if k in upstream.headers}
        headers["Cache-Control"] = "private, max-age=3600"
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers,
                                 background=BackgroundTask(upstream.aclose))
    raise Problem(404, "YouTube refused the preview file.")


@router.post("/previews/{youtube_id}/prepare", status_code=204, summary="Find a preview's file ahead "
             "(the pointer is on its card), so it starts sooner")
async def prepare_preview(youtube_id: str):
    if _YOUTUBE_ID.fullmatch(youtube_id):
        try:
            await catalog.preview_url(youtube_id)
        except Problem:
            pass
    return Response(status_code=204)


@router.get("/files/{token}", summary="Download a file of a library, by the url /videos/{id}/files gave",
            responses={404: {"model": schemas.Problem}, 200: {"content": {"application/octet-stream": {}}}})
async def library_file(token: str):
    file = library.by_token(token)
    if not file:
        raise Problem(404, "The library doesn't hold that file (any more).")
    try:
        chunks, size = await library.open(file)
    except OSError as exc:
        raise Problem(404, str(exc)) from exc
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.name)}"}
    if size:
        headers["Content-Length"] = str(size)
    return StreamingResponse(chunks, media_type="application/octet-stream", headers=headers)


@router.get("/videos/{video_id}/credits", summary="Who made the song: performed, written, produced by…",
            response_model=Credits, responses=_404)
async def credits(video_id: str):
    return await catalog.credits(video_id)


@router.get("/videos/{video_id}/lyrics", summary="The song's words: timed lines (LRCLIB, else YouTube Music) "
            "or plain text", response_model=Lyrics, responses=_404)
async def lyrics(video_id: str):
    return await catalog.lyrics(video_id)


def _file_name(detail: VideoDetail, ext: str) -> str:
    name = f"{detail.artist} - {detail.title}"
    name = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "lyrics"
    return f"{name}.{ext}"


def _lrc(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


@router.get("/videos/{video_id}/lyrics.{fmt}", summary="The lyrics as a file: .lrc (timed) or .txt",
            response_class=Response, responses={200: {"content": {"text/plain": {}}}, **_404})
async def lyrics_file(video_id: str, fmt: Literal["lrc", "txt"]):
    found, detail = await catalog.lyrics(video_id), await catalog.video(video_id)
    if fmt == "lrc":
        if not found.synced:
            raise Problem(404, "There are no timed lyrics for this song.")
        head = f"[ar:{detail.artist}]\n[ti:{detail.title}]\n" + (f"[al:{detail.audio.album}]\n" if detail.audio and
                                                                   detail.audio.album else "")
        body = head + "\n".join(f"[{_lrc(line.start)}]{line.text}" for line in found.synced) + "\n"
    else:
        if not found.plain:
            raise Problem(404, "There are no lyrics for this song.")
        body = found.plain + "\n"
    name = _file_name(detail, fmt)
    return Response(body, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/videos/{video_id}/requests", summary="The newest request of each kind for one video",
            response_model=schemas.VideoRequests)
def video_requests(video_id: str):
    found = {"audio": None, "video": None}
    for request in requests.for_videos([video_id]):
        if found[request["kind"]] is None:
            found[request["kind"]] = request
    return found
