import hashlib
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field, ValidationError, field_validator

from .. import __version__, detect, options, problems, schemas
from ..accounts import Principal
from ..auth import Who, need
from ..context import manager
from ..jobs import JobError
from .links import LinkIn

router = APIRouter(tags=["Jobs"])
log = logging.getLogger("songdrop")


class JobIn(LinkIn):
    options: dict | None = Field(None, description="Download options; see GET /api/v1/options. "
                                                   "Left out: the saved defaults.")
    callback_url: str | None = Field(None, max_length=2048,
                                     description="Gets the job (POST, JSON) when it ends and when a later try ends.")
    callback_secret: str | None = Field(None, min_length=16, max_length=200,
                                        description="Signs each callback: X-Songdrop-Signature is sha256=<hex "
                                                    "HMAC-SHA256 of \"<X-Songdrop-Timestamp>.<body>\">, this as "
                                                    "key. Never answered back.")

    @field_validator("callback_url")
    @classmethod
    def _http(cls, value):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("callback_url must start with http:// or https://.")
        return value or None


def _theirs(job_id: str, who: Principal) -> None:
    """The job is one who may see: their own, or anyone's with "everyone". Someone else's is "no such
    job", as one that doesn't exist: nothing is said about what others asked for."""
    if not who.can("everyone") and manager.owner(job_id) != who.owner:
        raise JobError(404, "No such job.")


def _changes(job_id: str, who: Principal) -> None:
    """Stopping, retrying, removing: who asks for downloads, on a job they may see."""
    _theirs(job_id, who)
    if not who.can("download"):
        raise JobError(403, f"{who.name} may not ask for downloads, nor stop, retry or remove them.")


# Clip times and chapters: what options.py's validators say, for people.
_OPTION_MESSAGES = (("not a time", "Write clip times like 1:23 or 1:02:03."),
                    ("ends before", "The clip ends before it starts."),
                    ("not both", "Choose one song per chapter or a clip, not both."))


@router.get("/jobs", summary="Every job, newest first: one's own, or everyone's with the permission",
            response_model=schemas.JobList, responses={304: {"description": "The caller's copy (If-None-Match) is current."}})
def list_jobs(request: Request, who: Who):
    """Answers an ETag: the page polls every second or two while something
    runs, and gets a bodyless 304 while nothing changed."""
    mine = None if who.can("everyone") else who.owner
    tag = 'W/"' + hashlib.blake2b(f"{__version__}|{manager.version()}|{mine}".encode(), digest_size=10).hexdigest() + '"'
    if tag in (t.strip() for t in request.headers.get("if-none-match", "").split(",")):
        return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-cache"})
    body = schemas.JobList.model_validate({"jobs": manager.list(mine), "retention_hours": manager.settings.retention_hours})
    return Response(body.model_dump_json(), media_type="application/json", headers={"ETag": tag, "Cache-Control": "no-cache"})


@router.post("/jobs", status_code=201, summary="Download a link", response_model=schemas.Job,
             responses={400: {"model": schemas.Problem}, 403: {"model": schemas.Problem}, 422: {"model": schemas.Problem}})
def create_job(body: JobIn, who=need("download")):
    """Queues the link and answers at once with the job. 400: a link songdrop
    can't take; 403: a callback asked without an API key; 422: an option
    that isn't valid."""
    if body.callback_url and who.kind != "key":
        # The server would post to any address: only an app (its key, an admin's choice) may ask that.
        raise JobError(403, "Callbacks are for apps: ask with an API key (Settings → API keys).")
    try:
        detection = detect.detect(body.url)
    except detect.UnsupportedLink as exc:
        raise JobError(400, str(exc)) from exc
    try:
        chosen = options.with_defaults(detection.engine, manager.settings.defaults, body.options)
    except ValidationError as exc:
        text = problems.explain(exc.errors())
        message = next((m for clue, m in _OPTION_MESSAGES if clue in text), "One of the options isn't valid.")
        raise JobError(422, message) from exc
    job = manager.submit(detection, chosen, body.callback_url, owner=who.owner, owner_name=who.name,
                         callback_secret=body.callback_secret)
    log.info("%s asked for %s (job %s)", who.name, detection.url, job["id"])
    return job


@router.get("/jobs/{job_id}", summary="One job", response_model=schemas.Job,
            responses={404: {"model": schemas.Problem}})
def get_job(job_id: str, who: Who):
    _theirs(job_id, who)
    return manager.get(job_id)


@router.post("/jobs/{job_id}/cancel", summary="Stop a queued or running job", response_model=schemas.Job)
def cancel_job(job_id: str, who: Who):
    """A queued job is cancelled at once; a running one stops within seconds,
    keeping the songs it finished."""
    _changes(job_id, who)
    return manager.cancel(job_id)


@router.post("/jobs/{job_id}/errors/{index}/retry", summary="Try a missing song again now",
             response_model=schemas.Job)
def retry_song(job_id: str, index: int, who: Who):
    """`index` is the missing song's `index` in `errors`. Works in any state,
    also after songdrop gave up. 404: the song has no link of its own; 409:
    the job is running."""
    _changes(job_id, who)
    return manager.retry(job_id, index)


@router.delete("/jobs/{job_id}", status_code=204, summary="Remove a finished job and its files")
def delete_job(job_id: str, who: Who):
    """Copies already in the output folder stay. 409 while it runs: cancel it first."""
    _changes(job_id, who)
    manager.delete(job_id)
    return Response(status_code=204)


@router.get("/jobs/{job_id}/files/{file_id}", tags=["Files"], summary="Download one song",
            response_class=FileResponse)
def download_file(job_id: str, file_id: str, who: Who):
    """410 once the job has expired."""
    _theirs(job_id, who)
    path, name = manager.file(job_id, file_id)
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.get("/jobs/{job_id}/archive", tags=["Files"], summary="Download a job's songs as one zip",
            response_class=FileResponse)
def download_archive(job_id: str, who: Who):
    """409 while the job runs."""
    _theirs(job_id, who)
    path, name = manager.archive(job_id)
    return FileResponse(path, filename=name, media_type="application/zip")
