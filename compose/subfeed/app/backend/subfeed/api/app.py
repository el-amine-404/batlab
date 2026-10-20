"""subfeed's web server: the API (/api/v1, routes/), its reference (/docs)
and the page (everything else).

LAN-only behind Caddy. Everyone who uses it is known: a person signed in on
the page (its session cookie) or an app with its API key (api/auth.py); the
page is a client of the API like any other. Errors answer
{"detail": "<sentence>"}, whatever went wrong."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, problems
from ..config import load
from ..domain import Conflict, Invalid, NotFound
from ..services import Services, build
from .auth import RequestLog, gate, principal
from .routes import OWN_CHECK, PUBLIC, ROUTERS

log = logging.getLogger("subfeed")

DESCRIPTION = """\
What communities post (subreddits today), as feeds a program can read. Make a
feed (`POST /api/v1/feeds {"target": "SexyMusicVideos"}`), then read it as
**JSON Feed** (`GET /api/v1/feeds/{slug}/feed.json`), **Atom**
(`…/feed.atom`) or the API's own JSON (`…/posts`), newest first.

Every post comes out unless asked otherwise; narrow with the parameters and
repeat one for several values, e.g. `…/feed.json?video=youtube&unique=true`:
the posts carrying a YouTube video, one post a video. subfeed says what a post
links to and which videos it carries; what they are is for its readers to
judge.

Who asks is known: a person signed in on the page, or an app with its API
key (`X-Api-Key`, made in Settings → API keys; a feed reader may put it in
the feed's address, `?key=…`). Reading needs nothing more; changing feeds,
settings and notifications needs the permission.

Reads answer an `ETag`: send it back as `If-None-Match` and an unchanged
answer is a bodyless `304`. Errors answer `{"detail": "<sentence>"}`. Times
are Unix seconds, except JSON Feed's and Atom's own (RFC 3339).
"""

TAGS = [
    {"name": "Feeds", "description": "The communities read, how often, and how their last read went."},
    {"name": "Posts", "description": "What the feeds found, newest first: as JSON, JSON Feed or Atom."},
    {"name": "Notifications", "description": "Where subfeed's events go: a feed failing, working again, new "
                                              "posts."},
    {"name": "Settings", "description": "What Settings, General changes."},
    {"name": "System", "description": "Health and status."},
    {"name": "Account", "description": "Signing in, and one's own account."},
    {"name": "People and keys", "description": "Who may use subfeed, and the apps' API keys: the admins'."},
]

# The page loads only its own files, and YouTube's pictures of the videos.
PAGE_POLICY = ("default-src 'self'; img-src 'self' data: https://i.ytimg.com; style-src 'self' 'unsafe-inline'; "
               "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
               "form-action 'self'; object-src 'none'")

API_DOCS = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>subfeed API</title><link rel="icon" href="/favicon.svg"></head>
<body><script id="api-reference" data-url="/api/v1/openapi.json"
data-configuration='{"theme":"saturn","darkMode":true,"hideDownloadButton":false,"metaData":{"title":"subfeed API"}}'>
</script><script src="/api-docs/scalar.js"></script></body></html>"""


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)


def _file_in(folder: Path, rel: str) -> Path | None:
    """folder/rel, never outside folder."""
    folder = folder.resolve()
    candidate = (folder / rel.strip("/")).resolve()
    return candidate if folder in candidate.parents and candidate.is_file() else None


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await services.start()
        yield
        await services.stop()

    app = FastAPI(title="subfeed", version=__version__, description=DESCRIPTION, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json", openapi_tags=TAGS, lifespan=lifespan)
    app.state.services = services
    # People and keys (api/auth.py): their refusals answered, the accounts the routes use.
    gate.install(app, services.accounts)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestLog, name="subfeed")

    api = APIRouter(prefix="/api/v1")
    for router in PUBLIC + OWN_CHECK:
        api.include_router(router)
    for router in ROUTERS:
        api.include_router(router, dependencies=[Depends(principal)])
    app.include_router(api)

    # ---- errors: {"detail": "<sentence>"} ------------------------------------------

    for kind, status in ((NotFound, 404), (Conflict, 409), (Invalid, 422)):
        app.add_exception_handler(kind, lambda _request, exc, status=status: _error(status, str(exc)))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError):
        return _error(422, problems.explain(exc.errors()))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        reference = request.scope.get("state", {}).get("request_id", "?")
        log.error("unhandled error in %s %s [%s]", request.method, request.url.path, reference, exc_info=exc)
        return _error(500, f"Something went wrong on the server (reference {reference}: subfeed's log says what).")

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
        (/feeds/…, /settings/…)."""
        found = _file_in(static, rest) if rest else None
        if found and found.name != "index.html":
            return FileResponse(found)
        index = static / "index.html"
        if not index.is_file():
            return _error(503, "The page isn't built.")
        return FileResponse(index, media_type="text/html")

    return app


def create_default() -> FastAPI:
    """The app python -m subfeed serves (uvicorn's factory)."""
    return create_app(build(load()))
