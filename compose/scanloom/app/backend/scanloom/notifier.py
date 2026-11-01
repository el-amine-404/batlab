"""Tells the admin what was asked for and what was scanned, through Apprise
(batlab_common.notify). Runs on its own: the only part of Scanloom that can reach
beyond its private network, reading finished thumbnails and never a user's upload.

Previews are of pages the worker already made. "safe": so small and blurred that a
page can be recognised but not read, since these are often IDs and go to whatever the
channel is, Discord included. "readable": the thumbnail. "off": none.
"""
from __future__ import annotations

import logging
import os
import select
import time
from urllib.parse import urlsplit

import cv2
import numpy as np
from batlab_common import notify

from .config import Config
from .db import Database
from .storage import object_path

logger = logging.getLogger("scanloom.notifier")

NOTIFY_DEFAULTS = {
    "enabled": True,
    "url": os.getenv("SCANLOOM_APPRISE_URL", ""),
    "tag": os.getenv("SCANLOOM_APPRISE_TAG", ""),
    "on_requested": True,
    "on_finished": True,
    "previews": "safe",
    "link": os.getenv("SCANLOOM_PUBLIC_URL", "https://scanloom.homelab.lan"),
}
MAX_PREVIEWS = 4
SAFE_EDGE = 220
MAX_ATTEMPTS = 6


def clean_settings(values: dict) -> dict:
    """What may be stored; ValueError with a sentence otherwise."""
    unknown = set(values) - set(NOTIFY_DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}")
    merged = NOTIFY_DEFAULTS | values
    for key in ("enabled", "on_requested", "on_finished"):
        if not isinstance(merged[key], bool):
            raise ValueError(f"{key} must be true or false")
    if merged["previews"] not in {"off", "safe", "readable"}:
        raise ValueError("previews must be off, safe or readable")
    for key in ("url", "tag", "link"):
        if not isinstance(merged[key], str) or len(merged[key]) > 500:
            raise ValueError(f"{key} must be text of at most 500 characters")
    for key in ("url", "link"):
        if merged[key]:
            parts = urlsplit(merged[key])
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                raise ValueError(f"{key} must be an http or https address")
    if merged["enabled"] and not merged["url"]:
        raise ValueError("Give Apprise's address, like http://apprise:8000/notify/scanloom")
    return merged


def load_settings(db: Database) -> dict:
    row = db.one("SELECT values FROM notify_settings WHERE singleton=true")
    stored = dict(row["values"]) if row else {}
    return NOTIFY_DEFAULTS | {key: value for key, value in stored.items() if key in NOTIFY_DEFAULTS}


def preview(data: bytes, level: str) -> bytes | None:
    """A JPEG of a thumbnail, which every channel can show."""
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    if level == "safe":
        scale = SAFE_EDGE / max(image.shape[:2])
        if scale < 1:
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        image = cv2.GaussianBlur(image, (0, 0), 2.2)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return encoded.tobytes() if ok else None


