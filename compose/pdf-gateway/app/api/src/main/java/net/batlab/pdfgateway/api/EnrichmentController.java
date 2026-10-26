package net.batlab.pdfgateway.api;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;
import net.batlab.pdfgateway.config.GatewayProperties;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.core.io.FileSystemResource;
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
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * Pull-based enrichment for an agent on another machine (the AI laptop).
 *
 * The agent identity is an ENRICHMENT-role token that can only use these
 * endpoints. It can fetch only the released archive copy of a job it holds a
 * lease on, never quarantined originals. Results are stored as submitted data;
 * the archive worker validates them before anything is applied.
 */
@RestController
public class EnrichmentController {
    private static final Pattern AGENT = Pattern.compile("^[a-z0-9][a-z0-9._-]{0,63}$");
    private static final Set<String> KINDS = Set.of("OCR", "SUGGEST");
    private static final int MAX_REPORT = 16 * 1024;
    private static final int MAX_RESULT = 4 * 1024 * 1024;
    private static final int MAX_CONTEXT_CHARS = 12_000;
    private final JdbcTemplate jdbc;
    private final GatewayProperties properties;
    private final ArchiveService archive;

    public EnrichmentController(JdbcTemplate jdbc, GatewayProperties properties, ArchiveService archive) {
        this.jdbc = jdbc;
        this.properties = properties;
        this.archive = archive;
    }

    // ------------------------------------------------------------- agent side

