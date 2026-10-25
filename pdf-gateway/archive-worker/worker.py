#!/usr/bin/env python3
"""Durable archive publisher: the only writer of the portable archive.

Consumes pdf.gateway.archive.v1. It re-derives eligibility from committed
evidence (a PASS final validation of exactly these CDR bytes), organises the
document deterministically, publishes atomically, then records the index and
marks the scan READY. Publication is idempotent: the document ID is derived
from the scan ID, so a crash between publishing and committing is recovered by
recognising the already-published bytes instead of publishing twice.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pika
import psycopg2

import metadata
from archive import Archive, ArchiveError, SIDECAR, sha256_file
from organize import default_taxonomy, organise, validated

QUEUE = "pdf.gateway.archive.v1"
EDIT_QUEUE = "pdf.gateway.metadata-edit.v1"
STORAGE = Path(os.getenv("PDF_GATEWAY_STORAGE_ROOT", "/var/lib/pdf-gateway"))
ARCHIVE = Archive(Path(os.getenv("PDF_GATEWAY_ARCHIVE_ROOT", "/var/lib/pdf-archive")))
MAX_ATTEMPTS = int(os.getenv("PDF_GATEWAY_ARCHIVE_MAX_ATTEMPTS", "5"))
RETRY_SECONDS = int(os.getenv("PDF_GATEWAY_ARCHIVE_RETRY_SECONDS", "60"))
# Embed allowlisted metadata into a new version that must pass validation again.
EMBED_METADATA = os.getenv("PDF_GATEWAY_EMBED_METADATA", "true") == "true"
# Storage or configuration trouble is retried; changed bytes or invalid evidence are not.
RETRYABLE = {"ARCHIVE_CONFIG_INVALID", "STORAGE_ERROR"}


class ArchiveStageError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:300]


def database():
    return psycopg2.connect(
        host=os.getenv("PDF_GATEWAY_DATABASE_HOST", "postgres"),
        dbname=os.getenv("PDF_GATEWAY_DATABASE_NAME", "pdf_gateway"),
        user=os.getenv("PDF_GATEWAY_DATABASE_USER", "pdf_gateway"),
        password=os.environ["PDF_GATEWAY_DATABASE_PASSWORD"],
    )


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def audit(cursor, scan_id: str, action: str, outcome: str, details: dict) -> None:
    cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES (%s,'SCAN',%s,%s,%s,%s::jsonb)",
                   (action, scan_id, outcome, str(uuid.uuid4()), json.dumps(details)))


def claim(scan_id: str) -> dict | None:
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status,policy,artifact_state,source_sha256,original_retention,quarantine_object,created_at,intent FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] not in ("AWAITING_ARCHIVE", "ARCHIVING") or row[1] != "ALLOW_SANITIZED" or row[2] != "FINAL_VALIDATED":
            return None
        _status, _policy, _state, source_hash, retention, quarantine_object, created_at, intent = row
        cursor.execute("UPDATE job_steps SET status='FAILED',finished_at=now(),error_code='INTERRUPTED' WHERE scan_id=%s AND step_type='ARCHIVE' AND status='RUNNING'", (scan_id,))
        cursor.execute("""SELECT a.id,a.storage_object,a.sha256,a.bytes,f.policy_version,f.attempt,a.kind
                            FROM final_validations f JOIN artifacts a ON a.id=f.artifact_id
                           WHERE f.scan_id=%s AND f.verdict='PASS' AND f.source_sha256=%s AND a.state='FINAL_VALIDATED'
                             AND a.kind IN ('CDR_OUTPUT','EMBEDDED_METADATA') AND a.sha256=f.output_sha256
                           ORDER BY f.attempt DESC LIMIT 1""", (scan_id, source_hash))
        artifact = cursor.fetchone()
        cursor.execute("""SELECT details FROM job_steps WHERE scan_id=%s AND step_type='ARCHIVE' AND status='SUCCEEDED'
                           AND details->>'phase'='EMBED' ORDER BY attempt DESC LIMIT 1""", (scan_id,))
        embedded = (cursor.fetchone() or [None])[0]
        if artifact is not None and artifact[6] == "EMBEDDED_METADATA" and not embedded:
            artifact = None  # a metadata version without its recorded organisation is not trusted
        cursor.execute("SELECT state,signature_count FROM signature_verifications WHERE scan_id=%s ORDER BY attempt DESC LIMIT 1", (scan_id,))
        signature = cursor.fetchone()
        cursor.execute("SELECT details->'textExtraction' FROM job_steps WHERE scan_id=%s AND step_type='ANALYSIS' AND status='SUCCEEDED' ORDER BY attempt DESC LIMIT 1", (scan_id,))
        extraction = (cursor.fetchone() or [None])[0]
        cursor.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM job_steps WHERE scan_id=%s AND step_type='ARCHIVE'", (scan_id,))
        attempt = cursor.fetchone()[0]
        if artifact is None or signature is None or attempt > MAX_ATTEMPTS:
            code = "RETRY_LIMIT" if attempt > MAX_ATTEMPTS else "RELEASE_EVIDENCE_MISSING"
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',policy='REVIEW_REQUIRED',artifact_state='ORIGINAL_QUARANTINED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
            audit(cursor, scan_id, "ARCHIVE_FAILED", "FAILURE", {"code": code})
            return None
        cursor.execute("UPDATE scan_jobs SET status='ARCHIVING',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        cursor.execute("INSERT INTO job_steps(id,scan_id,step_type,status,attempt,started_at) VALUES (%s,%s,'ARCHIVE','RUNNING',%s,now())",
                       (str(uuid.uuid4()), scan_id, attempt))
        return {"scanId": scan_id, "attempt": attempt, "artifactId": artifact[0], "output": STORAGE / artifact[1],
                "sha256": artifact[2], "bytes": artifact[3], "policyVersion": artifact[4],
                "finalReportId": f"validation-{scan_id[5:]}-{artifact[5]}", "sourceSha256": source_hash,
                "kind": artifact[6], "embedPhase": embedded, "intent": intent,
                "signedOriginal": retention == "SIGNED_RESTRICTED" or (signature[1] or 0) > 0,
                "signatureState": signature[0], "originalEvidenceId": quarantine_object,
                "extraction": extraction if isinstance(extraction, dict) else None,
                "ingested": created_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}


def build_sidecar(job: dict, text: str) -> tuple[dict, dict]:
    try:
        rules = validated(ARCHIVE.manifests / "rules.json", "rules.schema.json",
                          {"schemaVersion": "1.0.0", "revision": 1, "updatedAt": now(), "rules": []})
        taxonomy = validated(ARCHIVE.manifests / "taxonomy.json", "taxonomy.schema.json", default_taxonomy(now()))
    except Exception as error:  # an invalid manifest must not silently change filing
        raise ArchiveStageError("ARCHIVE_CONFIG_INVALID", type(error).__name__) from error
    organisation = organise(text, rules, taxonomy)
    summary = ("The original carried a cryptographic signature "
               f"({job['signatureState']}); it is retained restricted as evidence. "
               "This copy is a visual reconstruction and is not signed." if job["signedOriginal"] else
               "No cryptographic signature was found on the original. Visible signature-like marks are not verified.")
    sidecar = {
        "documentId": "doc-" + job["scanId"][5:], "artifactVersion": 1, "metadataRevision": 1,
        **organisation.fields,
        "derivedFrom": {"scanId": job["scanId"], "kind": "CDR_RECONSTRUCTION", "originalEvidenceId": job["originalEvidenceId"]},
        "approval": {"policyVersion": job["policyVersion"], "decision": "ALLOW_SANITIZED", "finalReportId": job["finalReportId"]},
        "signature": {"originalSigned": job["signedOriginal"], "viewingCopySigned": False, "summary": summary},
        "integrations": {}, "createdAt": job["ingested"], "updatedAt": now(),
    }
    extraction = job["extraction"]
    if text and extraction and extraction.get("status") == "OK":
        sidecar["textExtraction"] = {key: extraction[key] for key in ("method", "source", "engine", "pages", "characters", "truncated") if key in extraction}
    return sidecar, organisation.evidence


def verified_inputs(job: dict) -> str:
    """Recheck the validated bytes; return the untrusted text used for filing."""
    output = job["output"]
    try:
        if not output.is_file() or sha256_file(output) != job["sha256"]:
            raise ArchiveStageError("VALIDATED_BYTES_CHANGED", "artifact no longer matches the validated hash")
        text_path = STORAGE / "text-extracts" / f"{job['scanId']}.txt"
        text = text_path.read_text(encoding="utf-8", errors="replace") if text_path.is_file() else ""
        if text and (job["extraction"] or {}).get("sha256") not in (None, sha256_file(text_path)):
            text = ""  # an extract that changed since analysis is not trusted for filing
    except OSError as error:
        raise ArchiveStageError("STORAGE_ERROR", type(error).__name__) from error
    return text


def embed(job: dict) -> tuple[Path, dict, dict, dict]:
    """Phase 1: organise and write allowlisted metadata into a new PDF version."""
    text = verified_inputs(job)
    sidecar, evidence = build_sidecar(job, text)
    values = metadata.fields_for(sidecar)
    directory = STORAGE / "metadata-versions"
    target = directory / f"{job['scanId']}-1.pdf"
    try:
        directory.mkdir(mode=0o750, exist_ok=True)
        metadata.write(job["output"], target, values)
    except OSError as error:
        raise ArchiveStageError("STORAGE_ERROR", type(error).__name__) from error
    except Exception as error:  # pikepdf refusing a validated output is not retryable
        raise ArchiveStageError("METADATA_WRITE_FAILED", type(error).__name__) from error
    return target, sidecar, evidence, values


def commit_embed(job: dict, target: Path, sidecar: dict, evidence: dict, values: dict) -> None:
    scan_id = job["scanId"]
    artifact_id = f"meta-{scan_id[5:]}-1"
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != "ARCHIVING":
            raise RuntimeError("scan state changed before metadata commit")
        cursor.execute("""INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state,parent_artifact_id)
                          VALUES (%s,%s,'EMBEDDED_METADATA',%s,%s,%s,'SANITIZED_STAGED',%s)""",
                       (artifact_id, scan_id, f"metadata-versions/{target.name}", sha256_file(target), target.stat().st_size, job["artifactId"]))
        # Different bytes: back through final validation before anything is released.
        cursor.execute("UPDATE scan_jobs SET status='VALIDATING_OUTPUT',policy='CDR_ELIGIBLE',artifact_state='SANITIZED_STAGED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        cursor.execute("UPDATE job_steps SET status='SUCCEEDED',finished_at=now(),engine_version=%s,details=%s::jsonb WHERE scan_id=%s AND step_type='ARCHIVE' AND attempt=%s",
                       (metadata.WRITER_VERSION, json.dumps({"phase": "EMBED", "sidecar": sidecar, "organisation": evidence,
                                                             "embedded": values, "artifactId": artifact_id}), scan_id, job["attempt"]))
        cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'SCAN',%s,'VALIDATION_QUEUED',jsonb_build_object('scanId',%s,'artifactId',%s))",
                       (str(uuid.uuid4()), scan_id, scan_id, artifact_id))
        audit(cursor, scan_id, "METADATA_EMBEDDED", "SUCCESS", {"artifactId": artifact_id, "fields": sorted(values)})


def publish(job: dict) -> tuple[Path, dict, dict]:
    text = verified_inputs(job)
    if job["kind"] == "EMBEDDED_METADATA":
        # Publish the organisation recorded when the metadata was written, so the
        # sidecar and the embedded values cannot disagree.
        sidecar = dict(job["embedPhase"]["sidecar"], updatedAt=now())
        sidecar["embeddedMetadata"] = {"writer": metadata.WRITER_VERSION,
                                       "fields": sorted(key.lstrip("/") for key in job["embedPhase"]["embedded"])}
        evidence = dict(job["embedPhase"]["organisation"], publishedVersion="EMBEDDED_METADATA")
    else:
        sidecar, evidence = build_sidecar(job, text)
    existing = ARCHIVE.find(sidecar["documentId"])
    if existing is not None:
        # Published by an earlier attempt that died before committing the index.
        current = json.loads((existing / SIDECAR).read_text(encoding="utf-8"))
        if current["integrity"]["sha256"] != job["sha256"] or ARCHIVE.check_document(existing):
            raise ArchiveStageError("ARCHIVE_CONFLICT", "a different or damaged document holds this ID")
        evidence["recoveredExistingPublication"] = True
        return existing, current, evidence
    try:
        directory = ARCHIVE.publish(job["output"], job["sha256"], sidecar, text or None)
    except ArchiveError as error:
        raise ArchiveStageError("STORAGE_ERROR" if error.code == "STAGING_NOT_SAME_FILESYSTEM" else error.code, error.detail) from error
    except OSError as error:
        raise ArchiveStageError("STORAGE_ERROR", type(error).__name__) from error
    return directory, json.loads((directory / SIDECAR).read_text(encoding="utf-8")), evidence


def commit(job: dict, directory: Path, sidecar: dict, evidence: dict) -> None:
    scan_id = job["scanId"]
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM scan_jobs WHERE id=%s FOR UPDATE", (scan_id,))
        row = cursor.fetchone()
        if row is None or row[0] != "ARCHIVING":
            raise RuntimeError("scan state changed before archive commit")
        cursor.execute("""INSERT INTO archive_documents(document_id,scan_id,archive_path,pdf_file,title,category,review_state,
                                                        current_version,metadata_revision,sha256,bytes,sidecar)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                       (sidecar["documentId"], scan_id, sidecar["archivePath"], sidecar["relativePdfPath"], sidecar["title"],
                        sidecar["organization"]["category"], sidecar["organization"]["reviewState"], sidecar["artifactVersion"],
                        sidecar["metadataRevision"], sidecar["integrity"]["sha256"], sidecar["integrity"]["bytes"], json.dumps(sidecar)))
        cursor.execute("INSERT INTO archive_versions(document_id,artifact_version,artifact_id,sha256) VALUES (%s,%s,%s,%s)",
                       (sidecar["documentId"], sidecar["artifactVersion"], job["artifactId"], sidecar["integrity"]["sha256"]))
        cursor.execute("UPDATE artifacts SET state='PUBLISHED' WHERE id=%s", (job["artifactId"],))
        cursor.execute("UPDATE scan_jobs SET status='READY',artifact_state='PUBLISHED',updated_at=now(),version=version+1 WHERE id=%s", (scan_id,))
        cursor.execute("UPDATE job_steps SET status='SUCCEEDED',finished_at=now(),engine_version=%s,details=%s::jsonb WHERE scan_id=%s AND step_type='ARCHIVE' AND attempt=%s",
                       (evidence.get("organizerVersion"), json.dumps({"documentId": sidecar["documentId"], "organisation": evidence}),
                        scan_id, job["attempt"]))
        if job.get("intent") == "ARCHIVE_AND_IMPORT":
            # Paperless is a projection: queue it only after the archive holds the document.
            cursor.execute("INSERT INTO paperless_imports(document_id,artifact_sha256) VALUES (%s,%s) ON CONFLICT (document_id) DO NOTHING",
                           (sidecar["documentId"], sidecar["integrity"]["sha256"]))
            cursor.execute("UPDATE scan_jobs SET import_status='PENDING' WHERE id=%s", (scan_id,))
            cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'DOCUMENT',%s,'PAPERLESS_QUEUED',jsonb_build_object('documentId',%s::text))",
                           (str(uuid.uuid4()), sidecar["documentId"], sidecar["documentId"]))
        audit(cursor, scan_id, "ARCHIVE_PUBLISHED", "SUCCESS",
              {"documentId": sidecar["documentId"], "category": sidecar["organization"]["category"],
               "reviewState": sidecar["organization"]["reviewState"]})


