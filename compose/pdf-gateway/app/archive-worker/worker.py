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
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pika
import psycopg2

import enrichment
import metadata
from archive import Archive, ArchiveError, SIDECAR, canonical_json, sha256_file
from organize import DEFAULT_TAGS_REVISION, default_taxonomy, organise, validated

QUEUE = "pdf.gateway.archive.v1"
EDIT_QUEUE = "pdf.gateway.metadata-edit.v1"
ENRICHMENT_QUEUE = "pdf.gateway.enrichment-result.v1"
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


def audit_document(cursor, document_id: str, action: str, outcome: str, details: dict) -> None:
    cursor.execute("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id,details) VALUES (%s,'DOCUMENT',%s,%s,%s,%s::jsonb)",
                   (action, document_id, outcome, str(uuid.uuid4()), json.dumps(details)))


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


class Deferred(Exception):
    """The archive waits for the AI laptop's answer; try again shortly (not a failed attempt)."""


AI_WAIT_SECONDS = int(os.getenv("PDF_GATEWAY_AI_WAIT_SECONDS", "600"))
AI_RETRY_SECONDS = int(os.getenv("PDF_GATEWAY_AI_RETRY_SECONDS", "20"))
CONTEXT_CHARS = 12_000


def layer_text(path: Path) -> str:
    """The clean copy's own OCR text layer (validated bytes), bounded; '' when there is none."""
    try:
        result = subprocess.run(["pdftotext", "-q", "-layout", "-enc", "UTF-8", "-l", "20", str(path), "-"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=60, check=False, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": "/nonexistent"})
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    text = result.stdout[:256 * 1024].decode("utf-8", "ignore")
    text = "\n".join(re.sub(r" {4,}", "   ", line).rstrip() for line in text.split("\n"))
    return enrichment.clean(text).strip()


def ai_filing(job: dict, text: str, sidecar: dict, taxonomy: dict) -> tuple[str, dict | None]:
    """Whether to wait for, apply, or go without the AI's answer: ('WAIT'|'APPLY'|'SKIP', fields)."""
    if not enrichment.ENABLED or sidecar["organization"]["reviewState"] not in ("FALLBACK", "REVIEW_REQUIRED") or not text.strip():
        return "SKIP", None
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status, applied, deadline_at > now() FROM enrichment_jobs WHERE scan_id=%s AND kind='SUGGEST' FOR UPDATE",
                       (job["scanId"],))
        row = cursor.fetchone()
        if row is None:
            # Wait only for an agent that is there and able to answer.
            cursor.execute("""SELECT EXISTS (SELECT 1 FROM ai_agents WHERE last_seen_at > now() - interval '3 minutes'
                                AND (breaker_open_until IS NULL OR breaker_open_until < now())
                                AND report->'llm'->>'service' = 'UP' AND (report->'llm'->>'modelInstalled')::boolean)""")
            ready = cursor.fetchone()[0]
            context = {"text": text[:CONTEXT_CHARS], "truncated": len(text) > CONTEXT_CHARS,
                       "types": sorted(enrichment.TYPES), "sections": enrichment.SECTIONS,
                       "tags": [tag["id"] for tag in taxonomy.get("tags", []) if not tag.get("blocked") and not tag.get("private")]}
            cursor.execute("""INSERT INTO enrichment_jobs(id,scan_id,artifact_sha256,kind,context,deadline_at)
                              VALUES (%s,%s,%s,'SUGGEST',%s::jsonb,now()+make_interval(secs=>%s))""",
                           (str(uuid.uuid4()), job["scanId"], job["sha256"], json.dumps(context), AI_WAIT_SECONDS if ready else 0))
            audit(cursor, job["scanId"], "AI_FILING_REQUESTED", "SUCCESS", {"waiting": ready})
            return ("WAIT", None) if ready else ("SKIP", None)
        status, applied, waiting = row
        if status == "COMPLETED" and applied:
            return "APPLY", applied
        if status in ("WAITING_FOR_AI", "LEASED", "SUBMITTED") and waiting:
            return "WAIT", None
        return "SKIP", None


def defer(job: dict) -> None:
    """Undo this claim without counting an attempt, and look again shortly."""
    with database() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT status FROM scan_jobs WHERE id=%s FOR UPDATE", (job["scanId"],))
        row = cursor.fetchone()
        if row is None or row[0] != "ARCHIVING":
            return
        cursor.execute("DELETE FROM job_steps WHERE scan_id=%s AND step_type='ARCHIVE' AND attempt=%s AND status='RUNNING'",
                       (job["scanId"], job["attempt"]))
        cursor.execute("UPDATE scan_jobs SET status='AWAITING_ARCHIVE',updated_at=now(),version=version+1 WHERE id=%s", (job["scanId"],))
        cursor.execute("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload,available_at) VALUES (%s,'SCAN',%s,'ARCHIVE_QUEUED',jsonb_build_object('scanId',%s,'artifactId',%s),now()+make_interval(secs=>%s))",
                       (str(uuid.uuid4()), job["scanId"], job["scanId"], job["artifactId"], AI_RETRY_SECONDS))


def filing_text(job: dict) -> str:
    """The original's text, or for an image-only original the clean copy's OCR layer."""
    text = verified_inputs(job)
    if not text.strip():
        text = layer_text(job["output"])
        if text:
            job["extraction"] = {"status": "OK", "method": "ocr", "source": "viewing_copy", "engine": "dangerzone-ocr",
                                 "pages": max(1, text.count("\f") + 1), "characters": len(text), "truncated": False}
    return text


def organised(job: dict, text: str) -> tuple[dict, dict]:
    """The sidecar: filed by the rules, else by the AI's checked answer (raises Deferred to wait for it)."""
    sidecar, evidence = build_sidecar(job, text)
    _rules, taxonomy = manifests()
    decision, fields = ai_filing(job, text, sidecar, taxonomy)
    if decision == "WAIT":
        raise Deferred()
    if decision == "APPLY":
        sidecar.update(enrichment.sidecar_changes(fields, sidecar))
        evidence["ai"] = {"fields": sorted(fields)}
    return sidecar, evidence


def embed(job: dict) -> tuple[Path, dict, dict, dict]:
    """Phase 1: organise (rules, else the AI) and write allowlisted metadata into a new PDF version."""
    text = filing_text(job)
    sidecar, evidence = organised(job, text)
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
    # An image-only original has no text of its own; its clean copy carries the OCR layer.
    text = filing_text(job)
    if job["kind"] == "EMBEDDED_METADATA":
        # Publish the organisation recorded when the metadata was written, so the
        # sidecar and the embedded values cannot disagree.
        sidecar = dict(job["embedPhase"]["sidecar"], updatedAt=now())
        sidecar["embeddedMetadata"] = {"writer": metadata.WRITER_VERSION,
                                       "fields": sorted(key.lstrip("/") for key in job["embedPhase"]["embedded"]),
                                       "linearized": True}
        evidence = dict(job["embedPhase"]["organisation"], publishedVersion="EMBEDDED_METADATA")
    else:
        sidecar, evidence = organised(job, text)
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
        if enrichment.ENABLED:
            # The AI job made before publication now belongs to the document.
            cursor.execute("UPDATE enrichment_jobs SET document_id=%s, artifact_sha256=%s WHERE scan_id=%s",
                           (sidecar["documentId"], sidecar["integrity"]["sha256"], scan_id))
            if cursor.rowcount == 0:
                enrichment.queue_initial(cursor, sidecar["documentId"], sidecar["integrity"]["sha256"],
                                         sidecar["organization"]["reviewState"], bool(sidecar.get("relativeTextPath")))
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
            try:
                if job["kind"] == "CDR_OUTPUT" and EMBED_METADATA:
                    commit_embed(job, *embed(job))
                else:
                    directory, sidecar, evidence = publish(job)
                    commit(job, directory, sidecar, evidence)
            except Deferred:
                defer(job)
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


def manifests() -> tuple[dict, dict]:
    try:
        rules = validated(ARCHIVE.manifests / "rules.json", "rules.schema.json",
                          {"schemaVersion": "1.0.0", "revision": 1, "updatedAt": now(), "rules": []})
        taxonomy = validated(ARCHIVE.manifests / "taxonomy.json", "taxonomy.schema.json", default_taxonomy(now()))
    except Exception as error:
        raise ArchiveStageError("ARCHIVE_CONFIG_INVALID", type(error).__name__) from error
    return rules, taxonomy


def index_update(cursor, sidecar: dict) -> None:
    cursor.execute("""UPDATE archive_documents SET archive_path=%s, pdf_file=%s, title=%s, category=%s, review_state=%s,
                             metadata_revision=%s, sidecar=%s::jsonb, updated_at=now() WHERE document_id=%s""",
                   (sidecar["archivePath"], sidecar["relativePdfPath"], sidecar["title"], sidecar["organization"]["category"],
                    sidecar["organization"]["reviewState"], sidecar["metadataRevision"], json.dumps(sidecar), sidecar["documentId"]))
    cursor.execute("""INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload)
                      SELECT %s,'DOCUMENT',%s,'PAPERLESS_QUEUED',jsonb_build_object('documentId',%s::text)
                       WHERE EXISTS (SELECT 1 FROM paperless_imports WHERE document_id=%s AND status<>'FAILED_FINAL')""",
                   (str(uuid.uuid4()), sidecar["documentId"], sidecar["documentId"], sidecar["documentId"]))


def handle_enrichment(channel, method, _properties, body: bytes) -> None:
    """Validate a submitted OCR/AI result and apply it (OCR) or store it as a proposal (AI)."""
    try:
        job_id = str(uuid.UUID(json.loads(body)["jobId"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        channel.basic_reject(method.delivery_tag, requeue=False)
        return
    try:
        with database() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT j.status,j.kind,j.document_id,j.artifact_sha256,j.result,j.lease_agent,d.sha256,d.metadata_revision,
                                     j.scan_id,j.context,d.review_state
                                FROM enrichment_jobs j LEFT JOIN archive_documents d ON d.document_id=j.document_id
                               WHERE j.id=%s FOR UPDATE OF j""", (job_id,))
            row = cursor.fetchone()
            if row is None or row[0] != "SUBMITTED":
                channel.basic_ack(method.delivery_tag)
                return
            _status, kind, document_id, job_sha, result, agent, current_sha, revision, scan_id, context, review = row
            if kind == "SUGGEST" and document_id is None:
                # Before publication: check the answer against the text it was given; the
                # waiting archive step picks the checked fields up at once.
                try:
                    _rules, taxonomy = manifests()
                    fields, dropped = enrichment.validate_filing(result if isinstance(result, dict) else {},
                                                                 (context or {}).get("text", ""), taxonomy)
                    cursor.execute("UPDATE enrichment_jobs SET applied=%s::jsonb WHERE id=%s", (json.dumps(fields), job_id))
                    enrichment.record_success(cursor, job_id, agent)
                    audit(cursor, scan_id, "AI_FILING", "SUCCESS", {"jobId": job_id, "fields": sorted(fields), "dropped": dropped[:20]})
                except enrichment.InvalidResult as invalid:
                    enrichment.record_failure(cursor, job_id, agent, "INVALID_RESULT")
                    audit(cursor, scan_id, "ENRICHMENT_REJECTED", "FAILURE", {"jobId": job_id, "reason": str(invalid)[:200]})
                cursor.execute("""INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload)
                                  SELECT %s,'SCAN',s.id,'ARCHIVE_QUEUED',jsonb_build_object('scanId',s.id,'artifactId',a.id)
                                    FROM scan_jobs s JOIN artifacts a ON a.scan_id=s.id AND a.kind='CDR_OUTPUT' AND a.state='FINAL_VALIDATED'
                                   WHERE s.id=%s AND s.status='AWAITING_ARCHIVE'""", (str(uuid.uuid4()), scan_id))
                channel.basic_ack(method.delivery_tag)
                return
            if document_id is None:
                enrichment.record_failure(cursor, job_id, None, "NO_DOCUMENT")
            elif job_sha != current_sha:
                enrichment.record_failure(cursor, job_id, None, "DOCUMENT_CHANGED")
            else:
                try:
                    rules, taxonomy = manifests()
                    if kind == "OCR":
                        text, meta = enrichment.validate_ocr(result if isinstance(result, dict) else {})
                        organisation = organise(text, rules, taxonomy)
                        changes = {}
                        if organisation.fields["organization"]["reviewState"] == "RULE":
                            changes = dict(organisation.fields)
                            if "documentDate" in changes:
                                changes["documentDate"] = dict(changes["documentDate"], source="ocr")
                        updated = ARCHIVE.reorganise(document_id, revision, changes, text=text, text_extraction=meta)
                        index_update(cursor, updated)
                        enrichment.record_success(cursor, job_id, agent)
                        if updated["organization"]["reviewState"] in ("FALLBACK", "REVIEW_REQUIRED"):
                            enrichment.queue(cursor, document_id, current_sha, "SUGGEST")
                        audit_document(cursor, document_id, "OCR_APPLIED", "SUCCESS",
                              {"jobId": job_id, "refiled": bool(changes), "category": updated["organization"]["category"]})
                    else:
                        directory = ARCHIVE.find(document_id)
                        text = (context or {}).get("text", "")
                        text_path = directory / "content.txt" if directory else None
                        if not text and text_path and text_path.is_file():
                            text = text_path.read_text(encoding="utf-8", errors="replace")
                        fields, dropped = enrichment.validate_filing(result if isinstance(result, dict) else {}, text, taxonomy)
                        cursor.execute("UPDATE enrichment_jobs SET applied=%s::jsonb WHERE id=%s", (json.dumps(fields), job_id))
                        if fields and review in ("FALLBACK", "REVIEW_REQUIRED"):
                            # Published before the answer came (the laptop was away): file it now.
                            current = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
                            updated = ARCHIVE.reorganise(document_id, revision, enrichment.sidecar_changes(fields, current))
                            index_update(cursor, updated)
                        elif fields:
                            # Someone filed it already: keep the answer as a suggestion only.
                            cursor.execute("INSERT INTO ai_suggestions(id,document_id,job_id,model,prompt_version,fields) VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
                                           enrichment.suggestion_record(job_id, document_id, result, fields, dropped))
                        enrichment.record_success(cursor, job_id, agent)
                        audit_document(cursor, document_id, "AI_FILING", "SUCCESS", {"jobId": job_id, "fields": sorted(fields), "dropped": dropped[:20]})
                except enrichment.InvalidResult as invalid:
                    enrichment.record_failure(cursor, job_id, agent, "INVALID_RESULT")
                    audit_document(cursor, document_id, "ENRICHMENT_REJECTED", "FAILURE", {"jobId": job_id, "reason": str(invalid)[:200]})
                except (ArchiveError, ArchiveStageError) as error:
                    # Our side could not apply it (e.g. a concurrent edit); not the agent's fault.
                    enrichment.record_failure(cursor, job_id, None, "APPLY_" + getattr(error, "code", "FAILED"))
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        print(json.dumps({"event": "enrichment_apply_error", "jobId": job_id, "errorType": type(error).__name__}), flush=True)
        channel.basic_nack(method.delivery_tag, requeue=True)
        time.sleep(5)


def ensure_taxonomy() -> None:
    """The portable archive carries its own vocabulary (manifests/taxonomy.json).

    An archive made with an older default vocabulary gets the new default tags added
    once (nothing it has, blocked or private, is changed or removed)."""
    path = ARCHIVE.manifests / "taxonomy.json"
    if path.exists():
        current = validated(path, "taxonomy.schema.json", None)
        if current["revision"] >= DEFAULT_TAGS_REVISION:
            return
        known = {tag["id"] for tag in current["tags"]}
        added = [tag for tag in default_taxonomy(now())["tags"] if tag["id"] not in known]
        value = dict(current, revision=DEFAULT_TAGS_REVISION, updatedAt=now(), tags=current["tags"] + added)
    else:
        value = default_taxonomy(now())
    temporary = path.with_name(f".taxonomy.{uuid.uuid4().hex}")
    temporary.write_bytes(canonical_json(value))
    os.replace(temporary, path)


def main() -> None:
    ARCHIVE.initialise()
    ensure_taxonomy()
    credentials = pika.PlainCredentials(os.environ["PDF_GATEWAY_RABBITMQ_USER"], os.environ["PDF_GATEWAY_RABBITMQ_PASSWORD"])
    parameters = pika.ConnectionParameters(os.getenv("PDF_GATEWAY_RABBITMQ_HOST", "rabbitmq"), credentials=credentials,
                                           heartbeat=600, blocked_connection_timeout=60)
    while True:
        try:
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.queue_declare(queue=QUEUE, durable=True)
            channel.queue_declare(queue=EDIT_QUEUE, durable=True)
            channel.queue_declare(queue=ENRICHMENT_QUEUE, durable=True)
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE, on_message_callback=handle)
            channel.basic_consume(queue=EDIT_QUEUE, on_message_callback=handle_edit)
            channel.basic_consume(queue=ENRICHMENT_QUEUE, on_message_callback=handle_enrichment)
            channel.start_consuming()
        except (pika.exceptions.AMQPError, psycopg2.Error, OSError):
            time.sleep(5)


if __name__ == "__main__":
    main()