    @PostMapping(path = "/api/v1/enrichment/agents/{agentId}/heartbeat", consumes = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasRole('ENRICHMENT')")
    @Transactional
    public ResponseEntity<Void> heartbeat(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String agentId,
                                          @RequestBody String report) {
        requireAgentName(agentId);
        if (report.length() > MAX_REPORT) throw new ApiException(413, "REPORT_TOO_LARGE", "Heartbeat report is too large");
        int updated = jdbc.update("""
                INSERT INTO ai_agents(agent_id,user_id,version,last_seen_at,report) VALUES (?,?,left(?::jsonb->>'version',80),now(),?::jsonb)
                ON CONFLICT (agent_id) DO UPDATE SET version=EXCLUDED.version, last_seen_at=now(), report=EXCLUDED.report
                 WHERE ai_agents.user_id=EXCLUDED.user_id""", agentId, principal.userId(), report, report);
        if (updated == 0) throw new ApiException(403, "AGENT_OWNED_ELSEWHERE", "This agent name belongs to another account");
        return ResponseEntity.noContent().build();
    }

    @PostMapping(path = "/api/v1/enrichment/agents/{agentId}/lease", produces = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasRole('ENRICHMENT')")
    @Transactional
    public ResponseEntity<String> lease(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String agentId,
                                        @RequestParam(defaultValue = "OCR,SUGGEST") String kinds) {
        ownAgent(principal, agentId);
        List<String> wanted = List.of(kinds.split(","));
        if (wanted.isEmpty() || !KINDS.containsAll(wanted)) throw new ApiException(400, "INVALID_KIND", "kinds must be OCR and/or SUGGEST");
        Boolean open = jdbc.queryForObject("SELECT breaker_open_until > now() FROM ai_agents WHERE agent_id=?", Boolean.class, agentId);
        if (Boolean.TRUE.equals(open)) throw new ApiException(429, "BREAKER_OPEN", "Too many invalid results; leases paused");
        // Housekeeping: expired leases return to the queue; old jobs expire.
        jdbc.update("UPDATE enrichment_jobs SET status='WAITING_FOR_AI', lease_agent=NULL, lease_until=NULL WHERE status='LEASED' AND lease_until < now()");
        jdbc.update("UPDATE enrichment_jobs SET status='EXPIRED', completed_at=now() WHERE status='WAITING_FOR_AI' AND expires_at < now()");
        jdbc.update("UPDATE enrichment_jobs SET status='FAILED', error_code='MAX_ATTEMPTS', completed_at=now() WHERE status='WAITING_FOR_AI' AND attempts >= 5");
        List<String> rows = jdbc.query("""
                WITH next AS (SELECT id FROM enrichment_jobs WHERE status='WAITING_FOR_AI' AND kind = ANY(?::text[])
                               ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                UPDATE enrichment_jobs j SET status='LEASED', lease_agent=?, lease_until=now()+interval '10 minutes', attempts=attempts+1
                  FROM next WHERE j.id=next.id
                RETURNING jsonb_build_object('jobId', j.id, 'kind', j.kind, 'documentId', j.document_id, 'sha256', j.artifact_sha256,
                          'leaseUntil', j.lease_until,
                          'documentUrl', CASE WHEN j.document_id IS NOT NULL OR j.kind='OCR' THEN '/api/v1/enrichment/jobs/' || j.id || '/document' END,
                          'contextUrl', '/api/v1/enrichment/jobs/' || j.id || '/context',
                          'resultUrl', '/api/v1/enrichment/jobs/' || j.id || '/result')::text""",
                (rs, row) -> rs.getString(1), wanted.toArray(String[]::new), agentId);
        return rows.isEmpty() ? ResponseEntity.noContent().build() : ResponseEntity.ok(rows.getFirst());
    }

    @GetMapping("/api/v1/enrichment/jobs/{jobId}/document")
    @PreAuthorize("hasRole('ENRICHMENT')")
    public ResponseEntity<FileSystemResource> document(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String jobId,
                                                       @RequestHeader("X-Agent-Id") String agentId) throws IOException {
        Path pdf = leasedDocument(principal, jobId, agentId);
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF).header(HttpHeaders.CACHE_CONTROL, "no-store")
                .header("X-Content-Type-Options", "nosniff").body(new FileSystemResource(pdf));
    }

    @GetMapping(path = "/api/v1/enrichment/jobs/{jobId}/context", produces = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasRole('ENRICHMENT')")
    public ResponseEntity<String> context(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String jobId,
                                          @RequestHeader("X-Agent-Id") String agentId) throws IOException {
        ownAgent(principal, agentId);
        // A job made before publication carries the text it may read (written by the archive worker).
        List<String> carried = jdbc.query("""
                SELECT context::text FROM enrichment_jobs
                 WHERE id=? AND status='LEASED' AND lease_agent=? AND lease_until > now() AND context IS NOT NULL""",
                (rs, row) -> rs.getString(1), parseJob(jobId), agentId);
        if (!carried.isEmpty()) return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(carried.getFirst());
        Path pdf = leasedDocument(principal, jobId, agentId);
        Path text = pdf.resolveSibling("." + pdf.getFileName() + ".txt");
        String content = Files.isRegularFile(text, LinkOption.NOFOLLOW_LINKS) ? Files.readString(text, StandardCharsets.UTF_8) : "";
        if (content.length() > MAX_CONTEXT_CHARS) content = content.substring(0, MAX_CONTEXT_CHARS);
        Path taxonomy = properties.archiveRoot().resolve("manifests").resolve("taxonomy.json");
        String vocabulary = Files.isRegularFile(taxonomy, LinkOption.NOFOLLOW_LINKS) ? Files.readString(taxonomy, StandardCharsets.UTF_8) : "{}";
        // Only the text of the released copy and the controlled vocabulary; nothing else about the user.
        String body = jdbc.queryForObject("""
                SELECT jsonb_build_object('text', ?::text, 'truncated', ?,
                       'tags', COALESCE((SELECT jsonb_agg(t->>'id') FROM jsonb_array_elements(?::jsonb->'tags') t WHERE NOT (t->>'blocked')::boolean
                                          AND NOT (t->>'private')::boolean), '[]'::jsonb),
                       'categories', COALESCE(?::jsonb->'categories', '[]'::jsonb))::text""",
                String.class, content, content.length() >= MAX_CONTEXT_CHARS, vocabulary, vocabulary);
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(body);
    }

    @PostMapping(path = "/api/v1/enrichment/jobs/{jobId}/result", consumes = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasRole('ENRICHMENT')")
    @Transactional
    public ResponseEntity<Void> result(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String jobId,
                                       @RequestHeader("X-Agent-Id") String agentId, @RequestBody String result) {
        ownAgent(principal, agentId);
        if (result.length() > MAX_RESULT) throw new ApiException(413, "RESULT_TOO_LARGE", "Result is too large");
        UUID id = parseJob(jobId);
        // Authorise first: only a current lease holder may submit, whatever the body is.
        Boolean held = jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM enrichment_jobs WHERE id=? AND status='LEASED' AND lease_agent=? AND lease_until > now())",
                Boolean.class, id, agentId);
        if (!Boolean.TRUE.equals(held)) throw new ApiException(409, "LEASE_NOT_HELD", "This agent does not hold a valid lease on the job");
        int updated;
        try {
            updated = jdbc.update("""
                    UPDATE enrichment_jobs SET status='SUBMITTED', result=?::jsonb, lease_until=NULL
                     WHERE id=? AND status='LEASED' AND lease_agent=? AND lease_until > now() AND jsonb_typeof(?::jsonb)='object'""",
                    result, id, agentId, result);
        } catch (org.springframework.dao.DataAccessException invalidJson) {
            throw new ApiException(400, "INVALID_RESULT", "Result must be a JSON object");
        }
        if (updated == 0) throw new ApiException(409, "LEASE_NOT_HELD", "This agent does not hold a valid lease on the job");
        jdbc.update("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (?,'ENRICHMENT',?,'ENRICHMENT_RESULT_QUEUED',jsonb_build_object('jobId',?::text))",
                UUID.randomUUID(), id.toString(), id.toString());
        return ResponseEntity.accepted().build();
    }

    // ------------------------------------------------------------- user side

    /** Availability as seen by the gateway; never claims the laptop's power state. */
    @GetMapping(path = "/api/v1/system/ai", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> availability() {
        boolean enabled = Boolean.parseBoolean(System.getenv().getOrDefault("PDF_GATEWAY_ENRICHMENT_ENABLED", "false"));
        String body = jdbc.queryForObject("""
                WITH a AS (SELECT * FROM ai_agents ORDER BY last_seen_at DESC LIMIT 1),
                     q AS (SELECT count(*) FILTER (WHERE status='WAITING_FOR_AI') waiting,
                                  count(*) FILTER (WHERE status IN ('LEASED','SUBMITTED')) running,
                                  count(*) FILTER (WHERE status='FAILED') failed FROM enrichment_jobs)
                SELECT jsonb_build_object(
                  'enabled', ?,
                  'state', CASE WHEN NOT ? THEN 'DISABLED'
                                WHEN NOT EXISTS (SELECT 1 FROM a) THEN 'UNKNOWN'
                                WHEN (SELECT last_seen_at FROM a) < now() - interval '3 minutes' THEN 'OFFLINE'
                                WHEN (SELECT breaker_open_until > now() FROM a) THEN 'DEGRADED'
                                WHEN (SELECT report->'llm'->>'service' FROM a) IS DISTINCT FROM 'UP' THEN 'SERVICE_DOWN'
                                WHEN (SELECT (report->'llm'->>'modelInstalled')::boolean FROM a) IS NOT TRUE THEN 'MODEL_MISSING'
                                WHEN (SELECT (report->>'busy')::boolean FROM a) THEN 'BUSY'
                                WHEN (SELECT last_success_at > now() - interval '1 hour' FROM a) THEN 'READY'
                                ELSE 'AVAILABLE' END,
                  'agent', (SELECT agent_id FROM a), 'model', (SELECT report->'llm'->>'configuredModel' FROM a),
                  'ocrReady', (SELECT (report->'ocr'->>'ready')::boolean FROM a),
                  'lastSeenAt', (SELECT last_seen_at FROM a), 'lastSuccessAt', (SELECT last_success_at FROM a),
                  'reason', (SELECT last_error FROM a),
                  'queue', (SELECT jsonb_build_object('waiting', waiting, 'running', running, 'failed', failed) FROM q),
                  'note', 'Availability is inferred from the agent''s own heartbeats; the gateway never contacts or wakes the laptop.')::text""",
                String.class, enabled, enabled);
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(body);
    }

    @GetMapping(path = "/api/v1/archive/{documentId}/suggestions", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> suggestions(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId) {
        archive.locate(principal, documentId);
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(jdbc.queryForObject("""
                SELECT COALESCE(jsonb_agg(jsonb_build_object('id', id, 'model', model, 'promptVersion', prompt_version,
                         'fields', fields, 'status', status, 'createdAt', created_at) ORDER BY created_at DESC), '[]'::jsonb)::text
                  FROM ai_suggestions WHERE document_id=?""", String.class, documentId));
    }

    /** Ask the AI laptop to file a published document again (its answer is applied, a new PDF version follows). */
    @PostMapping(path = "/api/v1/archive/{documentId}/refile", produces = MediaType.APPLICATION_JSON_VALUE)
    @PreAuthorize("hasAnyRole('ADMIN','USER')")
    @Transactional
    public ResponseEntity<String> refile(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId) throws IOException {
        if (!Boolean.parseBoolean(System.getenv().getOrDefault("PDF_GATEWAY_ENRICHMENT_ENABLED", "false")))
            throw new ApiException(409, "AI_DISABLED", "OCR and AI are not set up");
        ArchiveService.Located located = archive.locate(principal, documentId);
        Path text = located.directory().resolve("." + located.pdfFile() + ".txt");
        String content = Files.isRegularFile(text, LinkOption.NOFOLLOW_LINKS) ? Files.readString(text, StandardCharsets.UTF_8) : "";
        if (content.isBlank()) throw new ApiException(409, "NO_TEXT", "This document has no text for the AI to read");
        if (content.length() > MAX_CONTEXT_CHARS) content = content.substring(0, MAX_CONTEXT_CHARS);
        Path taxonomy = properties.archiveRoot().resolve("manifests").resolve("taxonomy.json");
        String vocabulary = Files.isRegularFile(taxonomy, LinkOption.NOFOLLOW_LINKS) ? Files.readString(taxonomy, StandardCharsets.UTF_8) : "{}";
        String sha = located.view().sha256();
        Boolean moving = jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM enrichment_jobs WHERE document_id=? AND kind='SUGGEST' AND status IN ('WAITING_FOR_AI','LEASED','SUBMITTED'))",
                Boolean.class, documentId);
        if (Boolean.TRUE.equals(moving)) throw new ApiException(409, "ALREADY_ASKED", "The AI is already on it");
        String context = jdbc.queryForObject("""
                SELECT jsonb_build_object('text', ?::text, 'truncated', ?, 'force', true,
                       'types', ?::jsonb, 'sections', ?::jsonb,
                       'tags', COALESCE((SELECT jsonb_agg(t->>'id') FROM jsonb_array_elements(?::jsonb->'tags') t
                                          WHERE NOT (t->>'blocked')::boolean AND NOT (t->>'private')::boolean), '[]'::jsonb),
                       'correspondents', COALESCE((SELECT jsonb_agg(DISTINCT sidecar->'issuer'->>'value') FROM archive_documents
                                                    WHERE sidecar->'issuer'->>'value' IS NOT NULL), '[]'::jsonb))::text""",
                String.class, content, content.length() >= MAX_CONTEXT_CHARS, ArchiveService.json(ArchiveService.TYPES),
                ArchiveService.json(ArchiveService.SECTIONS.subList(0, ArchiveService.SECTIONS.size() - 1)), vocabulary);
        int reused = jdbc.update("""
                UPDATE enrichment_jobs SET status='WAITING_FOR_AI', context=?::jsonb, artifact_sha256=?, result=NULL, applied=NULL,
                       lease_agent=NULL, lease_until=NULL, attempts=0, error_code=NULL, completed_at=NULL, deadline_at=NULL,
                       expires_at=now()+interval '30 days'
                 WHERE id=(SELECT id FROM enrichment_jobs WHERE document_id=? AND kind='SUGGEST' ORDER BY created_at DESC LIMIT 1)""",
                context, sha, documentId);
        if (reused == 0)
            jdbc.update("INSERT INTO enrichment_jobs(id,document_id,artifact_sha256,kind,context) VALUES (?,?,?,'SUGGEST',?::jsonb)",
                    UUID.randomUUID(), documentId, sha, context);
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id) VALUES (?,'AI_REFILE_REQUESTED','DOCUMENT',?,'SUCCESS',?)",
                principal.userId(), documentId, UUID.randomUUID().toString());
        return ResponseEntity.accepted().body("{\"status\":\"WAITING_FOR_AI\"}");
    }

    /** A scan's AI job before (and after) its document is published, for its page. */
    @GetMapping(path = "/api/v1/scans/{scanId}/enrichment", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> scanEnrichment(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String scanId) {
        Boolean visible = jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM scan_jobs WHERE id=? AND (owner_id=? OR ?))",
                Boolean.class, scanId, principal.userId(), principal.admin());
        if (!Boolean.TRUE.equals(visible)) throw new ApiException(404, "SCAN_NOT_FOUND", "Scan not found");
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(jdbc.queryForObject("""
                SELECT COALESCE(jsonb_agg(jsonb_build_object('kind', j.kind, 'status', j.status, 'errorCode', j.error_code,
                         'createdAt', j.created_at, 'completedAt', j.completed_at, 'deadlineAt', j.deadline_at,
                         'applied', j.applied) ORDER BY j.created_at), '[]'::jsonb)::text
                  FROM enrichment_jobs j LEFT JOIN archive_documents d ON d.document_id=j.document_id
                 WHERE j.scan_id=? OR d.scan_id=?""", String.class, scanId, scanId));
    }

    /** The document's OCR/AI jobs, so its page can say what the AI laptop is doing with it. */
    @GetMapping(path = "/api/v1/archive/{documentId}/enrichment", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<String> enrichment(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId) {
        archive.locate(principal, documentId);
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "private, no-store").body(jdbc.queryForObject("""
                SELECT COALESCE(jsonb_agg(jsonb_build_object('kind', kind, 'status', status, 'errorCode', error_code,
                         'createdAt', created_at, 'completedAt', completed_at) ORDER BY created_at), '[]'::jsonb)::text
                  FROM enrichment_jobs WHERE document_id=?""", String.class, documentId));
    }

    /** Apply a proposal's title and tags as an ordinary, user-authorised CAS sidecar edit. */
    @PostMapping(path = "/api/v1/archive/{documentId}/suggestions/{suggestionId}/apply")
    @PreAuthorize("hasAnyRole('ADMIN','USER')")
    @Transactional
    public ResponseEntity<ArchiveService.EditView> apply(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId,
                                                         @PathVariable String suggestionId,
                                                         @RequestHeader(value = HttpHeaders.IF_MATCH, required = false) String ifMatch) throws IOException {
        UUID id = parseJob(suggestionId);
        List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT fields->'title'->>'value' AS title, COALESCE(ARRAY(SELECT jsonb_array_elements_text(fields->'tags')), '{}') AS tags
                  FROM ai_suggestions WHERE id=? AND document_id=? AND status='PROPOSED'""", id, documentId);
        if (rows.isEmpty()) throw new ApiException(404, "SUGGESTION_NOT_FOUND", "No open suggestion with that ID");
        Map<String, Object> changes = new java.util.LinkedHashMap<>();
        if (rows.getFirst().get("title") != null) changes.put("title", rows.getFirst().get("title"));
        Object tags = rows.getFirst().get("tags");
        if (tags instanceof java.sql.Array array) {
            try { changes.put("tags", List.of((String[]) array.getArray())); }
            catch (java.sql.SQLException unreadable) { throw new IllegalStateException(unreadable); }
        }
        ArchiveService.EditView edit = archive.requestEdit(principal, documentId, ifMatch, changes);
        jdbc.update("UPDATE ai_suggestions SET status='APPLIED', decided_at=now(), decided_by=? WHERE id=?", principal.userId(), id);
        return ResponseEntity.accepted().header(HttpHeaders.LOCATION, edit.statusUrl()).body(edit);
    }

    @PostMapping("/api/v1/archive/{documentId}/suggestions/{suggestionId}/dismiss")
    @PreAuthorize("hasAnyRole('ADMIN','USER')")
    public ResponseEntity<Void> dismiss(@AuthenticationPrincipal GatewayPrincipal principal, @PathVariable String documentId,
                                        @PathVariable String suggestionId) {
        archive.locate(principal, documentId);
        int updated = jdbc.update("UPDATE ai_suggestions SET status='DISMISSED', decided_at=now(), decided_by=? WHERE id=? AND document_id=? AND status='PROPOSED'",
                principal.userId(), parseJob(suggestionId), documentId);
        if (updated == 0) throw new ApiException(404, "SUGGESTION_NOT_FOUND", "No open suggestion with that ID");
        return ResponseEntity.noContent().build();
    }

    // ------------------------------------------------------------- helpers

    private static void requireAgentName(String agentId) {
        if (agentId == null || !AGENT.matcher(agentId).matches()) throw new ApiException(400, "INVALID_AGENT", "Invalid agent name");
    }

    private void ownAgent(GatewayPrincipal principal, String agentId) {
        requireAgentName(agentId);
        Boolean mine = jdbc.queryForObject("SELECT EXISTS (SELECT 1 FROM ai_agents WHERE agent_id=? AND user_id=?)", Boolean.class, agentId, principal.userId());
        if (!Boolean.TRUE.equals(mine)) throw new ApiException(403, "AGENT_NOT_REGISTERED", "Send a heartbeat first");
    }

    private static UUID parseJob(String jobId) {
        try { return UUID.fromString(jobId); }
        catch (IllegalArgumentException invalid) { throw new ApiException(404, "JOB_NOT_FOUND", "Not found"); }
    }

    /** The released archive PDF of a job leased by this agent, hash-checked; never a quarantine path. */
    private Path leasedDocument(GatewayPrincipal principal, String jobId, String agentId) throws IOException {
        ownAgent(principal, agentId);
        // An OCR job before publication reads the validated clean copy (final validation PASS, exact bytes).
        List<String[]> staged = jdbc.query("""
                SELECT a.storage_object, a.sha256 FROM enrichment_jobs j
                  JOIN artifacts a ON a.scan_id=j.scan_id AND a.kind='CDR_OUTPUT' AND a.state='FINAL_VALIDATED' AND a.sha256=j.artifact_sha256
                  JOIN final_validations f ON f.artifact_id=a.id AND f.verdict='PASS' AND f.output_sha256=a.sha256
                 WHERE j.id=? AND j.document_id IS NULL AND j.kind='OCR' AND j.status='LEASED' AND j.lease_agent=? AND j.lease_until > now()""",
                (rs, row) -> new String[]{rs.getString(1), rs.getString(2)}, parseJob(jobId), agentId);
        if (!staged.isEmpty()) {
            Path root = properties.storageRoot().resolve("cdr-output").toRealPath();
            Path file = properties.storageRoot().resolve(staged.getFirst()[0]).normalize();
            if (!file.startsWith(root) || !Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) || !file.toRealPath().equals(file))
                throw new ApiException(409, "ARCHIVE_INTEGRITY", "Validated copy is not available");
            if (!sha256Hex(file).equals(staged.getFirst()[1])) throw new ApiException(409, "ARCHIVE_INTEGRITY", "Validated bytes changed");
            return file;
        }
        List<String[]> rows = jdbc.query("""
                SELECT d.archive_path, d.pdf_file, d.sha256 FROM enrichment_jobs j JOIN archive_documents d USING (document_id)
                 WHERE j.id=? AND j.status='LEASED' AND j.lease_agent=? AND j.lease_until > now() AND j.artifact_sha256=d.sha256""",
                (rs, row) -> new String[]{rs.getString(1), rs.getString(2), rs.getString(3)}, parseJob(jobId), agentId);
        if (rows.isEmpty()) throw new ApiException(404, "JOB_NOT_FOUND", "Not found");
        Path root = properties.archiveRoot().resolve("archive").toRealPath();
        Path file = root.resolve(rows.getFirst()[0]).resolve(rows.getFirst()[1]).normalize();
        if (!file.startsWith(root) || !Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS) || !file.toRealPath().equals(file))
            throw new ApiException(409, "ARCHIVE_INTEGRITY", "Archived file is not available");
        if (!sha256Hex(file).equals(rows.getFirst()[2]))
            throw new ApiException(409, "ARCHIVE_INTEGRITY", "Archived bytes do not match the index");
        return file;
    }

    private static String sha256Hex(Path file) throws IOException {
        MessageDigest digest;
        try { digest = MessageDigest.getInstance("SHA-256"); } catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
        try (InputStream input = Files.newInputStream(file, LinkOption.NOFOLLOW_LINKS)) {
            byte[] buffer = new byte[1 << 16];
            for (int read; (read = input.read(buffer)) > 0; ) digest.update(buffer, 0, read);
        }
        return HexFormat.of().formatHex(digest.digest());
    }
}