class Notifier:
    def __init__(self, config: Config, db: Database):
        self.config, self.db = config, db

    def _previews(self, project_id, level: str) -> list[tuple[str, bytes]]:
        if level == "off":
            return []
        rows = self.db.all("""SELECT position,thumb_object FROM pages WHERE project_id=%s AND thumb_object IS NOT NULL
                              ORDER BY position LIMIT %s""", (project_id, MAX_PREVIEWS))
        found = []
        for row in rows:
            try:
                data = object_path(self.config.work / str(project_id), row["thumb_object"]).read_bytes()
            except OSError:
                continue
            made = preview(data, level)
            if made:
                found.append((f"page-{row['position'] + 1}.jpg", made))
        return found

    def _card(self, item: dict, settings: dict) -> tuple[str, str, str, list] | None:
        """(title, body, kind, attachments), or None when this isn't to be told."""
        if item["kind"] == "test":
            return "Scanloom test", notify.fields_text([("Previews", settings["previews"])]), "info", []
        if not settings[f"on_{item['kind']}"]:
            return None
        project = self.db.one("SELECT * FROM projects WHERE id=%s", (item["project_id"],))
        if not project or project["status"] == "deleting":
            return None
        fields = [("Scan", project["title"]), ("By", project["owner_name"] or "-")]
        if item["kind"] == "requested":
            count = self.db.one("SELECT count(*)::int AS n FROM pages WHERE project_id=%s AND status NOT IN ('ready','failed','blocked')", (project["id"],))["n"]
            title, kind = f"Scan asked for: {count} photo{'' if count == 1 else 's'}", "info"
        else:
            bad = project["failed_count"]
            fields.append(("Pages", f"{project['completed_count']} finished"))
            if project["warning_count"]:
                fields.append(("To review", str(project["warning_count"])))
            if bad:
                fields.append(("Failed", str(bad)))
                errors = self.db.all("SELECT original_name,error FROM pages WHERE project_id=%s AND status IN ('failed','blocked') ORDER BY position LIMIT 3", (project["id"],))
                fields.append(("Why", "\n".join(f"{row['original_name']}: {row['error'] or 'unknown'}" for row in errors)))
            title, kind = ("Scan finished with failed pages", "warning") if bad else ("Scan finished", "success")
        if settings["link"]:
            fields.append(("Open", f"{settings['link'].rstrip('/')}/scans/{project['public_id']}"))
        return title, notify.fields_text(fields), kind, self._previews(project["id"], settings["previews"])

    def _next(self) -> dict | None:
        return self.db.one("""UPDATE notifications SET attempts=attempts+1,available_at=now()+interval '1 minute' WHERE id=(
                                  SELECT id FROM notifications WHERE status='queued' AND available_at<=now()
                                  ORDER BY available_at,id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""")

    def run_one(self) -> bool:
        item = self._next()
        if not item:
            return False
        settings = load_settings(self.db)
        forced = item["kind"] == "test"
        if (not settings["enabled"] and not forced) or not settings["url"]:
            self.db.execute("UPDATE notifications SET status='skipped',error=%s WHERE id=%s",
                            (None if settings["url"] else "No Apprise address is set", item["id"]))
            return True
        card = self._card(item, settings)
        if card is None:
            self.db.execute("UPDATE notifications SET status='skipped' WHERE id=%s", (item["id"],))
            return True
        title, body, kind, attachments = card
        try:
            notify.send(settings["url"], title, body or title, tag=settings["tag"] or None, kind=kind, attach=attachments, timeout=30)
        except notify.NotSent as error:
            retry = not forced and error.reached is False and item["attempts"] < MAX_ATTEMPTS
            logger.warning("Notification %s not sent: %s", item["id"], error)
            self.db.execute("""UPDATE notifications SET status=%s,title=%s,error=%s,available_at=now()+(%s*interval '1 second') WHERE id=%s""",
                            ("queued" if retry else "failed", title, str(error)[:500], min(900, 30 * 2 ** item["attempts"]), item["id"]))
            return True
        self.db.execute("UPDATE notifications SET status='sent',title=%s,error=NULL,sent_at=now() WHERE id=%s", (title, item["id"]))
        logger.info("Sent: %s", title)
        return True


def loop(config: Config, db: Database) -> None:
    notifier = Notifier(config, db)
    logger.info("Notifier started")
    wake = None
    while True:
        try:
            if wake is None or wake.closed:
                wake = db.connect()
                with wake.cursor() as cursor:
                    cursor.execute("LISTEN scanloom_notify")
            while notifier.run_one():
                pass
            db.execute("DELETE FROM notifications WHERE created_at < now()-interval '30 days'")
            if select.select([wake], [], [], 30)[0]:
                wake.poll()
                wake.notifies.clear()
        except Exception:
            logger.exception("The notifier's loop failed; trying again")
            wake = None
            time.sleep(5)


__all__ = ["NOTIFY_DEFAULTS", "Notifier", "clean_settings", "load_settings", "loop"]
