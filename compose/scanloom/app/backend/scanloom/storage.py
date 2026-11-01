"""Where objects are, by an opaque name: no path ever crosses the API."""
from __future__ import annotations

import json
from pathlib import Path


def event(cursor, project: dict, name: str, data: dict | None = None) -> None:
    """Recorded, and told to the browsers (the table's trigger) when the transaction commits."""
    cursor.execute("INSERT INTO project_events(project_id,public_id,owner,event,data) VALUES (%s,%s,%s,%s,%s::jsonb)",
                   (project["id"], project["public_id"], project["owner"], name, json.dumps(data or {})))


def object_path(root: Path, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("Invalid storage object")
    return root / name


def scan_ended(cursor, project_id) -> None:
    """After a page's end: when that was the last of a scan someone asked for, it is told, once."""
    cursor.execute("""UPDATE projects SET scan_pending=false WHERE id=%s AND scan_pending AND status IN ('ready','failed') RETURNING id""", (project_id,))
    if cursor.fetchone():
        cursor.execute("INSERT INTO notifications(project_id,kind) VALUES (%s,'finished')", (project_id,))
