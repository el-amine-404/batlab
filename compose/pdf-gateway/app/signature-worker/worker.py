#!/usr/bin/env python3
"""Durable, idempotent signature-evidence worker."""

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

QUEUE = "pdf.gateway.signature.v1"
STORAGE = Path(os.getenv("PDF_GATEWAY_STORAGE_ROOT", "/var/lib/pdf-gateway"))
MAX_ATTEMPTS = int(os.getenv("PDF_GATEWAY_SIGNATURE_MAX_ATTEMPTS", "3"))


def database():
    return psycopg2.connect(
        host=os.getenv("PDF_GATEWAY_DATABASE_HOST", "postgres"),
        dbname=os.getenv("PDF_GATEWAY_DATABASE_NAME", "pdf_gateway"),
        user=os.getenv("PDF_GATEWAY_DATABASE_USER", "pdf_gateway"),
        password=os.environ["PDF_GATEWAY_DATABASE_PASSWORD"],
    )


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def claim(scan_id: str) -> tuple[Path, int] | None:
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status,quarantine_object,source_sha256 FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != "AWAITING_POLICY":
            return None
        cursor.execute("SELECT 1 FROM signature_verifications WHERE scan_id=%s LIMIT 1", (scan_id,))
        if cursor.fetchone():
            return None
        cursor.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM job_steps WHERE scan_id=%s AND step_type='SIGNATURE'", (scan_id,))
        attempt = cursor.fetchone()[0]
        path = STORAGE / "quarantine" / f"{row[1]}.bin"
        if attempt > MAX_ATTEMPTS or not path.is_file() or digest(path) != row[2]:
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',policy='REVIEW_REQUIRED',artifact_state='ORIGINAL_QUARANTINED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('SIGNATURE_FAILED','SCAN',%s,'FAILURE',%s,%s::jsonb)",
                           (scan_id, str(uuid.uuid4()), json.dumps({"code": "QUARANTINE_INTEGRITY_OR_RETRY_LIMIT"})))
            return None
        cursor.execute("UPDATE scan_jobs SET status='ANALYZING',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        cursor.execute("INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at) VALUES (%s,%s,'SIGNATURE','RUNNING',%s,now())",
                       (str(uuid.uuid4()), scan_id, attempt))
        return path, attempt


def write_report(scan_id: str, report: dict) -> tuple[str, int]:
    directory = STORAGE / "signature-reports"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    target = directory / f"{scan_id}.json"
    with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{scan_id}.", delete=False) as temporary:
        temporary.write(encoded)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, target)
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def persist(scan_id: str, attempt: int, report: dict) -> None:
    report_hash, report_bytes = write_report(scan_id, report)
    review = report["policyDecision"] == "REVIEW_REQUIRED"
    next_status = "REVIEW_REQUIRED" if review else "AWAITING_CDR"
    next_policy = "REVIEW_REQUIRED" if review else "CDR_ELIGIBLE"
    retention = "SIGNED_RESTRICTED" if report["preserveOriginal"] else "STANDARD"
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source_sha256 FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != report["sourceSha256"]:
            raise RuntimeError("source identity changed before signature commit")
        cursor.execute("INSERT INTO signature_verifications(id,scan_id,attempt,source_sha256,state,policy_decision,preserve_original,signature_count,report) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                       (str(uuid.uuid4()), scan_id, attempt, report["sourceSha256"], report["state"], report["policyDecision"], report["preserveOriginal"], report["signatureCount"], json.dumps(report)))
        # Retention is only ever raised; the V003 trigger also rejects lowering it.
        cursor.execute("UPDATE scan_jobs SET status=%s,policy=%s,original_retention=CASE WHEN original_retention='SIGNED_RESTRICTED' THEN original_retention ELSE %s END,updated_at=now(),version=version+1 WHERE id=%s",
                       (next_status, next_policy, retention, scan_id))
        cursor.execute("UPDATE job_steps SET status='SUCCEEDED',finished_at=now(),engine_version=%s,details=%s::jsonb WHERE scan_id=%s AND step_type='SIGNATURE' AND attempt=%s",
                       (f"pyHanko {report['engine']['version']}", json.dumps({"state": report["state"], "policyDecision": report["policyDecision"]}), scan_id, attempt))
        cursor.execute("INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES (%s,%s,'SIGNATURE_REPORT',%s,%s,%s,'RESTRICTED')",
                       (f"signature-report-{scan_id[5:]}", scan_id, f"signature-reports/{scan_id}.json", report_hash, report_bytes))
        cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES ('SIGNATURE_COMPLETED','SCAN',%s,'SUCCESS',%s,%s::jsonb)",
                       (scan_id, str(uuid.uuid4()), json.dumps({"state": report["state"], "preserveOriginal": report["preserveOriginal"]})))
        if not review:
            cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'SCAN',%s,'CDR_QUEUED',jsonb_build_object('scanId',%s))",
                           (str(uuid.uuid4()), scan_id, scan_id))


def fail(scan_id: str, attempt: int, error: Exception) -> None:
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE job_steps SET status='FAILED',finished_at=now(),error_code=%s WHERE scan_id=%s AND step_type='SIGNATURE' AND attempt=%s",
                       (type(error).__name__[:80], scan_id, attempt))
        cursor.execute("UPDATE scan_jobs SET status=%s,updated_at=now(),version=version+1 WHERE id=%s",
                       ("REVIEW_REQUIRED" if attempt >= MAX_ATTEMPTS else "AWAITING_POLICY", scan_id))


def handle(channel, method, _properties, body: bytes) -> None:
    scan_id = ""
    attempt = 0
    try:
        event = json.loads(body)
        scan_id = event["scanId"]
    except (json.JSONDecodeError, KeyError, TypeError):
        channel.basic_reject(method.delivery_tag, requeue=False)
        return
    try:
        claimed = claim(scan_id)
        if claimed is None:
            channel.basic_ack(method.delivery_tag)
            return
        path, attempt = claimed
        command = ["python", "/opt/pdf-gateway/inspect_signatures.py"]
        for root in filter(None, os.getenv("PDF_GATEWAY_SIGNATURE_TRUST_ROOTS", "").split(":")):
            command.extend(["--trust-root", root])
        command.append(str(path))
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=90, check=False, env={"PATH": "/usr/local/bin:/usr/bin:/bin"})
        report = json.loads(result.stdout)
        if result.returncode not in (0, 2):
            raise RuntimeError("signature inspector exited without a typed report")
        persist(scan_id, attempt, report)
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        if scan_id and attempt:
            fail(scan_id, attempt, error)
        channel.basic_nack(method.delivery_tag, requeue=attempt == 0 or attempt < MAX_ATTEMPTS)


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
