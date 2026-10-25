#!/usr/bin/env python3
"""Single-concurrency, idempotent RabbitMQ analysis worker."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pika
import psycopg2

QUEUE = "pdf.gateway.scan.v1"
STORAGE = Path(os.getenv("PDF_GATEWAY_STORAGE_ROOT", "/var/lib/pdf-gateway"))
MAX_ATTEMPTS = int(os.getenv("PDF_GATEWAY_SCAN_MAX_ATTEMPTS", "3"))


def database():
    return psycopg2.connect(
        host=os.getenv("PDF_GATEWAY_DATABASE_HOST", "postgres"),
        dbname=os.getenv("PDF_GATEWAY_DATABASE_NAME", "pdf_gateway"),
        user=os.getenv("PDF_GATEWAY_DATABASE_USER", "pdf_gateway"),
        password=os.environ["PDF_GATEWAY_DATABASE_PASSWORD"],
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def claim(scan_id: str) -> tuple[Path, int] | None:
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status,quarantine_object,source_sha256 FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] in ("AWAITING_POLICY", "REVIEW_REQUIRED", "BLOCKED"):
            return None
        cursor.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM job_steps WHERE scan_id=%s AND step_type='ANALYSIS'", (scan_id,))
        attempt = cursor.fetchone()[0]
        if attempt > MAX_ATTEMPTS:
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',detection='UNKNOWN',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            return None
        path = STORAGE / "quarantine" / f"{row[1]}.bin"
        if not path.is_file() or sha256(path) != row[2]:
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',detection='UNKNOWN',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('ANALYSIS_FAILED','SCAN',%s,'FAILURE',%s,%s::jsonb)",
                           (scan_id, str(uuid.uuid4()), json.dumps({"code": "QUARANTINE_INTEGRITY_FAILED"})))
            return None
        cursor.execute("UPDATE scan_jobs SET status='SCANNING',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        cursor.execute("INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at) VALUES (%s,%s,'ANALYSIS','RUNNING',%s,now())",
                       (str(uuid.uuid4()), scan_id, attempt))
        return path, attempt


def persist(scan_id: str, attempt: int, report: dict) -> None:
    report_dir = STORAGE / "reports"
    report_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    target = report_dir / f"{scan_id}.json"
    with tempfile.NamedTemporaryFile(dir=report_dir, prefix=f".{scan_id}.", delete=False) as temporary:
        temporary.write(encoded)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, target)
    report_hash = hashlib.sha256(encoded).hexdigest()
    status = {"BLOCK": "BLOCKED", "REVIEW_REQUIRED": "REVIEW_REQUIRED", "CDR_ELIGIBLE": "AWAITING_POLICY"}[report["policyDecision"]]
    engine_versions = {item["engine"]: item.get("version", "UNKNOWN") for item in report["engines"]}
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source_sha256 FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != report["sourceSha256"]:
            raise RuntimeError("source identity changed before analysis commit")
        cursor.execute("UPDATE scan_jobs SET status=%s,detection=%s,policy=%s,updated_at=now(),version=version+1 WHERE id=%s",
                       (status, report["detection"], report["policyDecision"], scan_id))
        cursor.execute("UPDATE job_steps SET status='SUCCEEDED',finished_at=now(),engine_version=%s,details=%s::jsonb WHERE scan_id=%s AND step_type='ANALYSIS' AND attempt=%s",
                       (json.dumps(engine_versions, sort_keys=True)[:160], json.dumps(report), scan_id, attempt))
        cursor.execute("INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES (%s,%s,'SCAN_REPORT',%s,%s,%s,'RESTRICTED') ON CONFLICT (id) DO UPDATE SET sha256=EXCLUDED.sha256,bytes=EXCLUDED.bytes",
                       (f"report-{scan_id[5:]}", scan_id, f"reports/{scan_id}.json", report_hash, len(encoded)))
        cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('ANALYSIS_COMPLETED','SCAN',%s,'SUCCESS',%s,%s::jsonb)",
                       (scan_id, str(uuid.uuid4()), json.dumps({"detection": report["detection"], "policy": report["policyDecision"]})))
        if status == "AWAITING_POLICY":
            cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'SCAN',%s,'SIGNATURE_QUEUED',jsonb_build_object('scanId',%s))",
                           (str(uuid.uuid4()), scan_id, scan_id))


def fail_attempt(scan_id: str, attempt: int, error: Exception) -> None:
    code = type(error).__name__[:80]
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE job_steps SET status='FAILED',finished_at=now(),error_code=%s WHERE scan_id=%s AND step_type='ANALYSIS' AND attempt=%s", (code, scan_id, attempt))
        if attempt >= MAX_ATTEMPTS:
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',detection='UNKNOWN',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))


def handle(channel, method, _properties, body: bytes) -> None:
    scan_id = ""
    attempt = 0
    try:
        event = json.loads(body)
        scan_id = event["scanId"]
        claimed = claim(scan_id)
        if claimed is None:
            channel.basic_ack(method.delivery_tag)
            return
        path, attempt = claimed
        result = subprocess.run(["python3", "/opt/pdf-gateway/scan.py", str(path)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=180, check=False, env=os.environ.copy())
        report = json.loads(result.stdout)
        if result.returncode not in (0, 2):
            raise RuntimeError("scanner exited without a typed fail-closed report")
        persist(scan_id, attempt, report)
        channel.basic_ack(method.delivery_tag)
    except (json.JSONDecodeError, KeyError, TypeError):
        # Invalid envelopes cannot become valid through retry and must not form
        # a poison-message loop.
        channel.basic_reject(method.delivery_tag, requeue=False)
    except Exception as error:
        if scan_id and attempt:
            fail_attempt(scan_id, attempt, error)
        channel.basic_nack(method.delivery_tag, requeue=attempt < MAX_ATTEMPTS)


def main() -> None:
    credentials = pika.PlainCredentials(os.environ["PDF_GATEWAY_RABBITMQ_USER"], os.environ["PDF_GATEWAY_RABBITMQ_PASSWORD"])
    parameters = pika.ConnectionParameters(os.getenv("PDF_GATEWAY_RABBITMQ_HOST", "rabbitmq"), credentials=credentials,
                                           heartbeat=600, blocked_connection_timeout=60)
    while True:
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE, on_message_callback=handle)
            channel.start_consuming()
        except (pika.exceptions.AMQPError, psycopg2.Error, OSError):
            time.sleep(5)


if __name__ == "__main__":
    main()
