from typing import Literal

import logging

from fastapi import APIRouter, Query, Response

from .. import media, schemas
from ..accounts import Principal
from ..auth import Who, need
from ..context import catalog, engine, requests
from ..problems import Problem
from ..services import Update
from ..tracker.store import ACTIVE

router = APIRouter(tags=["Requests"])
log = logging.getLogger("clipseerr")
_PROBLEMS = {403: {"model": schemas.Problem}, 404: {"model": schemas.Problem}, 409: {"model": schemas.Problem},
             422: {"model": schemas.Problem}}


def _theirs(request_id: str, who: Principal) -> None:
    """A request who may see: their own, or anyone's with "everyone". Someone else's is "no such
    request", as one that doesn't exist."""
    if not who.can("everyone") and requests.owner(request_id) != who.owner:
        raise Problem(404, "No such request.")


def _changes(request_id: str, who: Principal) -> None:
    """Retrying, stopping, removing: who asks for songs and videos, on a request they may see."""
    _theirs(request_id, who)
    if not who.can("request"):
        raise Problem(403, f"{who.name} may not ask for songs and videos, nor retry, stop or remove requests.")


@router.get("/requests", summary="Requests, newest first", response_model=schemas.RequestList)
def list_requests(who: Who, status: Literal["all", "active", "completed", "failed"] = "all",
                  q: str = Query("", max_length=100, description="Words of the title or artist."),
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    """One's own, or everyone's with the permission."""
    mine = None if who.can("everyone") else who.owner
    found, total = requests.list(status, limit, offset, q.strip(), owner=mine)
    return {"requests": found, "total": total, "counts": requests.counts(mine)}


@router.post("/requests", status_code=201, summary="Ask for a song (audio) or a video",
             description="Answers the request already made for this video and kind when there is one "
                         "(200, not 201), rather than asking twice.",
             response_model=schemas.Request, responses={**_PROBLEMS, 200: {"model": schemas.Request}})
async def create_request(body: schemas.RequestIn, response: Response, who=need("request")):
    video = await catalog.video(body.video_id)
    existing = requests.open_for(video.id, body.kind)
    if existing:
        response.status_code = 200
        return existing
    if body.kind == "video" and not video.youtube_id:
        raise Problem(409, "This video isn't on YouTube, so there is no file to download. Its audio can be asked for.")
    request = requests.create(body.kind, media.snapshot(video, body.kind), owner=who.owner, owner_name=who.name)
    log.info("%s asked for the %s of %s (request %s)", who.name, body.kind, video.id, request["id"])
    engine.created(request)
    return request


@router.get("/requests/{request_id}", summary="One request", response_model=schemas.Request, responses=_PROBLEMS)
def get_request(request_id: str, who: Who):
    _theirs(request_id, who)
    return requests.get(request_id)


@router.post("/requests/{request_id}/retry", summary="Ask every service again, from the first",
             response_model=schemas.Request, responses=_PROBLEMS)
def retry_request(request_id: str, who: Who):
    _changes(request_id, who)
    request = requests.get(request_id)
    if request["status"] in ACTIVE:
        raise Problem(409, "This request is still under way.")
    return engine.retry(request)


@router.post("/requests/{request_id}/cancel", summary="Stop following a request",
             response_model=schemas.Request, responses=_PROBLEMS)
def cancel_request(request_id: str, who: Who):
    _changes(request_id, who)
    request = requests.get(request_id)
    if request["status"] not in ACTIVE:
        raise Problem(409, "This request has already ended.")
    return engine.cancel(request)


@router.delete("/requests/{request_id}", status_code=204, summary="Remove a request from the list",
               responses=_PROBLEMS)
def delete_request(request_id: str, who: Who):
    _changes(request_id, who)
    request = requests.get(request_id)
    if request["status"] in ACTIVE:
        raise Problem(409, "Cancel it first: it is still under way.")
    requests.delete(request_id)
    return Response(status_code=204)


@router.post("/requests/{request_id}/report", summary="An app says where its attempt is (Webhook services)",
             response_model=schemas.Request, responses=_PROBLEMS)
def report(request_id: str, body: schemas.ReportIn, _who=need("request")):
    request = requests.get(request_id)
    if request["status"] not in ACTIVE:
        raise Problem(409, "This request has already ended.")
    try:
        return engine.report(request, Update(body.status, body.message, quality=body.quality,
                                             progress=body.progress))
    except LookupError as exc:
        raise Problem(409, str(exc)) from exc
