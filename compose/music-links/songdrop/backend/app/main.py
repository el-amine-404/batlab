"""songdrop's web server: the API (/api/v1, routes/), the docs (/docs) and
the page (everything else).

LAN-only behind Caddy, without a login, like MeTube. The page is a client of
the API like any other.
"""

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import connections, cookies, detect, folders, preferences, problems
from .context import base_settings, manager
from .routes import ROUTERS

log = logging.getLogger("songdrop")

DESCRIPTION = """\
Turns a link into audio files. Start a download with `POST /api/v1/jobs`, then
poll `GET /api/v1/jobs/{id}` or pass a `callback_url`.

Errors answer a 4xx or 5xx status with `{"detail": "<sentence>"}`. Times are
Unix seconds. The guide: [/docs](/docs/).
"""


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
    description=DESCRIPTION,
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

api = APIRouter(prefix="/api/v1")
for router in ROUTERS:
    api.include_router(router)
app.include_router(api)


# ---- errors: {"detail": "<sentence>"} ----------------------------------------------

def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


# Exceptions whose message is meant for the user, and their status.
_STATUS: dict[type[Exception], int] = {
    preferences.Invalid: 422,
    connections.Invalid: 422,
    connections.NotFound: 404,
    folders.Outside: 400,
    cookies.BadCookies: 400,
}


@app.exception_handler(problems.Problem)
async def problem(_request: Request, exc: problems.Problem):
    return _error(exc.status, exc.message)


for _kind, _status in _STATUS.items():
    app.add_exception_handler(_kind, lambda _request, exc, status=_status: _error(status, str(exc)))


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError):
    return _error(422, problems.explain(exc.errors()))


@app.exception_handler(Exception)
async def unexpected(_request: Request, exc: Exception):
    log.exception("unhandled error", exc_info=exc)
    return _error(500, "Something went wrong on the server.")


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str):
    return _error(404, "Not found. The API is under /api/v1/ (see /docs/api/).")


# ---- the docs and the page -----------------------------------------------------------

static = base_settings.static_dir


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if "/assets/" in path and response.status_code == 200:
        # Hashed names: a new build has new names.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _file_in(folder: Path, rel: str) -> Path | None:
    """folder/rel, folder/rel.html or folder/rel/index.html, never outside folder."""
    folder = folder.resolve()
    rel = rel.strip("/")
    for candidate in ((folder / rel) if rel else None, folder / f"{rel}.html", folder / rel / "index.html"):
        if candidate is None:
            continue
        candidate = candidate.resolve()
        if (candidate == folder or folder in candidate.parents) and candidate.is_file():
            return candidate
    return None


@app.get("/docs", include_in_schema=False)
def docs_home():
    return RedirectResponse("/docs/")


@app.get("/docs/{rest:path}", include_in_schema=False)
def docs(rest: str):
    """The docs site (docs/, built with VitePress) and its API reference (/docs/api/)."""
    found = _file_in(static / "docs", rest)
    if found:
        return FileResponse(found)
    missing = static / "docs" / "404.html"
    if missing.is_file():
        return FileResponse(missing, status_code=404, media_type="text/html")
    return _error(404, "No such page in the docs.")


if (static / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")


@app.get("/{rest:path}", include_in_schema=False)
def page(rest: str):
    """A file of the page, else the page itself: it has its own routes
    (/activity/queue, /settings/general…)."""
    found = _file_in(static, rest) if rest else None
    if found and found.name != "index.html":
        return FileResponse(found)
    index = static / "index.html"
    if not index.is_file():
        return _error(503, "The page isn't built.")
    return FileResponse(index, media_type="text/html")
