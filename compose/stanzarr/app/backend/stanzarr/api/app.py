"""stanzarr's web server: the API (/api/v1, routes/), its reference (/docs)
and the page (everything else).

LAN-only behind Caddy. Everyone who uses it is known: a person signed in on
the page (its session cookie) or an app with its API key (api/auth.py); the
page is a client of the API like any other. Errors answer
{"detail": "<sentence>"}, whatever went wrong."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from batlab_common.web import BodyLimit, api_not_found, set_threads
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, problems
from ..config import Config
from ..domain import Conflict, Invalid, NotFound
from ..operations import ReadOnly
from ..services import Services, build
from .auth import RequestLog, gate, principal
from .routes import PUBLIC, ROUTERS, WORKERS
from .routes.workers import MAX_UPLOAD

log = logging.getLogger("stanzarr")

DESCRIPTION = """\
Lyrics for the music libraries: every song's lyrics tracked, what is missing
asked of the sources (add-ons: LRCLIB first), plain lyrics turned into synced
ones by AI on a GPU worker, and an editor to fix the timings.

**For other apps**: `GET /api/v1/lyrics?artist=…&title=…&duration=…` gives
any song's lyrics, synced when there are some: the library's when the song is
there, else the sources'.

Who asks is known: a person signed in on the page, or an app with its API key
(`X-Api-Key`, made in Settings → API keys). Reading needs nothing more;
editing, queuing AI syncs and changing settings need the permission. The GPU
workers speak `/api/v1/worker/…` with the workers' key (`X-Stanzarr-Key`).
Errors answer `{"detail": "<sentence>"}`. Times are Unix seconds.
"""

TAGS = [
    {"name": "Library", "description": "The songs, their lyrics files, the editor, the audio."},
    {"name": "Lyrics", "description": "Any song's lyrics, for other apps."},
    {"name": "AI sync", "description": "The queue of AI syncs and the GPU workers."},
    {"name": "Workers' protocol", "description": "What a GPU worker speaks, with the workers' key."},
    {"name": "Sources", "description": "Where lyrics are asked for: add-ons, first to last."},
    {"name": "Notifications", "description": "Where Stanzarr's events go: AI syncs done or failed, lyrics found."},
    {"name": "Settings", "description": "What Settings, General changes."},
    {"name": "System", "description": "Health, status and the activity."},
    {"name": "Account", "description": "Signing in, and one's own account."},
    {"name": "People and keys", "description": "Who may use Stanzarr, and the apps' API keys: the admins'."},
]

# The page loads only its own files; the editor plays the song and draws on canvases (blob:).
# Find lyrics' song choice shows Deezer's album covers.
PAGE_POLICY = ("default-src 'self'; img-src 'self' data: blob: https://*.dzcdn.net; media-src 'self' blob:; style-src 'self' "
               "'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
               "form-action 'self'; object-src 'none'")

API_DOCS = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>stanzarr API</title><link rel="icon" href="/favicon.svg"></head>
<body><script id="api-reference" data-url="/api/v1/openapi.json"
data-configuration='{"theme":"deepSpace","darkMode":true,"hideDownloadButton":false,"metaData":{"title":"stanzarr API"}}'>
</script><script src="/api-docs/scalar.js"></script></body></html>"""


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


def _file_in(folder: Path, rel: str) -> Path | None:
    """folder/rel, never outside folder."""
    folder = folder.resolve()
    candidate = (folder / rel.strip("/")).resolve()
    return candidate if folder in candidate.parents and candidate.is_file() else None


def create_app(services: Services, run_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        set_threads("stanzarr")
        await services.start(run_background)
        yield
        await services.stop()

    app = FastAPI(title="stanzarr", version=__version__, description=DESCRIPTION, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json", openapi_tags=TAGS, lifespan=lifespan)
    app.state.services = services
    # People and keys (api/auth.py): their refusals answered, the accounts the routes use.
    gate.install(app, services.accounts)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(BodyLimit, limit=MAX_UPLOAD)
    app.add_middleware(RequestLog, name="stanzarr")

    api = APIRouter(prefix="/api/v1")
    for router in PUBLIC + WORKERS:
        api.include_router(router)
    for router in ROUTERS:
        api.include_router(router, dependencies=[Depends(principal)])
    app.include_router(api)
    # The workers before 1.0 spoke /api/worker/…: they go on working until updated.
    for router in WORKERS:
        app.include_router(router, prefix="/api", include_in_schema=False)

    # ---- errors: {"detail": "<sentence>"} ------------------------------------------

    for kind, status in ((NotFound, 404), (Conflict, 409), (Invalid, 422), (ReadOnly, 409)):
        app.add_exception_handler(kind, lambda _request, exc, status=status: _error(status, str(exc)))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        return _error(422, problems.explain(exc.errors()))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        reference = request.scope.get("state", {}).get("request_id", "?")
        log.error("unhandled error in %s %s [%s]", request.method, request.url.path, reference, exc_info=exc)
        return _error(500, f"Something went wrong on the server (reference {reference}: stanzarr's log says what).")

    # ---- headers ------------------------------------------------------------------------

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/") and response.status_code == 200:
            # Hashed names: a new build has new names.
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
        if path not in ("/docs", "/api/docs"):
            # The reference runs Scalar, which styles and fetches as it likes; the rest is held to the policy.
            response.headers["Content-Security-Policy"] = PAGE_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # ---- the API reference, and the page -------------------------------------------------

    static = Path(services.config.static_dir)

    @app.get("/docs", include_in_schema=False)
    def api_docs():
        return HTMLResponse(API_DOCS)

    @app.get("/api/docs", include_in_schema=False)
    @app.get("/api", include_in_schema=False)
    def api_home():
        return RedirectResponse("/docs")

    api_not_found(app, docs="/docs")

    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/{rest:path}", include_in_schema=False)
    def page(rest: str):
        """A file of the page, else the page itself: it has its own routes
        (/songs/…, /settings/…)."""
        found = _file_in(static, rest) if rest else None
        if found and found.name != "index.html":
            return FileResponse(found)
        index = static / "index.html"
        if not index.is_file():
            return _error(503, "The page isn't built.")
        return FileResponse(index, media_type="text/html")

    return app


def create_default() -> FastAPI:
    """The app python -m stanzarr serves (uvicorn's factory)."""
    return create_app(build(Config()))