def fail(job: dict, error: ArchiveStageError) -> None:
    retry = error.code in RETRYABLE and job["attempt"] < MAX_ATTEMPTS
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM scan_jobs WHERE id=%s FOR UPDATE", (job["scanId"],))
        row = cursor.fetchone()
        if row is None or row[0] != "ARCHIVING":
            return
        cursor.execute("UPDATE job_steps SET status='FAILED',finished_at=now(),error_code=%s,details=%s::jsonb WHERE scan_id=%s AND step_type='ARCHIVE' AND attempt=%s",
                       (error.code[:80], json.dumps({"detail": error.detail}), job["scanId"], job["attempt"]))
        if retry:
            cursor.execute("UPDATE scan_jobs SET status='AWAITING_ARCHIVE',updated_at=now(),version=version+1 WHERE id=%s", (job["scanId"],))
            cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload,available_at) VALUES (%s,'SCAN',%s,'ARCHIVE_QUEUED',jsonb_build_object('scanId',%s,'artifactId',%s),now()+make_interval(secs=>%s))",
                           (str(uuid.uuid4()), job["scanId"], job["scanId"], job["artifactId"], RETRY_SECONDS * 2 ** (job["attempt"] - 1)))
        else:
            # The validated bytes are no longer trustworthy for release.
            cursor.execute("UPDATE artifacts SET state='REJECTED' WHERE id=%s", (job["artifactId"],))
            cursor.execute("UPDATE scan_jobs SET status='REVIEW_REQUIRED',policy='REVIEW_REQUIRED',artifact_state='ORIGINAL_QUARANTINED',updated_at=now(),version=version+1 WHERE id=%s", (job["scanId"],))
        audit(cursor, job["scanId"], "ARCHIVE_FAILED", "FAILURE", {"code": error.code, "retryScheduled": retry})


