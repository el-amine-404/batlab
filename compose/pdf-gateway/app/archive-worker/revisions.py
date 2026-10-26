"""New versions of published documents, people's edits, and deletion (archive side).

A new version never bypasses final validation: the metadata is written onto the
validated CDR output (the published version's parent), that file goes through
final validation like the first one (pdf.gateway.revision-validation.v1), and
only a PASS is swapped into the archive (Archive.replace_pdf). Until then, and
if it fails, the published version stays as it is.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import enrichment
import metadata
from archive import ArchiveError, sha256_file

TYPES = enrichment.TYPES
SECTIONS = set(enrichment.SECTIONS) | {"Unsorted"}


def edit_changes(changes: dict, current: dict) -> dict:
    """A person's edit (API form) as sidecar changes; what a person sets counts as verified."""
    out: dict = {}
    if "title" in changes:
        out.update(title=changes["title"], titleSource="user_verified")
    if "tags" in changes:
        out["tags"] = changes["tags"]
    for name in ("issuer", "reference"):
        if name in changes:
            out[name] = {"value": changes[name], "source": "user_verified", "reviewState": "VERIFIED"} if changes[name] else None
    if "documentDate" in changes:
        out["documentDate"] = ({"value": changes["documentDate"], "source": "user_verified", "reviewState": "VERIFIED"}
                               if changes["documentDate"] else None)
    if "documentType" in changes:
        out["documentType"] = changes["documentType"] or None
    if "amount" in changes:
        amount = changes["amount"]
        out["amount"] = {"value": amount["value"], "currency": amount["currency"], "source": "user_verified"} if amount else None
    if {"section", "documentType", "documentDate"} & set(changes):
        section = changes.get("section", current["organization"]["category"])
        kind = out.get("documentType", current.get("documentType")) if "documentType" in changes else current.get("documentType")
        dated = (out.get("documentDate") if "documentDate" in changes else current.get("documentDate"))
        organization = {"category": section, "reviewState": "VERIFIED", "reason": "Filed by a person"}
        if section != "Unsorted" and kind in TYPES and dated:
            organization["folder"] = TYPES[kind]
        out["organization"] = organization
    return out


def queue_revision(cursor, storage: Path, sidecar: dict) -> str | None:
    """Write the sidecar's metadata onto the validated CDR output as a new artifact and send it
    to final validation. Returns the artifact ID (None when the scan has no validated output)."""
    scan_id = sidecar["derivedFrom"]["scanId"]
    cursor.execute("""SELECT id, storage_object FROM artifacts WHERE scan_id=%s AND kind='CDR_OUTPUT' AND state='FINAL_VALIDATED'
                       ORDER BY created_at DESC LIMIT 1""", (scan_id,))
    parent = cursor.fetchone()
    if parent is None:
        return None
    cursor.execute("SELECT count(*) FROM artifacts WHERE scan_id=%s AND kind='EMBEDDED_METADATA'", (scan_id,))
    number = cursor.fetchone()[0] + 1
    target = storage / "metadata-versions" / f"{scan_id}-{number}.pdf"
    values = metadata.fields_for(sidecar)
    metadata.write(storage / parent[1], target, values)
    artifact_id = f"meta-{scan_id[5:]}-{number}"
    cursor.execute("""INSERT INTO artifacts(id,scan_id,kind,storage_object,sha256,bytes,state,parent_artifact_id)
                      VALUES (%s,%s,'EMBEDDED_METADATA',%s,%s,%s,'SANITIZED_STAGED',%s)""",
                   (artifact_id, scan_id, f"metadata-versions/{target.name}", sha256_file(target), target.stat().st_size, parent[0]))
    cursor.execute("""INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload)
                      VALUES (%s,'SCAN',%s,'REVISION_VALIDATION_QUEUED',
                              jsonb_build_object('scanId',%s::text,'artifactId',%s::text,'documentId',%s::text,'embedded',%s::jsonb))""",
                   (str(uuid.uuid4()), scan_id, scan_id, artifact_id, sidecar["documentId"], json.dumps(values)))
    return artifact_id


def publish_revision(cursor, archive, storage: Path, event: dict) -> dict | None:
    """Swap a version that passed final validation into the archive. None when it is stale."""
    scan_id, artifact_id, document_id = event["scanId"], event["artifactId"], event["documentId"]
    cursor.execute("""SELECT a.sha256, a.storage_object FROM artifacts a
                        JOIN final_validations f ON f.artifact_id=a.id AND f.verdict='PASS' AND f.output_sha256=a.sha256
                       WHERE a.id=%s AND a.scan_id=%s AND a.kind='EMBEDDED_METADATA' AND a.state='FINAL_VALIDATED'""",
                   (artifact_id, scan_id))
    row = cursor.fetchone()
    cursor.execute("SELECT id FROM artifacts WHERE scan_id=%s AND kind='EMBEDDED_METADATA' ORDER BY created_at DESC LIMIT 1", (scan_id,))
    newest = cursor.fetchone()
    cursor.execute("SELECT metadata_revision, current_version FROM archive_documents WHERE document_id=%s FOR UPDATE", (document_id,))
    document = cursor.fetchone()
    if row is None or newest is None or newest[0] != artifact_id or document is None:
        return None  # superseded by a newer edit, or gone
    sha, storage_object = row
    revision, version = document
    current = archive.find(document_id).read()
    embedded = event.get("embedded") or {}
    if metadata.fields_for(current) != embedded:
        return None  # the sidecar changed since this version was written; a newer one is on its way
    changes = {"embeddedMetadata": {"writer": metadata.WRITER_VERSION, "fields": sorted(key.lstrip("/") for key in embedded),
                                    "linearized": True, "values": {key.lstrip("/"): value for key, value in embedded.items()}}}
    updated = archive.replace_pdf(document_id, revision, storage / storage_object, sha, changes)
    cursor.execute("INSERT INTO archive_versions(document_id,artifact_version,artifact_id,sha256) VALUES (%s,%s,%s,%s)",
                   (document_id, updated["artifactVersion"], artifact_id, sha))
    cursor.execute("""UPDATE archive_documents SET sha256=%s, bytes=%s, current_version=%s, metadata_revision=%s, sidecar=%s::jsonb,
                             title=%s, updated_at=now() WHERE document_id=%s""",
                   (sha, updated["integrity"]["bytes"], updated["artifactVersion"], updated["metadataRevision"],
                    json.dumps(updated), updated["title"], document_id))
    cursor.execute("UPDATE artifacts SET state='PUBLISHED' WHERE id=%s", (artifact_id,))
    return updated


def delete_files(storage: Path, archive, event: dict) -> dict:
    """This stage's part of a deletion: the archived document and its metadata versions."""
    scan_id = event["scanId"]
    removed = 0
    for path in (storage / "metadata-versions").glob(f"{scan_id}-*.pdf"):
        path.unlink(missing_ok=True)
        removed += 1
    archived = archive.delete(event["documentId"]) if event.get("documentId") else None
    return {"metadataVersions": removed, "archivePath": archived}


__all__ = ["edit_changes", "queue_revision", "publish_revision", "delete_files", "ArchiveError"]
