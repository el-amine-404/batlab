from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal

from batlab_common.accounts import Principal
from batlab_common.web import BodyLimit, Gate, RequestLog, api_not_found, set_threads
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders
from starlette.requests import ClientDisconnect

from . import __version__
from .accounts import KEY_DEFAULT, PERMISSIONS, open_accounts
from .config import Config, load
from .db import Database
from .events import Broker
from .logs import RECENT
from .pagespec import parse_pages
from .security import looks_like_image
from .settings import known, validate
from .storage import event, object_path

logger = logging.getLogger("scanloom.api")

Profile = Literal["faithful", "paper", "mono"]
Appearance = Literal["auto", "color", "gray", "mono"]
Quarter = Literal[0, 90, 180, 270]

gate = Gate("scanloom", PERMISSIONS, user_default=["scan"], key_default=KEY_DEFAULT, key_for="a script")
Who = gate.Who
Scanner = Annotated[Principal, gate.need("scan")]

ACTIVE_STAGES = ("queued", "scanning", "decoding", "detecting", "rendering")
PRIORITY = {"build_export": 20, "render_page": 10, "prepare_page": 8, "process_page": 5}
MIN_FREE_BYTES = 512 * 1024 * 1024
# A PDF is built in memory: more than this of lossless pages wouldn't fit the worker's.
LOSSLESS_PDF_LIMIT = 1024 ** 3
DEFAULT_PAGE_SECONDS = 25.0
FORMS = {"thumb": "thumb_object", "preview": "preview_object", "source": "source_preview_object",
         "png": "result_object", "jpeg": "jpeg_object"}
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


class ProjectIn(BaseModel):
    title: str | None = Field(None, max_length=160)
    profile: Profile | None = None


class ProjectEdit(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=160)
    profile: Profile | None = None
    page_order: list[uuid.UUID] | None = Field(None, description="Every page of the scan, in the new order")


class PageEdit(BaseModel):
    corners: list[list[float]] | None = Field(None, description="Four [x, y] from 0 to 1: top left, top right, bottom right, bottom left")
    rotation: Quarter | None = None
    rotate_by: Quarter | None = Field(None, description="Clockwise, from the page's rotation when the request arrives")
    appearance: Appearance | None = None
    redetect: bool = Field(False, description="Forget the crop and find the page again")


class ManyPagesEdit(PageEdit):
    page_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


class ManyPages(BaseModel):
    page_ids: list[uuid.UUID] | None = Field(None, max_length=1000, description="Left out: every page this applies to")


class ExportRequest(BaseModel):
    kind: Literal["zip", "pdf"]
    pages: str | None = Field(None, max_length=2000, description="Page numbers in the order wanted: 1-3,6,7-9. A range may run backwards (9-7); a page named twice comes once.")
    page_ids: list[uuid.UUID] | None = Field(None, max_length=1000)
    quality: Literal["lossless", "balanced", "small"] = "balanced"
    filename: str | None = Field(None, max_length=160, description="Without the extension; the scan's name when left out")
    color: Literal["keep", "gray", "mono"] = "keep"
    long_edge: int | None = Field(None, ge=300, le=12000, description="Largest side of each image, in pixels; never enlarged")
    jpeg_quality: int | None = Field(None, ge=30, le=100)
    paper: Literal["fit", "a4", "a5", "letter"] = Field("fit", description="PDF: each page the size of its image, or one paper size")
    margin_mm: float = Field(0, ge=0, le=50, description="PDF, on a paper size")


class BatchExport(BaseModel):
    project_ids: list[str] = Field(min_length=1, max_length=200)
    format: Literal["pdf", "images"] = "pdf"
    quality: Literal["lossless", "balanced", "small"] = "balanced"


class SettingsEdit(BaseModel):
    values: dict
    revision: int


def clean_name(value: str | None) -> str:
    name = re.sub(r"[\x00-\x1f\x7f\\/]", "_", value or "image").strip() or "image"
    return name[:255]


def default_title() -> str:
    return datetime.now(timezone.utc).strftime("Scan %Y-%m-%d %H:%M")


def jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def page_view(public_id: str, row: dict) -> dict:
    base = f"/api/v1/projects/{public_id}/pages/{row['id']}"
    version = row["version"]
    has_result = bool(row["result_object"])

    def image(form: str, fallback: str | None = None) -> str | None:
        column = FORMS[form]
        chosen = form if row[column] else fallback
        return f"{base}/image/{chosen}?v={version}" if chosen else None

    quality = row["quality"] or {}
    answer = {
        "id": str(row["id"]), "position": row["position"], "original_name": row["original_name"],
        "status": row["status"], "error": row["error"], "version": version,
        "width": quality.get("width") or row["width"], "height": quality.get("height") or row["height"],
        "corners": row["corners"], "rotation": row["rotation"], "appearance": row["appearance"],
        "corner_radius": row["corner_radius"], "confidence": row["confidence"],
        "warnings": quality.get("warnings") or [], "timings": row["timings"] or {},
        "detection_method": quality.get("detection_method"),
        "stage_started_at": jsonable(row["stage_started_at"]),
        "source_bytes": row["source_bytes"], "result_bytes": row["result_bytes"], "jpeg_bytes": row["jpeg_bytes"],
        "reused_from": None,
        "thumb_url": image("thumb", "png" if has_result else None),
        "preview_url": image("preview", "png" if has_result else "source" if row["source_preview_object"] else None),
        "hold": row["hold"],
        "source_url": image("source"),
        "png_url": f"{base}/image/png?v={version}&download=1" if has_result else None,
        "jpeg_url": f"{base}/image/jpeg?v={version}&download=1" if row["jpeg_object"] else None,
    }
    if row.get("reused_public_id"):
        answer["reused_from"] = {"project": row["reused_public_id"], "title": row["reused_title"], "page": row["reused_position"] + 1,
                                # A scan of this photo alone: scanning it again would only make a copy of that scan.
                                "alone": row["reused_page_count"] == 1 and row["reused_public_id"] != public_id}
    return answer


