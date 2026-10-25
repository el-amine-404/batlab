#!/usr/bin/env python3
"""Durable final-validation stage for staged CDR output.

The only path to ALLOW_SANITIZED/FINAL_VALIDATED. It validates the exact staged
bytes recorded by the CDR stage; the database guard (V004) independently
refuses any release state without a passing record for those bytes.
"""

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

QUEUE = "pdf.gateway.validation.v1"
STORAGE = Path(os.getenv("PDF_GATEWAY_STORAGE_ROOT", "/var/lib/pdf-gateway"))
MAX_ATTEMPTS = int(os.getenv("PDF_GATEWAY_VALIDATION_MAX_ATTEMPTS", "3"))
RETRY_SECONDS = int(os.getenv("PDF_GATEWAY_VALIDATION_RETRY_SECONDS", "60"))


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


def audit(cursor, scan_id: str, action: str, outcome: str, details: dict) -> None:
    cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES (%s,'SCAN',%s,%s,%s,%s::jsonb)",
                   (action, scan_id, outcome, str(uuid.uuid4()), json.dumps(details)))


def claim(scan_id: str) -> dict | None:
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status,artifact_state,quarantine_object,source_sha256 FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != "VALIDATING_OUTPUT" or row[1] != "SANITIZED_STAGED":
            return None
        _status, _state, quarantine_object, source_hash = row
        cursor.execute("UPDATE job_steps SET status='FAILED',finished_at=now(),error_code='INTERRUPTED' WHERE scan_id=%s AND step_type='VALIDATION' AND status='RUNNING'", (scan_id,))
        # The artifact must be the output of a successful CDR of this exact source,
        # or an embedded-metadata version written from such an output after it passed.
        cursor.execute("""SELECT id,storage_object,sha256 FROM (
                            SELECT a.id,a.storage_object,a.sha256,a.created_at FROM cdr_results c JOIN artifacts a ON a.id=c.output_artifact_id
                             WHERE c.scan_id=%s AND c.status='SUCCEEDED' AND c.source_sha256=%s AND a.kind='CDR_OUTPUT'
                               AND a.state='SANITIZED_STAGED' AND a.sha256=c.output_sha256
                            UNION ALL
                            SELECT a.id,a.storage_object,a.sha256,a.created_at FROM artifacts a JOIN artifacts p ON p.id=a.parent_artifact_id
                              JOIN final_validations f ON f.artifact_id=p.id AND f.verdict='PASS' AND f.output_sha256=p.sha256
                             WHERE a.scan_id=%s AND a.kind='EMBEDDED_METADATA' AND a.state='SANITIZED_STAGED'
                               AND p.kind='CDR_OUTPUT' AND p.state='FINAL_VALIDATED' AND f.source_sha256=%s
                          ) candidates ORDER BY created_at DESC LIMIT 1""", (scan_id, source_hash, scan_id, source_hash))
        artifact = cursor.fetchone()
        cursor.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM job_steps WHERE scan_id=%s AND step_type='VALIDATION'", (scan_id,))
        attempt = cursor.fetchone()[0]
        if artifact is None or attempt > MAX_ATTEMPTS:
            code = "CDR_ARTIFACT_MISSING" if artifact is None else "RETRY_LIMIT"
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            audit(cursor, scan_id, "VALIDATION_FAILED", "FAILURE", {"code": code})
            return None
        cursor.execute("INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at) VALUES (%s,%s,'VALIDATION','RUNNING',%s,now())",
                       (str(uuid.uuid4()), scan_id, attempt))
        return {"attempt": attempt, "artifactId": artifact[0], "output": STORAGE / artifact[1], "outputSha256": artifact[2],
                "source": STORAGE / "quarantine" / f"{quarantine_object}.bin", "sourceSha256": source_hash}


