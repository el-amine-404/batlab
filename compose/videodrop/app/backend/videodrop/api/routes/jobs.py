import asyncio
import json
import mimetypes
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ...broker import TooMany
from ...domain import FINAL, STATUSES, NotFound, Status
from ...jobs import MAX_PAGE, JobIn, JobQuery
from .. import schemas
from ..deps import Base, Ctx

router = APIRouter(prefix="/jobs", tags=["Jobs"])
_PROBLEMS = {404: {"model": schemas.Problem}, 409: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}
_STREAM = {200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events: `event: job`, "
                                                                     "`data: <the job>`."}}
# Browsers reconnect a dropped stream after this many milliseconds.
RECONNECT_MS = 3000


@router.post("", status_code=201, summary="Ask for a download", response_model=schemas.Job,
             responses={**_PROBLEMS, 200: {"model": schemas.Job, "description": "The same download was already "
                                                                                  "queued or running: that job."}})
def create_job(body: JobIn, ctx: Ctx, base: Base, response: Response):
    """Queued at once (201); its id follows it: GET it, stream it, or give a
    callback_url. Asking again for the same link, destination and options
    while it is queued or running answers that job (200), so a caller can
    retry a request safely. 422 when the link isn't one videodrop downloads
    (a site yt-dlp doesn't know, an address on this network)."""
    row, new = ctx.submit(body, base)
    response.status_code = 201 if new else 200
    response.headers["Location"] = f"{base}/api/v1/jobs/{row['id']}"
    return ctx.jobs.public(row, base)


@router.get("", summary="The downloads, newest first", response_model=schemas.JobPage)
def list_jobs(ctx: Ctx, base: Base,
              status: list[Status] = Query([], description="Only these; repeat for several."),
              destination: str | None = Query(None, max_length=40),
              client: str | None = Query(None, max_length=40),
              q: str | None = Query(None, max_length=200, description="In the title, the link and the uploader."),
              limit: int = Query(50, ge=1, le=MAX_PAGE),
              offset: int = Query(0, ge=0)):
    rows, total = ctx.jobs.query(JobQuery(tuple(status), destination, client, (q or "").strip() or None, limit,
                                          offset))
    return {"jobs": [ctx.jobs.public(row, base) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/counts", summary="How many downloads there are of each status", response_model=schemas.JobCounts)
def job_counts(ctx: Ctx):
    return ctx.jobs.counts()


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def _stream(request: Request, ctx, base: str, ids: frozenset[str] | None, until_final: bool,
                  first: list[dict]) -> AsyncIterator[str]:
    try:
        subscription = ctx.broker.subscribe(ids)
    except TooMany as exc:
        yield _sse("problem", {"detail": str(exc)})
        return
    try:
        yield f"retry: {RECONNECT_MS}\n\n"
        for job in first:
            yield _sse("job", job)
            if until_final and job["status"] in FINAL:
                return
        async for message in subscription.messages():
            if await request.is_disconnected():
                return
            if message is None:
                yield ": keep-alive\n\n"
                continue
            try:
                job = ctx.jobs.public(ctx.jobs.row(message), base)
            except NotFound:
                yield _sse("removed", {"id": message})
                if until_final:
                    return
                continue
            yield _sse("job", job)
            if until_final and job["status"] in FINAL:
                return
    except asyncio.CancelledError:
        return
    finally:
        subscription.close()


def _streaming(body: AsyncIterator[str]) -> StreamingResponse:
    # no-transform: no proxy may compress or hold it back.
    return StreamingResponse(body, media_type="text/event-stream",
                             headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})


@router.get("/events", summary="Every download's changes, live", response_class=StreamingResponse,
            responses=_STREAM)
def stream_jobs(request: Request, ctx: Ctx, base: Base,
                id: list[str] = Query([], description="Only these jobs; repeat for several. None: every job.")):
    """Server-Sent Events: `event: job` with the job, as GET /jobs/{id}
    answers it, each time it changes (progress at most once a second);
    `event: removed` with its id when it is forgotten. A comment line every
    15 s keeps the connection open. Read the list first, then follow it
    here."""
    return _streaming(_stream(request, ctx, base, frozenset(id) if id else None, False, []))


@router.get("/{job_id}", summary="One download", response_model=schemas.Job, responses=_PROBLEMS)
def get_job(job_id: str, ctx: Ctx, base: Base):
    return ctx.jobs.public(ctx.jobs.row(job_id), base)


@router.get("/{job_id}/events", summary="One download, live, until it ends", response_class=StreamingResponse,
            responses={**_STREAM, 404: {"model": schemas.Problem}})
def stream_job(job_id: str, request: Request, ctx: Ctx, base: Base):
    """Server-Sent Events: the job now, then each time it changes; the
    stream ends after its final state (finished, failed, cancelled)."""
    first = ctx.jobs.public(ctx.jobs.row(job_id), base)
    return _streaming(_stream(request, ctx, base, frozenset({job_id}), True, [first]))


@router.post("/{job_id}/cancel", summary="Stop a download", response_model=schemas.Job, responses=_PROBLEMS)
async def cancel_job(job_id: str, ctx: Ctx, base: Base):
    """A queued one is cancelled at once; a running one within seconds (its
    process is stopped, what it downloaded so far is dropped). An ended one
    is left as it is."""
    ctx.scheduler.cancel(job_id)
    return ctx.jobs.public(ctx.jobs.row(job_id), base)


@router.post("/{job_id}/retry", summary="Try a failed or cancelled download again", response_model=schemas.Job,
             responses=_PROBLEMS)
def retry_job(job_id: str, ctx: Ctx, base: Base):
    return ctx.jobs.public(ctx.jobs.retry(job_id), base)


@router.delete("/{job_id}", status_code=204, summary="Forget an ended download", responses=_PROBLEMS)
def delete_job(job_id: str, ctx: Ctx):
    """Its held files go with it; files in a destination stay. 409 while it
    is queued or running."""
    ctx.remove_job(job_id)
    return Response(status_code=204)


@router.get("/{job_id}/files/{index}", summary="Download one of its files", response_class=FileResponse,
            responses={200: {"content": {"application/octet-stream": {}}}, 404: {"model": schemas.Problem}})
def get_file(job_id: str, index: int, ctx: Ctx,
             inline: bool = Query(False, description="To play in the browser rather than save.")):
    """The file itself. Range requests work (resume a download, seek in a
    player); the ETag and Last-Modified let a client skip what it has."""
    ctx.jobs.row(job_id)  # 404 when there's no such job
    for file in ctx.jobs.files(job_id):
        if file["position"] == index and file["removed_at"] is None:
            path = ctx.jobs.file_path(file)
            if path.is_file():
                media = mimetypes.guess_type(file["name"])[0] or "application/octet-stream"
                return FileResponse(path, media_type=media, filename=file["name"],
                                    content_disposition_type="inline" if inline else "attachment")
    return JSONResponse({"detail": "That file isn't there any more."}, status_code=404)