def handle(channel, method, _properties, body: bytes) -> None:
    try:
        scan_id = json.loads(body)["scanId"]
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
            if job["kind"] == "CDR_OUTPUT" and EMBED_METADATA:
                commit_embed(job, *embed(job))
            else:
                directory, sidecar, evidence = publish(job)
                commit(job, directory, sidecar, evidence)
        except ArchiveStageError as error:
            fail(job, error)
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        print(json.dumps({"event": "archive_worker_error", "scanId": scan_id, "errorType": type(error).__name__}), flush=True)
        channel.basic_nack(method.delivery_tag, requeue=job is None or job["attempt"] < MAX_ATTEMPTS)


def handle_edit(channel, method, _properties, body: bytes) -> None:
    """Apply one API-requested sidecar edit with compare-and-swap. Never touches the PDF."""
    try:
        edit_id = str(uuid.UUID(json.loads(body)["editId"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        channel.basic_reject(method.delivery_tag, requeue=False)
        return
    try:
        with database() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT document_id,expected_revision,changes,status,actor_id FROM metadata_edits WHERE id=%s FOR UPDATE", (edit_id,))
            row = cursor.fetchone()
            if row is None or row[3] != "PENDING":
                channel.basic_ack(method.delivery_tag)
                return
            document_id, expected, changes, _status, actor = row
            status, code, revision = "APPLIED", None, None
            try:
                sidecar = ARCHIVE.update_sidecar(document_id, expected, changes)
                revision = sidecar["metadataRevision"]
            except ArchiveError as error:
                directory = ARCHIVE.find(document_id) if error.code == "REVISION_CONFLICT" else None
                current = json.loads((directory / SIDECAR).read_text(encoding="utf-8")) if directory else None
                if current and current["metadataRevision"] == expected + 1 and all(current.get(k) == v for k, v in changes.items()):
                    sidecar, revision = current, current["metadataRevision"]  # applied before a crash; finish recording it
                else:
                    status = "CONFLICT" if error.code == "REVISION_CONFLICT" else "INVALID"
                    code = error.code
            if status == "APPLIED":
                cursor.execute("UPDATE archive_documents SET title=%s,metadata_revision=%s,sidecar=%s::jsonb,updated_at=now() WHERE document_id=%s",
                               (sidecar["title"], revision, json.dumps(sidecar), document_id))
                # Keep the Paperless projection in step with the sidecar (title and tags).
                cursor.execute("""INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload)
                                  SELECT %s,'DOCUMENT',%s,'PAPERLESS_QUEUED',jsonb_build_object('documentId',%s::text)
                                   WHERE EXISTS (SELECT 1 FROM paperless_imports WHERE document_id=%s AND status<>'FAILED_FINAL')""",
                               (str(uuid.uuid4()), document_id, document_id, document_id))
            cursor.execute("UPDATE metadata_edits SET status=%s,result_revision=%s,error_code=%s,finished_at=now() WHERE id=%s",
                           (status, revision, code, edit_id))
            cursor.execute("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id,details) VALUES (%s,'METADATA_EDIT_APPLIED','DOCUMENT',%s,%s,%s,%s::jsonb)",
                           (actor, document_id, "SUCCESS" if status == "APPLIED" else "FAILURE", str(uuid.uuid4()),
                            json.dumps({"editId": edit_id, "status": status, "code": code, "revision": revision})))
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        print(json.dumps({"event": "metadata_edit_error", "editId": edit_id, "errorType": type(error).__name__}), flush=True)
        channel.basic_nack(method.delivery_tag, requeue=True)


def main() -> None:
    ARCHIVE.initialise()
    credentials = pika.PlainCredentials(os.environ["PDF_GATEWAY_RABBITMQ_USER"], os.environ["PDF_GATEWAY_RABBITMQ_PASSWORD"])
    parameters = pika.ConnectionParameters(os.getenv("PDF_GATEWAY_RABBITMQ_HOST", "rabbitmq"), credentials=credentials,
                                           heartbeat=600, blocked_connection_timeout=60)
    while True:
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE, durable=True)
            channel.queue_declare(queue=EDIT_QUEUE, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE, on_message_callback=handle)
            channel.basic_consume(queue=EDIT_QUEUE, on_message_callback=handle_edit)
            channel.start_consuming()
        except (pika.exceptions.AMQPError, psycopg2.Error, OSError):
            time.sleep(5)


if __name__ == "__main__":
    main()
