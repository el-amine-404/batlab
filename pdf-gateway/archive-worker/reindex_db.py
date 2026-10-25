"""Rebuild the PostgreSQL archive index from the archive's sidecars alone.

Used after database loss: every valid document directory becomes an
archive_documents/archive_versions row. Links to scans and artifacts are kept
only when those rows still exist; otherwise the document is visible to
administrators until ownership is reassigned. Invalid documents are reported,
never indexed.
"""

from __future__ import annotations

import json

from archive import SIDECAR, Archive


def reindex_db(archive: Archive, connection) -> dict:
    indexed, rejected = 0, {}
    with connection, connection.cursor() as cursor:
        for directory in archive.document_dirs():
            relative = directory.relative_to(archive.archive).as_posix()
            problems = archive.check_document(directory)
            if problems:
                rejected[relative] = problems
                continue
            sidecar = json.loads((directory / SIDECAR).read_text(encoding="utf-8"))
            scan_id = sidecar["derivedFrom"]["scanId"]
            cursor.execute("SELECT 1 FROM scan_jobs WHERE id=%s", (scan_id,))
            scan = scan_id if cursor.fetchone() else None
            cursor.execute("""INSERT INTO archive_documents(document_id,scan_id,archive_path,pdf_file,title,category,review_state,
                                                            current_version,metadata_revision,sha256,bytes,sidecar)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                              ON CONFLICT (document_id) DO UPDATE SET archive_path=EXCLUDED.archive_path,pdf_file=EXCLUDED.pdf_file,
                                title=EXCLUDED.title,category=EXCLUDED.category,review_state=EXCLUDED.review_state,
                                current_version=EXCLUDED.current_version,metadata_revision=EXCLUDED.metadata_revision,
                                sha256=EXCLUDED.sha256,bytes=EXCLUDED.bytes,sidecar=EXCLUDED.sidecar,updated_at=now()""",
                           (sidecar["documentId"], scan, relative, sidecar["relativePdfPath"], sidecar["title"],
                            sidecar["organization"]["category"], sidecar["organization"]["reviewState"], sidecar["artifactVersion"],
                            sidecar["metadataRevision"], sidecar["integrity"]["sha256"], sidecar["integrity"]["bytes"], json.dumps(sidecar)))
            cursor.execute("SELECT id FROM artifacts WHERE sha256=%s AND scan_id=%s AND kind IN ('CDR_OUTPUT','EMBEDDED_METADATA')",
                           (sidecar["integrity"]["sha256"], scan))
            artifact = cursor.fetchone()
            cursor.execute("""INSERT INTO archive_versions(document_id,artifact_version,artifact_id,sha256) VALUES (%s,%s,%s,%s)
                              ON CONFLICT (document_id,artifact_version) DO NOTHING""",
                           (sidecar["documentId"], sidecar["artifactVersion"], artifact[0] if artifact else None, sidecar["integrity"]["sha256"]))
            indexed += 1
    return {"indexed": indexed, "rejected": rejected, "ok": not rejected}
