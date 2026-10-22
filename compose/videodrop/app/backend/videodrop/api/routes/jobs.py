import asyncio
import json
import mimetypes
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from ... import links
from ...broker import TooMany
from ...domain import FINAL, Forbidden, Invalid, NotFound, Status
from ...jobs import MAX_PAGE, JobIn, JobQuery
from ...journal import PAGE
from .. import schemas
from ..auth import Who, need
from ..deps import MAX_NUMBER, Base, Ctx, FileIndex

router = APIRouter(prefix="/jobs", tags=["Jobs"])
_PROBLEMS = {404: {"model": schemas.Problem}, 409: {"model": schemas.Problem}, 422: {"model": schemas.Problem}}
_GONE = {410: {"model": schemas.GoneProblem, "description": "The file was there and isn't any more: removed after "
                                                             "its hold, or moved away. POST `again` to download "
                                                             "it again."}}
_STREAM = {200: {"content": {"text/event-stream": {}}, "description": "Server-Sent Events: `event: job`, "
                                                                     "`data: <the job>`."}}
# Browsers reconnect a dropped stream after this many milliseconds.
RECONNECT_MS = 3000


def visible(who, row) -> bool:
    """One's own downloads; everyone's with the permission."""
    return who.can("everyone") or row["owner"] == who.owner


def _seen(ctx, who, job_id: str):
    """The job, when who may see it; else as if it didn't exist."""
    row = ctx.jobs.row(job_id)
    if not visible(who, row):
        raise NotFound(f"There's no download {job_id!r}.")
    return row


def _theirs(ctx, who, job_id: str):
    """The job, when who may change it: theirs (or anyone's, with the
    permission), and they may ask for downloads."""
    row = _seen(ctx, who, job_id)
    if not who.can("download"):
        raise Forbidden(f"{who.name} may not ask for downloads, nor stop, retry or remove them.")
    return row


@router.post("", status_code=201, summary="Ask for a download", response_model=schemas.Job,
             responses={**_PROBLEMS, 200: {"model": schemas.Job, "description": "The same download was already "
                                                                                  "queued or running: that job."}})
async def create_job(body: JobIn, ctx: Ctx, base: Base, response: Response, who=need("download")):
    """Queued at once (201); its id follows it: GET it, stream it, or give a
    callback_url. Asking again for the same link, destination and options
    while it is queued or running answers that job (200), so a caller can
    retry a request safely. 422 when the link isn't one videodrop downloads
    (a site yt-dlp doesn't know, an address on this network). 403 for a
    destination without that permission, and for a callback asked by a
    person (callbacks are for apps, with an API key)."""
    await links.reachable(links.check(body.url))
    row, new = await run_in_threadpool(ctx.submit, body, base, who)
    response.status_code = 201 if new else 200
    response.headers["Location"] = f"{base}/api/v1/jobs/{row['id']}"
    return ctx.jobs.public(row, base)


