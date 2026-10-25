#!/usr/bin/env python3
"""Durable CDR adapter: dispatches eligible quarantine objects to the host provider.

The worker never decides eligibility from the queue message. It re-derives it
from committed analysis and signature evidence for the exact source hash, then
stages the provider's output privately as SANITIZED_STAGED. Nothing it produces
is releasable until the final-validation stage passes on the exact bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import time
import uuid
from pathlib import Path

import pika
import psycopg2

QUEUE = "pdf.gateway.cdr.v1"
PROTOCOL = "pdf-cdr-1"
STORAGE = Path(os.getenv("PDF_GATEWAY_STORAGE_ROOT", "/var/lib/pdf-gateway"))
SOCKET = os.getenv("PDF_GATEWAY_CDR_SOCKET", "/run/pdf-cdr/cdr.sock")
MAX_ATTEMPTS = int(os.getenv("PDF_GATEWAY_CDR_MAX_ATTEMPTS", "4"))
RETRY_SECONDS = int(os.getenv("PDF_GATEWAY_CDR_RETRY_SECONDS", "60"))
RESPONSE_TIMEOUT = int(os.getenv("PDF_GATEWAY_CDR_RESPONSE_TIMEOUT_SECONDS", "720"))
MAX_OUTPUT = int(os.getenv("PDF_GATEWAY_CDR_MAX_OUTPUT_BYTES", str(256 * 1024 * 1024)))
MAX_HEADER = 4096
SIGNATURE_DECISIONS = ("CDR_REQUIRED", "CDR_REQUIRED_PRESERVE_ORIGINAL")
# Infrastructure failures are retried with backoff; document-specific failures
# are not, because retrying a hostile or pathological input only spends budget.
RETRYABLE = {"PROVIDER_UNAVAILABLE", "CAPABILITY_UNAVAILABLE", "ISOLATION_EVIDENCE_MISSING",
             "PEER_NOT_ALLOWED", "PROTOCOL_ERROR", "CLEANUP_FAILED", "INTERNAL_ERROR"}


class CdrFailure(Exception):
    def __init__(self, code: str, detail: str = "", evidence: dict | None = None):
        super().__init__(code)
        self.code = code[:48]
        self.detail = detail[:300]
        self.evidence = evidence or {}


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


def audit(cursor, scan_id: str, action: str, outcome: str, details: dict) -> None:
    cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES (%s,'SCAN',%s,%s,%s,%s::jsonb)",
                   (action, scan_id, outcome, str(uuid.uuid4()), json.dumps(details)))


def hold_for_review(cursor, scan_id: str, code: str) -> None:
    cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
    audit(cursor, scan_id, "CDR_PRECONDITION_FAILED", "FAILURE", {"code": code})


def claim(scan_id: str) -> tuple[Path, str, int] | None:
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status,policy,quarantine_object,source_sha256 FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] not in ("AWAITING_CDR", "SANITIZING"):
            return None
        status, policy, quarantine_object, source_hash = row
        if status == "SANITIZING":
            # A previous worker died mid-conversion; that attempt is not trusted.
            cursor.execute("UPDATE job_steps SET status='FAILED',finished_at=now(),error_code='INTERRUPTED' WHERE scan_id=%s AND step_type='CDR' AND status='RUNNING'", (scan_id,))
        if policy != "CDR_ELIGIBLE":
            hold_for_review(cursor, scan_id, "POLICY_NOT_CDR_ELIGIBLE")
            return None
        cursor.execute("""SELECT details->>'policyDecision', details->>'sourceSha256', details->>'detection'
                            FROM job_steps WHERE scan_id=%s AND step_type='ANALYSIS' AND status='SUCCEEDED'
                           ORDER BY attempt DESC LIMIT 1""", (scan_id,))
        analysis = cursor.fetchone()
        if analysis is None or analysis[0] != "CDR_ELIGIBLE" or analysis[1] != source_hash or analysis[2] == "MALICIOUS":
            hold_for_review(cursor, scan_id, "ANALYSIS_EVIDENCE_MISSING")
            return None
        cursor.execute("SELECT policy_decision,source_sha256 FROM signature_verifications WHERE scan_id=%s ORDER BY attempt DESC LIMIT 1", (scan_id,))
        signature = cursor.fetchone()
        if signature is None or signature[0] not in SIGNATURE_DECISIONS or signature[1] != source_hash:
            hold_for_review(cursor, scan_id, "SIGNATURE_EVIDENCE_MISSING")
            return None
        cursor.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM job_steps WHERE scan_id=%s AND step_type='CDR'", (scan_id,))
        attempt = cursor.fetchone()[0]
        path = STORAGE / "quarantine" / f"{quarantine_object}.bin"
        if attempt > MAX_ATTEMPTS:
            cursor.execute("UPDATE scan_jobs SET status='FAILED',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            audit(cursor, scan_id, "CDR_FAILED", "FAILURE", {"code": "RETRY_LIMIT"})
            return None
        if not path.is_file() or digest(path) != source_hash:
            hold_for_review(cursor, scan_id, "QUARANTINE_INTEGRITY_FAILED")
            return None
        cursor.execute("UPDATE scan_jobs SET status='SANITIZING',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        cursor.execute("INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at) VALUES (%s,%s,'CDR','RUNNING',%s,now())",
                       (str(uuid.uuid4()), scan_id, attempt))
        return path, source_hash, attempt


def recv_exact(sock: socket.socket, count: int, sink) -> None:
    while count:
        chunk = sock.recv(min(1024 * 1024, count))
        if not chunk:
            raise CdrFailure("PROTOCOL_ERROR", "provider closed the stream early")
        sink(chunk)
        count -= len(chunk)


def read_header(sock: socket.socket) -> dict:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            raise CdrFailure("PROTOCOL_ERROR", "provider closed before responding")
        data.extend(chunk)
        if len(data) > MAX_HEADER:
            raise CdrFailure("PROTOCOL_ERROR", "provider header too large")
    try:
        header = json.loads(data)
    except json.JSONDecodeError as error:
        raise CdrFailure("PROTOCOL_ERROR", "provider header is not JSON") from error
    if not isinstance(header, dict) or header.get("protocol") != PROTOCOL:
        raise CdrFailure("PROTOCOL_ERROR", "unexpected provider protocol")
    return header


def convert(scan_id: str, source: Path, source_hash: str, attempt: int) -> tuple[Path, dict]:
    """Stream the source to the provider and stage the returned bytes privately."""
    size = source.stat().st_size
    staging = STORAGE / "cdr-output"
    staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(RESPONSE_TIMEOUT)
    try:
        try:
            sock.connect(SOCKET)
        except OSError as error:
            raise CdrFailure("PROVIDER_UNAVAILABLE", type(error).__name__) from error
        request = {"protocol": PROTOCOL, "jobId": scan_id, "sha256": source_hash, "bytes": size}
        sock.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
        sent = hashlib.sha256()
        with source.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                sent.update(chunk)
                sock.sendall(chunk)
        if sent.hexdigest() != source_hash:
            raise CdrFailure("QUARANTINE_INTEGRITY_FAILED", "source changed while streaming")
        header = read_header(sock)
        if header.get("status") != "OK":
            raise CdrFailure(str(header.get("code") or "PROTOCOL_ERROR"), str(header.get("detail") or ""))
        output_bytes = header.get("outputBytes")
        output_hash = header.get("outputSha256")
        if (header.get("jobId") != scan_id or header.get("sourceSha256") != source_hash
                or not isinstance(output_bytes, int) or isinstance(output_bytes, bool)
                or not 0 < output_bytes <= MAX_OUTPUT or not isinstance(output_hash, str) or len(output_hash) != 64
                or not isinstance(header.get("imageDigest"), str) or not isinstance(header.get("isolation"), dict)):
            raise CdrFailure("PROTOCOL_ERROR", "provider response failed validation")
        target = staging / f"{scan_id}-{attempt}.pdf"
        received = hashlib.sha256()
        with tempfile.NamedTemporaryFile(dir=staging, prefix=f".{scan_id}.", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                os.fchmod(temporary.fileno(), 0o600)

                def sink(chunk: bytes) -> None:
                    received.update(chunk)
                    temporary.write(chunk)

                recv_exact(sock, output_bytes, sink)
                temporary.flush()
                os.fsync(temporary.fileno())
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise
        if received.hexdigest() != output_hash:
            temporary_path.unlink(missing_ok=True)
            raise CdrFailure("OUTPUT_HASH_MISMATCH", "received bytes do not match provider hash")
        with temporary_path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                temporary_path.unlink(missing_ok=True)
                raise CdrFailure("OUTPUT_INVALID", "output is not a PDF")
        os.replace(temporary_path, target)
        directory = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return target, header
    except socket.timeout as error:
        raise CdrFailure("PROVIDER_TIMEOUT", "no complete provider response in time") from error
    except OSError as error:
        raise CdrFailure("PROVIDER_UNAVAILABLE", type(error).__name__) from error
    finally:
        sock.close()


def persist(scan_id: str, attempt: int, source_hash: str, output: Path, header: dict) -> None:
    output_hash = digest(output)
    if output_hash != header["outputSha256"]:
        raise CdrFailure("OUTPUT_HASH_MISMATCH", "staged output changed before commit")
    artifact_id = f"cdr-{scan_id[5:]}-{attempt}"
    evidence = {key: header.get(key) for key in ("isolation", "durationMs", "outerRuntime")}
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source_sha256,status FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != source_hash or row[1] != "SANITIZING":
            raise RuntimeError("scan identity or state changed before CDR commit")
        cursor.execute("INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state) VALUES (%s,%s,'CDR_OUTPUT',%s,%s,%s,'SANITIZED_STAGED')",
                       (artifact_id, scan_id, f"cdr-output/{output.name}", output_hash, output.stat().st_size))
        cursor.execute("""INSERT INTO cdr_results(id,scan_id,attempt,status,source_sha256,output_artifact_id,output_sha256,output_bytes,
                                                  provider_version,dangerzone_version,image_digest,evidence,duration_ms)
                          VALUES (%s,%s,%s,'SUCCEEDED',%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                       (str(uuid.uuid4()), scan_id, attempt, source_hash, artifact_id, output_hash, output.stat().st_size,
                        str(header.get("providerVersion"))[:80], str(header.get("dangerzoneVersion"))[:40],
                        str(header.get("imageDigest"))[:80], json.dumps(evidence), header.get("durationMs")))
        cursor.execute("UPDATE scan_jobs SET status='VALIDATING_OUTPUT',artifact_state='SANITIZED_STAGED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        cursor.execute("UPDATE job_steps SET status='SUCCEEDED',finished_at=now(),engine_version=%s,details=%s::jsonb WHERE scan_id=%s AND step_type='CDR' AND attempt=%s",
                       (f"Dangerzone {header.get('dangerzoneVersion')} {header.get('imageDigest')}"[:160],
                        json.dumps({"artifactId": artifact_id, "outputSha256": output_hash}), scan_id, attempt))
        audit(cursor, scan_id, "CDR_COMPLETED", "SUCCESS", {"artifactId": artifact_id, "attempt": attempt})
        cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'SCAN',%s,'VALIDATION_QUEUED',jsonb_build_object('scanId',%s,'artifactId',%s))",
                       (str(uuid.uuid4()), scan_id, scan_id, artifact_id))


