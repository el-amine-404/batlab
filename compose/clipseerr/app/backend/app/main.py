"""clipseerr's web server: the API (/api/v1, routes/), its reference
(/api/docs) and the page (everything else).

LAN-only behind Caddy, without a login, like songdrop and Seerr's request
page. The page is a client of the API like any other.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, integrations, problems, seed
from .context import (cache, catalog, conf, db, engine, http, library, library_store, notification_store, notifier,
                      service_store)
from .routes import ROUTERS
from .tracker import store as request_store

log = logging.getLogger("clipseerr")

WARM_EVERY = 20 * 60
CACHE_KEEP = 30 * 86400

DESCRIPTION = """\
Browse the newest music videos (YouTube Music, Apple Music: no account, no
key), watch them, and ask for the song or the video: clipseerr hands the
request to the services set up in Settings (Aurral, songdrop, videodrop, any app
through a Webhook), follows it, falls back to the next service when one fails,
and tells the notifications.

Ask with `POST /api/v1/requests` `{"video_id": "yt:…", "kind": "audio"}`, then
follow `GET /api/v1/requests/{id}`. Errors answer `{"detail": "<sentence>"}`.
Times are Unix seconds.
"""


async def _housekeeping() -> None:
    """Keeps the front page warm and the cache small."""
    catalog.warm_every = WARM_EVERY
    while True:
        await catalog.warm()
        cache.prune(CACHE_KEEP)
        db.tidy()
        catalog.next_warm_at = time.time() + WARM_EVERY
        await asyncio.sleep(WARM_EVERY)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed.run(db, conf, service_store, notification_store, library_store)
    engine.start()
    library.start()
    housekeeping = asyncio.get_running_loop().create_task(_housekeeping(), name="housekeeping")
    yield
    housekeeping.cancel()
    with suppress(asyncio.CancelledError):
        await housekeeping
    await engine.stop()
    await library.stop()
    await notifier.drain()
    await http.aclose()
    db.close()


app = FastAPI(
    title="clipseerr",
    version=__version__,
    description=DESCRIPTION,
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    openapi_tags=[
        {"name": "Catalog", "description": "What there is to watch: shelves, genres, search, artists, videos."},
        {"name": "Requests", "description": "A song or a video asked for, and every service's attempt at it."},
        {"name": "Services", "description": "The apps requests go to, in order (Settings, Services)."},
        {"name": "Notifications", "description": "Where clipseerr's events go (Settings, Notifications)."},
        {"name": "Libraries", "description": "Where songs and music videos already are (Settings, Libraries): "
                                             "what they hold is marked on every video."},
        {"name": "Hot", "description": "The Hot category's own picks, and the artists it has shown."},
        {"name": "Settings", "description": "What Settings, General changes."},
        {"name": "System", "description": "Health, versions, the cache."},
        {"name": "Tags", "description": "What the badges say, and the videos that carry each."},
        {"name": "Hooks", "description": "Where services call back."},
    ],
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

api = APIRouter(prefix="/api/v1")
for router in ROUTERS:
    api.include_router(router)
app.include_router(api)


# ---- errors: {"detail": "<sentence>"} -------------------------------------------------

def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


_STATUS: dict[type[Exception], int] = {
    integrations.Invalid: 422,
    integrations.NotFound: 404,
    request_store.NotFound: 404,
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


# ---- the API reference, and the page ------------------------------------------------------

static = conf.static_dir

API_DOCS = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>clipseerr API</title><link rel="icon" href="/favicon.svg"></head>
<body><script id="api-reference" data-url="/api/v1/openapi.json"
data-configuration='{"theme":"purple","darkMode":true,"hideDownloadButton":false,"metaData":{"title":"clipseerr API"}}'>
</script><script src="/api-docs/scalar.js"></script></body></html>"""


@app.get("/api/docs", include_in_schema=False)
def api_docs():
    return HTMLResponse(API_DOCS)


@app.get("/api", include_in_schema=False)
def api_home():
    return RedirectResponse("/api/docs")


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str):
    return _error(404, "Not found. The API is under /api/v1/ (see /api/docs).")


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/") and response.status_code == 200:
        # Hashed names: a new build has new names.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif not path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    else:
        response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


def _file_in(folder: Path, rel: str) -> Path | None:
    """folder/rel, never outside folder."""
    folder = folder.resolve()
    candidate = (folder / rel.strip("/")).resolve()
    if folder in candidate.parents and candidate.is_file():
        return candidate
    return None


if (static / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")


@app.get("/{rest:path}", include_in_schema=False)
def page(rest: str):
    """A file of the page, else the page itself: it has its own routes
    (/watch/yt:…, /settings/services…)."""
    found = _file_in(static, rest) if rest else None
    if found and found.name != "index.html":
        return FileResponse(found)
    index = static / "index.html"
    if not index.is_file():
        return _error(503, "The page isn't built.")
    return FileResponse(index, media_type="text/html")
