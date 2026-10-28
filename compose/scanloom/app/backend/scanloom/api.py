from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders

from . import __version__
from .config import Config, load
from .db import Database
from .processor import event, object_path
from .settings import validate


class ProjectEdit(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=160)
    profile: str | None = None
    page_order: list[uuid.UUID] | None = None


class PageEdit(BaseModel):
    corners: list[list[float]] | None = None
    rotation: int | None = None
    appearance: str | None = None
    corner_radius: float | None = Field(None, ge=0, le=.25)


class ExportRequest(BaseModel):
    kind: str
    page_ids: list[uuid.UUID] | None = None
    page_from: int | None = Field(None, ge=1)
    page_to: int | None = Field(None, ge=1)
    quality: str = "lossless"


class SettingsEdit(BaseModel):
    values: dict
    revision: int


def clean_name(value: str | None) -> str:
    name = re.sub(r"[\x00-\x1f\\/]", "_", value or "image").strip() or "image"
    return name[:255]


def jsonable(value):
    if isinstance(value, (datetime, uuid.UUID)):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def project_view(db: Database, public_id: str) -> dict:
    project = db.one("SELECT * FROM projects WHERE public_id=%s", (public_id,))
    if not project:
        raise HTTPException(404, "Project not found or expired")
    pages = db.all("SELECT * FROM pages WHERE project_id=%s ORDER BY position", (project["id"],))
    exports = db.all("SELECT id,kind,status,filename,bytes,sha256,error,expires_at,created_at,ready_at FROM exports WHERE project_id=%s ORDER BY created_at DESC", (project["id"],))
    answer = jsonable(dict(project))
    answer["pages"] = [jsonable(dict(page)) | {
        "preview_url": f"/api/v1/projects/{public_id}/pages/{page['id']}/preview?v={page['version']}" if page["normalized_object"] else None,
        "result_url": f"/api/v1/projects/{public_id}/pages/{page['id']}/result?v={page['version']}" if page["result_object"] else None,
        "download_url": f"/api/v1/projects/{public_id}/pages/{page['id']}/download?v={page['version']}" if page["result_object"] else None,
        "jpeg_download_url": f"/api/v1/projects/{public_id}/pages/{page['id']}/download?format=jpeg&v={page['version']}" if page["result_object"] else None,
    } for page in pages]
    answer["exports"] = [jsonable(dict(item)) for item in exports]
    answer.pop("id", None)
    for page in answer["pages"]:
        for hidden in ("project_id", "source_object", "normalized_object", "result_object"):
            page.pop(hidden, None)
    return answer