def fail(scan_id: str, attempt: int, source_hash: str, failure: CdrFailure) -> None:
    retry = failure.code in RETRYABLE and attempt < MAX_ATTEMPTS
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != "SANITIZING":
            return
        cursor.execute("""INSERT INTO cdr_results(id,scan_id,attempt,status,failure_code,retryable,source_sha256,evidence)
                          VALUES (%s,%s,%s,'FAILED',%s,%s,%s,%s::jsonb) ON CONFLICT (scan_id,attempt) DO NOTHING""",
                       (str(uuid.uuid4()), scan_id, attempt, failure.code, failure.code in RETRYABLE, source_hash,
                        json.dumps({"detail": failure.detail})))
        cursor.execute("UPDATE job_steps SET status='FAILED',finished_at=now(),error_code=%s WHERE scan_id=%s AND step_type='CDR' AND attempt=%s",
                       (failure.code, scan_id, attempt))
        if retry:
            cursor.execute("UPDATE scan_jobs SET status='AWAITING_CDR',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload,available_at) VALUES (%s,'SCAN',%s,'CDR_QUEUED',jsonb_build_object('scanId',%s),now()+make_interval(secs=>%s))",
                           (str(uuid.uuid4()), scan_id, scan_id, RETRY_SECONDS * 2 ** (attempt - 1)))
        elif failure.code in RETRYABLE:
            cursor.execute("UPDATE scan_jobs SET status='FAILED',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        else:
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',policy='REVIEW_REQUIRED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        audit(cursor, scan_id, "CDR_FAILED", "FAILURE", {"code": failure.code, "attempt": attempt, "retryScheduled": retry})


def handle(channel, method, _properties, body: bytes) -> None:
    try:
        event = json.loads(body)
        scan_id = event["scanId"]
        if not isinstance(scan_id, str):
            raise TypeError("scanId")
    except (json.JSONDecodeError, KeyError, TypeError):
        channel.basic_reject(method.delivery_tag, requeue=False)
        return
    attempt = 0
    try:
        claimed = claim(scan_id)
        if claimed is None:
            channel.basic_ack(method.delivery_tag)
            return
        source, source_hash, attempt = claimed
        try:
            output, header = convert(scan_id, source, source_hash, attempt)
            persist(scan_id, attempt, source_hash, output, header)
        except CdrFailure as failure:
            fail(scan_id, attempt, source_hash, failure)
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        # Database/broker trouble: leave the job SANITIZING so the next delivery
        # records this attempt as INTERRUPTED instead of trusting partial work.
        print(json.dumps({"event": "cdr_worker_error", "scanId": scan_id, "errorType": type(error).__name__}), flush=True)
        channel.basic_nack(method.delivery_tag, requeue=attempt < MAX_ATTEMPTS)


def main() -> None:
    credentials = pika.PlainCredentials(os.environ["PDF_GATEWAY_RABBITMQ_USER"], os.environ["PDF_GATEWAY_RABBITMQ_PASSWORD"])
    # The provider can legitimately take minutes; heartbeats must outlast it.
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
