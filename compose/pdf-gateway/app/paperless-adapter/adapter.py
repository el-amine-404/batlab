#!/usr/bin/env python3
"""Exactly-once projection of archived documents into Paperless-ngx.

Paperless is a downstream copy, never the source of truth. This worker holds
the only Paperless token (environment; never logged, stored or returned). It
reads approved PDFs from the archive (read-only) and uses only the documented
REST API: no database or media writes.

Exactly once (Paperless 3.x accepts duplicate uploads and exposes no checksum):
  1. look up the marker custom field gateway_document_id before any upload;
  2. record upload_started_at durably *before* uploading, then the task ID;
  3. after a crash mid-upload, follow the consume task found by the
     deterministic filename "<documentId>.pdf" instead of uploading again.

  python adapter.py           consume pdf.gateway.paperless.v1
  python adapter.py resync    reconcile every import with Paperless and requeue drift
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import pika
import psycopg2

QUEUE = "pdf.gateway.paperless.v1"
MARKER_FIELD = "gateway_document_id"
ARCHIVE = Path(os.getenv("PDF_GATEWAY_ARCHIVE_ROOT", "/var/lib/pdf-archive")) / "archive"
MAX_ATTEMPTS = int(os.getenv("PDF_GATEWAY_PAPERLESS_MAX_ATTEMPTS", "20"))
TASK_WAIT_SECONDS = int(os.getenv("PDF_GATEWAY_PAPERLESS_TASK_WAIT_SECONDS", "60"))
MAX_UPLOAD = 256 * 1024 * 1024


class PaperlessError(Exception):
    def __init__(self, code: str, retryable: bool, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.detail = detail[:200]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a redirect could leak the token elsewhere
        return None


class Paperless:
    def __init__(self, base: str, token: str, timeout: int = 20):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/"):
            raise SystemExit("PDF_GATEWAY_PAPERLESS_URL must be a bare http(s) origin")
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect)
        self._field_id: int | None = None

    def call(self, method: str, path: str, *, query: dict | None = None, body: dict | None = None,
             multipart: tuple[bytes, str] | None = None):
        if not path.startswith("/api/"):
            raise ValueError("API paths only")
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Authorization": f"Token {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if multipart is not None:
            data, headers["Content-Type"] = multipart
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(16 * 1024 * 1024)
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            status = error.code
            if status in (401, 403):
                raise PaperlessError("PAPERLESS_AUTH", True, f"HTTP {status}") from None
            if status in (301, 302, 303, 307, 308):
                raise PaperlessError("PAPERLESS_REDIRECT_REFUSED", True, f"HTTP {status}") from None
            if status == 429 or status >= 500:
                raise PaperlessError("PAPERLESS_UNAVAILABLE", True, f"HTTP {status}") from None
            raise PaperlessError("PAPERLESS_REJECTED", False, f"HTTP {status}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
            raise PaperlessError("PAPERLESS_UNAVAILABLE", True, type(error).__name__) from None
        except json.JSONDecodeError:
            raise PaperlessError("PAPERLESS_PROTOCOL", True, "non-JSON response") from None

    def field_id(self) -> int:
        if self._field_id is None:
            found = self.call("GET", "/api/custom_fields/", query={"name__iexact": MARKER_FIELD})["results"]
            if not found:
                found = [self.call("POST", "/api/custom_fields/", body={"name": MARKER_FIELD, "data_type": "string"})]
            self._field_id = int(found[0]["id"])
        return self._field_id

    def find_by_marker(self, document_id: str) -> list[int]:
        self.field_id()
        query = json.dumps([MARKER_FIELD, "exact", document_id])
        results = self.call("GET", "/api/documents/", query={"custom_field_query": query, "fields": "id,custom_fields"})["results"]
        # Double-check the marker value client-side; never trust a filter we cannot see.
        return sorted(int(r["id"]) for r in results
                      if any(f.get("field") == self._field_id and f.get("value") == document_id for f in r.get("custom_fields", [])))

    def tag_ids(self, names: list[str]) -> list[int]:
        ids = []
        for name in names:
            found = self.call("GET", "/api/tags/", query={"name__iexact": name})["results"]
            if not found:
                # matching_algorithm 0: never auto-assign this tag to other documents.
                found = [self.call("POST", "/api/tags/", body={"name": name, "matching_algorithm": 0})]
            ids.append(int(found[0]["id"]))
        return ids

    def upload(self, pdf: bytes, document_id: str, title: str, created: str | None, tags: list[int]) -> str:
        boundary = uuid.uuid4().hex
        parts = []

        def field(name: str, value: str) -> None:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())

        field("title", title)
        if created:
            field("created", created)
        for tag in tags:
            field("tags", str(tag))
        field("custom_fields", json.dumps({str(self.field_id()): document_id}))
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{document_id}.pdf"\r\n'
                     f"Content-Type: application/pdf\r\n\r\n".encode() + pdf + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        task = self.call("POST", "/api/documents/post_document/", multipart=(b"".join(parts), f"multipart/form-data; boundary={boundary}"))
        if not isinstance(task, str) or len(task) > 100:
            raise PaperlessError("PAPERLESS_PROTOCOL", True, "no task id")
        return task

    def task(self, task_id: str) -> dict | None:
        results = self.call("GET", "/api/tasks/", query={"task_id": task_id})["results"]
        return results[0] if results else None

    def task_for_filename(self, filename: str) -> dict | None:
        tasks = self.call("GET", "/api/tasks/", query={"page_size": 100, "task_name": "consume_file"})["results"]
        matches = [t for t in tasks if (t.get("input_data") or {}).get("filename") == filename]
        return max(matches, key=lambda t: t.get("date_created") or "") if matches else None

    def exists(self, paperless_id: int, document_id: str) -> bool:
        try:
            doc = self.call("GET", f"/api/documents/{int(paperless_id)}/", query={"fields": "id,custom_fields"})
        except PaperlessError as error:
            if error.code == "PAPERLESS_REJECTED":
                return False
            raise
        return any(f.get("field") == self.field_id() and f.get("value") == document_id for f in doc.get("custom_fields", []))

    def update(self, paperless_id: int, title: str, tags: list[int]) -> None:
        self.call("PATCH", f"/api/documents/{int(paperless_id)}/", body={"title": title, "tags": tags})


def database():
    return psycopg2.connect(
        host=os.getenv("PDF_GATEWAY_DATABASE_HOST", "postgres"),
        dbname=os.getenv("PDF_GATEWAY_DATABASE_NAME", "pdf_gateway"),
        user=os.getenv("PDF_GATEWAY_DATABASE_USER", "pdf_gateway"),
        password=os.environ["PDF_GATEWAY_DATABASE_PASSWORD"],
    )


def paperless() -> Paperless:
    return Paperless(os.environ["PDF_GATEWAY_PAPERLESS_URL"], os.environ["PDF_GATEWAY_PAPERLESS_TOKEN"])


def set_state(cursor, document_id: str, status: str, **fields) -> None:
    columns = ", ".join(f"{key}=%s" for key in fields)
    cursor.execute(f"UPDATE paperless_imports SET status=%s{', ' + columns if columns else ''}, updated_at=now() WHERE document_id=%s",
                   (status, *fields.values(), document_id))
    cursor.execute("UPDATE scan_jobs SET import_status=%s, updated_at=now() WHERE id=(SELECT scan_id FROM archive_documents WHERE document_id=%s)",
                   (status, document_id))


def enqueue(cursor, document_id: str, delay_seconds: int = 0) -> None:
    cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload,available_at) VALUES (%s,'DOCUMENT',%s,'PAPERLESS_QUEUED',jsonb_build_object('documentId',%s::text),now()+make_interval(secs=>%s))",
                   (str(uuid.uuid4()), document_id, document_id, delay_seconds))


def resolve_task(client: Paperless, task: dict | None) -> int | None:
    """Paperless document ID for a finished task, None while pending; raises on failure."""
    if task is None:
        return None
    status = str(task.get("status", "")).lower()
    if status == "success":
        ids = task.get("related_document_ids") or [(task.get("result_data") or {}).get("document_id")]
        if not ids or ids[0] is None:
            raise PaperlessError("PAPERLESS_PROTOCOL", True, "task without document")
        return int(ids[0])
    if status in ("failure", "revoked"):
        raise PaperlessError("PAPERLESS_TASK_FAILED", True, status)
    return None


def project(document_id: str, client: Paperless) -> None:
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT i.status,i.task_id,i.upload_started_at,i.paperless_document_id,i.synced_revision,i.attempts,
                                 d.archive_path,d.pdf_file,d.sha256,d.metadata_revision,d.sidecar
                            FROM paperless_imports i JOIN archive_documents d USING (document_id)
                           WHERE i.document_id=%s FOR UPDATE OF i""", (document_id,))
        row = cursor.fetchone()
        if row is None or row[0] == "FAILED_FINAL":
            return
        status, task_id, upload_started, paperless_id, synced, attempts, archive_path, pdf_file, sha, revision, sidecar = row
        if status == "IMPORTED" and synced is not None and synced >= revision:
            return
        syncing = status == "IMPORTED"  # already in Paperless: only title/tags need to catch up
        if attempts >= MAX_ATTEMPTS and not syncing:
            set_state(cursor, document_id, "FAILED_FINAL", last_error="RETRY_LIMIT")
            return
        if not syncing:
            set_state(cursor, document_id, "IMPORTING", attempts=attempts + 1)
    try:
        if paperless_id is None:
            found = client.find_by_marker(document_id)
            if len(found) > 1:
                raise PaperlessError("DUPLICATE_IN_PAPERLESS", False, f"{len(found)} documents carry this marker")
            paperless_id = found[0] if found else None
        if paperless_id is None and task_id is None and upload_started is not None:
            # A previous attempt may have uploaded before it could record the task.
            task = client.task_for_filename(f"{document_id}.pdf")
            if task is not None:
                task_id = task["task_id"]
                with database() as connection, connection.cursor() as cursor:
                    cursor.execute("UPDATE paperless_imports SET task_id=%s WHERE document_id=%s", (task_id, document_id))
        if paperless_id is None and task_id is not None:
            paperless_id = wait_for_task(client, task_id)
            if paperless_id is None:
                raise PaperlessError("PAPERLESS_TASK_PENDING", True)
        if paperless_id is None:
            path = ARCHIVE / archive_path / pdf_file
            data = path.read_bytes()
            if len(data) > MAX_UPLOAD or hashlib.sha256(data).hexdigest() != sha:
                raise PaperlessError("ARCHIVE_INTEGRITY", False, "archived bytes do not match the index")
            with database() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE paperless_imports SET upload_started_at=now(), task_id=NULL WHERE document_id=%s", (document_id,))
            tags = client.tag_ids(sidecar.get("tags", []))
            created = (sidecar.get("documentDate") or {}).get("value")
            task_id = client.upload(data, document_id, sidecar["title"], created, tags)
            with database() as connection, connection.cursor() as cursor:
                cursor.execute("UPDATE paperless_imports SET task_id=%s WHERE document_id=%s", (task_id, document_id))
            paperless_id = wait_for_task(client, task_id)
            if paperless_id is None:
                raise PaperlessError("PAPERLESS_TASK_PENDING", True)
            synced_now = revision  # uploaded with the current title and tags
        else:
            # Existing Paperless document: bring title and tags up to the sidecar revision.
            client.update(paperless_id, sidecar["title"], client.tag_ids(sidecar.get("tags", [])))
            synced_now = revision
        with database() as connection, connection.cursor() as cursor:
            set_state(cursor, document_id, "IMPORTED", paperless_document_id=paperless_id, synced_revision=synced_now,
                      last_error=None)
            record_mapping(cursor, document_id, paperless_id, task_id, sidecar, revision)
            cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('PAPERLESS_IMPORTED','DOCUMENT',%s,'SUCCESS',%s,%s::jsonb)",
                           (document_id, str(uuid.uuid4()), json.dumps({"paperlessDocumentId": paperless_id, "revision": synced_now})))
    except PaperlessError as error:
        with database() as connection, connection.cursor() as cursor:
            if syncing:
                # The document is safely in Paperless; a failed metadata sync is retried, not a failed import.
                cursor.execute("UPDATE paperless_imports SET last_error=%s, updated_at=now() WHERE document_id=%s",
                               (f"SYNC_{error.code}"[:200], document_id))
                enqueue(cursor, document_id, min(3600, 60 * 2 ** min(attempts, 6)))
            elif error.retryable:
                delay = 15 if error.code == "PAPERLESS_TASK_PENDING" else min(3600, 30 * 2 ** min(attempts, 7))
                set_state(cursor, document_id, "FAILED_RETRYABLE", last_error=error.code)
                if error.code == "PAPERLESS_TASK_FAILED":
                    cursor.execute("UPDATE paperless_imports SET task_id=NULL, upload_started_at=NULL WHERE document_id=%s", (document_id,))
                enqueue(cursor, document_id, delay)
            else:
                set_state(cursor, document_id, "FAILED_FINAL", last_error=error.code)
            cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('PAPERLESS_IMPORT_FAILED','DOCUMENT',%s,'FAILURE',%s,%s::jsonb)",
                           (document_id, str(uuid.uuid4()), json.dumps({"code": error.code, "retryable": error.retryable})))


