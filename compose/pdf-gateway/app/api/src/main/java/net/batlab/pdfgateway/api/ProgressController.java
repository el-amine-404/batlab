package net.batlab.pdfgateway.api;

import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/**
 * What an estimate needs for one scan: how long each step usually takes here (medians of the
 * last 50 successful runs, the rebuild per page), the file's page count, and how many files are
 * ahead of it. The page turns these into a progress bar and an honest "about N min".
 */
@RestController
public class ProgressController {
    private final JdbcTemplate jdbc;

    public ProgressController(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @GetMapping(path = "/api/v1/scans/{scanId}/progress", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> progress(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId) {
        Boolean visible = jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM scan_jobs WHERE id=? AND (owner_id=? OR ?))",
                Boolean.class, scanId, principal.userId(), principal.admin());
        if (!Boolean.TRUE.equals(visible)) throw new ApiException(404, "SCAN_NOT_FOUND", "Scan not found");
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(jdbc.queryForObject("""
                WITH recent AS (
                    SELECT j.step_type, extract(epoch FROM j.finished_at - j.started_at) AS seconds,
                           greatest(1, COALESCE((SELECT (a.details->'textExtraction'->>'pages')::int FROM job_steps a
                                                  WHERE a.scan_id=j.scan_id AND a.step_type='ANALYSIS' AND a.status='SUCCEEDED'
                                                  ORDER BY a.attempt DESC LIMIT 1), 1)) AS pages,
                           row_number() OVER (PARTITION BY j.step_type ORDER BY j.finished_at DESC) AS n
                      FROM job_steps j
                     WHERE j.status='SUCCEEDED' AND j.started_at IS NOT NULL AND j.finished_at >= j.started_at),
                typical AS (
                    SELECT jsonb_object_agg(step_type, round(median::numeric, 1)) AS steps,
                           (SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds / pages)::numeric, 1)
                              FROM recent WHERE step_type='CDR' AND n <= 50) AS cdr_per_page
                      FROM (SELECT step_type, percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) AS median
                              FROM recent WHERE n <= 50 GROUP BY step_type) m)
                SELECT jsonb_build_object(
                    'now', now(),
                    'pages', (SELECT (details->'textExtraction'->>'pages')::int FROM job_steps
                               WHERE scan_id=s.id AND step_type='ANALYSIS' AND status='SUCCEEDED' ORDER BY attempt DESC LIMIT 1),
                    'ahead', (SELECT count(*) FROM scan_jobs o WHERE o.created_at < s.created_at
                               AND o.status NOT IN ('READY','REVIEW_REQUIRED','BLOCKED','FAILED','CANCELLED')),
                    'typical', COALESCE((SELECT steps FROM typical), '{}'::jsonb) || jsonb_build_object(
                        'CDR_PER_PAGE', (SELECT cdr_per_page FROM typical),
                        'AI', (SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM completed_at - created_at))::numeric, 1)
                                 FROM (SELECT completed_at, created_at FROM enrichment_jobs
                                        WHERE kind='SUGGEST' AND status='COMPLETED' AND scan_id IS NOT NULL AND completed_at IS NOT NULL
                                        ORDER BY completed_at DESC LIMIT 50) ai),
                        'PAPERLESS', (SELECT round(percentile_cont(0.5) WITHIN GROUP (ORDER BY least(600, extract(epoch FROM updated_at - created_at)))::numeric, 1)
                                        FROM (SELECT updated_at, created_at FROM paperless_imports WHERE status='IMPORTED'
                                               ORDER BY created_at DESC LIMIT 50) p))
                )::text FROM scan_jobs s WHERE s.id=?""", String.class, scanId));
    }
}