@router.get("", summary="The downloads, newest first", response_model=schemas.JobPage)
def list_jobs(ctx: Ctx, base: Base, who: Who,
              status: list[Status] = Query([], description="Only these; repeat for several."),
              destination: str | None = Query(None, max_length=40),
              client: str | None = Query(None, max_length=40),
              q: str | None = Query(None, max_length=200, description="In the title, the link and the uploader."),
              limit: int = Query(50, ge=1, le=MAX_PAGE),
              offset: int = Query(0, ge=0, le=MAX_NUMBER)):
    """One's own; everyone's with that permission."""
    rows, total = ctx.jobs.query(JobQuery(tuple(status), destination, client, (q or "").strip() or None, limit,
                                          offset, None if who.can("everyone") else who.owner))
    return {"jobs": [ctx.jobs.public(row, base) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/counts", summary="How many downloads there are of each status", response_model=schemas.JobCounts)
def job_counts(ctx: Ctx, who: Who):
    """One's own; everyone's with that permission."""
    return ctx.jobs.counts(None if who.can("everyone") else who.owner)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def _stream(request: Request, ctx, who, base: str, ids: frozenset[str] | None, until_final: bool,
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
                row = ctx.jobs.row(message)
                if not visible(who, row):
                    continue
                job = ctx.jobs.public(row, base)
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
def stream_jobs(request: Request, ctx: Ctx, base: Base, who: Who,
                id: list[str] = Query([], description="Only these jobs; repeat for several. None: every job.")):
    """Server-Sent Events: `event: job` with the job, as GET /jobs/{id}
    answers it, each time it changes (progress at most once a second);
    `event: removed` with its id when it is forgotten. A comment line every
    15 s keeps the connection open. Read the list first, then follow it
    here."""
    return _streaming(_stream(request, ctx, who, base, frozenset(id) if id else None, False, []))


@router.get("/{job_id}", summary="One download", response_model=schemas.Job, responses=_PROBLEMS)
def get_job(job_id: str, ctx: Ctx, base: Base, who: Who):
    return ctx.jobs.public(_seen(ctx, who, job_id), base)


@router.get("/{job_id}/events", summary="One download, live, until it ends", response_class=StreamingResponse,
            responses={**_STREAM, 404: {"model": schemas.Problem}})
def stream_job(job_id: str, request: Request, ctx: Ctx, base: Base, who: Who):
    """Server-Sent Events: the job now, then each time it changes; the
    stream ends after its final state (finished, failed, cancelled)."""
    first = ctx.jobs.public(_seen(ctx, who, job_id), base)
    return _streaming(_stream(request, ctx, who, base, frozenset({job_id}), True, [first]))


@router.post("/{job_id}/cancel", summary="Stop a download", response_model=schemas.Job, responses=_PROBLEMS)
async def cancel_job(job_id: str, ctx: Ctx, base: Base, who: Who):
    """A queued one is cancelled at once; a running one within seconds (its
    process is stopped, what it downloaded so far is dropped). An ended one
    is left as it is."""
    _theirs(ctx, who, job_id)
    ctx.scheduler.cancel(job_id)
    return ctx.jobs.public(ctx.jobs.row(job_id), base)


@router.post("/{job_id}/retry", summary="Download it again", response_model=schemas.Job, responses=_PROBLEMS)
def retry_job(job_id: str, ctx: Ctx, base: Base, who: Who):
    """A failed or cancelled download, or a finished one whose files are
    gone (removed after their hold): queued again, the same job, the same
    links. 409 while its files are still there."""
    _theirs(ctx, who, job_id)
    row = ctx.jobs.retry(job_id)
    ctx.journal.add(job_id, f"Asked again by {who.name}: queued from the start.")
    return ctx.jobs.public(row, base)


@router.delete("/{job_id}", status_code=204, summary="Forget an ended download", responses=_PROBLEMS)
def delete_job(job_id: str, ctx: Ctx, who: Who):
    """Its held files go with it; files in a destination stay. 409 while it
    is queued or running."""
    _theirs(ctx, who, job_id)
    ctx.remove_job(job_id)
    return Response(status_code=204)


@router.get("/{job_id}/log", summary="What happened to it, line by line", response_model=schemas.JobLog,
            responses=_PROBLEMS)
def job_log(job_id: str, ctx: Ctx, who: Who,
            after: int = Query(0, ge=0, le=MAX_NUMBER, description="Only the lines after this one (its seq): to follow it."),
            limit: int = Query(PAGE, ge=1, le=PAGE)):
    """Who asked, each try, what yt-dlp said, the fingerprint, each step
    and how long it took, where the files went, each callback, the held
    files removed. Without secrets (passwords, tokens, signed addresses are
    hidden). The newest 3000 lines of a download are kept."""
    _seen(ctx, who, job_id)
    lines = ctx.journal.lines(job_id, after, limit)
    return {"lines": lines, "more": len(lines) == limit}


@router.get("/{job_id}/log.txt", summary="Its log, as a text file", response_class=PlainTextResponse,
            responses={200: {"content": {"text/plain": {}}}, 404: {"model": schemas.Problem}})
def job_log_text(job_id: str, ctx: Ctx, who: Who):
    _seen(ctx, who, job_id)
    return PlainTextResponse(ctx.journal.text(job_id), headers={
        "Content-Disposition": f'attachment; filename="videodrop-{job_id[:8]}.log"'})


@router.get("/{job_id}/files/{index}", summary="Download one of its files", response_class=FileResponse,
            responses={200: {"content": {"application/octet-stream": {}}}, 404: {"model": schemas.Problem}, **_GONE})
def get_file(job_id: str, index: FileIndex, ctx: Ctx, base: Base, who: Who,
             inline: bool = Query(False, description="To play in the browser rather than save.")):
    """The file itself. Range requests work (resume a download, seek in a
    player); the ETag and Last-Modified let a client skip what it has. A
    held file is there until its expires_at; after that, 410 Gone."""
    _seen(ctx, who, job_id)
    file, path = ctx.jobs.available(job_id, index, base)
    media = mimetypes.guess_type(file["name"])[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=file["name"],
                        content_disposition_type="inline" if inline else "attachment")


def _video(ctx, who, job_id: str, index: int):
    _seen(ctx, who, job_id)
    file, path = ctx.jobs.available(job_id, index)
    if file["kind"] != "video":
        raise NotFound("That file isn't a video.")
    return file, path


@router.get("/{job_id}/files/{index}/details", summary="A video file's details and pictures",
            response_model=schemas.FileDetails, responses=_PROBLEMS)
def file_details(job_id: str, index: FileIndex, ctx: Ctx, base: Base, who: Who):
    """As ffprobe reads it: its codecs, bitrates, tracks and chapters; and
    the addresses of its pictures (a gallery, a storyboard for a seek bar)
    and of its subtitle tracks as WebVTT. The pictures are made the first
    time they are asked for, or right after the download: a few seconds.
    The gallery has a picture at each chapter's start (3 to 24 chapters),
    else 4 to 24 across the video, by its length."""
    file, path = _video(ctx, who, job_id, index)
    try:
        details = ctx.previews.details(path)
    except ValueError as exc:
        raise Invalid(f"The file can't be read: {exc}") from None
    here = f"{base}/api/v1/jobs/{job_id}/files/{index}"
    board = details["storyboard"]
    return {
        **details, "name": file["name"], "size": path.stat().st_size,
        "frames": [{**frame, "url": f"{here}/frames/{n}.jpg"} for n, frame in enumerate(details["frames"])],
        "storyboard": {**board, "url": f"{here}/storyboard.jpg"} if board else None,
        "subtitles": [{**{k: v for k, v in track.items() if k != "text"},
                       "url": f"{here}/subtitles/{track['index']}.vtt" if track["text"] else None}
                      for track in details["subtitles"]],
    }


_PICTURE = {200: {"content": {"image/jpeg": {}}}, 404: {"model": schemas.Problem}}


@router.get("/{job_id}/files/{index}/frames/{n}.jpg", summary="One of a video's gallery pictures",
            response_class=FileResponse, responses=_PICTURE)
def file_frame(job_id: str, index: FileIndex, n: FileIndex, ctx: Ctx, who: Who):
    _, path = _video(ctx, who, job_id, index)
    found = ctx.previews.frame(path, n)
    if not found:
        raise NotFound("There's no such picture.")
    return FileResponse(found, media_type="image/jpeg")


@router.get("/{job_id}/files/{index}/storyboard.jpg", summary="A video's storyboard",
            response_class=FileResponse, responses=_PICTURE)
def file_storyboard(job_id: str, index: FileIndex, ctx: Ctx, who: Who):
    """Small pictures of the whole video in one image (details.storyboard says how to read it)."""
    _, path = _video(ctx, who, job_id, index)
    found = ctx.previews.storyboard(path)
    if not found:
        raise NotFound("This video has no storyboard.")
    return FileResponse(found, media_type="image/jpeg")


@router.get("/{job_id}/files/{index}/subtitles/{n}.vtt", summary="A video's subtitle track, as WebVTT",
            response_class=FileResponse, responses={200: {"content": {"text/vtt": {}}}, 404: {"model": schemas.Problem}})
def file_subtitles(job_id: str, index: FileIndex, n: FileIndex, ctx: Ctx, who: Who):
    _, path = _video(ctx, who, job_id, index)
    found = ctx.previews.subtitles(path, n)
    if not found:
        raise NotFound("There's no such text subtitle track.")
    return FileResponse(found, media_type="text/vtt; charset=utf-8")
