from fastapi import APIRouter, Response
from fastapi.responses import FileResponse
from pydantic import Field, ValidationError, field_validator

from .. import detect, options, problems, schemas
from ..context import manager
from ..jobs import JobError
from .links import LinkIn

router = APIRouter(tags=["Jobs"])


class JobIn(LinkIn):
    options: dict | None = Field(None, description="Download options; see GET /api/v1/options. "
                                                   "Left out: the saved defaults.")
    callback_url: str | None = Field(None, max_length=2048,
                                     description="Gets the job (POST, JSON) when it ends and when a later try ends.")

    @field_validator("callback_url")
    @classmethod
    def _http(cls, value):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("callback_url must start with http:// or https://.")
        return value or None


# Clip times and chapters: what options.py's validators say, for people.
_OPTION_MESSAGES = (("not a time", "Write clip times like 1:23 or 1:02:03."),
                    ("ends before", "The clip ends before it starts."),
                    ("not both", "Choose one song per chapter or a clip, not both."))


@router.get("/jobs", summary="Every job, newest first", response_model=schemas.JobList)
def list_jobs():
    return {"jobs": manager.list(), "retention_hours": manager.settings.retention_hours}


@router.post("/jobs", status_code=201, summary="Download a link", response_model=schemas.Job,
             responses={400: {"model": schemas.Problem}, 422: {"model": schemas.Problem}})
def create_job(body: JobIn):
    """Queues the link and answers at once with the job. 400: a link songdrop
    can't take; 422: an option that isn't valid."""
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
    return manager.submit(detection, chosen, body.callback_url)


@router.get("/jobs/{job_id}", summary="One job", response_model=schemas.Job,
            responses={404: {"model": schemas.Problem}})
def get_job(job_id: str):
    return manager.get(job_id)


@router.post("/jobs/{job_id}/cancel", summary="Stop a queued or running job", response_model=schemas.Job)
def cancel_job(job_id: str):
    """A queued job is cancelled at once; a running one stops within seconds,
    keeping the songs it finished."""
    return manager.cancel(job_id)


@router.post("/jobs/{job_id}/errors/{index}/retry", summary="Try a missing song again now",
             response_model=schemas.Job)
def retry_song(job_id: str, index: int):
    """`index` is the missing song's `index` in `errors`. Works in any state,
    also after songdrop gave up. 404: the song has no link of its own; 409:
    the job is running."""
    return manager.retry(job_id, index)


@router.delete("/jobs/{job_id}", status_code=204, summary="Remove a finished job and its files")
def delete_job(job_id: str):
    """Copies already in the output folder stay. 409 while it runs: cancel it first."""
    manager.delete(job_id)
    return Response(status_code=204)


@router.get("/jobs/{job_id}/files/{file_id}", tags=["Files"], summary="Download one song",
            response_class=FileResponse)
def download_file(job_id: str, file_id: str):
    """410 once the job has expired."""
    path, name = manager.file(job_id, file_id)
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.get("/jobs/{job_id}/archive", tags=["Files"], summary="Download a job's songs as one zip",
            response_class=FileResponse)
def download_archive(job_id: str):
    """409 while the job runs."""
    path, name = manager.archive(job_id)
    return FileResponse(path, filename=name, media_type="application/zip")
