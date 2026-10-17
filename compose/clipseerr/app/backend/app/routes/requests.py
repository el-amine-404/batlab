from typing import Literal

from fastapi import APIRouter, Query, Response

from .. import media, schemas
from ..context import catalog, engine, requests
from ..problems import Problem
from ..services import Update
from ..tracker.store import ACTIVE

router = APIRouter(tags=["Requests"])
_PROBLEMS = {404: {"model": schemas.Problem}, 409: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}


@router.get("/requests", summary="Requests, newest first", response_model=schemas.RequestList)
def list_requests(status: Literal["all", "active", "completed", "failed"] = "all",
                  q: str = Query("", max_length=100, description="Words of the title or artist."),
                  limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    found, total = requests.list(status, limit, offset, q.strip())
    return {"requests": found, "total": total, "counts": requests.counts()}


@router.post("/requests", status_code=201, summary="Ask for a song (audio) or a video",
             description="Answers the request already made for this video and kind when there is one "
                         "(200, not 201), rather than asking twice.",
             response_model=schemas.Request, responses={**_PROBLEMS, 200: {"model": schemas.Request}})
async def create_request(body: schemas.RequestIn, response: Response):
    video = await catalog.video(body.video_id)
    existing = requests.open_for(video.id, body.kind)
    if existing:
        response.status_code = 200
        return existing
    if body.kind == "video" and not video.youtube_id:
        raise Problem(409, "This video isn't on YouTube, so there is no file to download. Its audio can be asked for.")
    request = requests.create(body.kind, media.snapshot(video, body.kind))
    engine.created(request)
    return request


@router.get("/requests/{request_id}", summary="One request", response_model=schemas.Request, responses=_PROBLEMS)
def get_request(request_id: str):
    return requests.get(request_id)


@router.post("/requests/{request_id}/retry", summary="Ask every service again, from the first",
             response_model=schemas.Request, responses=_PROBLEMS)
def retry_request(request_id: str):
    request = requests.get(request_id)
    if request["status"] in ACTIVE:
        raise Problem(409, "This request is still under way.")
    return engine.retry(request)


@router.post("/requests/{request_id}/cancel", summary="Stop following a request",
             response_model=schemas.Request, responses=_PROBLEMS)
def cancel_request(request_id: str):
    request = requests.get(request_id)
    if request["status"] not in ACTIVE:
        raise Problem(409, "This request has already ended.")
    return engine.cancel(request)


@router.delete("/requests/{request_id}", status_code=204, summary="Remove a request from the list",
               responses=_PROBLEMS)
def delete_request(request_id: str):
    request = requests.get(request_id)
    if request["status"] in ACTIVE:
        raise Problem(409, "Cancel it first: it is still under way.")
    requests.delete(request_id)
    return Response(status_code=204)


@router.post("/requests/{request_id}/report", summary="An app says where its attempt is (Webhook services)",
             response_model=schemas.Request, responses=_PROBLEMS)
def report(request_id: str, body: schemas.ReportIn):
    request = requests.get(request_id)
    if request["status"] not in ACTIVE:
        raise Problem(409, "This request has already ended.")
    try:
        return engine.report(request, Update(body.status, body.message, quality=body.quality,
                                             progress=body.progress))
    except LookupError as exc:
        raise Problem(409, str(exc)) from exc
