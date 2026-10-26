#!/usr/bin/env python3
"""Discord (through Apprise) messages about each file: one when it arrives, one when
it is done: ready (with its folder and whether it reached Paperless), held, blocked
or failed. Only facts that help, no IDs; each message links to the file's page.

It reads the gateway's database and remembers what it sent (pdf_notifications), so a
restart sends nothing twice and files that were there before it started are skipped.
"""

from __future__ import annotations

import json
import os
import time

import psycopg2

import notify

ENDPOINT = os.getenv("PDF_GATEWAY_APPRISE_URL", "http://apprise:8000/notify/pdf-gateway")
TAG = os.getenv("PDF_GATEWAY_NOTIFY_TAG", "documents")
PUBLIC_URL = os.getenv("PDF_GATEWAY_PUBLIC_URL", "").rstrip("/")
PAPERLESS_URL = os.getenv("PDF_GATEWAY_PAPERLESS_PUBLIC_URL", "").rstrip("/")
POLL_SECONDS = int(os.getenv("PDF_GATEWAY_NOTIFY_POLL_SECONDS", "10"))
# The done message waits for the Paperless copy, at most this long.
PAPERLESS_WAIT_SECONDS = int(os.getenv("PDF_GATEWAY_NOTIFY_PAPERLESS_WAIT_SECONDS", "900"))
TERMINAL = ("READY", "REVIEW_REQUIRED", "BLOCKED", "FAILED")


def database():
    return psycopg2.connect(host=os.getenv("PDF_GATEWAY_DATABASE_HOST", "postgres"),
                            dbname=os.getenv("PDF_GATEWAY_DATABASE_NAME", "pdf_gateway"),
                            user=os.getenv("PDF_GATEWAY_DATABASE_USER", "pdf_gateway"),
                            password=os.environ["PDF_GATEWAY_DATABASE_PASSWORD"])


def size(bytes_: int) -> str:
    return f"{bytes_ / 1024:.0f} KB" if bytes_ < 1024 * 1024 else f"{bytes_ / 1024 / 1024:.1f} MB"


def link(scan_id: str) -> str:
    return f"[Open in the gateway]({PUBLIC_URL}/documents/{scan_id})" if PUBLIC_URL else ""


def done_message(row: dict) -> tuple[str, str, str]:
    """(title, body, kind) for a finished file."""
    name, status = row["display_name"], row["status"]
    if status == "READY":
        sidecar = row["sidecar"] or {}
        lines = [f"Folder: {row['archive_path']}"] if row["archive_path"] else []
        issuer = (sidecar.get("issuer") or {}).get("value")
        if issuer:
            lines.append(f"From: {issuer}")
        filed = (sidecar.get("organization") or {}).get("reviewState")
        if filed == "AI":
            lines.append("Filed by the AI from its text; check it.")
        elif filed in ("FALLBACK", "REVIEW_REQUIRED"):
            lines.append("Not filed: give it a title and folder.")
        paperless = row["paperless_status"]
        if paperless == "IMPORTED":
            target = f"[document {row['paperless_id']}]({PAPERLESS_URL}/documents/{row['paperless_id']}/details)" if PAPERLESS_URL else f"document {row['paperless_id']}"
            lines.append(f"In Paperless: {target}")
        elif paperless in ("FAILED_FINAL", "FAILED_RETRYABLE"):
            lines.append("Paperless refused it; retry from its page.")
        elif paperless in ("PENDING", "IMPORTING"):
            lines.append("Paperless: still on its way.")
        lines.append(link(row["id"]))
        return f"Ready: {row['title'] or name}", "\n".join(line for line in lines if line), "success"
    if status == "BLOCKED":
        return f"Blocked: {name}", f"The checks found it dangerous. It stays locked away and was not released.\n{link(row['id'])}", "failure"
    if status == "FAILED":
        return f"Couldn't process: {name}", f"Nothing was released. Upload it again later or look at its page.\n{link(row['id'])}", "warning"
    return f"Held: {name}", f"A check could not be completed or found something unusual; nothing was released.\n{link(row['id'])}", "warning"


def tick(connection) -> int:
    """One look. Each message is committed as sent right after it goes out, so a failure
    later in the same look neither loses nor repeats it."""
    cursor = connection.cursor()
    sent = 0
    # Files new since the last look.
    cursor.execute("INSERT INTO pdf_notifications(scan_id) SELECT id FROM scan_jobs ON CONFLICT DO NOTHING")
    connection.commit()
    cursor.execute("""SELECT n.scan_id, s.display_name, s.source_bytes FROM pdf_notifications n JOIN scan_jobs s ON s.id=n.scan_id
                       WHERE n.received_at IS NULL ORDER BY s.created_at LIMIT 20""")
    for scan_id, name, bytes_ in cursor.fetchall():
        notify.send(ENDPOINT, f"Received: {name}", f"Checking it now ({size(bytes_)}).\n{link(scan_id)}", tag=TAG, kind="info")
        cursor.execute("UPDATE pdf_notifications SET received_at=now() WHERE scan_id=%s", (scan_id,))
        connection.commit()
        sent += 1
    cursor.execute("""SELECT s.id, s.display_name, s.status, s.updated_at < now() - make_interval(secs=>%s) AS long_ago,
                             d.title, d.archive_path, d.sidecar, p.status, p.paperless_document_id, s.import_status
                        FROM pdf_notifications n JOIN scan_jobs s ON s.id=n.scan_id
                        LEFT JOIN archive_documents d ON d.scan_id=s.id
                        LEFT JOIN paperless_imports p ON p.document_id=d.document_id
                       WHERE n.done_at IS NULL AND n.received_at IS NOT NULL AND s.status = ANY(%s)
                       ORDER BY s.updated_at LIMIT 20""", (PAPERLESS_WAIT_SECONDS, list(TERMINAL)))
    for scan_id, name, status, long_ago, title, path, sidecar, paperless, paperless_id, wanted in cursor.fetchall():
        waiting = status == "READY" and (paperless in ("PENDING", "IMPORTING", "FAILED_RETRYABLE")
                                         or (wanted == "PENDING" and paperless is None))
        if waiting and not long_ago:
            continue  # tell once the Paperless copy is there (or refused)
        message = done_message({"id": scan_id, "display_name": name, "status": status, "title": title, "archive_path": path,
                                "sidecar": sidecar if isinstance(sidecar, dict) else json.loads(sidecar or "{}"),
                                "paperless_status": paperless, "paperless_id": paperless_id})
        notify.send(ENDPOINT, message[0], message[1], tag=TAG, kind=message[2])
        cursor.execute("UPDATE pdf_notifications SET done_at=now() WHERE scan_id=%s", (scan_id,))
        connection.commit()
        sent += 1
    return sent


def main() -> None:
    while True:
        try:
            connection = database()
            try:
                tick(connection)
            finally:
                connection.close()
        except notify.NotSent as error:
            # Apprise down or refusing: nothing is marked sent, so it is tried again.
            print(json.dumps({"event": "notify_not_sent", "reason": str(error)[:200]}), flush=True)
        except (psycopg2.Error, OSError) as error:
            print(json.dumps({"event": "notifier_error", "errorType": type(error).__name__}), flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
