"""songdrop's web server: the API (/api/v1, routes/), its reference (/docs) and
the page (everything else).

LAN-only behind Caddy. Everyone who uses it is known: a person signed in on
the page (its session cookie) or an app with its API key (auth.py); the page
is a client of the API like any other.
"""

import logging
import mimetypes
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from batlab_common.web import BodyLimit
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from . import __version__, connections, cookies, detect, folders, preferences, problems
from .auth import RequestLog, gate, principal
from .context import accounts, base_settings, egress, manager
from .routes import PUBLIC, ROUTERS

log = logging.getLogger("songdrop")

DESCRIPTION = """\
Turns a link into audio files. Start a download with `POST /api/v1/jobs`, then
poll `GET /api/v1/jobs/{id}` or pass a `callback_url`.

Who asks is known: a person signed in on the page, or an app with its API key
(`X-Api-Key`, made in Settings → API keys). Each sees and changes their own
downloads, unless allowed everyone's; only an app's key may ask for a
`callback_url`.

Errors answer a 4xx or 5xx status with `{"detail": "<sentence>"}`. Times are
Unix seconds. The guide: [songdrop's docs](https://el-amine-404.github.io/batlab/songdrop/guide/api).
"""


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # The yt-dlp downloads connect through it (batlab_common/egress.py): the internet, not the home network.
    manager.proxy = egress.start()
    accounts.announce(log)
    manager.start()
    # The first detection compiles ~1,800 extractor patterns; do it now.
    threading.Thread(target=detect.warm_up, name="warm-up", daemon=True).start()
    yield
    manager.stop()
    egress.stop()


app = FastAPI(
    title="songdrop",
    version=__version__,
    description=DESCRIPTION,
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json",
    openapi_tags=[
        {"name": "Jobs", "description": "A job is one link being downloaded: its songs, the missing ones, and their later tries."},
        {"name": "Files", "description": "The songs of a job, while it is kept."},
        {"name": "Links", "description": "What songdrop makes of a link, and the download options."},
        {"name": "Settings", "description": "What the Settings screens change. Secrets are write-only."},
        {"name": "Folders", "description": "The folders under the output root, for choosing the output folder."},
        {"name": "Connections", "description": "Where events go (Settings, Connect): one connection per destination."},
        {"name": "System", "description": "Tool versions, disk space, jobs at a glance."},
        {"name": "Service", "description": "Health."},
        {"name": "Account", "description": "Signing in, and one's own account."},
        {"name": "People and keys", "description": "Who may use songdrop, and the apps' API keys: the admins'."},
    ],
    lifespan=lifespan,
)

# People and keys (auth.py): their refusals answered, the accounts the routes use.
gate.install(app, accounts)
api = APIRouter(prefix="/api/v1")
for router in PUBLIC:
    api.include_router(router)
for router in ROUTERS:
    api.include_router(router, dependencies=[Depends(principal)])
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
async def unexpected(request: Request, exc: Exception):
    reference = request.scope.get("state", {}).get("request_id", "?")
    log.error("unhandled error in %s %s [%s]", request.method, request.url.path, reference, exc_info=exc)
    return _error(500, f"Something went wrong on the server (reference {reference}: songdrop's log says what).")


# ---- the API reference and the page ------------------------------------------------

static = base_settings.static_dir


# The page's manifest (its install and Share menu entry): browsers want its own type.
mimetypes.add_type("application/manifest+json", ".webmanifest")

# The page loads only its own files. The API reference (Scalar) styles and fetches as it likes.
PAGE_POLICY = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


class Headers:
    """Every answer's caching and security headers, set as it starts (pure ASGI)."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self._app(scope, receive, send)
        path = scope.get("path", "")

        async def start(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "/assets/" in path and message["status"] == 200:
                    # Hashed names: a new build has new names.
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                elif not path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-cache")
                if path != "/docs" and not path.startswith("/api/"):
                    headers["Content-Security-Policy"] = PAGE_POLICY
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
            await send(message)

        await self._app(scope, receive, start)


class CompressSome:
    """gzip for the API's JSON and the page; never a song or a zip
    (audio doesn't compress, and a download wants its length)."""

    def __init__(self, app):
        self._plain = app
        self._gzip = GZipMiddleware(app, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        plain = scope["type"] != "http" or "/files/" in path or path.endswith("/archive")
        await (self._plain if plain else self._gzip)(scope, receive, send)


app.add_middleware(CompressSome)
app.add_middleware(Headers)
app.add_middleware(BodyLimit)
app.add_middleware(RequestLog, name="songdrop")


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


# The API reference: Scalar, from the page's own files (frontend/scripts/copy-scalar.mjs), on
# the API's description as it runs. The guide is a site of its own (docs/, on GitHub Pages).
API_DOCS = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>songdrop API</title><link rel="icon" href="/favicon.svg"></head>
<body><script id="api-reference" data-url="/api/v1/openapi.json"
data-configuration='{"theme":"purple","darkMode":true,"hideDownloadButton":false,"metaData":{"title":"songdrop API"}}'>
</script><script src="/api-docs/scalar.js"></script></body></html>"""


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
    (/activity/queue, /settings/general…)."""
    found = _file_in(static, rest) if rest else None
    if found and found.name != "index.html":
        return FileResponse(found)
    index = static / "index.html"
    if not index.is_file():
        return _error(503, "The page isn't built.")
    return FileResponse(index, media_type="text/html")
