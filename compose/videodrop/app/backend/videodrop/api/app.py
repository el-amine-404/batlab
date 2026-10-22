"""videodrop's web server: the API (/api/v1, routes/), its reference (/docs)
and the page (everything else).

LAN-only behind Caddy, without a login, like songdrop and clipseerr
(docs/services.md, "The API contract"). The page is a client of the API like
any other. Errors answer {"detail": "<sentence>"}, whatever went wrong."""

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

from batlab_common.web import BodyLimit
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from .. import __version__, problems
from ..config import load
from ..domain import Conflict, Forbidden, Gone, Invalid, NotFound
from ..services import Services, build
from .auth import RequestLog, gate, principal
from .routes import PUBLIC, ROUTERS

log = logging.getLogger("videodrop")

DESCRIPTION = """\
Getting videos, for whoever asks. Ask for a download
(`POST /api/v1/jobs {"url": "https://www.youtube.com/watch?v=…", "destination": "music-videos"}`)
with the options you want; videodrop downloads it with yt-dlp, a few at once,
and its files go to the destination's folder, or are held for you to fetch
when you name none.

Follow a download however suits you: read it (`GET /api/v1/jobs/{id}`),
stream it live (`GET /api/v1/jobs/{id}/events`, Server-Sent Events), or give
a `callback_url` to be told as it goes, signed with your `callback_secret`.
Its files' `download_url`s give them back, Range requests included. Asking
twice for the same download while it runs answers the same job.

Destinations (`/api/v1/destinations`) are named folders with a file-name
template and options of their own: callers name one, never a path. Options
come from the job, then its destination, then the settings.

Errors answer `{"detail": "<sentence>"}`. Times are Unix seconds. A held
file asked for after its job's hold (`files[].expires_at`) answers 410 Gone,
with `again`: POST there and the same job downloads it again. Each answer
carries an `X-Request-ID` (yours, when you send one); videodrop's log names
it beside each change and each error, and a 500 says it.
"""

TAGS = [
    {"name": "Jobs", "description": "The downloads: ask for one, follow it (live, or by callback), stop it, try "
                                     "it again, fetch its files."},
    {"name": "Links", "description": "Before asking: whether a link is taken, and what it would download."},
    {"name": "Destinations", "description": "Where downloads go, how their files are named there, and the options "
                                             "they take."},
    {"name": "Notifications", "description": "Where videodrop tells people: a download failing, done, the disk "
                                              "nearly full."},
    {"name": "Settings", "description": "What Settings, General and Cookies change."},
    {"name": "System", "description": "Health and status."},
]

# The page loads only its own files, and the videos' pictures from their sites.
PAGE_POLICY = ("default-src 'self'; img-src 'self' data: https:; media-src 'self'; style-src 'self' 'unsafe-inline'; "
               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
               "form-action 'self'; object-src 'none'")

API_DOCS = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>videodrop API</title><link rel="icon" href="/favicon.svg"></head>
<body><script id="api-reference" data-url="/api/v1/openapi.json"
data-configuration='{"theme":"saturn","darkMode":true,"hideDownloadButton":false,"metaData":{"title":"videodrop API"}}'>
</script><script src="/api-docs/scalar.js"></script></body></html>"""


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


def _file_in(folder: Path, rel: str) -> Path | None:
    """folder/rel, never outside folder."""
    folder = folder.resolve()
    candidate = (folder / rel.strip("/")).resolve()
    return candidate if folder in candidate.parents and candidate.is_file() else None


class CompressSome:
    """gzip for the API's JSON and the page; never a live stream (it would be
    held back) nor a video file (already compressed; Range requests need it
    as it is)."""

    def __init__(self, app):
        self._plain = app
        self._gzip = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        plain = path.endswith("/events") or "/files/" in path
        await (self._plain if plain or scope["type"] != "http" else self._gzip)(scope, receive, send)


# The page's manifest (its install and Share menu entry): browsers want its own type.
mimetypes.add_type("application/manifest+json", ".webmanifest")


class Headers:
    """Every answer's caching and security headers, set as it starts (pure
    ASGI: a live stream passes through untouched)."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        path = scope.get("path", "")

        async def start(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if path.startswith("/assets/") and message["status"] == 200:
                    # Hashed names: a new build has new names.
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                elif "/files/" in path:
                    # A file: kept by the browser, checked again each time (ETag).
                    headers.setdefault("Cache-Control", "private, no-cache")
                elif path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
                else:
                    headers.setdefault("Cache-Control", "no-cache")
                if path not in ("/docs", "/api/docs"):
                    # The reference runs Scalar, which styles and fetches as it likes; the rest is held to the policy.
                    headers["Content-Security-Policy"] = PAGE_POLICY
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        await self._app(scope, receive, start)


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await services.start()
        yield
        await services.stop()

    app = FastAPI(title="videodrop", version=__version__, description=DESCRIPTION, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json", openapi_tags=TAGS, lifespan=lifespan)
    app.state.services = services
    # People and keys (api/auth.py): their refusals answered, the accounts the routes use.
    gate.install(app, services.accounts)
    app.add_middleware(CompressSome)
    app.add_middleware(Headers)
    app.add_middleware(BodyLimit)
    app.add_middleware(RequestLog, name="videodrop")

    api = APIRouter(prefix="/api/v1")
    for router in PUBLIC:
        api.include_router(router)
    for router in ROUTERS:
        api.include_router(router, dependencies=[Depends(principal)])
    app.include_router(api)

    # ---- errors: {"detail": "<sentence>"} ------------------------------------------

    for kind, status in ((NotFound, 404), (Conflict, 409), (Invalid, 422), (Forbidden, 403)):
        app.add_exception_handler(kind, lambda _request, exc, status=status: _error(status, str(exc)))

    @app.exception_handler(Gone)
    async def gone(_request: Request, exc: Gone):
        return JSONResponse({"detail": str(exc), "again": exc.again, "removed_at": exc.removed_at}, status_code=410)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        return _error(422, problems.explain(exc.errors()))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        reference = request.scope.get("state", {}).get("request_id", "?")
        log.error("unhandled error in %s %s [%s]", request.method, request.url.path, reference, exc_info=exc)
        return _error(500, f"Something went wrong on the server (reference {reference}: videodrop's log says what).")

    # ---- the API reference, and the page -------------------------------------------------

    static = services.config.static_dir

    @app.get("/docs", include_in_schema=False)
    def api_docs():
        return HTMLResponse(API_DOCS)

    @app.get("/api/docs", include_in_schema=False)
    @app.get("/api", include_in_schema=False)
    def api_home():
        return RedirectResponse("/docs")

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(rest: str):
        return _error(404, "Not found. The API is under /api/v1/ (see /docs).")

    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{rest:path}", include_in_schema=False)
    def page(rest: str):
        """A file of the page, else the page itself: it has its own routes
        (/jobs/…, /settings/…)."""
        found = _file_in(static, rest) if rest else None
        if found and found.name != "index.html":
            return FileResponse(found)
        index = static / "index.html"
        if not index.is_file():
            return _error(503, "The page isn't built.")
        return FileResponse(index, media_type="text/html")

    return app


def create_default() -> FastAPI:
    """The app python -m videodrop serves (uvicorn's factory)."""
    return create_app(build(load()))
