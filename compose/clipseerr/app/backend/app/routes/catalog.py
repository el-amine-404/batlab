from fastapi import APIRouter, Query

from .. import schemas
from ..catalog.models import ArtistPage, CategoryGroup, SearchResults, Shelf, VideoDetail, Versions
from ..context import catalog, requests, settings

router = APIRouter(tags=["Catalog"])
_404 = {404: {"model": schemas.Problem}, 502: {"model": schemas.Problem}}


def _clean(items: list) -> list:
    """Without explicit videos when the settings say so."""
    return [v for v in items if not v.explicit] if settings()["hide_explicit"] else items


@router.get("/home", summary="The front page: the newest videos and the charts, each with its first items",
            response_model=schemas.Home, responses=_404)
async def home():
    shelves = await catalog.home()
    for shelf in shelves:
        shelf.items = _clean(shelf.items)
    return {"shelves": shelves}


@router.get("/shelves/{shelf_id}", summary="One shelf, all of it; \"releases\": what came out lately and "
            "what is coming, newest first", response_model=Shelf, responses=_404)
async def shelf(shelf_id: str):
    found = await catalog.shelf(shelf_id)
    found.items = _clean(found.items)
    return found


@router.get("/categories", summary="Genres to browse, by source", response_model=list[CategoryGroup],
            responses=_404)
async def categories():
    return await catalog.categories()


@router.get("/categories/{category_id}", summary="The videos of a genre", response_model=Shelf, responses=_404)
async def category(category_id: str):
    found = await catalog.category(category_id)
    found.items = _clean(found.items)
    return found


@router.get("/search", summary="Videos and artists by name", response_model=SearchResults, responses=_404)
async def search(q: str = Query("", max_length=200, description="Words of the title or the artist.")):
    found = await catalog.search(q)
    found.videos = _clean(found.videos)
    return found


@router.get("/artists/{artist_id}", summary="An artist (YouTube Music channel) and their videos",
            response_model=ArtistPage, responses=_404)
async def artist(artist_id: str):
    found = await catalog.artist(artist_id)
    found.videos = _clean(found.videos)
    return found


@router.get("/videos/{video_id}", summary="One video: what it is, where it plays, its official audio",
            response_model=VideoDetail, responses=_404)
async def video(video_id: str):
    return await catalog.video(video_id)


@router.get("/videos/{video_id}/versions", summary="Other takes of a video's song: clean or explicit, "
            "remixes, live, its official video", response_model=Versions, responses=_404)
async def versions(video_id: str):
    found = await catalog.versions(video_id)
    found.videos = _clean(found.videos)
    found.songs = _clean(found.songs)
    return found


@router.get("/videos/{video_id}/requests", summary="The newest request of each kind for one video",
            response_model=schemas.VideoRequests)
def video_requests(video_id: str):
    found = {"audio": None, "video": None}
    for request in requests.for_videos([video_id]):
        if found[request["kind"]] is None:
            found[request["kind"]] = request
    return found
