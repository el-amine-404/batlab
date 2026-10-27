from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import time
import zipfile
from pathlib import Path

import cv2
import img2pdf

from .config import Config
from .db import Database
from .imaging import (detect, normalize, normalized_corners, pixel_corners,
                      quality, warp, write_jpeg)
from .security import Refused, clamav, inspect_container, sha256


def event(cursor, project_id, name: str, data: dict | None = None):
    cursor.execute("INSERT INTO project_events(project_id,event,data) VALUES (%s,%s,%s::jsonb)",
                   (project_id, name, json.dumps(data or {})))


def object_path(root: Path, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("Invalid storage object")
    return root / name


class Processor:
    def __init__(self, config: Config, db: Database):
        self.config, self.db = config, db
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"

    def settings(self) -> dict:
        return dict(self.db.one("SELECT values FROM settings WHERE singleton=true")["values"])

    def claim(self):
        with self.db.transaction() as cursor:
            cursor.execute("""
                WITH candidate AS (
                    SELECT id FROM jobs
                    WHERE (status='queued' AND available_at <= now())
                       OR (status='running' AND lease_until < now())
                    ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE jobs j SET status='running', attempts=j.attempts+1,
                    worker_id=%s, started_at=COALESCE(j.started_at,now()), lease_until=now()+interval '5 minutes'
                FROM candidate WHERE j.id=candidate.id RETURNING j.*
            """, (self.worker_id,))
            return cursor.fetchone()

    def finish(self, job_id: int):
        self.db.execute("UPDATE jobs SET status='done',finished_at=now(),lease_until=NULL WHERE id=%s", (job_id,))

    def fail(self, job, error: Exception):
        maximum = int(self.settings()["worker_max_attempts"])
        retry = job["attempts"] < maximum and not isinstance(error, Refused)
        status = "queued" if retry else "failed"
        delay = min(300, 5 * (2 ** max(0, job["attempts"] - 1)))
        with self.db.transaction() as cursor:
            cursor.execute("UPDATE jobs SET status=%s,error=%s,available_at=now()+(%s*interval '1 second'),lease_until=NULL WHERE id=%s",
                           (status, str(error)[:500], delay, job["id"]))
            if not retry:
                if job["page_id"]:
                    page_status = "blocked" if isinstance(error, Refused) else "failed"
                    cursor.execute("UPDATE pages SET status=%s,error=%s WHERE id=%s", (page_status, str(error)[:500], job["page_id"]))
                    cursor.execute("UPDATE projects SET status='failed',error=%s WHERE id=%s",
                                   (str(error)[:500], job["project_id"]))
                else:
                    export_id = dict(job["payload"]).get("export_id")
                    if export_id:
                        cursor.execute("UPDATE exports SET status='failed',error=%s WHERE id=%s",
                                       (str(error)[:500], export_id))
                event(cursor, job["project_id"], "failed", {"message": str(error), "page_id": str(job["page_id"] or "")})

    def run_one(self) -> bool:
        job = self.claim()
        if not job:
            return False
        try:
            if job["kind"] in {"process_page", "render_page"}:
                self.process_page(job, scan=job["kind"] == "process_page")
            elif job["kind"] == "build_export":
                self.build_export(job)
            elif job["kind"] == "cleanup":
                self.cleanup()
            self.finish(job["id"])
        except Exception as error:
            self.fail(job, error)
        return True

    def process_page(self, job, scan: bool):
        settings = self.settings()
        page = self.db.one("SELECT p.*,pr.profile FROM pages p JOIN projects pr ON pr.id=p.project_id WHERE p.id=%s", (job["page_id"],))
        if not page:
            return
        project_id, page_id = page["project_id"], page["id"]
        source = object_path(self.config.incoming, page["source_object"])
        project_dir = self.config.work / str(project_id)
        normalized_name = page["normalized_object"] or f"{page_id}.png"
        normalized = object_path(project_dir, normalized_name)
        if scan:
            self.db.execute("UPDATE projects SET status='processing' WHERE id=%s", (project_id,))
            self.db.execute("UPDATE pages SET status='scanning' WHERE id=%s", (page_id,))
            clamav(source, self.config.clamd_host, self.config.clamd_port, int(settings["max_upload_bytes"]))
            mime, integrity = inspect_container(source)
            self.db.execute("UPDATE pages SET malware_status='clean',detected_mime=%s,integrity=%s::jsonb,status='decoding' WHERE id=%s",
                            (mime, json.dumps(integrity), page_id))
            width, height = normalize(source, normalized, int(settings["max_pixels"]))
            self.db.execute("UPDATE pages SET width=%s,height=%s,normalized_object=%s,status='detecting' WHERE id=%s",
                            (width, height, normalized_name, page_id))
        image = cv2.imread(str(normalized), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Normalized image could not be read")
        if scan or not page["corners"]:
            found, confidence, method = detect(image)
            corners = normalized_corners(found, image.shape[1], image.shape[0])
        else:
            corners, confidence, method = page["corners"], float(page["confidence"] or 0), "manual"
            found = pixel_corners(corners, image.shape[1], image.shape[0])
        appearance = page["appearance"]
        if appearance == "auto":
            appearance = {"faithful": "color", "paper": "gray", "mono": "mono"}[page["profile"]]
        self.db.execute("UPDATE pages SET status='rendering' WHERE id=%s", (page_id,))
        corrected = warp(image, found, int(page["rotation"]), appearance)
        report = quality(corrected, confidence) | {"detection_method": method}
        result_name = f"{page_id}.jpg"
        result = object_path(project_dir, result_name)
        write_jpeg(corrected, result, int(settings["jpeg_quality"]))
        result_hash, result_bytes = sha256(result), result.stat().st_size
        with self.db.transaction() as cursor:
            cursor.execute("""UPDATE pages SET status='ready',corners=%s::jsonb,confidence=%s,quality=%s::jsonb,
                              normalized_object=%s,result_object=%s,result_sha256=%s,result_bytes=%s,error=NULL,version=version+1
                              WHERE id=%s""",
                           (json.dumps(corners), confidence, json.dumps(report), normalized_name, result_name,
                            result_hash, result_bytes, page_id))
            cursor.execute("""UPDATE projects SET completed_count=(SELECT count(*) FROM pages WHERE project_id=%s AND status='ready'),
                              warning_count=(SELECT count(*) FROM pages WHERE project_id=%s AND jsonb_array_length(quality->'warnings')>0),
                              status=CASE
                                WHEN EXISTS (SELECT 1 FROM pages WHERE project_id=%s AND status IN ('failed','blocked')) THEN 'failed'
                                WHEN NOT EXISTS (SELECT 1 FROM pages WHERE project_id=%s AND status <> 'ready') THEN 'ready'
                                ELSE 'processing' END,
                              error=CASE WHEN EXISTS (SELECT 1 FROM pages WHERE project_id=%s AND status IN ('failed','blocked'))
                                         THEN error ELSE NULL END,version=version+1 WHERE id=%s""",
                           (project_id, project_id, project_id, project_id, project_id, project_id))
            event(cursor, project_id, "page_ready", {"page_id": str(page_id), "position": page["position"],
                                                      "confidence": confidence, "warnings": report["warnings"]})
            cursor.execute("SELECT status FROM projects WHERE id=%s", (project_id,))
            if cursor.fetchone()["status"] == "ready":
                event(cursor, project_id, "project_ready")

    def build_export(self, job):
        payload = dict(job["payload"])
        export = self.db.one("SELECT * FROM exports WHERE id=%s", (payload["export_id"],))
        if not export:
            return
        pages = self.db.all("SELECT * FROM pages WHERE project_id=%s AND status='ready' ORDER BY position", (job["project_id"],))
        if not pages:
            raise Refused("Project has no corrected pages to export")
        self.db.execute("UPDATE exports SET status='building' WHERE id=%s", (export["id"],))
        sources = [object_path(self.config.work / str(job["project_id"]), row["result_object"]) for row in pages]
        destination_name = f"{export['id']}.{export['kind'] if export['kind'] != 'image' else 'jpg'}"
        destination = object_path(self.config.exports, destination_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if export["kind"] == "image":
            if len(sources) != 1:
                raise Refused("Use ZIP to download several corrected images")
            shutil.copyfile(sources[0], destination)
        elif export["kind"] == "zip":
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for index, source in enumerate(sources, 1):
                    archive.write(source, f"page-{index:03d}.jpg")
        else:
            destination.write_bytes(img2pdf.convert([str(source) for source in sources], rotation=img2pdf.Rotation.ifvalid))
        digest = sha256(destination)
        with self.db.transaction() as cursor:
            cursor.execute("UPDATE exports SET status='ready',object_name=%s,bytes=%s,sha256=%s,ready_at=now() WHERE id=%s",
                           (destination_name, destination.stat().st_size, digest, export["id"]))
            event(cursor, job["project_id"], "export_ready", {"export_id": str(export["id"]), "kind": export["kind"]})

    def cleanup(self):
        expired = self.db.all("SELECT id FROM projects WHERE expires_at < now() FOR UPDATE SKIP LOCKED LIMIT 100")
        for row in expired:
            project_id = row["id"]
            pages = self.db.all("SELECT source_object FROM pages WHERE project_id=%s", (project_id,))
            exports = self.db.all("SELECT object_name FROM exports WHERE project_id=%s AND object_name IS NOT NULL", (project_id,))
            for page in pages:
                object_path(self.config.incoming, page["source_object"]).unlink(missing_ok=True)
            for item in exports:
                object_path(self.config.exports, item["object_name"]).unlink(missing_ok=True)
            shutil.rmtree(self.config.work / str(project_id), ignore_errors=True)
            self.db.execute("DELETE FROM projects WHERE id=%s", (project_id,))


def loop(config: Config, db: Database):
    processor = Processor(config, db)
    next_cleanup = 0.0
    while True:
        worked = processor.run_one()
        now = time.monotonic()
        if now >= next_cleanup:
            try:
                processor.cleanup()
            finally:
                next_cleanup = now + 300
        if not worked:
            time.sleep(0.75)
