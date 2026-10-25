package net.batlab.pdfgateway.api;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import net.batlab.pdfgateway.config.GatewayProperties;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Read models for the web UI, built as JSON by PostgreSQL from an explicit
 * allowlist: typed codes, severities, versions and verdicts only. Report
 * evidence blobs and any document text stay in restricted storage.
 */
@Service
public class InsightService {
    private final JdbcTemplate jdbc;
    private final RabbitTemplate rabbit;
    private final GatewayProperties properties;

    public InsightService(JdbcTemplate jdbc, RabbitTemplate rabbit, GatewayProperties properties) {
        this.jdbc = jdbc;
        this.rabbit = rabbit;
        this.properties = properties;
    }

    public String findings(GatewayPrincipal principal, String scanId) {
        List<String> rows = jdbc.query("""
                SELECT jsonb_build_object(
                  'scanId', s.id,
                  'analysis', (SELECT jsonb_build_object(
                      'detection', d->'detection', 'policyDecision', d->'policyDecision', 'policyVersion', d->'policyVersion',
                      'rulesetVersion', d->'rulesetVersion',
                      'engines', COALESCE((SELECT jsonb_agg(jsonb_build_object('engine', e->'engine', 'status', e->'status', 'version', e->'version',
                                                                               'databaseVersion', e->'databaseVersion', 'databaseDate', e->'databaseDate'))
                                           FROM jsonb_array_elements(d->'engines') e), '[]'::jsonb),
                      'findings', COALESCE((SELECT jsonb_agg(jsonb_build_object('engine', f->'engine', 'code', f->'code',
                                                                                'severity', f->'severity', 'message', f->'message'))
                                            FROM jsonb_array_elements(d->'findings') f), '[]'::jsonb),
                      'textExtraction', jsonb_build_object('status', d->'textExtraction'->'status', 'pages', d->'textExtraction'->'pages',
                                                           'characters', d->'textExtraction'->'characters'))
                    FROM (SELECT details AS d FROM job_steps WHERE scan_id=s.id AND step_type='ANALYSIS' AND status='SUCCEEDED'
                           ORDER BY attempt DESC LIMIT 1) a),
                  'signature', (SELECT jsonb_build_object(
                      'state', v.state, 'policyDecision', v.policy_decision, 'signatureCount', v.signature_count,
                      'engine', v.report->'engine'->'version', 'policyVersion', v.report->'policyVersion',
                      'signatures', COALESCE((SELECT jsonb_agg(jsonb_build_object(
                          'integrity', g->'integrity', 'cryptographicValidity', g->'cryptographicValidity', 'trust', g->'trust',
                          'revocation', g->'revocation', 'modificationLevel', g->'modificationLevel', 'docMdpPermitted', g->'docMdpPermitted',
                          'digestAlgorithm', g->'digestAlgorithm', 'signerSubject', g->'signerCertificate'->'subject',
                          'signerIssuer', g->'signerCertificate'->'issuer'))
                        FROM jsonb_array_elements(v.report->'signatures') g), '[]'::jsonb),
                      'limitations', COALESCE(v.report->'limitations', '[]'::jsonb))
                    FROM signature_verifications v WHERE v.scan_id=s.id ORDER BY v.attempt DESC LIMIT 1),
                  'cdr', COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt', c.attempt, 'status', c.status, 'failureCode', c.failure_code,
                                    'retryable', c.retryable, 'durationMs', c.duration_ms, 'dangerzoneVersion', c.dangerzone_version,
                                    'imageDigest', c.image_digest, 'isolation', c.evidence->'isolation') ORDER BY c.attempt)
                                   FROM cdr_results c WHERE c.scan_id=s.id), '[]'::jsonb),
                  'validation', COALESCE((SELECT jsonb_agg(jsonb_build_object('attempt', f.attempt, 'artifact',
                                    (SELECT kind FROM artifacts WHERE id=f.artifact_id), 'verdict', f.verdict, 'detection', f.detection,
                                    'policyDecision', f.policy_decision, 'failureCodes', f.failure_codes, 'policyVersion', f.policy_version,
                                    'engines', f.engines) ORDER BY f.attempt)
                                   FROM final_validations f WHERE f.scan_id=s.id), '[]'::jsonb),
                  'archive', (SELECT jsonb_build_object('documentId', d.document_id, 'category', d.category, 'reviewState', d.review_state)
                                FROM archive_documents d WHERE d.scan_id=s.id),
                  'steps', COALESCE((SELECT jsonb_agg(jsonb_build_object('type', j.step_type, 'status', j.status, 'attempt', j.attempt,
                                    'startedAt', j.started_at, 'finishedAt', j.finished_at, 'errorCode', j.error_code) ORDER BY j.started_at, j.attempt)
                                   FROM job_steps j WHERE j.scan_id=s.id), '[]'::jsonb),
                  'limitations', jsonb_build_array(
                      'No scanner can prove a PDF is safe; the released copy is a visual reconstruction.',
                      'Visible or handwritten signature-like marks are not cryptographic signatures and are not verified.'))::text
                  FROM scan_jobs s WHERE s.id=? AND (s.owner_id=? OR ?)""",
                (rs, row) -> rs.getString(1), scanId, principal.userId(), principal.admin());
        if (rows.isEmpty()) throw new ApiException(404, "SCAN_NOT_FOUND", "Scan not found");
        return rows.getFirst();
    }

