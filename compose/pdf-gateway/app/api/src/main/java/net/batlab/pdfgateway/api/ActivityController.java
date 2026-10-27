package net.batlab.pdfgateway.api;

import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * What the gateway is doing and has just done, for the Activity page: files still being
 * checked, the AI laptop's jobs (waiting, running, done or failed, with the model that ran
 * them) and copies on their way to Paperless. A person sees their own files; an admin all.
 */
@RestController
public class ActivityController {
    private final JdbcTemplate jdbc;

    public ActivityController(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @GetMapping(path = "/api/v1/system/activity", produces = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<String> activity(@AuthenticationPrincipal GatewayPrincipal principal) {
        String body = jdbc.queryForObject("""
                SELECT jsonb_build_object(
                  'files', COALESCE((SELECT jsonb_agg(f ORDER BY f->>'createdAt') FROM (
                      SELECT jsonb_build_object('scanId', s.id, 'title', s.display_name, 'status', s.status,
                                                'createdAt', s.created_at, 'updatedAt', s.updated_at) f
                        FROM scan_jobs s
                       WHERE (s.owner_id=? OR ?) AND s.status NOT IN ('READY','REVIEW_REQUIRED','BLOCKED','FAILED','CANCELLED')
                       ORDER BY s.created_at LIMIT 100) files), '[]'::jsonb),
                  'ai', COALESCE((SELECT jsonb_agg(j ORDER BY (j->>'active')::boolean DESC, j->>'createdAt' DESC) FROM (
                      SELECT jsonb_build_object('kind', e.kind, 'status', e.status, 'scanId', s.id,
                                                'title', COALESCE(d.title, s.display_name), 'model', e.result->>'model',
                                                'engine', e.result->>'engine', 'agent', e.lease_agent, 'attempts', e.attempts,
                                                'errorCode', e.error_code, 'createdAt', e.created_at, 'completedAt', e.completed_at,
                                                'leaseUntil', e.lease_until,
                                                'active', e.status IN ('WAITING_FOR_AI','LEASED','SUBMITTED')) j
                        FROM enrichment_jobs e
                        LEFT JOIN archive_documents d ON d.document_id=e.document_id
                        JOIN scan_jobs s ON s.id=COALESCE(e.scan_id, d.scan_id)
                       WHERE (s.owner_id=? OR ?)
                         AND (e.status IN ('WAITING_FOR_AI','LEASED','SUBMITTED') OR e.created_at > now() - interval '14 days')
                       ORDER BY e.created_at DESC LIMIT 100) jobs), '[]'::jsonb),
                  'paperless', COALESCE((SELECT jsonb_agg(p ORDER BY p->>'updatedAt' DESC) FROM (
                      SELECT jsonb_build_object('scanId', s.id, 'title', d.title, 'status', i.status, 'attempts', i.attempts,
                                                'lastError', i.last_error, 'paperlessDocumentId', i.paperless_document_id,
                                                'updatedAt', i.updated_at) p
                        FROM paperless_imports i JOIN archive_documents d ON d.document_id=i.document_id JOIN scan_jobs s ON s.id=d.scan_id
                       WHERE (s.owner_id=? OR ?)
                         AND (i.status <> 'IMPORTED' OR i.updated_at > now() - interval '2 days')
                       ORDER BY i.updated_at DESC LIMIT 50) imports), '[]'::jsonb),
                  'now', now())::text""", String.class,
                principal.userId(), principal.admin(), principal.userId(), principal.admin(), principal.userId(), principal.admin());
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(body);
    }
}