PAGE_ROWS = """SELECT p.*, rpr.public_id AS reused_public_id, rpr.title AS reused_title, rp.position AS reused_position, rpr.page_count AS reused_page_count
               FROM pages p LEFT JOIN pages rp ON rp.id=p.reused_from LEFT JOIN projects rpr ON rpr.id=rp.project_id"""


class SecurityHeaders:
    POLICY = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
              "script-src 'self' 'unsafe-inline'; connect-src 'self'; font-src 'self' data:; worker-src 'self' blob:; "
              "manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def start(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Content-Security-Policy"] = self.POLICY
                if scope.get("path", "").startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)
        await self.app(scope, receive, start)


class Limits:
    """Two megabytes for every request, but for an upload, which goes straight to
    disk and has its own limit from the settings."""

    def __init__(self, app):
        self.limited = BodyLimit(app)
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST" and re.fullmatch(r"/api/v1/projects/[^/]+/pages", scope["path"]):
            return await self.app(scope, receive, send)
        return await self.limited(scope, receive, send)


def create_app(config: Config | None = None, db: Database | None = None) -> FastAPI:
    config = config or load()
    db = db or Database(config.database_url)
    config.state.mkdir(parents=True, exist_ok=True)
    accounts = open_accounts(config.state)
    broker = Broker(db.connect)

    @asynccontextmanager
    async def lifespan(_app):
        set_threads("scanloom")
        db.migrate()
        accounts.announce(logger)
        broker.start(asyncio.get_running_loop())
        yield
        broker.stop()

    app = FastAPI(title="Scanloom API", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json", lifespan=lifespan,
                  description="Photographs in, clean document pages out. Sign in on the page, or send an API key (X-Api-Key).")
    gate.install(app, accounts)
    app.add_middleware(Limits)
    app.add_middleware(RequestLog, name="scanloom")
    app.add_middleware(SecurityHeaders)

    public = APIRouter(prefix="/api/v1")
    api = APIRouter(prefix="/api/v1", dependencies=[Depends(gate.principal)])
    public.include_router(gate.public)
    api.include_router(gate.router)

    # ---- system ----------------------------------------------------------------------------

    @public.get("/health", tags=["System"])
    def health():
        db.one("SELECT 1 AS ok")
        return {"ok": True, "status": "ok", "version": __version__}

    @public.get("/system/status", tags=["System"], summary="Counts only, for Homepage")
    def status():
        row = db.one("""SELECT count(*) FILTER (WHERE status='processing') AS processing,
                              count(*) FILTER (WHERE status='ready') AS ready,
                              COALESCE(sum(page_count),0) AS pages,
                              COALESCE(sum(failed_count),0) AS failed FROM projects WHERE status<>'deleting'""")
        return dict(row)

    @api.get("/system/logs", tags=["System"], dependencies=[gate.need("settings")])
    def logs(after: int = 0, level: str | None = None, limit: int = Query(200, ge=1, le=1000)):
        return {"lines": RECENT.lines(after=after, level=level, limit=limit)}

    @api.get("/settings", tags=["Settings"])
    def get_settings():
        row = dict(db.one("SELECT revision,values,updated_at FROM settings WHERE singleton=true"))
        row["values"] = known(dict(row["values"]))
        return jsonable(row)

    @api.put("/settings", tags=["Settings"], dependencies=[gate.need("settings")])
    def put_settings(edit: SettingsEdit):
        try:
            values = validate(edit.values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        row = db.one("""UPDATE settings SET values=%s::jsonb,revision=revision+1,updated_at=now()
                        WHERE singleton=true AND revision=%s RETURNING revision,values,updated_at""",
                     (json.dumps(values), edit.revision))
        if not row:
            raise HTTPException(409, "Settings changed; reload before saving")
        return jsonable(dict(row))

    # ---- whose scans --------------------------------------------------------------------------

    def scope_of(who) -> str | None:
        """Whose scans someone may see; None: everybody's."""
        return None if who.can("everyone") else who.owner

    def find(public_id: str, who) -> dict:
        project = db.one("SELECT * FROM projects WHERE public_id=%s AND status<>'deleting'", (public_id,))
        scope = scope_of(who)
        if not project or (scope is not None and project["owner"] != scope):
            raise HTTPException(404, "That scan doesn't exist, or was deleted")
        return project

    def page_rows(project_id, *, limit: int | None = None, offset: int = 0, only: str | None = None) -> list[dict]:
        where = {None: " AND NOT p.hold", "held": " AND p.hold", "review": " AND jsonb_array_length(COALESCE(p.quality->'warnings','[]'::jsonb))>0",
                 "failed": " AND p.status IN ('failed','blocked')"}[only]
        return db.all(f"{PAGE_ROWS} WHERE p.project_id=%s{where} ORDER BY p.position LIMIT %s OFFSET %s",
                      (project_id, limit, offset))

    # ---- the pipeline, as the page shows it ---------------------------------------------------

    def page_seconds() -> tuple[bool, float]:
        """How long a page has been taking, and whether that is measured or a first guess."""
        row = db.one("""SELECT avg(seconds)::float AS average FROM (
                            SELECT EXTRACT(EPOCH FROM j.finished_at-j.started_at) AS seconds FROM jobs j
                            WHERE j.kind='process_page' AND j.status='done' AND j.finished_at-j.started_at > interval '3 seconds'
                            ORDER BY j.finished_at DESC LIMIT 30) newest""")
        return bool(row["average"]), row["average"] or DEFAULT_PAGE_SECONDS

    def progress(project: dict) -> dict | None:
        if project["status"] != "processing":
            return None
        stages = db.all("SELECT status,count(*)::int AS n FROM pages WHERE project_id=%s GROUP BY 1", (project["id"],))
        by_stage = {row["status"]: row["n"] for row in stages}
        remaining = db.one(f"SELECT count(*)::int AS n FROM pages WHERE project_id=%s AND NOT hold AND status IN {ACTIVE_STAGES}", (project["id"],))["n"]
        current = db.one("""SELECT id,position,original_name,status,stage_started_at FROM pages
                            WHERE project_id=%s AND status IN ('scanning','decoding','detecting','rendering')
                            ORDER BY position LIMIT 1""", (project["id"],))
        recent, average = page_seconds()
        # A page that is only being redone (a new look, a crop) takes moments, not a whole scan.
        redoing = db.one("""SELECT avg(seconds)::float AS average FROM (
                                SELECT EXTRACT(EPOCH FROM j.finished_at-j.started_at) AS seconds FROM jobs j
                                WHERE j.kind='render_page' AND j.status='done' ORDER BY j.finished_at DESC LIMIT 30) newest""")["average"] or 2.0
        quick = db.one(f"SELECT count(*)::int AS n FROM pages WHERE project_id=%s AND NOT hold AND status IN {ACTIVE_STAGES} AND result_object IS NOT NULL", (project["id"],))["n"]
        ahead = db.one("""SELECT count(*)::int AS n FROM jobs j WHERE j.status IN ('queued','running') AND j.page_id IS NOT NULL
                          AND j.kind IN ('process_page','render_page') AND j.project_id<>%s
                          AND j.id < COALESCE((SELECT min(id) FROM jobs WHERE project_id=%s AND status IN ('queued','running')), 0)""",
                       (project["id"], project["id"]))["n"]
        elapsed = 0.0
        if current and current["stage_started_at"]:
            started = db.one("SELECT EXTRACT(EPOCH FROM now()-started_at)::float AS s FROM jobs WHERE page_id=%s AND status='running' ORDER BY id DESC LIMIT 1",
                             (current["id"],))
            elapsed = started["s"] if started and started["s"] else 0.0
        return {"by_stage": by_stage, "remaining": remaining, "ahead": ahead,
                "average_seconds": round(average, 1), "measured": recent,
                "eta_seconds": max(1, round((remaining - quick + ahead) * average + quick * redoing - elapsed)) if remaining else None,
                "redoing": quick,
                "current": jsonable(dict(current)) if current else None}

    def project_view(project: dict) -> dict:
        answer = jsonable({key: value for key, value in project.items() if key != "id"})
        answer["progress"] = progress(project)
        answer["page_seconds"] = round(page_seconds()[1], 1)
        sizes = db.one("SELECT COALESCE(sum(result_bytes),0)::bigint AS result_bytes FROM pages WHERE project_id=%s", (project["id"],))
        answer["bytes"] = sizes["result_bytes"]
        answer["exports"] = [jsonable(dict(row)) | {"download_url": f"/api/v1/projects/{project['public_id']}/exports/{row['id']}/download" if row["status"] == "ready" else None}
                             for row in db.all("""SELECT id,kind,status,filename,bytes,error,expires_at,created_at,ready_at,page_count
                                                  FROM exports WHERE project_id=%s AND expires_at>now() ORDER BY created_at DESC LIMIT 20""", (project["id"],))]
        return answer

    # ---- scans --------------------------------------------------------------------------------

    @api.post("/projects", status_code=201, tags=["Scans"], summary="A new scan, empty until pages are added to it")
    def create_project(body: ProjectIn, who: Scanner):
        settings = db.settings()
        public_id = secrets.token_urlsafe(18).replace("-", "A").replace("_", "B")
        days = int(settings["retention_days"])
        expires = datetime.now(timezone.utc) + timedelta(days=days) if days else None
        title = (body.title or "").strip() or default_title()
        with db.transaction() as cursor:
            cursor.execute("""INSERT INTO projects(public_id,title,profile,status,expires_at,owner)
                              VALUES (%s,%s,%s,'empty',%s,%s) RETURNING *""",
                           (public_id, title, body.profile or settings["default_profile"], expires, who.owner))
            project = dict(cursor.fetchone())
            event(cursor, project, "project_created")
        logger.info("Created scan %s for %s", public_id, who.name)
        return project_view(project)

    @api.get("/projects", tags=["Scans"])
    def list_projects(who: Who, limit: int = Query(24, ge=1, le=1000), offset: int = Query(0, ge=0),
                      q: str | None = Query(None, max_length=160), status: Literal["processing", "ready", "failed", "draft"] | None = None):
        where, params = ["pr.status<>'deleting'", "(pr.page_count>0 OR pr.created_at>now()-interval '1 hour')"], []
        scope = scope_of(who)
        if scope is not None:
            where.append("pr.owner=%s")
            params.append(scope)
        if q and q.strip():
            where.append("pr.title ILIKE %s")
            params.append("%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if status == "failed":
            where.append("pr.failed_count>0")
        elif status:
            where.append("pr.status=%s")
            params.append(status)
        condition = " AND ".join(where)
        total = db.one(f"SELECT count(*)::int AS n FROM projects pr WHERE {condition}", tuple(params))["n"]
        rows = db.all(f"""SELECT pr.public_id,pr.title,pr.profile,pr.status,pr.page_count,pr.completed_count,pr.failed_count,
                                 pr.warning_count,pr.held_count,pr.expires_at,pr.created_at,pr.updated_at,pr.owner,
                                 first_page.id AS page_id,first_page.version AS page_version,first_page.thumb_object,first_page.result_object
                          FROM projects pr LEFT JOIN LATERAL (
                              SELECT p.id,p.version,p.thumb_object,p.result_object FROM pages p
                              WHERE p.project_id=pr.id AND p.result_object IS NOT NULL ORDER BY p.position LIMIT 1) first_page ON true
                          WHERE {condition} ORDER BY pr.created_at DESC LIMIT %s OFFSET %s""", (*params, limit, offset))
        items = []
        seconds = page_seconds()[1]
        for row in rows:
            item = jsonable(dict(row))
            left = row["page_count"] - row["completed_count"] - row["failed_count"]
            item["eta_seconds"] = max(1, round((left - row["held_count"]) * seconds)) if row["status"] == "processing" and left > row["held_count"] else None
            page_id, version = item.pop("page_id"), item.pop("page_version")
            form = "thumb" if item.pop("thumb_object") else "png" if item.pop("result_object", None) else None
            item.pop("result_object", None)
            item["thumbnail_url"] = f"/api/v1/projects/{row['public_id']}/pages/{page_id}/image/{form}?v={version}" if page_id and form else None
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @api.get("/projects/{public_id}", tags=["Scans"])
    def get_project(public_id: str, who: Who):
        return project_view(find(public_id, who))

    @api.patch("/projects/{public_id}", tags=["Scans"], summary="Rename, change the finish, or reorder")
    def edit_project(public_id: str, edit: ProjectEdit, who: Scanner):
        project = find(public_id, who)
        title = edit.title.strip() if edit.title is not None else None
        if edit.title is not None and not title:
            raise HTTPException(422, "A scan's name can't be empty")
        with db.transaction() as cursor:
            if title is not None and title != project["title"]:
                cursor.execute("UPDATE projects SET title=%s,version=version+1 WHERE id=%s", (title, project["id"]))
                event(cursor, project, "project_renamed")
            if edit.profile is not None and edit.profile != project["profile"]:
                cursor.execute("UPDATE projects SET profile=%s WHERE id=%s", (edit.profile, project["id"]))
                cursor.execute("""UPDATE pages SET status='queued',edit_rev=edit_rev+1,error=NULL,stage_started_at=NULL
                                  WHERE project_id=%s AND appearance='auto' AND normalized_object IS NOT NULL AND status='ready' RETURNING id""",
                               (project["id"],))
                for row in cursor.fetchall():
                    cursor.execute("INSERT INTO jobs(project_id,page_id,kind,priority) VALUES (%s,%s,'render_page',%s) ON CONFLICT DO NOTHING",
                                   (project["id"], row["id"], PRIORITY["render_page"]))
                cursor.execute("SELECT project_refresh(%s)", (project["id"],))
                event(cursor, project, "project_changed")
            if edit.page_order is not None:
                cursor.execute("SELECT id FROM pages WHERE project_id=%s FOR UPDATE", (project["id"],))
                existing = {row["id"] for row in cursor.fetchall()}
                if set(edit.page_order) != existing or len(edit.page_order) != len(existing):
                    raise HTTPException(409, "The scan's pages changed meanwhile: reload and arrange again")
                cursor.execute("UPDATE pages SET position=position+1000000 WHERE project_id=%s", (project["id"],))
                cursor.execute("""UPDATE pages p SET position=o.place-1 FROM unnest(%s::uuid[]) WITH ORDINALITY o(id,place)
                                  WHERE p.id=o.id""", (edit.page_order,))
                event(cursor, project, "pages_reordered")
        return project_view(find(public_id, who))

    @api.delete("/projects/{public_id}", status_code=204, tags=["Scans"], summary="Delete a scan and all its files")
    def delete_project(public_id: str, who: Scanner):
        project = find(public_id, who)
        # The API can't touch finished files (it mounts them read-only): the worker erases them.
        with db.transaction() as cursor:
            cursor.execute("UPDATE projects SET status='deleting' WHERE id=%s", (project["id"],))
            cursor.execute("INSERT INTO jobs(project_id,kind,priority) VALUES (%s,'cleanup',%s)", (project["id"], PRIORITY["render_page"]))
            event(cursor, project, "project_deleted")

    # ---- pages --------------------------------------------------------------------------------

    @api.get("/projects/{public_id}/pages", tags=["Pages"], summary="A scan's pages, a stretch at a time")
    def list_pages(public_id: str, who: Who, limit: int = Query(48, ge=1, le=1000), offset: int = Query(0, ge=0),
                   only: Literal["review", "failed", "held"] | None = None):
        project = find(public_id, who)
        rows = page_rows(project["id"], limit=limit, offset=offset, only=only)
        counts = db.one("""SELECT (count(*) FILTER (WHERE NOT hold))::int AS total,
                                  (count(*) FILTER (WHERE hold))::int AS held,
                                  (count(*) FILTER (WHERE jsonb_array_length(COALESCE(quality->'warnings','[]'::jsonb))>0))::int AS review,
                                  (count(*) FILTER (WHERE status IN ('failed','blocked')))::int AS failed
                           FROM pages WHERE project_id=%s""", (project["id"],))
        total = {"review": counts["review"], "failed": counts["failed"], "held": counts["held"]}.get(only, counts["total"])
        return {"items": [page_view(public_id, row) for row in rows], "total": total, "counts": dict(counts),
                "limit": limit, "offset": offset}

    def receive_refusal(project: dict, settings: dict, request: Request, name: str) -> None:
        if project["page_count"] >= int(settings["max_files_per_project"]):
            raise HTTPException(422, f"A scan holds at most {settings['max_files_per_project']} pages")
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > int(settings["max_upload_bytes"]):
            raise HTTPException(413, f"{name} is larger than the {int(settings['max_upload_bytes']) // 1048576} MB limit")
        if shutil.disk_usage(config.incoming).free < MIN_FREE_BYTES:
            raise HTTPException(507, "The server is nearly out of disk space")

    def add_page(project: dict, name: str, object_name: str, digest: str, total: int, hint, hold: bool) -> dict:
        with db.transaction() as cursor:
            # One at a time per scan: what decides a page's place and whether it is a repeat.
            cursor.execute("SELECT id FROM projects WHERE id=%s FOR UPDATE", (project["id"],))
            cursor.execute(f"{PAGE_ROWS} WHERE p.project_id=%s AND p.source_sha256=%s ORDER BY p.position LIMIT 1", (project["id"], digest))
            same = cursor.fetchone()
            if same:
                object_path(config.incoming, object_name).unlink(missing_ok=True)
                return {"duplicate": True, "page": page_view(project["public_id"], same)}
            cursor.execute("""INSERT INTO pages(project_id,position,original_name,source_object,source_sha256,source_bytes,corners_hint,hold)
                              VALUES (%s,(SELECT COALESCE(max(position)+1,0) FROM pages WHERE project_id=%s),%s,%s,%s,%s,%s::jsonb,%s) RETURNING id""",
                           (project["id"], project["id"], name, object_name, digest, total, json.dumps(hint) if hint else None, hold))
            page_id = cursor.fetchone()["id"]
            kind = "prepare_page" if hold else "process_page"
            cursor.execute("INSERT INTO jobs(project_id,page_id,kind,priority) VALUES (%s,%s,%s,%s)",
                           (project["id"], page_id, kind, PRIORITY[kind]))
            cursor.execute("SELECT project_refresh(%s)", (project["id"],))
            event(cursor, project, "page_added", {"page_id": str(page_id)})
            cursor.execute(f"{PAGE_ROWS} WHERE p.id=%s", (page_id,))
            return {"duplicate": False, "page": page_view(project["public_id"], cursor.fetchone())}

    @api.post("/projects/{public_id}/pages", status_code=201, tags=["Pages"],
              summary="Add one photo: the file itself is the request's body",
              description="`curl --data-binary @photo.heic -H 'X-Api-Key: …' '…/pages?name=photo.heic'`. "
                          "A file this scan already has isn't added (`duplicate: true`); one you scanned in another scan is not processed again (the hash of each original is kept).")
    async def upload_page(public_id: str, request: Request, who: Scanner, name: str = Query(max_length=400),
                          hint: str | None = Query(None, max_length=400, description="Four [x, y] the camera saw, as JSON"),
                          hold: bool = Query(False, description="Check it and show it, but scan only when /start is asked")):
        clean = clean_name(name)
        corners = None
        if hint:
            try:
                corners = json.loads(hint)
                check_corners(corners)
            except (ValueError, TypeError):
                corners = None
        project = await run_in_threadpool(find, public_id, who)
        settings = await run_in_threadpool(db.settings)
        await run_in_threadpool(receive_refusal, project, settings, request, clean)
        maximum = int(settings["max_upload_bytes"])
        object_name = f"{uuid.uuid4()}.upload"
        target = object_path(config.incoming, object_name)
        digest = hashlib.sha256()
        total = 0
        keep = False
        output = await run_in_threadpool(target.open, "xb")
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if total == 0 and not looks_like_image(chunk[:16]):
                    raise HTTPException(415, f"{clean} isn't a JPEG, PNG, WebP or HEIC image")
                total += len(chunk)
                if total > maximum:
                    raise HTTPException(413, f"{clean} is larger than the {maximum // 1048576} MB limit")
                digest.update(chunk)
                await run_in_threadpool(output.write, chunk)
            if total == 0:
                raise HTTPException(422, f"{clean} is empty")
            await run_in_threadpool(output.close)
            answer = await run_in_threadpool(add_page, project, clean, object_name, digest.hexdigest(), total, corners, hold)
            keep = True
            return answer
        except ClientDisconnect:
            raise HTTPException(499, "The upload was cancelled") from None
        finally:
            output.close()
            if not keep:
                target.unlink(missing_ok=True)

    def check_corners(corners) -> None:
        if len(corners) != 4 or any(len(point) != 2 or any(not -.05 <= float(value) <= 1.05 for value in point) for point in corners):
            raise ValueError("Corners are four [x, y] points no more than 5% outside the image")

    def edit_pages(project: dict, page_ids: list[uuid.UUID], edit: PageEdit) -> None:
        if edit.corners is not None:
            try:
                check_corners(edit.corners)
            except (ValueError, TypeError) as error:
                raise HTTPException(422, str(error)) from error
        with db.transaction() as cursor:
            cursor.execute("SELECT id,status,normalized_object FROM pages WHERE project_id=%s AND id=ANY(%s::uuid[]) FOR UPDATE",
                           (project["id"], page_ids))
            rows = cursor.fetchall()
            if len(rows) != len(set(page_ids)):
                raise HTTPException(404, "That page doesn't exist, or was deleted")
            cursor.execute("SELECT count(*) AS n FROM pages WHERE id=ANY(%s::uuid[]) AND hold", (page_ids,))
            if cursor.fetchone()["n"]:
                raise HTTPException(409, "That photo hasn't been scanned yet")
            if any(not row["normalized_object"] for row in rows):
                raise HTTPException(409, "That page is still being scanned")
            sets, values = ["status='queued'", "edit_rev=edit_rev+1", "error=NULL", "stage_started_at=NULL"], []
            if edit.rotate_by is not None:
                sets.append("rotation=(rotation+%s)%%360")
                values.append(edit.rotate_by)
            elif edit.rotation is not None:
                sets.append("rotation=%s")
                values.append(edit.rotation)
            if edit.corners is not None:
                sets.append("corners=%s::jsonb")
                values.append(json.dumps(edit.corners))
            if edit.appearance is not None:
                sets.append("appearance=%s")
                values.append(edit.appearance)
            if edit.redetect:
                sets.append("corners=NULL")
            cursor.execute(f"UPDATE pages SET {','.join(sets)} WHERE id=ANY(%s::uuid[])", (*values, page_ids))
            for page_id in page_ids:
                cursor.execute("INSERT INTO jobs(project_id,page_id,kind,priority) VALUES (%s,%s,'render_page',%s) ON CONFLICT DO NOTHING",
                               (project["id"], page_id, PRIORITY["render_page"]))
                event(cursor, project, "page_edit_queued", {"page_id": str(page_id)})
            cursor.execute("SELECT project_refresh(%s)", (project["id"],))

    @api.patch("/projects/{public_id}/pages", status_code=202, tags=["Pages"], summary="Change several pages at once")
    def edit_many(public_id: str, edit: ManyPagesEdit, who: Scanner):
        project = find(public_id, who)
        edit_pages(project, edit.page_ids, edit)
        return project_view(find(public_id, who))

    @api.patch("/projects/{public_id}/pages/{page_id}", status_code=202, tags=["Pages"], summary="Change a page's crop, rotation or look")
    def edit_page(public_id: str, page_id: uuid.UUID, edit: PageEdit, who: Scanner):
        project = find(public_id, who)
        edit_pages(project, [page_id], edit)
        return project_view(find(public_id, who))

    @api.post("/projects/{public_id}/start", status_code=202, tags=["Scans"], summary="Scan the photos waiting for review")
    def start_scan(public_id: str, who: Scanner):
        project = find(public_id, who)
        with db.transaction() as cursor:
            cursor.execute("SELECT count(*) AS n FROM pages WHERE project_id=%s AND hold AND status NOT IN ('held','failed','blocked')", (project["id"],))
            if cursor.fetchone()["n"]:
                raise HTTPException(409, "Some photos are still being checked")
            # A photo already scanned on its own isn't scanned into a copy. As part of another scan, it is welcome.
            cursor.execute("""SELECT p.* FROM pages p JOIN pages rp ON rp.id=p.reused_from JOIN projects rpr ON rpr.id=rp.project_id
                              WHERE p.project_id=%s AND p.hold AND p.status='held' AND rpr.page_count=1 AND rpr.id<>p.project_id""", (project["id"],))
            copies = cursor.fetchall()
            cursor.execute("SELECT count(*) AS n FROM pages WHERE project_id=%s AND hold AND status='held'", (project["id"],))
            if copies and cursor.fetchone()["n"] == len(copies):
                raise HTTPException(409, "These photos were already scanned on their own")
            if copies:
                cursor.execute("DELETE FROM pages WHERE id=ANY(%s::uuid[])", ([row["id"] for row in copies],))
                objects = [row[column] for row in copies for column in (*FORMS.values(), "normalized_object") if row[column]]
                cursor.execute("INSERT INTO jobs(project_id,kind,priority,payload) VALUES (%s,'remove_objects',%s,%s::jsonb)",
                               (project["id"], PRIORITY["render_page"], json.dumps({"objects": objects, "incoming": [row["source_object"] for row in copies]})))
            cursor.execute("UPDATE pages SET hold=false,status=CASE WHEN status='held' THEN 'queued' ELSE status END WHERE project_id=%s AND hold RETURNING id,status", (project["id"],))
            for row in cursor.fetchall():
                if row["status"] == "queued":
                    cursor.execute("INSERT INTO jobs(project_id,page_id,kind,priority) VALUES (%s,%s,'process_page',%s) ON CONFLICT DO NOTHING",
                                   (project["id"], row["id"], PRIORITY["process_page"]))
            cursor.execute("SELECT project_refresh(%s)", (project["id"],))
            event(cursor, project, "project_started")
        return project_view(find(public_id, who))

    @api.post("/projects/{public_id}/pages/retry", status_code=202, tags=["Pages"], summary="Try failed pages again")
    def retry_pages(public_id: str, body: ManyPages, who: Scanner):
        project = find(public_id, who)
        with db.transaction() as cursor:
            cursor.execute("""UPDATE pages SET status='queued',error=NULL WHERE project_id=%s AND status IN ('failed','blocked')
                              AND (%s::uuid[] IS NULL OR id=ANY(%s::uuid[])) RETURNING id""", (project["id"], body.page_ids, body.page_ids))
            for row in cursor.fetchall():
                cursor.execute("INSERT INTO jobs(project_id,page_id,kind,priority) VALUES (%s,%s,'process_page',%s) ON CONFLICT DO NOTHING",
                               (project["id"], row["id"], PRIORITY["process_page"]))
                event(cursor, project, "page_edit_queued", {"page_id": str(row["id"])})
            cursor.execute("UPDATE projects SET error=NULL WHERE id=%s", (project["id"],))
            cursor.execute("SELECT project_refresh(%s)", (project["id"],))
        return project_view(find(public_id, who))

    @api.post("/projects/{public_id}/pages/delete", tags=["Pages"], summary="Delete pages from a scan")
    def delete_pages(public_id: str, body: ManyPages, who: Scanner):
        project = find(public_id, who)
        if not body.page_ids:
            raise HTTPException(422, "Say which pages to delete")
        with db.transaction() as cursor:
            cursor.execute("SELECT id FROM projects WHERE id=%s FOR UPDATE", (project["id"],))
            cursor.execute("DELETE FROM pages WHERE project_id=%s AND id=ANY(%s::uuid[]) RETURNING *", (project["id"], body.page_ids))
            gone = cursor.fetchall()
            objects = [row[column] for row in gone for column in (*FORMS.values(), "normalized_object") if row[column]]
            if objects or gone:
                cursor.execute("INSERT INTO jobs(project_id,kind,priority,payload) VALUES (%s,'remove_objects',%s,%s::jsonb)",
                               (project["id"], PRIORITY["render_page"],
                                json.dumps({"objects": sorted(set(objects)), "incoming": [row["source_object"] for row in gone]})))
            # The positions close up, through a range no page is in, because they are unique.
            cursor.execute("UPDATE pages SET position=position+1000000 WHERE project_id=%s", (project["id"],))
            cursor.execute("""UPDATE pages p SET position=o.place-1 FROM (
                                  SELECT id,row_number() OVER (ORDER BY position) AS place FROM pages WHERE project_id=%s) o
                              WHERE p.id=o.id""", (project["id"],))
            cursor.execute("SELECT project_refresh(%s)", (project["id"],))
            event(cursor, project, "pages_deleted", {"count": len(gone)})
        return project_view(find(public_id, who))

    @api.get("/projects/{public_id}/pages/{page_id}/image/{form}", tags=["Pages"],
             summary="A page's image: thumb, preview, source (the photo), png or jpeg")
    def page_image(public_id: str, page_id: uuid.UUID, form: Literal["thumb", "preview", "source", "png", "jpeg"], who: Who,
                   download: bool = False):
        project = find(public_id, who)
        page = db.one("SELECT * FROM pages WHERE project_id=%s AND id=%s", (project["id"], page_id))
        if not page:
            raise HTTPException(404, "That page doesn't exist, or was deleted")
        name = page[FORMS[form]]
        if not name:
            raise HTTPException(404, "That image isn't ready")
        path = object_path(config.work / str(project["id"]), name)
        if not path.is_file():
            raise HTTPException(404, "That image isn't ready")
        filename = None
        if download:
            stem = Path(clean_name(page["original_name"])).stem or f"page-{page['position'] + 1}"
            filename = f"{stem}-scanloom{path.suffix.lower()}"
        # An address carries the page's version, and what it names never changes.
        return FileResponse(path, media_type=MEDIA.get(path.suffix.lower(), "application/octet-stream"), filename=filename,
                            content_disposition_type="attachment" if download else "inline",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    # ---- exports ------------------------------------------------------------------------------

    @api.post("/projects/{public_id}/exports", status_code=202, tags=["Exports"], summary="Make a ZIP or a PDF of a scan's pages")
    def create_export(public_id: str, request: ExportRequest, who: Scanner):
        if request.page_ids is not None and request.pages is not None:
            raise HTTPException(422, "Use page_ids or pages, not both")
        project = find(public_id, who)
        rows = db.all("SELECT id,position,version,result_bytes FROM pages WHERE project_id=%s AND status='ready' AND result_object IS NOT NULL ORDER BY position", (project["id"],))
        if not rows:
            raise HTTPException(409, "This scan has no finished pages yet")
        chosen = rows
        if request.page_ids is not None:
            wanted = set(request.page_ids)
            chosen = [row for row in rows if row["id"] in wanted]
            if not chosen or len(chosen) != len(wanted):
                raise HTTPException(422, "Some of those pages aren't finished, or aren't in this scan")
        elif request.pages is not None:
            try:
                numbers = parse_pages(request.pages, int(project["page_count"]))
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            by_number = {row["position"] + 1: row for row in rows}
            missing = [number for number in numbers if number not in by_number]
            if missing:
                raise HTTPException(422, f"Page {missing[0]} isn't finished")
            chosen = [by_number[number] for number in numbers]
        if (request.kind == "pdf" and request.quality == "lossless"
                and sum(row["result_bytes"] or 0 for row in chosen) > LOSSLESS_PDF_LIMIT):
            raise HTTPException(422, "That many lossless pages are too large for one PDF: choose Balanced, or fewer pages")
        settings = db.settings()
        options = {"color": request.color, "long_edge": request.long_edge, "jpeg_quality": request.jpeg_quality,
                   "paper": request.paper, "margin_mm": request.margin_mm if request.paper != "fit" else 0}
        fingerprint = json.dumps([request.kind, request.quality, options, [[str(row["id"]), row["version"]] for row in chosen]])
        cache_key = hashlib.sha256(fingerprint.encode()).hexdigest()
        expires = datetime.now(timezone.utc) + timedelta(minutes=int(settings["export_ttl_minutes"]))
        stem = re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|]+', "-", request.filename or project["title"]).strip(" .-") or "scan"
        filename = f"{stem}.{request.kind}"
        with db.transaction() as cursor:
            # The same pages asked for again: the file already made.
            cursor.execute("""SELECT id,status FROM exports WHERE project_id=%s AND cache_key=%s AND expires_at>now()
                              AND status IN ('queued','building','ready') ORDER BY created_at DESC LIMIT 1""", (project["id"], cache_key))
            existing = cursor.fetchone()
            if existing:
                cursor.execute("UPDATE exports SET expires_at=%s,filename=%s WHERE id=%s", (expires, filename, existing["id"]))
                export_id, export_status = existing["id"], existing["status"]
            else:
                cursor.execute("""INSERT INTO exports(project_id,kind,filename,expires_at,cache_key,page_count) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                               (project["id"], request.kind, filename, expires, cache_key, len(chosen)))
                export_id, export_status = cursor.fetchone()["id"], "queued"
                cursor.execute("INSERT INTO jobs(project_id,kind,priority,payload) VALUES (%s,'build_export',%s,%s::jsonb)",
                               (project["id"], PRIORITY["build_export"],
                                json.dumps({"export_id": str(export_id), "page_ids": [str(row["id"]) for row in chosen], "quality": request.quality, "options": options})))
                event(cursor, project, "export_queued", {"export_id": str(export_id), "kind": request.kind})
        return {"id": str(export_id), "kind": request.kind, "status": export_status, "page_count": len(chosen), "quality": request.quality,
                "download_url": f"/api/v1/projects/{public_id}/exports/{export_id}/download"}

    @api.post("/exports/batch", status_code=202, tags=["Exports"], summary="One ZIP of several scans")
    def batch_export(body: BatchExport, who: Scanner):
        scans = [find(public_id, who) for public_id in dict.fromkeys(body.project_ids)]
        pages = sum(scan["completed_count"] for scan in scans)
        if not pages:
            raise HTTPException(409, "None of those scans has finished pages yet")
        settings = db.settings()
        expires = datetime.now(timezone.utc) + timedelta(minutes=int(settings["export_ttl_minutes"]))
        first = scans[0]
        with db.transaction() as cursor:
            cursor.execute("INSERT INTO exports(project_id,kind,filename,expires_at,page_count) VALUES (%s,'zip',%s,%s,%s) RETURNING id",
                           (first["id"], f"Scanloom {len(scans)} scans.zip", expires, pages))
            export_id = cursor.fetchone()["id"]
            cursor.execute("INSERT INTO jobs(project_id,kind,priority,payload) VALUES (%s,'build_export',%s,%s::jsonb)",
                           (first["id"], PRIORITY["build_export"],
                            json.dumps({"export_id": str(export_id), "batch": [str(scan["id"]) for scan in scans], "format": body.format, "quality": body.quality})))
            event(cursor, first, "export_queued", {"export_id": str(export_id), "kind": "zip"})
        return {"id": str(export_id), "project": first["public_id"], "page_count": pages,
                "download_url": f"/api/v1/projects/{first['public_id']}/exports/{export_id}/download"}

    @api.get("/projects/{public_id}/exports/{export_id}/download", tags=["Exports"])
    def download_export(public_id: str, export_id: uuid.UUID, who: Who):
        project = find(public_id, who)
        item = db.one("SELECT * FROM exports WHERE id=%s AND project_id=%s AND expires_at>now()", (export_id, project["id"]))
        if not item:
            raise HTTPException(404, "That download expired: make it again")
        if item["status"] != "ready":
            raise HTTPException(409, "That download is still being made")
        media = {"zip": "application/zip", "pdf": "application/pdf"}[item["kind"]]
        return FileResponse(object_path(config.exports, item["object_name"]), media_type=media, filename=item["filename"])

    # ---- live updates -------------------------------------------------------------------------

    @api.get("/events", tags=["System"], summary="Server-sent events: what changed in the scans one may see")
    async def events(request: Request, who: Who):
        scope = scope_of(who)
        cookie = request.cookies.get(gate.cookie)
        queue = broker.subscribe()

        async def stream():
            try:
                yield "retry: 3000\nevent: hello\ndata: {}\n\n"
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), 20)
                    except TimeoutError:
                        if await request.is_disconnected():
                            return
                        # A stream ends with the session that opened it.
                        if cookie and not await run_in_threadpool(accounts.session, cookie):
                            return
                        yield ": keepalive\n\n"
                        continue
                    if scope is None or message.get("owner") == scope or message.get("event") == "resync":
                        public = {key: message.get(key) for key in ("project", "event", "page")}
                        yield f"event: change\ndata: {json.dumps(public)}\n\n"
            finally:
                broker.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    app.include_router(public)
    app.include_router(api)

    # ---- the API's own page -------------------------------------------------------------------

    @app.get("/docs", include_in_schema=False)
    def docs():
        return HTMLResponse("""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Scanloom API</title></head><body><script id=api-reference data-url=/api/v1/openapi.json></script><script src=/api-docs/scalar.js></script></body></html>""")

    if config.static.joinpath("api-docs").is_dir():
        app.mount("/api-docs", StaticFiles(directory=config.static / "api-docs"), name="api-docs")
    api_not_found(app, docs="/docs")
    return app