    public String summary(GatewayPrincipal principal) {
        return jdbc.queryForObject("""
                WITH mine AS (SELECT * FROM scan_jobs WHERE owner_id=? OR ?)
                SELECT jsonb_build_object(
                  'scans', (SELECT count(*) FROM mine),
                  'last24h', (SELECT count(*) FROM mine WHERE created_at > now() - interval '24 hours'),
                  'last7d', (SELECT count(*) FROM mine WHERE created_at > now() - interval '7 days'),
                  'byStatus', COALESCE((SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) n FROM mine GROUP BY status) x), '{}'::jsonb),
                  'malicious', (SELECT count(*) FROM mine WHERE detection='MALICIOUS'),
                  'quarantined', (SELECT count(*) FROM mine WHERE status IN ('REVIEW_REQUIRED','BLOCKED','FAILED')),
                  'archived', (SELECT count(*) FROM archive_documents d JOIN mine m ON m.id=d.scan_id),
                  'cdrFailures', (SELECT count(*) FROM cdr_results c JOIN mine m ON m.id=c.scan_id WHERE c.status='FAILED'),
                  'readyLatencySeconds', (SELECT jsonb_build_object(
                      'p50', percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM updated_at-created_at)),
                      'p95', percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM updated_at-created_at)))
                    FROM mine WHERE status='READY'))::text""", String.class, principal.userId(), principal.admin());
    }

    public String health() {
        String broker;
        try {
            broker = Boolean.TRUE.equals(rabbit.execute(channel -> channel.isOpen())) ? "UP" : "DOWN";
        } catch (RuntimeException unavailable) {
            broker = "DOWN";
        }
        boolean archive = Files.isDirectory(properties.archiveRoot().resolve("archive"), LinkOption.NOFOLLOW_LINKS);
        return jdbc.queryForObject("""
                SELECT jsonb_build_object(
                  'database', 'UP', 'broker', ?, 'archiveReadable', ?,
                  'outbox', (SELECT jsonb_build_object('pending', count(*),
                                    'oldestSeconds', COALESCE(extract(epoch FROM now() - min(created_at)), 0))
                               FROM outbox_events WHERE published_at IS NULL),
                  'pipeline', COALESCE((SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) n FROM scan_jobs
                                         WHERE status NOT IN ('READY','REVIEW_REQUIRED','BLOCKED','FAILED','CANCELLED') GROUP BY status) x), '{}'::jsonb),
                  'clamav', (SELECT jsonb_build_object('databaseVersion', e->'databaseVersion', 'databaseDate', e->'databaseDate', 'observedAt', j.finished_at)
                               FROM job_steps j, jsonb_array_elements(j.details->'engines') e
                              WHERE j.step_type='ANALYSIS' AND j.status='SUCCEEDED' AND e->>'engine'='clamav'
                              ORDER BY j.finished_at DESC LIMIT 1),
                  'cdr', (SELECT jsonb_build_object('lastStatus', status, 'lastFailureCode', failure_code, 'lastAt', created_at,
                                                    'dangerzoneVersion', dangerzone_version)
                            FROM cdr_results ORDER BY created_at DESC LIMIT 1),
                  'limitations', jsonb_build_array('CDR provider health is inferred from the latest conversion, not probed.'))::text""",
                String.class, broker, archive);
    }

    /** Remove a held original. Never for released documents; signed originals need explicit confirmation. */
    @Transactional
    public void deleteQuarantined(GatewayPrincipal principal, String scanId, boolean confirmSigned) throws IOException {
        if (!principal.admin()) throw new ApiException(403, "FORBIDDEN", "Only administrators can delete quarantined originals");
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT status, quarantine_object, original_retention FROM scan_jobs WHERE id=? FOR UPDATE", scanId);
        if (rows.isEmpty()) throw new ApiException(404, "SCAN_NOT_FOUND", "Scan not found");
        Map<String, Object> row = rows.getFirst();
        if (!List.of("REVIEW_REQUIRED", "BLOCKED", "FAILED").contains(String.valueOf(row.get("status"))))
            throw new ApiException(409, "NOT_QUARANTINED", "Only held documents can be deleted");
        if ("SIGNED_RESTRICTED".equals(row.get("original_retention")) && !confirmSigned)
            throw new ApiException(409, "SIGNED_ORIGINAL", "This original is retained signed evidence; confirm explicitly");
        jdbc.update("UPDATE scan_jobs SET status='CANCELLED', updated_at=now(), version=version+1 WHERE id=?", scanId);
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id,details) VALUES (?,'QUARANTINE_DELETED','SCAN',?,'SUCCESS',?,jsonb_build_object('signedOriginal',?))",
                principal.userId(), scanId, UUID.randomUUID().toString(), "SIGNED_RESTRICTED".equals(row.get("original_retention")));
        // Last, so a failed delete rolls the transaction back instead of leaving a stale record.
        Files.deleteIfExists(properties.storageRoot().resolve("quarantine").resolve(row.get("quarantine_object") + ".bin"));
    }
}