def write_report(scan_id: str, attempt: int, report: dict) -> tuple[str, int]:
    directory = STORAGE / "validation-reports"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    encoded = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=directory, prefix=f".{scan_id}.", delete=False) as temporary:
        temporary.write(encoded)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, directory / f"{scan_id}-{attempt}.json")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def persist(scan_id: str, job: dict, report: dict) -> None:
    attempt = job["attempt"]
    report_hash, report_bytes = write_report(scan_id, attempt, report)
    verdict = report["verdict"]
    if verdict == "PASS" and report.get("outputSha256") != job["outputSha256"]:
        # A PASS describing other bytes is not a PASS for these.
        verdict = "FAIL"
        report["failureCodes"] = sorted(set(report.get("failureCodes", [])) | {"OUTPUT_HASH_MISMATCH"})
    engines = {item["engine"]: item.get("version", item.get("status")) for item in report.get("engines", [])}
    retry = verdict == "UNKNOWN" and attempt < MAX_ATTEMPTS
    if verdict == "PASS":
        decision = "ALLOW_SANITIZED"
    else:
        decision = "BLOCK" if report["policyDecision"] == "BLOCK" else "REVIEW_REQUIRED"
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status,source_sha256 FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != "VALIDATING_OUTPUT" or row[1] != job["sourceSha256"]:
            raise RuntimeError("scan state changed before validation commit")
        cursor.execute("""INSERT INTO final_validations(id,scan_id,attempt,artifact_id,source_sha256,output_sha256,verdict,detection,
                                                        policy_decision,failure_codes,policy_version,engines,report_sha256)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)""",
                       (str(uuid.uuid4()), scan_id, attempt, job["artifactId"], job["sourceSha256"], job["outputSha256"],
                        verdict, report["detection"], decision,
                        json.dumps(report["failureCodes"]), report["policyVersion"], json.dumps(engines), report_hash))
        cursor.execute("INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES (%s,%s,'VALIDATION_REPORT',%s,%s,%s,'RESTRICTED')",
                       (f"validation-{scan_id[5:]}-{attempt}", scan_id, f"validation-reports/{scan_id}-{attempt}.json", report_hash, report_bytes))
        if verdict == "PASS":
            cursor.execute("UPDATE artifacts SET state='FINAL_VALIDATED' WHERE id=%s", (job["artifactId"],))
            cursor.execute("UPDATE scan_jobs SET status='AWAITING_ARCHIVE',detection='NO_THREATS_DETECTED',policy='ALLOW_SANITIZED',artifact_state='FINAL_VALIDATED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'SCAN',%s,'ARCHIVE_QUEUED',jsonb_build_object('scanId',%s,'artifactId',%s))",
                           (str(uuid.uuid4()), scan_id, scan_id, job["artifactId"]))
        elif retry:
            cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload,available_at) VALUES (%s,'SCAN',%s,'VALIDATION_QUEUED',jsonb_build_object('scanId',%s,'artifactId',%s),now()+make_interval(secs=>%s))",
                           (str(uuid.uuid4()), scan_id, scan_id, job["artifactId"], RETRY_SECONDS * 2 ** (attempt - 1)))
        else:
            blocked = decision == "BLOCK"
            cursor.execute("UPDATE artifacts SET state='REJECTED' WHERE id=%s", (job["artifactId"],))
            cursor.execute("UPDATE scan_jobs SET status=%s,detection=%s,policy=%s,artifact_state='ORIGINAL_QUARANTINED',updated_at=now(),version=version+1 WHERE id=%s",
                           ("BLOCKED" if blocked else "REVIEW_REQUIRED", report["detection"], "BLOCK" if blocked else "REVIEW_REQUIRED", scan_id))
        cursor.execute("UPDATE job_steps SET status=%s,finished_at=now(),engine_version=%s,error_code=%s,details=%s::jsonb WHERE scan_id=%s AND step_type='VALIDATION' AND attempt=%s",
                       ("SUCCEEDED" if verdict == "PASS" else "FAILED", json.dumps(engines, sort_keys=True)[:160],
                        None if verdict == "PASS" else (report["failureCodes"] or ["UNKNOWN"])[0][:80],
                        json.dumps({"verdict": verdict, "failureCodes": report["failureCodes"]}), scan_id, attempt))
        audit(cursor, scan_id, "VALIDATION_COMPLETED", "SUCCESS" if verdict == "PASS" else "FAILURE",
              {"verdict": verdict, "failureCodes": report["failureCodes"], "retryScheduled": retry})


def unknown_report(code: str) -> dict:
    return {"schemaVersion": "final-validation-report-1", "policyVersion": "final-policy-1", "verdict": "UNKNOWN",
            "detection": "UNKNOWN", "policyDecision": "REVIEW_REQUIRED", "failureCodes": [code], "engines": [], "findings": []}


def handle(channel, method, _properties, body: bytes) -> None:
    try:
        event = json.loads(body)
        scan_id = event["scanId"]
        if not isinstance(scan_id, str):
            raise TypeError("scanId")
    except (json.JSONDecodeError, KeyError, TypeError):
        channel.basic_reject(method.delivery_tag, requeue=False)
        return
    job = None
    try:
        job = claim(scan_id)
        if job is None:
            channel.basic_ack(method.delivery_tag)
            return
        try:
            readable_hash = sha256(job["output"]) if job["output"].is_file() else None
        except OSError:
            readable_hash = OSError
        if readable_hash is OSError:
            # A local storage problem says nothing about the bytes: UNKNOWN, retried.
            report = unknown_report("OUTPUT_UNREADABLE")
        elif readable_hash != job["outputSha256"]:
            report = unknown_report("OUTPUT_HASH_MISMATCH")
            report.update(verdict="FAIL", detection="SUSPICIOUS", outputSha256=None)
        else:
            result = subprocess.run(["python3", "/opt/pdf-gateway/final_validate.py", str(job["output"]),
                                     "--expected-sha256", job["outputSha256"], "--source", str(job["source"])],
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=300, check=False,
                                    env=os.environ.copy())
            try:
                report = json.loads(result.stdout)
            except json.JSONDecodeError:
                report = unknown_report("VALIDATOR_NO_REPORT")
            if result.returncode not in (0, 1, 2) or report.get("verdict") not in ("PASS", "FAIL", "UNKNOWN"):
                report = unknown_report("VALIDATOR_NO_REPORT")
            # Exit status and verdict must agree; anything else is not a PASS.
            if report["verdict"] == "PASS" and result.returncode != 0:
                report = unknown_report("VALIDATOR_INCONSISTENT")
        persist(scan_id, job, report)
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        print(json.dumps({"event": "validation_worker_error", "scanId": scan_id, "errorType": type(error).__name__}), flush=True)
        channel.basic_nack(method.delivery_tag, requeue=job is None or job["attempt"] < MAX_ATTEMPTS)


def main() -> None:
    credentials = pika.PlainCredentials(os.environ["PDF_GATEWAY_RABBITMQ_USER"], os.environ["PDF_GATEWAY_RABBITMQ_PASSWORD"])
    parameters = pika.ConnectionParameters(os.getenv("PDF_GATEWAY_RABBITMQ_HOST", "rabbitmq"), credentials=credentials,
                                           heartbeat=0, blocked_connection_timeout=60)
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
