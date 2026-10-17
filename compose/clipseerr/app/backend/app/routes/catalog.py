from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Response

from .. import schemas
from ..catalog.models import ArtistPage, CategoryGroup, Credits, Lyrics, SearchResults, Shelf, VideoDetail, Versions
from ..context import catalog, library, requests, settings
from ..problems import Problem

router = APIRouter(tags=["Catalog"])
_404 = {404: {"model": schemas.Problem}, 502: {"model": schemas.Problem}}


def _clean(items: list) -> list:
    """Without explicit videos when the settings say so."""
    return [v for v in items if not v.explicit] if settings()["hide_explicit"] else items


def _page(shelf: Shelf, limit: int, offset: int, source: str | None = None) -> Shelf:
    """One page of a shelf; total counts all of it (after _clean, and of one
    source when asked)."""
    items = [v for v in _clean(shelf.items) if not source or v.source == source]
    shelf.total = len(items)
    shelf.items = items[offset:offset + limit]
    library.mark(shelf.items)
    return shelf


_LIMIT = Query(48, ge=1, le=500, description="Videos a page.")
_OFFSET = Query(0, ge=0, description="Videos to skip: the next page starts at offset + limit, until total.")
_SOURCE = Query(None, description="Only the videos from YouTube, or from Apple Music.")


@router.get("/home", summary="The front page: the newest videos and the charts, each with its first items",
            response_model=schemas.Home, responses=_404)
async def home():
    shelves = await catalog.home()
    for shelf in shelves:
        shelf.items = _clean(shelf.items)
        library.mark(shelf.items)
    return {"shelves": shelves}


@router.get("/shelves/{shelf_id}", summary="One shelf, a page at a time; \"releases\": what came out lately "
            "and what is coming, newest first", response_model=Shelf, responses=_404)
async def shelf(shelf_id: str, limit: int = _LIMIT, offset: int = _OFFSET,
                source: Literal["youtube", "apple"] | None = _SOURCE):
    return _page(await catalog.shelf(shelf_id), limit, offset, source)


@router.get("/categories", summary="Genres to browse, by source", response_model=list[CategoryGroup],
            responses=_404)
async def categories():
    return await catalog.categories()


@router.get("/categories/{category_id}", summary="The videos of a genre, a page at a time", response_model=Shelf,
            responses=_404)
async def category(category_id: str, limit: int = _LIMIT, offset: int = _OFFSET,
                   source: Literal["youtube", "apple"] | None = _SOURCE):
    return _page(await catalog.category(category_id), limit, offset, source)


@router.get("/search", summary="Videos and artists by name", response_model=SearchResults, responses=_404)
async def search(q: str = Query("", max_length=200, description="Words of the title or the artist.")):
    found = await catalog.search(q)
    found.videos = _clean(found.videos)
    library.mark(found.videos)
    return found


@router.get("/artists/{artist_id}", summary="An artist (YouTube Music channel) and their videos",
            response_model=ArtistPage, responses=_404)
async def artist(artist_id: str):
    found = await catalog.artist(artist_id)
    found.videos = _clean(found.videos)
    library.mark(found.videos)
    return found


@router.get("/videos/{video_id}", summary="One video: what it is, where it plays, its official audio",
            response_model=VideoDetail, responses=_404)
async def video(video_id: str):
    found = await catalog.video(video_id)
    library.mark([found])
    return found


@router.get("/videos/{video_id}/versions", summary="Other takes of a video's song: clean or explicit, "
            "remixes, live, its official video", response_model=Versions, responses=_404)
async def versions(video_id: str):
    found = await catalog.versions(video_id)
    found.videos = _clean(found.videos)
    found.songs = _clean(found.songs)
    library.mark([*found.videos, *found.songs])
    return found


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
