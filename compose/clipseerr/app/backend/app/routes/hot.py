"""The Hot category's own state: the videos added to it by hand, and the
artists it has shown."""

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from .. import schemas
from ..catalog.models import HotArtist, Video
from ..context import catalog, hotlist

router = APIRouter(prefix="/hot", tags=["Hot"])
_404 = {404: {"model": schemas.Problem}, 502: {"model": schemas.Problem}}


class Picks(BaseModel):
    ids: list[str] = Field(description="The videos added by hand (catalog ids), newest first.")


class HotArtists(BaseModel):
    artists: list[HotArtist]


@router.get("/picks", summary="The videos added to Hot by hand", response_model=Picks)
def picks():
    return {"ids": hotlist.pick_ids()}


@router.put("/picks/{video_id}", summary="Add a video to Hot", response_model=Picks, responses=_404)
async def add_pick(video_id: str):
    detail = await catalog.video(video_id)
    # The video as lists give it, under its YouTube id when it has one: Hot's entries play.
    video = detail.model_dump(include=set(Video.model_fields))
    if detail.youtube_id:
        video["id"] = f"yt:{detail.youtube_id}"
        video["source"] = "youtube"
    hotlist.pick(video)
    return {"ids": hotlist.pick_ids()}


@router.delete("/picks/{video_id}", summary="Take a video out of Hot's picks (its yt: id)", status_code=204)
def remove_pick(video_id: str):
    hotlist.unpick(video_id)
    return Response(status_code=204)


@router.get("/artists", summary="Every artist Hot has shown, the most videos first", response_model=HotArtists)
def artists():
    return {"artists": [{**a, "id": a.get("id") or None, "thumbnail": a.get("thumbnail") or None}
                        for a in hotlist.artists()]}

