"""songdrop's HTTP API and the page (docs/music.md).

LAN-only behind Caddy, without a login, like MeTube.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import config, cookies, detect, options
from .jobs import JobError, JobManager

log = logging.getLogger("songdrop")

settings = config.load()
manager = JobManager(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    manager.start()
    # The first detection compiles ~1,800 extractor patterns; do it now.
    threading.Thread(target=detect.warm_up, name="warm-up", daemon=True).start()
    yield
    manager.stop()


app = FastAPI(title="songdrop", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class LinkIn(BaseModel):
    url: str = Field(max_length=detect.MAX_URL_LENGTH)


class JobIn(LinkIn):
    options: dict | None = None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


@app.exception_handler(JobError)
async def job_error(_request: Request, exc: JobError):
    return _error(exc.status, exc.message)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    return _error(422, "That request wasn't understood.")


@app.exception_handler(Exception)
async def unexpected(_request: Request, exc: Exception):
    log.exception("unhandled error", exc_info=exc)
    return _error(500, "Something went wrong on the server.")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/options")
def get_options():
    return options.describe()


@app.post("/api/detect")
def post_detect(body: LinkIn):
    try:
        return {"supported": True, **detect.detect(body.url).as_dict()}
    except detect.UnsupportedLink as exc:
        return {"supported": False, "message": str(exc)}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": manager.list(), "retention_hours": settings.retention_hours}


@app.post("/api/jobs", status_code=201)
def create_job(body: JobIn):
    try:
        detection = detect.detect(body.url)
    except detect.UnsupportedLink as exc:
        return _error(400, str(exc))
    try:
        chosen = options.parse(detection.engine, body.options)
    except ValidationError as exc:
        text = " ".join(str(error.get("msg")) for error in exc.errors())
        for clue, message in (("not a time", "Write clip times like 1:23 or 1:02:03."),
                              ("ends before", "The clip ends before it starts."),
                              ("not both", "Choose one song per chapter or a clip, not both.")):
            if clue in text:
                return _error(422, message)
        return _error(422, "One of the options isn't valid.")
    return manager.submit(detection, chosen)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return manager.get(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return manager.cancel(job_id)


@app.post("/api/jobs/{job_id}/errors/{index}/retry")
def retry_song(job_id: str, index: int):
    return manager.retry(job_id, index)


@app.get("/api/cookies")
def cookie_status():
    return cookies.status(settings.state_dir)


@app.put("/api/cookies")
async def put_cookies(request: Request):
    """The body is the cookies.txt file itself."""
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > cookies.MAX_BYTES:
            return _error(413, "That file is too big for a cookies.txt.")
    try:
        return cookies.save(settings.state_dir, data)
    except cookies.BadCookies as exc:
        return _error(400, str(exc))


@app.delete("/api/cookies", status_code=204)
def delete_cookies():
    cookies.delete(settings.state_dir)
    return Response(status_code=204)


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    manager.delete(job_id)
    return Response(status_code=204)


@app.get("/api/jobs/{job_id}/files/{file_id}")
def download_file(job_id: str, file_id: str):
    path, name = manager.file(job_id, file_id)
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@app.get("/api/jobs/{job_id}/archive")
def download_archive(job_id: str):
    path, name = manager.archive(job_id)
    return FileResponse(path, filename=name, media_type="application/zip")


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def api_not_found(rest: str):
    return _error(404, "Not found.")


# The page: hashed assets are cached for good, index.html never.
if (settings.static_dir / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/{rest:path}", include_in_schema=False)
def page(rest: str):
    candidate = (settings.static_dir / rest).resolve() if rest else None
    if candidate and settings.static_dir.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    index = settings.static_dir / "index.html"
    if not index.is_file():
        return _error(503, "The page isn't built.")
    return FileResponse(index, media_type="text/html")