class SecurityHeaders:
    POLICY = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
              "script-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
              "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")

    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        async def start(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
                headers["Content-Security-Policy"] = self.POLICY
                if scope.get("path", "").startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
            await send(message)
        await self.app(scope, receive, start)


def create_app(config: Config | None = None, db: Database | None = None) -> FastAPI:
    config = config or load()
    db = db or Database(config.database_url)

    @asynccontextmanager
    async def lifespan(_app):
        db.migrate()
        for folder in (config.incoming, config.work, config.exports):
            folder.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="Scanloom API", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url="/api/v1/openapi.json", lifespan=lifespan,
                  description="Automatic, temporary image-to-document correction. No account required on the trusted LAN.")
    app.add_middleware(GZipMiddleware, minimum_size=1200)
    app.add_middleware(SecurityHeaders)

    @app.get("/api/v1/health", tags=["System"])
    def health():
        db.one("SELECT 1 AS ok")
        return {"status": "ok", "version": __version__}

    @app.get("/api/v1/system/status", tags=["System"])
    def status():
        row = db.one("""SELECT count(*) FILTER (WHERE status IN ('queued','processing')) AS processing,
                              count(*) FILTER (WHERE status='ready') AS ready,
                              COALESCE(sum(page_count),0) AS pages,
                              count(*) FILTER (WHERE status='failed') AS failed FROM projects WHERE expires_at>now()""")
        return dict(row)

    @app.get("/api/v1/settings", tags=["Settings"])
    def get_settings():
        return dict(db.one("SELECT revision,values,updated_at FROM settings WHERE singleton=true"))

    @app.put("/api/v1/settings", tags=["Settings"])
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
        return dict(row)

    @app.post("/api/v1/projects", status_code=202, tags=["Projects"])
    async def create_project(files: list[UploadFile] = File(...), title: str = Form("Untitled scan"),
                             profile: str | None = Form(None)):
        settings = dict(db.one("SELECT values FROM settings WHERE singleton=true")["values"])
        if not files or len(files) > int(settings["max_files_per_project"]):
            raise HTTPException(422, f"Choose 1 to {settings['max_files_per_project']} images")
        profile = profile or settings["default_profile"]
        if profile not in {"faithful", "paper", "mono"}:
            raise HTTPException(422, "Unknown profile")
        public_id = secrets.token_urlsafe(18).replace("-", "A").replace("_", "B")
        expires = datetime.now(timezone.utc) + timedelta(hours=int(settings["retention_hours"]))
        written: list[Path] = []
        rows = []
        try:
            for position, upload in enumerate(files):
                object_name = f"{uuid.uuid4()}.upload"
                target = object_path(config.incoming, object_name)
                digest = hashlib.sha256()
                total = 0
                with target.open("xb") as destination:
                    written.append(target)
                    while chunk := await upload.read(1024 * 1024):
                        total += len(chunk)
                        if total > int(settings["max_upload_bytes"]):
                            raise HTTPException(413, f"{clean_name(upload.filename)} exceeds the upload limit")
                        digest.update(chunk)
                        destination.write(chunk)
                if total == 0:
                    raise HTTPException(422, f"{clean_name(upload.filename)} is empty")
                rows.append((position, clean_name(upload.filename), object_name, digest.hexdigest(), total))
            with db.transaction() as cursor:
                cursor.execute("INSERT INTO projects(public_id,title,profile,status,page_count,expires_at) VALUES (%s,%s,%s,'queued',%s,%s) RETURNING id",
                               (public_id, title.strip()[:160] or "Untitled scan", profile, len(rows), expires))
                project_id = cursor.fetchone()["id"]
                for position, original, object_name, digest, total in rows:
                    cursor.execute("""INSERT INTO pages(project_id,position,original_name,source_object,source_sha256,source_bytes)
                                      VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                                   (project_id, position, original, object_name, digest, total))
                    page_id = cursor.fetchone()["id"]
                    cursor.execute("INSERT INTO jobs(project_id,page_id,kind) VALUES (%s,%s,'process_page')", (project_id, page_id))
                event(cursor, project_id, "project_queued", {"pages": len(rows)})
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return project_view(db, public_id)

    @app.get("/api/v1/projects/{public_id}", tags=["Projects"])
    def get_project(public_id: str): return project_view(db, public_id)

    @app.get("/api/v1/projects", tags=["Projects"])
    def list_projects(limit: int = 50):
        limit = max(1, min(100, limit))
        rows = db.all("""SELECT pr.public_id,pr.title,pr.profile,pr.status,pr.page_count,
                               pr.completed_count,pr.warning_count,pr.expires_at,pr.created_at,
                               (SELECT p.id FROM pages p WHERE p.project_id=pr.id AND p.result_object IS NOT NULL
                                ORDER BY p.position LIMIT 1) AS thumbnail_page_id
                        FROM projects pr WHERE pr.expires_at>now() AND pr.status<>'deleting'
                        ORDER BY pr.created_at DESC LIMIT %s""", (limit,))
        return {"items": [jsonable(dict(row)) | {
            "thumbnail_url": (f"/api/v1/projects/{row['public_id']}/pages/{row['thumbnail_page_id']}/result"
                              if row["thumbnail_page_id"] else None)
        } for row in rows]}

    @app.patch("/api/v1/projects/{public_id}", tags=["Projects"])
    def edit_project(public_id: str, edit: ProjectEdit):
        project = db.one("SELECT * FROM projects WHERE public_id=%s", (public_id,))
        if not project: raise HTTPException(404, "Project not found or expired")
        if edit.profile is not None and edit.profile not in {"faithful", "paper", "mono"}:
            raise HTTPException(422, "Unknown profile")
        with db.transaction() as cursor:
            if edit.title is not None:
                cursor.execute("UPDATE projects SET title=%s,version=version+1 WHERE id=%s", (edit.title.strip(), project["id"]))
            if edit.profile is not None:
                cursor.execute("UPDATE projects SET profile=%s,version=version+1 WHERE id=%s", (edit.profile, project["id"]))
            if edit.page_order is not None:
                cursor.execute("SELECT id FROM pages WHERE project_id=%s", (project["id"],))
                existing = {row["id"] for row in cursor.fetchall()}
                if set(edit.page_order) != existing or len(edit.page_order) != len(existing):
                    raise HTTPException(422, "page_order must contain every page exactly once")
                cursor.execute("UPDATE pages SET position=position+10000 WHERE project_id=%s", (project["id"],))
                for position, page_id in enumerate(edit.page_order):
                    cursor.execute("UPDATE pages SET position=%s WHERE id=%s", (position, page_id))
                event(cursor, project["id"], "pages_reordered")
        return project_view(db, public_id)

    @app.patch("/api/v1/projects/{public_id}/pages/{page_id}", status_code=202, tags=["Pages"])
    def edit_page(public_id: str, page_id: uuid.UUID, edit: PageEdit):
        page = db.one("SELECT p.* FROM pages p JOIN projects pr ON pr.id=p.project_id WHERE pr.public_id=%s AND p.id=%s", (public_id, page_id))
        if not page: raise HTTPException(404, "Page not found")
        rotation = page["rotation"] if edit.rotation is None else edit.rotation
        appearance = page["appearance"] if edit.appearance is None else edit.appearance
        corners = page["corners"] if edit.corners is None else edit.corners
        corner_radius = page["corner_radius"] if edit.corner_radius is None else edit.corner_radius
        if rotation not in {0, 90, 180, 270} or appearance not in {"auto", "color", "gray", "mono"}:
            raise HTTPException(422, "Invalid page correction")
        if corners is not None and (len(corners) != 4 or any(len(point) != 2 or any(not -.05 <= float(value) <= 1.05 for value in point) for point in corners)):
            raise HTTPException(422, "Corners are four [x,y] points no more than 5% outside the image")
        with db.transaction() as cursor:
            cursor.execute("UPDATE pages SET corners=%s::jsonb,rotation=%s,appearance=%s,corner_radius=%s,status='queued',error=NULL WHERE id=%s",
                           (json.dumps(corners), rotation, appearance, corner_radius, page_id))
            cursor.execute("INSERT INTO jobs(project_id,page_id,kind) VALUES (%s,%s,'render_page') ON CONFLICT DO NOTHING", (page["project_id"], page_id))
            cursor.execute("UPDATE projects SET status='processing',error=NULL WHERE id=%s", (page["project_id"],))
            event(cursor, page["project_id"], "page_edit_queued", {"page_id": str(page_id)})
        return project_view(db, public_id)

    @app.post("/api/v1/projects/{public_id}/render", status_code=202, tags=["Projects"])
    def render(public_id: str):
        project = db.one("SELECT * FROM projects WHERE public_id=%s", (public_id,))
        if not project: raise HTTPException(404, "Project not found or expired")
        with db.transaction() as cursor:
            cursor.execute("""INSERT INTO jobs(project_id,page_id,kind)
                              SELECT project_id,id,'render_page' FROM pages WHERE project_id=%s AND status='ready'
                              ON CONFLICT DO NOTHING""", (project["id"],))
            cursor.execute("UPDATE projects SET status='processing' WHERE id=%s", (project["id"],))
        return project_view(db, public_id)

    @app.post("/api/v1/projects/{public_id}/exports", status_code=202, tags=["Exports"])
    def create_export(public_id: str, request: ExportRequest):
        if request.kind not in {"image", "zip", "pdf"}: raise HTTPException(422, "kind must be image, zip, or pdf")
        if request.quality not in {"lossless", "balanced", "small"}:
            raise HTTPException(422, "quality must be lossless, balanced, or small")
        if request.page_ids is not None and (request.page_from is not None or request.page_to is not None):
            raise HTTPException(422, "Use page_ids or a page range, not both")
        project = db.one("SELECT * FROM projects WHERE public_id=%s", (public_id,))
        if not project: raise HTTPException(404, "Project not found or expired")
        if project["status"] != "ready": raise HTTPException(409, "Project is not ready")
        pages = db.all("SELECT id,position FROM pages WHERE project_id=%s AND status='ready' ORDER BY position", (project["id"],))
        if request.page_ids is not None:
            wanted = set(request.page_ids)
            if not wanted or not wanted.issubset({page["id"] for page in pages}):
                raise HTTPException(422, "page_ids must identify ready pages in this project")
            selected = [page["id"] for page in pages if page["id"] in wanted]
        elif request.page_from is not None or request.page_to is not None:
            first, last = request.page_from or 1, request.page_to or len(pages)
            if first > last or last > len(pages):
                raise HTTPException(422, "Page range is outside this project")
            selected = [page["id"] for page in pages[first - 1:last]]
        else:
            selected = [page["id"] for page in pages]
        if request.kind == "image" and len(selected) != 1:
            raise HTTPException(422, "An image export needs exactly one selected page")
        settings = dict(db.one("SELECT values FROM settings WHERE singleton=true")["values"])
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires = min(project["expires_at"], datetime.now(timezone.utc) + timedelta(minutes=int(settings["link_ttl_minutes"])))
        filename = re.sub(r"[^A-Za-z0-9._-]+", "-", project["title"]).strip("-") or "scanloom"
        filename += {"image": ".png", "zip": ".zip", "pdf": ".pdf"}[request.kind]
        with db.transaction() as cursor:
            cursor.execute("INSERT INTO exports(project_id,kind,token_hash,filename,expires_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                           (project["id"], request.kind, token_hash, filename, expires))
            export_id = cursor.fetchone()["id"]
            cursor.execute("INSERT INTO jobs(project_id,kind,payload) VALUES (%s,'build_export',%s::jsonb)",
                           (project["id"], json.dumps({"export_id": str(export_id),
                                                       "page_ids": [str(value) for value in selected],
                                                       "quality": request.quality})))
            event(cursor, project["id"], "export_queued", {"export_id": str(export_id), "kind": request.kind})
        return {"id": str(export_id), "kind": request.kind, "status": "queued", "expires_at": expires,
                "page_count": len(selected), "quality": request.quality,
                "download_url": f"/api/v1/downloads/{token}"}

    def page_file(public_id: str, page_id: uuid.UUID, result: bool, download: bool = False):
        page = db.one("SELECT p.* FROM pages p JOIN projects pr ON pr.id=p.project_id WHERE pr.public_id=%s AND p.id=%s", (public_id, page_id))
        if not page: raise HTTPException(404, "Page not found")
        name = page["result_object"] if result else page["normalized_object"]
        if not name: raise HTTPException(404, "Image is not ready")
        path = object_path(config.work / str(page["project_id"]), name)
        if not path.is_file(): raise HTTPException(404, "Image is not ready")
        extension = path.suffix.lower()
        media_type = "image/png" if extension == ".png" else "image/jpeg"
        filename = None
        if download:
            stem = Path(clean_name(page["original_name"])).stem or f"page-{page['position'] + 1}"
            filename = f"{stem}-scanloom{extension}"
        return FileResponse(path, media_type=media_type if result else "image/png",
                            filename=filename, content_disposition_type="attachment" if download else "inline")

    @app.get("/api/v1/projects/{public_id}/pages/{page_id}/preview", tags=["Pages"])
    def preview(public_id: str, page_id: uuid.UUID): return page_file(public_id, page_id, False)

    @app.get("/api/v1/projects/{public_id}/pages/{page_id}/result", tags=["Pages"])
    def result(public_id: str, page_id: uuid.UUID): return page_file(public_id, page_id, True)

    @app.get("/api/v1/projects/{public_id}/pages/{page_id}/download", tags=["Pages"])
    def page_download(public_id: str, page_id: uuid.UUID, format: str = "png"):
        if format not in {"png", "jpeg", "jpg"}:
            raise HTTPException(422, "format must be png or jpeg")
        if format == "png":
            return page_file(public_id, page_id, True, True)
        page = db.one("SELECT p.* FROM pages p JOIN projects pr ON pr.id=p.project_id WHERE pr.public_id=%s AND p.id=%s", (public_id, page_id))
        if not page or not page["result_object"]:
            raise HTTPException(404, "Image is not ready")
        path = object_path(config.work / str(page["project_id"]), page["result_object"])
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise HTTPException(404, "Image is not ready")
        if image.ndim == 3 and image.shape[2] == 4:
            alpha = image[:, :, 3:4].astype("float32") / 255
            image = (image[:, :, :3] * alpha + 255 * (1 - alpha)).astype("uint8")
        settings = dict(db.one("SELECT values FROM settings WHERE singleton=true")["values"])
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(settings["jpeg_quality"])])
        if not ok:
            raise HTTPException(500, "Could not create JPEG")
        stem = Path(clean_name(page["original_name"])).stem or f"page-{page['position'] + 1}"
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or f"page-{page['position'] + 1}"
        return Response(encoded.tobytes(), media_type="image/jpeg", headers={
            "Content-Disposition": f'attachment; filename="{stem}-scanloom-small.jpg"',
        })

    @app.get("/api/v1/downloads/{token}", tags=["Exports"])
    def download(token: str):
        digest = hashlib.sha256(token.encode()).hexdigest()
        item = db.one("SELECT * FROM exports WHERE token_hash=%s AND expires_at>now()", (digest,))
        if not item: raise HTTPException(404, "Download link not found or expired")
        if item["status"] != "ready": raise HTTPException(409, "Export is still being prepared")
        path = object_path(config.exports, item["object_name"])
        media = ({".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(path.suffix.lower(), "image/png")
                 if item["kind"] == "image" else {"zip": "application/zip", "pdf": "application/pdf"}[item["kind"]])
        return FileResponse(path, media_type=media, filename=item["filename"])

    @app.delete("/api/v1/projects/{public_id}", status_code=204, tags=["Projects"])
    def delete_project(public_id: str):
        project = db.one("SELECT id FROM projects WHERE public_id=%s", (public_id,))
        if not project: return None
        # The API intentionally has read-only derivative mounts. Let the isolated
        # worker erase every object, then cascade the database rows.
        with db.transaction() as cursor:
            cursor.execute("UPDATE projects SET status='deleting',expires_at=now() WHERE id=%s", (project["id"],))
            cursor.execute("INSERT INTO jobs(project_id,kind) VALUES (%s,'cleanup')", (project["id"],))
        return None

    @app.get("/api/v1/projects/{public_id}/events", tags=["Projects"])
    async def events(public_id: str, request: Request, after: int = 0):
        project = db.one("SELECT id FROM projects WHERE public_id=%s", (public_id,))
        if not project: raise HTTPException(404, "Project not found or expired")
        async def stream():
            cursor = after
            while not await request.is_disconnected():
                rows = db.all("SELECT * FROM project_events WHERE project_id=%s AND seq>%s ORDER BY seq LIMIT 100", (project["id"], cursor))
                for row in rows:
                    cursor = row["seq"]
                    yield f"id: {cursor}\nevent: {row['event']}\ndata: {json.dumps(jsonable(dict(row['data'])))}\n\n"
                if not rows: yield ": keepalive\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/docs", include_in_schema=False)
    def docs():
        return HTMLResponse("""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Scanloom API</title></head><body><script id=api-reference data-url=/api/v1/openapi.json></script><script src=/api-docs/scalar.js></script></body></html>""")

    static = config.static
    if static.joinpath("assets").is_dir(): app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")
    if static.joinpath("api-docs").is_dir(): app.mount("/api-docs", StaticFiles(directory=static / "api-docs"), name="api-docs")

    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str):
        candidate = (static / path).resolve()
        if static.resolve() in candidate.parents and candidate.is_file() and candidate.name != "index.html":
            return FileResponse(candidate)
        if not (static / "index.html").is_file(): return JSONResponse({"detail": "Page is not built"}, 503)
        return FileResponse(static / "index.html")
    return app
