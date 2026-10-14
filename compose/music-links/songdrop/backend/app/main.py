"""songdrop's HTTP API (/api/v1), its docs (/docs) and its page.

LAN-only behind Caddy, without a login, like MeTube. The page is a client of
the API like any other; docs/api.md describes every route.
"""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import config, cookies, detect, options, preferences
from .jobs import JobError, JobManager

log = logging.getLogger("songdrop")

base_settings = config.load()
manager = JobManager(preferences.apply(base_settings, preferences.stored(base_settings.state_dir)))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    manager.start()
    # The first detection compiles ~1,800 extractor patterns; do it now.
    threading.Thread(target=detect.warm_up, name="warm-up", daemon=True).start()
    yield
    manager.stop()


app = FastAPI(
    title="songdrop",
    version="1",
    summary="Turns a link into audio files.",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)
api = APIRouter(prefix="/api/v1")


class LinkIn(BaseModel):
    url: str = Field(max_length=detect.MAX_URL_LENGTH, description="The link to download.")


class JobIn(LinkIn):
    options: dict | None = Field(None, description="Download options; see GET /api/v1/options. "
                                                   "Left out: the saved defaults.")
    callback_url: str | None = Field(None, max_length=2048,
                                     description="Gets the job (POST, JSON) when it ends and when a later try ends.")

    @field_validator("callback_url")
    @classmethod
    def _http(cls, value):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("callback_url must start with http:// or https://")
        return value or None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


@app.exception_handler(JobError)
async def job_error(_request: Request, exc: JobError):
    return _error(exc.status, exc.message)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    for error in exc.errors():
        if "callback_url" in error.get("loc", ()):
            return _error(422, "callback_url must start with http:// or https://.")
    return _error(422, "That request wasn't understood.")


@app.exception_handler(Exception)
async def unexpected(_request: Request, exc: Exception):
    log.exception("unhandled error", exc_info=exc)
    return _error(500, "Something went wrong on the server.")


# ---- links and options ------------------------------------------------------

@api.get("/health", tags=["service"], summary="Whether songdrop is up")
def health():
    return {"ok": True}


@api.get("/options", tags=["links"], summary="The download options of each engine, with their defaults")
def get_options():
    return options.describe(manager.settings.defaults)


@api.post("/detect", tags=["links"], summary="Which engine would take a link, without downloading")
def post_detect(body: LinkIn):
    try:
        return {"supported": True, **detect.detect(body.url).as_dict()}
    except detect.UnsupportedLink as exc:
        return {"supported": False, "message": str(exc)}


# ---- jobs ------------------------------------------------------------------------

@api.get("/jobs", tags=["jobs"], summary="Every job, newest first")
def list_jobs():
    return {"jobs": manager.list(), "retention_hours": manager.settings.retention_hours}


@api.post("/jobs", status_code=201, tags=["jobs"], summary="Download a link")
def create_job(body: JobIn):
    try:
        detection = detect.detect(body.url)
    except detect.UnsupportedLink as exc:
        return _error(400, str(exc))
    try:
        chosen = options.with_defaults(detection.engine, manager.settings.defaults, body.options)
    except ValidationError as exc:
        text = " ".join(str(error.get("msg")) for error in exc.errors())
        for clue, message in (("not a time", "Write clip times like 1:23 or 1:02:03."),
                              ("ends before", "The clip ends before it starts."),
                              ("not both", "Choose one song per chapter or a clip, not both.")):
            if clue in text:
                return _error(422, message)
        return _error(422, "One of the options isn't valid.")
    return manager.submit(detection, chosen, body.callback_url)


@api.get("/jobs/{job_id}", tags=["jobs"], summary="One job")
def get_job(job_id: str):
    return manager.get(job_id)


@api.post("/jobs/{job_id}/cancel", tags=["jobs"], summary="Stop a queued or running job")
def cancel_job(job_id: str):
    return manager.cancel(job_id)


@api.post("/jobs/{job_id}/errors/{index}/retry", tags=["jobs"], summary="Try a missing song again now")
def retry_song(job_id: str, index: int):
    return manager.retry(job_id, index)


@api.delete("/jobs/{job_id}", status_code=204, tags=["jobs"], summary="Remove a finished job and its files")
def delete_job(job_id: str):
    manager.delete(job_id)
    return Response(status_code=204)


@api.get("/jobs/{job_id}/files/{file_id}", tags=["files"], summary="Download one song")
def download_file(job_id: str, file_id: str):
    path, name = manager.file(job_id, file_id)
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@api.get("/jobs/{job_id}/archive", tags=["files"], summary="Download a job's songs as one zip")
def download_archive(job_id: str):
    path, name = manager.archive(job_id)
    return FileResponse(path, filename=name, media_type="application/zip")


# ---- settings --------------------------------------------------------------------

@api.get("/settings", tags=["settings"], summary="The current settings")
def get_settings():
    return preferences.public(manager.settings)


@api.patch("/settings", tags=["settings"], summary="Change some settings; the others keep their value")
def patch_settings(body: dict):
    try:
        manager.settings = preferences.update(base_settings, base_settings.state_dir, body)
    except preferences.Invalid as exc:
        return _error(422, str(exc))
    log.info("settings changed: %s", ", ".join(sorted(key for key in body if key != "discord_webhook"))
             + (" (and the Discord webhook)" if "discord_webhook" in body else ""))
    return preferences.public(manager.settings)


@api.get("/cookies", tags=["settings"], summary="Whether YouTube cookies are saved, and until when")
def cookie_status():
    return cookies.status(base_settings.state_dir)


@api.put("/cookies", tags=["settings"], summary="Save a cookies.txt (the request body is the file)")
async def put_cookies(request: Request):
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > cookies.MAX_BYTES:
            return _error(413, "That file is too big for a cookies.txt.")
    try:
        return cookies.save(base_settings.state_dir, data)
    except cookies.BadCookies as exc:
        return _error(400, str(exc))


@api.delete("/cookies", status_code=204, tags=["settings"], summary="Remove the saved cookies")
def delete_cookies():
    cookies.delete(base_settings.state_dir)
    return Response(status_code=204)


app.include_router(api)


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str):
    return _error(404, "Not found. The API is under /api/v1/ (see /docs/api).")


# ---- docs and the page -------------------------------------------------------------

static = base_settings.static_dir
# The page: hashed assets are cached for good, index.html never.
if (static / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")


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


@app.get("/docs", include_in_schema=False)
def docs_home():
    return RedirectResponse("/docs/")


@app.get("/docs/{page:path}", include_in_schema=False)
def docs(page: str):
    """The docs, built from docs/*.md: /docs/ and /docs/api."""
    folder = (static / "docs").resolve()
    name = (page.strip("/") or "index").removesuffix(".html")
    candidate = (folder / f"{name}.html").resolve()
    if folder in candidate.parents and candidate.is_file():
        return FileResponse(candidate, media_type="text/html")
    asset = (folder / page).resolve()
    if folder in asset.parents and asset.is_file():
        return FileResponse(asset)
    return _error(404, "No such page in the docs.")


@app.get("/{rest:path}", include_in_schema=False)
def page(rest: str):
    candidate = (static / rest).resolve() if rest else None
    if candidate and static.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    index = static / "index.html"
    if not index.is_file():
        return _error(503, "The page isn't built.")
    return FileResponse(index, media_type="text/html")
