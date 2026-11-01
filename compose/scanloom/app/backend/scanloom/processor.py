from __future__ import annotations

import json
import logging
import os
import re
import secrets
import select
import shutil
import socket
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import cv2
import img2pdf
import numpy as np

from .config import Config
from .db import Database
from .imaging import (appearance, detect, detect_orientation, fitted, normalize, normalized_corners, on_white,
                      pixel_corners, quality, warp, write_jpeg, write_png, write_webp)
from .security import Refused, clamav, inspect_container, sha256
from .storage import event, object_path, scan_ended

logger = logging.getLogger("scanloom.worker")

LEASE_SECONDS = 120
THUMB_EDGE, PREVIEW_EDGE, SOURCE_PREVIEW_EDGE = 480, 1800, 2200
PDF_QUALITY = {"balanced": (2400, 88), "small": (1600, 74)}
PAPER = {"a4": (210, 297), "letter": (215.9, 279.4), "a5": (148, 210)}  # mm


def safe_name(title: str) -> str:
    return re.sub(r'[\x00-\x1f\x7f\\/:*?"<>|]+', "-", title).strip(" .-") or "scan"


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


class Processor:
    def __init__(self, config: Config, db: Database):
        self.config, self.db = config, db
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(3)}"
        self._current: int | None = None

    # ---- the queue ---------------------------------------------------------------------------

    def claim(self):
        with self.db.transaction() as cursor:
            # A job whose worker died is claimed again, but only so many times: an
            # image that kills the process must not take the worker down forever.
            maximum = 2 * int(self.db.settings()["worker_max_attempts"])
            cursor.execute("""UPDATE jobs SET status='failed',error='The worker stopped during this job too many times',
                              finished_at=now(),lease_until=NULL
                              WHERE status='running' AND lease_until < now() AND attempts >= %s
                              RETURNING project_id,page_id""", (maximum,))
            for row in cursor.fetchall():
                if row["page_id"]:
                    cursor.execute("UPDATE pages SET status='failed',error='Processing this image stopped the worker' WHERE id=%s", (row["page_id"],))
                    cursor.execute("SELECT project_refresh(%s)", (row["project_id"],))
            cursor.execute("""
                WITH candidate AS (
                    SELECT id FROM jobs
                    WHERE (status='queued' AND available_at <= now())
                       OR (status='running' AND lease_until < now())
                    ORDER BY priority DESC, available_at, id FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE jobs j SET status='running', attempts=j.attempts+1,
                    worker_id=%s, started_at=now(), lease_until=now()+(%s*interval '1 second')
                FROM candidate WHERE j.id=candidate.id RETURNING j.*
            """, (self.worker_id, LEASE_SECONDS))
            job = cursor.fetchone()
            if job:
                logger.info("Claimed job %d [kind=%s, project=%s, page=%s, attempt=%d]",
                            job["id"], job["kind"], job["project_id"], job["page_id"], job["attempts"])
            return job

    def _heartbeat(self):
        """A long job (a 200-page PDF) keeps its lease; a dead worker's runs out within two minutes."""
        while True:
            time.sleep(LEASE_SECONDS / 3)
            job = self._current
            if job is None:
                continue
            try:
                self.db.execute("UPDATE jobs SET lease_until=now()+(%s*interval '1 second') WHERE id=%s AND status='running'",
                                (LEASE_SECONDS, job))
            except Exception as error:
                logger.warning("Could not extend job %s: %s", job, error)

    def finish(self, job):
        self.db.execute("UPDATE jobs SET status='done',finished_at=now(),lease_until=NULL WHERE id=%s", (job["id"],))
        logger.info("Finished job %d [kind=%s, project=%s]", job["id"], job["kind"], job["project_id"])

    def _project(self, project_id) -> dict | None:
        return None if project_id is None else self.db.one("SELECT id,public_id,owner FROM projects WHERE id=%s", (project_id,))

    def fail(self, job, error: Exception):
        maximum = int(self.db.settings()["worker_max_attempts"])
        refused = isinstance(error, Refused)
        retry = job["attempts"] < maximum and not refused
        status = "queued" if retry else "failed"
        delay = min(300, 5 * (2 ** max(0, job["attempts"] - 1)))
        logger.exception("Job %d [kind=%s, project=%s, page=%s] failed (attempt %d/%d, status=%s): %s",
                         job["id"], job["kind"], job["project_id"], job["page_id"],
                         job["attempts"], maximum, status, error)
        message = str(error)[:500] or error.__class__.__name__
        project = self._project(job["project_id"])
        with self.db.transaction() as cursor:
            cursor.execute("UPDATE jobs SET status=%s,error=%s,available_at=now()+(%s*interval '1 second'),lease_until=NULL,finished_at=CASE WHEN %s THEN now() END WHERE id=%s",
                           (status, message, delay, not retry, job["id"]))
            if retry:
                return
            if not project:
                export_id = dict(job["payload"]).get("export_id")
                if export_id:  # a batch export belongs to no scan
                    cursor.execute("UPDATE exports SET status='failed',error=%s WHERE id=%s", (message, export_id))
                return
            if job["page_id"]:
                page_status = "blocked" if refused else "failed"
                # An edit that fails leaves the last good result in place and says so.
                cursor.execute("""UPDATE pages SET status=CASE WHEN result_object IS NULL THEN %s ELSE 'ready' END,error=%s,stage_started_at=NULL
                                  WHERE id=%s""", (page_status, message, job["page_id"]))
                cursor.execute("UPDATE projects SET error=%s WHERE id=%s", (message, job["project_id"]))
                cursor.execute("SELECT project_refresh(%s)", (job["project_id"],))
                scan_ended(cursor, job["project_id"])
            else:
                export_id = dict(job["payload"]).get("export_id")
                if export_id:
                    cursor.execute("UPDATE exports SET status='failed',error=%s WHERE id=%s", (message, export_id))
            event(cursor, project, "failed", {"message": message, "page_id": str(job["page_id"] or "")})

    def run_one(self) -> bool:
        job = self.claim()
        if not job:
            return False
        self._current = job["id"]
        try:
            kind = job["kind"]
            if kind == "prepare_page":
                self.prepare_page(job)
            elif kind == "process_page":
                self.process_page(job)
            elif kind == "render_page":
                self.render_page(job)
            elif kind == "derive_page":
                self.derive_page(job)
            elif kind == "build_export":
                self.build_export(job)
            elif kind == "remove_objects":
                self.remove_objects(job)
            elif kind == "cleanup":
                self.cleanup()
            self.finish(job)
        except Exception as error:
            self.fail(job, error)
        finally:
            self._current = None
        return True

    # ---- one page -----------------------------------------------------------------------------

    def _stage(self, project: dict, page_id, stage: str, **columns) -> None:
        assignments = "".join(f",{name}=%s" for name in columns)
        with self.db.transaction() as cursor:
            cursor.execute(f"UPDATE pages SET status=%s,stage_started_at=now(){assignments} WHERE id=%s",
                           (stage, *columns.values(), page_id))
            event(cursor, project, "page_stage", {"page_id": str(page_id), "stage": stage})

    def _load_page(self, page_id) -> dict | None:
        return self.db.one("SELECT p.*,pr.profile FROM pages p JOIN projects pr ON pr.id=p.project_id WHERE p.id=%s", (page_id,))

    def _appearance(self, page: dict) -> str:
        if page["appearance"] != "auto":
            return page["appearance"]
        return {"faithful": "color", "paper": "clean", "mono": "mono"}[page["profile"]]

    def _ingest(self, project: dict, page: dict, settings: dict) -> dict:
        """From an upload to a clean, metadata-free copy, safe to show: the security
        check and decoding, or, for a file this owner has already had scanned, the
        earlier copy and its crop. The hash of every original is in the database."""
        project_dir = self.config.work / str(page["project_id"])
        page_id = page["id"]
        source = object_path(self.config.incoming, page["source_object"])
        normalized_name, source_preview = f"{page_id}.png", f"{page_id}-source.webp"
        normalized = object_path(project_dir, normalized_name)
        donor = self.db.one("""SELECT p.* FROM pages p JOIN projects pr ON pr.id=p.project_id
                               WHERE p.source_sha256=%s AND p.id<>%s AND p.status='ready'
                                 AND p.normalized_object IS NOT NULL AND p.corners IS NOT NULL
                                 AND (pr.owner IS NOT DISTINCT FROM %s OR pr.owner IS NULL) AND pr.status<>'deleting'
                               ORDER BY (pr.page_count=1) DESC, p.created_at LIMIT 1""",
                            (page["source_sha256"], page_id, project["owner"]))
        if donor:
            started = time.monotonic()
            donor_dir = self.config.work / str(donor["project_id"])
            link_or_copy(object_path(donor_dir, donor["normalized_object"]), normalized)
            if donor["source_preview_object"]:
                link_or_copy(object_path(donor_dir, donor["source_preview_object"]), object_path(project_dir, source_preview))
            else:
                # A page from before there were such previews: made here, once.
                earlier = cv2.imread(str(normalized), cv2.IMREAD_COLOR)
                if earlier is None:
                    raise RuntimeError("The earlier copy of this image could not be read")
                write_webp(earlier, object_path(project_dir, source_preview), SOURCE_PREVIEW_EDGE, 82)
            with self.db.transaction() as cursor:
                cursor.execute("""UPDATE pages SET width=%s,height=%s,normalized_object=%s,source_preview_object=%s,malware_status=%s,detected_mime=%s,
                                  integrity=%s::jsonb,corners=%s::jsonb,rotation=%s,confidence=%s,reused_from=%s,quality=%s::jsonb WHERE id=%s""",
                               (donor["width"], donor["height"], normalized_name, source_preview, donor["malware_status"], donor["detected_mime"],
                                json.dumps(donor["integrity"]), json.dumps(donor["corners"]), donor["rotation"], donor["confidence"], donor["id"],
                                json.dumps({"detection_method": dict(donor["quality"] or {}).get("detection_method", "reused")}), page_id))
            source.unlink(missing_ok=True)
            logger.info("Page %s reuses the work on page %s", page_id, donor["id"])
            return {"reused": round(time.monotonic() - started, 2)}
        timings: dict[str, float] = {}
        clock = time.monotonic()
        self._stage(project, page_id, "scanning")
        clamav(source, self.config.clamd_host, self.config.clamd_port, int(settings["max_upload_bytes"]))
        mime, integrity = inspect_container(source)
        timings["scanning"] = round(time.monotonic() - clock, 2)
        clock = time.monotonic()
        self._stage(project, page_id, "decoding", malware_status="clean", detected_mime=mime, integrity=json.dumps(integrity))
        width, height = normalize(source, normalized, int(settings["max_pixels"]))
        image = cv2.imread(str(normalized), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Normalized image could not be read")
        write_webp(image, object_path(project_dir, source_preview), SOURCE_PREVIEW_EDGE, 82)
        timings["decoding"] = round(time.monotonic() - clock, 2)
        self.db.execute("UPDATE pages SET width=%s,height=%s,normalized_object=%s,source_preview_object=%s WHERE id=%s",
                        (width, height, normalized_name, source_preview, page_id))
        return timings

    def prepare_page(self, job):
        """Checked and made safe to show, then it waits to be asked for."""
        settings = self.db.settings()
        page = self._load_page(job["page_id"])
        if not page:
            return
        project = self._project(page["project_id"])
        timings = self._ingest(project, page, settings)
        page = self._load_page(page["id"])
        project_dir = self.config.work / str(page["project_id"])
        image = cv2.imread(str(object_path(project_dir, page["normalized_object"])), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Normalized image could not be read")
        thumb = f"{page['id']}-thumb.webp"
        write_webp(image, object_path(project_dir, thumb), THUMB_EDGE, 78)
        with self.db.transaction() as cursor:
            cursor.execute("UPDATE pages SET status='held',thumb_object=%s,timings=%s::jsonb,stage_started_at=NULL,version=version+1 WHERE id=%s",
                           (thumb, json.dumps(timings), page["id"]))
            cursor.execute("SELECT project_refresh(%s)", (page["project_id"],))
            event(cursor, project, "page_stage", {"page_id": str(page["id"]), "stage": "held"})

    def process_page(self, job):
        """To the finished page, from the upload or from what was prepared for review."""
        settings = self.db.settings()
        page = self._load_page(job["page_id"])
        if not page:
            return
        project = self._project(page["project_id"])
        page_id = page["id"]
        project_dir = self.config.work / str(page["project_id"])
        source = object_path(self.config.incoming, page["source_object"])
        timings = dict(page["timings"] or {})
        if not page["normalized_object"]:
            timings = self._ingest(project, page, settings)
            page = self._load_page(page_id)
        image = cv2.imread(str(object_path(project_dir, page["normalized_object"])), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Normalized image could not be read")
        width, height = image.shape[1], image.shape[0]
        if page["reused_from"] or "reused" in timings:
            self._stage(project, page_id, "rendering")
            found = pixel_corners(page["corners"], width, height)
            self._publish(project, page, image, found, float(page["confidence"] or 0),
                          dict(page["quality"] or {}).get("detection_method", "reused"), int(page["rotation"]), 0.0, timings, settings)
            source.unlink(missing_ok=True)
            return
        clock = time.monotonic()

        def lap(name: str) -> None:
            nonlocal clock
            now = time.monotonic()
            timings[name] = round(timings.get(name, 0) + now - clock, 2)
            clock = now

        self._stage(project, page_id, "detecting")
        found, confidence, method = detect(image)
        hint = page["corners_hint"]
        if hint and not (method.startswith("DocAligner") and confidence >= .72):
            # What the live camera followed for a moment beats any weaker second opinion on one frame.
            found, confidence, method = pixel_corners(hint, width, height), .72, "live camera boundary"
        lap("detecting")
        self._stage(project, page_id, "rendering")
        appearance_now = self._appearance(page)
        rotation, orientation_confidence = int(page["rotation"]), 0.0
        if rotation == 0:
            upright = warp(image, found, 0, appearance_now, float(page["corner_radius"]))
            rotation, orientation_confidence = detect_orientation(upright)
        lap("orienting")
        self.db.execute("UPDATE pages SET corners=%s::jsonb,rotation=%s,confidence=%s WHERE id=%s",
                        (json.dumps(normalized_corners(found, width, height)), rotation, confidence, page_id))
        page = self._load_page(page_id)
        self._publish(project, page, image, found, confidence, method, rotation, orientation_confidence, timings, settings, lap)
        # The metadata-free copy is the source from here on: the upload isn't kept.
        source.unlink(missing_ok=True)

    def render_page(self, job):
        """An edit: crop, rotation, look. Nothing is scanned or detected again."""
        settings = self.db.settings()
        page = self._load_page(job["page_id"])
        if not page or not page["normalized_object"]:
            return
        project = self._project(page["project_id"])
        self._stage(project, page["id"], "rendering")
        started = time.monotonic()
        normalized = object_path(self.config.work / str(page["project_id"]), page["normalized_object"])
        image = cv2.imread(str(normalized), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("Normalized image could not be read")
        if not page["source_preview_object"]:
            name = f"{page['id']}-source.webp"
            write_webp(image, object_path(normalized.parent, name), SOURCE_PREVIEW_EDGE, 82)
            self.db.execute("UPDATE pages SET source_preview_object=%s WHERE id=%s", (name, page["id"]))
            page = self._load_page(page["id"])
        if page["corners"]:
            found = pixel_corners(page["corners"], image.shape[1], image.shape[0])
            confidence, method = float(page["confidence"] or 0), "manual"
        else:
            found, confidence, method = detect(image)
            self.db.execute("UPDATE pages SET corners=%s::jsonb,confidence=%s WHERE id=%s",
                            (json.dumps(normalized_corners(found, image.shape[1], image.shape[0])), confidence, page["id"]))
        self._publish(project, page, image, found, confidence, method, int(page["rotation"]), 0.0,
                      {"edit": round(time.monotonic() - started, 2)}, settings)

    def _publish(self, project, page, image, found, confidence, method, rotation, orientation_confidence, timings, settings, lap=None):
        """The finished page and its smaller forms, then, in one step, the row
        that points at them; unless a newer edit came meanwhile, whose own job follows."""
        project_dir = self.config.work / str(page["project_id"])
        page_id = page["id"]
        corrected = warp(image, found, rotation, self._appearance(page), float(page["corner_radius"]))
        report = quality(corrected, confidence) | {"detection_method": method}
        report["automatic_rotation"] = rotation if orientation_confidence else 0
        report["orientation_confidence"] = orientation_confidence
        outside = int(np.count_nonzero((found[:, 0] < 0) | (found[:, 0] >= image.shape[1])
                                       | (found[:, 1] < 0) | (found[:, 1] >= image.shape[0])))
        report["outside_corner_count"] = outside
        if outside:
            report["warnings"].append(
                f"{outside} detected corner{'s' if outside != 1 else ''} extend beyond the photo; missing pixels cannot be recovered.")
        names = self._write_forms(corrected, project_dir, page_id, settings)
        result = object_path(project_dir, names["result"])
        if lap:
            lap("rendering")
        result_hash, result_bytes = sha256(result), result.stat().st_size
        jpeg_bytes = object_path(project_dir, names["jpeg"]).stat().st_size
        with self.db.transaction() as cursor:
            cursor.execute("""UPDATE pages SET status='ready',quality=%s::jsonb,result_object=%s,thumb_object=%s,preview_object=%s,
                              jpeg_object=%s,jpeg_bytes=%s,result_sha256=%s,result_bytes=%s,error=NULL,timings=%s::jsonb,
                              stage_started_at=NULL,version=version+1 WHERE id=%s AND edit_rev=%s""",
                           (json.dumps(report), names["result"], names["thumb"], names["preview"], names["jpeg"], jpeg_bytes,
                            result_hash, result_bytes, json.dumps(timings), page_id, page["edit_rev"]))
            if not cursor.rowcount:
                logger.info("Page %s changed during its render; the newer edit's job follows", page_id)
                return
            cursor.execute("SELECT project_refresh(%s)", (page["project_id"],))
            event(cursor, project, "page_ready", {"page_id": str(page_id), "position": page["position"],
                                                  "reused": bool(page["reused_from"])})
            cursor.execute("SELECT status FROM projects WHERE id=%s", (page["project_id"],))
            if cursor.fetchone()["status"] == "ready":
                event(cursor, project, "project_ready")
            scan_ended(cursor, page["project_id"])

    def _write_forms(self, corrected, project_dir: Path, page_id, settings) -> dict:
        names = {"result": f"{page_id}-result.png", "jpeg": f"{page_id}-result.jpg",
                 "preview": f"{page_id}-preview.webp", "thumb": f"{page_id}-thumb.webp"}
        write_png(corrected, object_path(project_dir, names["result"]))
        write_jpeg(corrected, object_path(project_dir, names["jpeg"]), int(settings["jpeg_quality"]))
        write_webp(corrected, object_path(project_dir, names["preview"]), PREVIEW_EDGE, 84)
        write_webp(corrected, object_path(project_dir, names["thumb"]), THUMB_EDGE, 78)
        return names

    def derive_page(self, job):
        """Smaller forms for a page finished before they existed."""
        page = self._load_page(job["page_id"])
        if not page or not page["result_object"]:
            return
        project_dir = self.config.work / str(page["project_id"])
        path = object_path(project_dir, page["result_object"])
        corrected = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if corrected is None:
            raise RuntimeError("Corrected page could not be read")
        settings = self.db.settings()
        page_id = page["id"]
        names = {"jpeg": f"{page_id}-result.jpg", "preview": f"{page_id}-preview.webp", "thumb": f"{page_id}-thumb.webp"}
        write_jpeg(corrected, object_path(project_dir, names["jpeg"]), int(settings["jpeg_quality"]))
        write_webp(corrected, object_path(project_dir, names["preview"]), PREVIEW_EDGE, 84)
        write_webp(corrected, object_path(project_dir, names["thumb"]), THUMB_EDGE, 78)
        source_preview = page["source_preview_object"]
        if not source_preview and page["normalized_object"]:
            image = cv2.imread(str(object_path(project_dir, page["normalized_object"])), cv2.IMREAD_COLOR)
            if image is not None:
                source_preview = f"{page_id}-source.webp"
                write_webp(image, object_path(project_dir, source_preview), SOURCE_PREVIEW_EDGE, 82)
        project = self._project(page["project_id"])
        with self.db.transaction() as cursor:
            cursor.execute("""UPDATE pages SET thumb_object=%s,preview_object=%s,jpeg_object=%s,jpeg_bytes=%s,source_preview_object=%s
                              WHERE id=%s""", (names["thumb"], names["preview"], names["jpeg"],
                                               object_path(project_dir, names["jpeg"]).stat().st_size, source_preview, page_id))
            event(cursor, project, "page_ready", {"page_id": str(page_id)})

    # ---- exports -----------------------------------------------------------------------------

    def build_export(self, job):
        payload = dict(job["payload"])
        export = self.db.one("SELECT * FROM exports WHERE id=%s", (payload["export_id"],))
        if not export:
            return
        if payload.get("batch"):
            return self._build_batch(job, payload, export)
        rows = self.db.all("SELECT * FROM pages WHERE project_id=%s AND status='ready' AND result_object IS NOT NULL ORDER BY position", (job["project_id"],))
        wanted = payload.get("page_ids") or []
        if wanted:
            # In the order asked for, which needn't be the scan's.
            by_id = {str(row["id"]): row for row in rows}
            rows = [by_id[page_id] for page_id in wanted if page_id in by_id]
        if not rows:
            raise Refused("This scan has no finished pages to export")
        project = self._project(job["project_id"])
        with self.db.transaction() as cursor:
            cursor.execute("UPDATE exports SET status='building' WHERE id=%s", (export["id"],))
            event(cursor, project, "export_building", {"export_id": str(export["id"])})
        project_dir = self.config.work / str(job["project_id"])
        options = dict(payload.get("options") or {}) | {"quality": payload.get("quality", "balanced")}
        destination_name = f"{export['id']}.{export['kind']}"
        destination = object_path(self.config.exports, destination_name)
        partial = destination.with_name(f".partial-{destination.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if export["kind"] == "zip":
                self._zip(partial, rows, project_dir, options)
            else:
                self._pdf(partial, rows, project_dir, options, export["filename"])
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
        digest = sha256(destination)
        with self.db.transaction() as cursor:
            cursor.execute("UPDATE exports SET status='ready',object_name=%s,bytes=%s,sha256=%s,ready_at=now() WHERE id=%s",
                           (destination_name, destination.stat().st_size, digest, export["id"]))
            event(cursor, project, "export_ready", {"export_id": str(export["id"]), "kind": export["kind"]})

    def _build_batch(self, job, payload: dict, export: dict) -> None:
        """One ZIP of several scans: a PDF each, or a folder of images each."""
        self.db.execute("UPDATE exports SET status='building' WHERE id=%s", (export["id"],))
        options = {"quality": payload.get("quality", "balanced")}
        destination_name = f"{export['id']}.zip"
        destination = object_path(self.config.exports, destination_name)
        partial = destination.with_name(f".partial-{destination.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        try:
            with tempfile.TemporaryDirectory(prefix="scanloom-batch-") as temporary, zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED) as archive:
                for project_id in payload["batch"]:
                    scan = self.db.one("SELECT id,title FROM projects WHERE id=%s AND status<>'deleting'", (project_id,))
                    if not scan:
                        continue
                    rows = self.db.all("SELECT * FROM pages WHERE project_id=%s AND status='ready' AND result_object IS NOT NULL ORDER BY position", (project_id,))
                    if not rows:
                        continue
                    stem = safe_name(scan["title"])
                    name, number = stem, 2
                    while name in used:
                        name, number = f"{stem} ({number})", number + 1
                    used.add(name)
                    folder = self.config.work / str(project_id)
                    if payload.get("format") == "images":
                        for index, row in enumerate(rows, 1):
                            chosen = row["result_object"] if options["quality"] == "lossless" or not row["jpeg_object"] else row["jpeg_object"]
                            source = object_path(folder, chosen)
                            archive.write(source, f"{name}/page-{index:03d}{source.suffix.lower()}")
                    else:
                        path = Path(temporary) / "scan.pdf"
                        self._pdf(path, rows, folder, options, f"{name}.pdf")
                        archive.write(path, f"{name}.pdf")
                        path.unlink()
            if not used:
                raise Refused("None of those scans has finished pages")
            os.replace(partial, destination)
        finally:
            partial.unlink(missing_ok=True)
        self.db.execute("UPDATE exports SET status='ready',object_name=%s,bytes=%s,sha256=%s,ready_at=now() WHERE id=%s",
                        (destination_name, destination.stat().st_size, sha256(destination), export["id"]))

    @staticmethod
    def _shaped(image, options: dict):
        """A page as an export asks for it: its colour, its largest size."""
        image = on_white(image)
        color = options.get("color", "keep")
        if color != "keep":
            image = appearance(image, color)
        if options.get("long_edge"):
            image = fitted(image, int(options["long_edge"]))
        return image

    def _zip(self, partial: Path, rows, project_dir: Path, options: dict) -> None:
        lossless = options["quality"] == "lossless"
        plain = options.get("color", "keep") == "keep" and not options.get("long_edge") and not options.get("jpeg_quality")
        with tempfile.TemporaryDirectory(prefix="scanloom-zip-") as temporary, zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_STORED) as archive:
            # Images are compressed already: storing them is as small and much faster.
            for index, row in enumerate(rows, 1):
                if plain:
                    source = object_path(project_dir, row["result_object"] if lossless or not row["jpeg_object"] else row["jpeg_object"])
                    archive.write(source, f"page-{index:03d}{source.suffix.lower()}")
                    continue
                image = cv2.imread(str(object_path(project_dir, row["result_object"])), cv2.IMREAD_UNCHANGED)
                if image is None:
                    raise RuntimeError(f"Page {index} could not be read for the ZIP")
                image = self._shaped(image, options)
                path = Path(temporary) / f"page-{index:03d}{'.png' if lossless else '.jpg'}"
                params = [cv2.IMWRITE_PNG_COMPRESSION, 3] if lossless else [cv2.IMWRITE_JPEG_QUALITY, int(options.get("jpeg_quality") or 88), cv2.IMWRITE_JPEG_OPTIMIZE, 1]
                if not cv2.imwrite(str(path), image, params):
                    raise RuntimeError(f"Page {index} could not be prepared for the ZIP")
                archive.write(path, path.name)
                path.unlink()

    def _pdf(self, partial: Path, rows, project_dir: Path, options: dict, filename: str | None) -> None:
        chosen = options["quality"]
        untouched = chosen == "lossless" and options.get("color", "keep") == "keep" and not options.get("long_edge")
        default_edge, default_jpeg = PDF_QUALITY.get(chosen, (None, None))
        shape = dict(options)
        if chosen != "lossless":
            shape["long_edge"] = options.get("long_edge") or default_edge
        with tempfile.TemporaryDirectory(prefix="scanloom-pdf-") as temporary:
            sources: list[str] = []
            for index, row in enumerate(rows, 1):
                source = object_path(project_dir, row["result_object"])
                if untouched and not float(row["corner_radius"]):
                    sources.append(str(source))
                    continue
                image = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
                if image is None:
                    raise RuntimeError(f"Page {index} could not be read for the PDF")
                image = self._shaped(image, shape)
                if chosen == "lossless":
                    path = Path(temporary) / f"page-{index:04d}.png"
                    written = cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
                else:
                    path = Path(temporary) / f"page-{index:04d}.jpg"
                    written = cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, int(options.get("jpeg_quality") or default_jpeg), cv2.IMWRITE_JPEG_OPTIMIZE, 1])
                if not written:
                    raise RuntimeError(f"Page {index} could not be prepared for the PDF")
                sources.append(str(path))
            layout = {}
            paper = PAPER.get(options.get("paper", "fit"))
            if paper:
                border = img2pdf.mm_to_pt(float(options.get("margin_mm") or 0))
                layout["layout_fun"] = img2pdf.get_layout_fun(pagesize=(img2pdf.mm_to_pt(paper[0]), img2pdf.mm_to_pt(paper[1])),
                                                              border=(border, border), fit=img2pdf.FitMode.into, auto_orient=True)
            with partial.open("wb") as output:
                img2pdf.convert(sources, rotation=img2pdf.Rotation.ifvalid, outputstream=output, title=Path(filename or "scan").stem, **layout)

    # ---- removing --------------------------------------------------------------------------

    def remove_objects(self, job):
        folder = self.config.work / str(job["project_id"])
        for name in dict(job["payload"]).get("objects", []):
            object_path(folder, name).unlink(missing_ok=True)
        for name in dict(job["payload"]).get("incoming", []):
            object_path(self.config.incoming, name).unlink(missing_ok=True)

    def _erase(self, project_id) -> None:
        for page in self.db.all("SELECT source_object FROM pages WHERE project_id=%s", (project_id,)):
            object_path(self.config.incoming, page["source_object"]).unlink(missing_ok=True)
        for item in self.db.all("SELECT object_name FROM exports WHERE project_id=%s AND object_name IS NOT NULL", (project_id,)):
            object_path(self.config.exports, item["object_name"]).unlink(missing_ok=True)
        shutil.rmtree(self.config.work / str(project_id), ignore_errors=True)
        self.db.execute("DELETE FROM projects WHERE id=%s", (project_id,))

    def cleanup(self):
        """What is due to go: deleted and expired scans, scans never given a page,
        old downloads, uploads nothing refers to, and the queue's own history."""
        due = self.db.all("""SELECT id FROM projects
                             WHERE status='deleting' OR expires_at < now()
                                OR (status='empty' AND created_at < now()-interval '1 day') LIMIT 100""")
        for row in due:
            self._erase(row["id"])
        for item in self.db.all("SELECT id,object_name FROM exports WHERE expires_at < now() LIMIT 500"):
            if item["object_name"]:
                object_path(self.config.exports, item["object_name"]).unlink(missing_ok=True)
            self.db.execute("DELETE FROM exports WHERE id=%s", (item["id"],))
        self.db.execute("DELETE FROM jobs WHERE status IN ('done','failed') AND finished_at < now()-interval '30 days'")
        self.db.execute("DELETE FROM project_events WHERE created_at < now()-interval '1 day'")
        cutoff = time.time() - 86400
        for path in self.config.incoming.glob("*"):
            try:
                if path.stat().st_mtime < cutoff and not self.db.one("SELECT 1 FROM pages WHERE source_object=%s", (path.name,)):
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass


def loop(config: Config, db: Database):
    processor = Processor(config, db)
    logger.info("Worker loop started (worker_id=%s)", processor.worker_id)
    threading.Thread(target=processor._heartbeat, name="scanloom-heartbeat", daemon=True).start()
    next_cleanup = 0.0
    wake = db.connect()
    with wake.cursor() as cursor:
        cursor.execute("LISTEN scanloom_jobs")
    while True:
        try:
            worked = processor.run_one()
            now = time.monotonic()
            if now >= next_cleanup:
                try:
                    processor.cleanup()
                except Exception:
                    logger.exception("Cleanup failed")
                next_cleanup = now + 300
        except Exception:
            logger.exception("The worker's loop failed; trying again")
            worked = False
            time.sleep(2)
        if not worked:
            # Woken by a new job at once, or looks again after a while (retries, leases).
            try:
                if select.select([wake], [], [], 5)[0]:
                    wake.poll()
                    wake.notifies.clear()
            except Exception:
                logger.warning("Lost the wake-up connection; reconnecting")
                try:
                    wake.close()
                    wake = db.connect()
                    with wake.cursor() as cursor:
                        cursor.execute("LISTEN scanloom_jobs")
                except Exception:
                    time.sleep(5)