def wait_for_task(client: Paperless, task_id: str) -> int | None:
    deadline = time.monotonic() + TASK_WAIT_SECONDS
    while True:
        paperless_id = resolve_task(client, client.task(task_id))
        if paperless_id is not None or time.monotonic() > deadline:
            return paperless_id
        time.sleep(2)


def record_mapping(cursor, document_id: str, paperless_id: int, task_id: str | None, sidecar: dict, revision: int) -> None:
    """Ask the archive's single writer to record the mapping in the sidecar."""
    integrations = sidecar.get("integrations") or {}
    if integrations.get("paperlessDocumentId") == paperless_id:
        return
    changes = {"integrations": {"paperlessDocumentId": paperless_id, "paperlessTaskId": task_id}}
    edit_id = str(uuid.uuid4())
    cursor.execute("INSERT INTO metadata_edits(id,document_id,expected_revision,changes) VALUES (%s,%s,%s,%s::jsonb)",
                   (edit_id, document_id, revision, json.dumps(changes)))
    cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'DOCUMENT',%s,'METADATA_EDIT_QUEUED',jsonb_build_object('editId',%s::text))",
                   (str(uuid.uuid4()), document_id, edit_id))


def resync(client: Paperless) -> dict:
    """Reconcile every projection with Paperless; requeue drift. Never deletes anything."""
    report = {"checked": 0, "missing": [], "requeued": [], "duplicates": []}
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT i.document_id,i.status,i.paperless_document_id,i.synced_revision,d.metadata_revision
                            FROM paperless_imports i JOIN archive_documents d USING (document_id) ORDER BY i.document_id""")
        rows = cursor.fetchall()
    for document_id, status, paperless_id, synced, revision in rows:
        report["checked"] += 1
        found = client.find_by_marker(document_id)
        if len(found) > 1:
            report["duplicates"].append(document_id)
            continue
        with database() as connection, connection.cursor() as cursor:
            if status == "IMPORTED" and not found:
                report["missing"].append(document_id)
                set_state(cursor, document_id, "PENDING", paperless_document_id=None, task_id=None,
                          upload_started_at=None, synced_revision=None, attempts=0)
                enqueue(cursor, document_id)
            elif status != "IMPORTED" or synced is None or synced < revision:
                if status == "FAILED_FINAL":
                    set_state(cursor, document_id, "PENDING", attempts=0)
                report["requeued"].append(document_id)
                enqueue(cursor, document_id)
    return report


def handle(channel, method, _properties, body: bytes, client: Paperless) -> None:
    try:
        document_id = json.loads(body)["documentId"]
        if not isinstance(document_id, str) or not document_id.startswith("doc-") or len(document_id) != 30:
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError):
        channel.basic_reject(method.delivery_tag, requeue=False)
        return
    try:
        project(document_id, client)
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        print(json.dumps({"event": "paperless_adapter_error", "documentId": document_id, "errorType": type(error).__name__}), flush=True)
        channel.basic_nack(method.delivery_tag, requeue=True)
        time.sleep(5)


def main() -> None:
    client = paperless()
    if sys.argv[1:] == ["resync"]:
        print(json.dumps(resync(client), sort_keys=True))
        return
    credentials = pika.PlainCredentials(os.environ["PDF_GATEWAY_RABBITMQ_USER"], os.environ["PDF_GATEWAY_RABBITMQ_PASSWORD"])
    parameters = pika.ConnectionParameters(os.getenv("PDF_GATEWAY_RABBITMQ_HOST", "rabbitmq"), credentials=credentials,
                                           heartbeat=0, blocked_connection_timeout=60)
    while True:
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE, on_message_callback=lambda ch, m, p, b: handle(ch, m, p, b, client))
            channel.start_consuming()
        except (pika.exceptions.AMQPError, psycopg2.Error, OSError):
            time.sleep(5)


if __name__ == "__main__":
    main()
