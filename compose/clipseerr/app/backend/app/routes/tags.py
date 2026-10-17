import re
from collections import Counter
from typing import Literal

from fastapi import APIRouter, Query

from .. import schemas, tags
from ..catalog.models import Shelf
from ..context import catalog, known, library, settings
from ..problems import Problem

router = APIRouter(tags=["Tags"])


def _tagged():
    """(video, tags) of every video known, the libraries' "in-library" added,
    explicit ones left out when the settings hide them."""
    hide = settings()["hide_explicit"]
    for video, found in known.tagged():
        if hide and video.explicit:
            continue
        if library.held(video.youtube_id, video.title, video.artist):
            found.add("in-library")
        yield video, found


def _count(views: str | None) -> float:
    """ "1.2M" → 1200000; an unknown count last."""
    found = re.fullmatch(r"([\d.]+)([KMB]?)", (views or "").replace(",", ""))
    return float(found[1]) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[found[2]] if found else -1


@router.get("/tags", summary="Every tag a video can carry (a badge), with how many of the known videos carry it",
            response_model=schemas.Tags)
def all_tags():
    counts = Counter(tag for _video, found in _tagged() for tag in found)
    order = list(tags.FIXED)
    described = [{**tags.describe(tag), "count": n} for tag, n in counts.items() if tags.describe(tag)]
    described.sort(key=lambda t: (t["id"] not in order, order.index(t["id"]) if t["id"] in order else 0,
                                  t["id"].split(":")[0], -t["count"], t["label"]))
    return {"tags": described}


@router.get("/tags/videos", summary="The videos carrying every tag asked, a page at a time, the latest seen first",
            response_model=Shelf, responses={404: {"model": schemas.Problem}})
def tagged_videos(tag: list[str] = Query(..., max_length=8, description='Tags, all of them: "explicit", "lang:el"…'),
                  sort: Literal["seen", "newest", "views", "title"] = Query(
                      "seen", description="Latest seen by clipseerr, newest out, most viewed, or A to Z."),
                  limit: int = Query(48, ge=1, le=500), offset: int = Query(0, ge=0)):
    wanted = set(tag)
    unknown = [t for t in wanted if not tags.describe(t)]
    if unknown:
        raise Problem(404, f"No such tag: {', '.join(sorted(unknown))}.")
    items = [video for video, found in _tagged() if wanted <= found]
    if sort == "newest":
        items.sort(key=lambda v: v.released or "", reverse=True)
    elif sort == "views":
        catalog.known_views(items)
        items.sort(key=lambda v: _count(v.views), reverse=True)
    elif sort == "title":
        items.sort(key=lambda v: v.title.casefold())
    labels = [tags.describe(t)["label"] for t in sorted(wanted)]
    page = items[offset:offset + limit]
    library.mark(page)
    catalog.known_views(page)
    return Shelf(id="tags", title=" · ".join(labels), source="youtube", items=page, total=len(items))
