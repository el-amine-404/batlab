package net.batlab.pdfgateway.api;

import java.util.List;
import java.util.UUID;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/** Request and observe the Paperless projection of an archived scan. Never exposes Paperless credentials. */
@RestController
public class PaperlessController {
    private final JdbcTemplate jdbc;
    public PaperlessController(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @PostMapping(path = "/api/v1/scans/{scanId}/paperless-import", produces = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasAnyRole('ADMIN','USER')")
    @Transactional
    public ResponseEntity<String> request(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId) {
        List<String[]> rows = jdbc.query("""
                SELECT d.document_id, d.sha256 FROM scan_jobs s JOIN archive_documents d ON d.scan_id=s.id
                 WHERE s.id=? AND (s.owner_id=? OR ?) AND s.status='READY'""",
                (rs, row) -> new String[]{rs.getString(1), rs.getString(2)}, scanId, principal.userId(), principal.admin());
        if (rows.isEmpty()) {
            boolean visible = Boolean.TRUE.equals(jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM scan_jobs WHERE id=? AND (owner_id=? OR ?))",
                    Boolean.class, scanId, principal.userId(), principal.admin()));
            throw visible ? new ApiException(409, "NOT_ARCHIVED", "Only documents published to the archive can be imported")
                          : new ApiException(404, "SCAN_NOT_FOUND", "Scan not found");
        }
        String documentId = rows.getFirst()[0];
        // Idempotent: an existing request keeps its state; a final failure can be retried explicitly.
        jdbc.update("""
                INSERT INTO paperless_imports(document_id, artifact_sha256, requested_by) VALUES (?,?,?)
                ON CONFLICT (document_id) DO UPDATE SET status = CASE WHEN paperless_imports.status='FAILED_FINAL' THEN 'PENDING' ELSE paperless_imports.status END,
                    attempts = CASE WHEN paperless_imports.status='FAILED_FINAL' THEN 0 ELSE paperless_imports.attempts END, updated_at=now()""",
                documentId, rows.getFirst()[1], principal.userId());
        jdbc.update("UPDATE scan_jobs SET import_status=(SELECT status FROM paperless_imports WHERE document_id=?) WHERE id=?", documentId, scanId);
        jdbc.update("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (?,'DOCUMENT',?,'PAPERLESS_QUEUED',jsonb_build_object('documentId',?::text))",
                UUID.randomUUID(), documentId, documentId);
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id) VALUES (?,'PAPERLESS_IMPORT_REQUESTED','DOCUMENT',?,'SUCCESS',?)",
                principal.userId(), documentId, UUID.randomUUID().toString());
        return ResponseEntity.accepted().header(HttpHeaders.LOCATION, "/api/v1/scans/" + scanId + "/paperless-import").body(status(principal, scanId));
    }

    @GetMapping(path = "/api/v1/scans/{scanId}/paperless-import", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> get(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId) {
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(status(principal, scanId));
    }

    private String status(GatewayPrincipal principal, String scanId) {
        List<String> rows = jdbc.query("""
                SELECT jsonb_build_object('scanId', s.id, 'documentId', d.document_id,
                         'status', COALESCE(i.status, 'NOT_REQUESTED'), 'paperlessDocumentId', i.paperless_document_id,
                         'syncedRevision', i.synced_revision, 'metadataRevision', d.metadata_revision,
                         'attempts', COALESCE(i.attempts, 0), 'lastError', i.last_error, 'updatedAt', i.updated_at)::text
                  FROM scan_jobs s LEFT JOIN archive_documents d ON d.scan_id=s.id LEFT JOIN paperless_imports i ON i.document_id=d.document_id
                 WHERE s.id=? AND (s.owner_id=? OR ?)""", (rs, row) -> rs.getString(1), scanId, principal.userId(), principal.admin());
        if (rows.isEmpty()) throw new ApiException(404, "SCAN_NOT_FOUND", "Scan not found");
        return rows.getFirst();
    }
}
