package net.batlab.pdfgateway.api;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import net.batlab.pdfgateway.config.GatewayProperties;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Read access to the portable archive plus queued sidecar edits.
 *
 * The API only reads the archive (mounted read-only); edits are compare-and-swap
 * requests applied by the archive worker, the archive's single writer. Every
 * file is located through the index, confined to the archive root, and checked
 * against the indexed SHA-256 before it is served.
 */
@Service
public class ArchiveService {
    private static final Pattern DOCUMENT_ID = Pattern.compile("^doc-[0-9A-HJKMNP-TV-Z]{26}$");
    private static final Pattern TAG = Pattern.compile("^[a-z0-9][a-z0-9._-]{0,63}$");
    private static final Pattern ETAG = Pattern.compile("^(?:W/)?\"rev-([1-9][0-9]{0,8})\"$");
    private static final Pattern SIDECAR_REVISION = Pattern.compile("\"metadataRevision\": ([0-9]+)");
    private static final Set<String> EDITABLE = Set.of("title", "tags");

    public record DocumentView(String documentId, String title, String category, String reviewState, String archivePath,
                               int currentVersion, int metadataRevision, String sha256, long bytes,
                               OffsetDateTime publishedAt, OffsetDateTime updatedAt, String scanId) {}
    public record DocumentPage(List<DocumentView> items, String nextCursor) {}
    public record EditView(String editId, String documentId, String status, int expectedRevision, Integer resultRevision,
                           String errorCode, String statusUrl) {}
    public record Located(DocumentView view, Path directory, String pdfFile) {}
    public record Sidecar(byte[] bytes, int revision) {}

    private static final String COLUMNS = """
            d.document_id,d.title,d.category,d.review_state,d.archive_path,d.current_version,d.metadata_revision,
            d.sha256,d.bytes,d.published_at,d.updated_at,d.pdf_file,d.scan_id""";
    private final JdbcTemplate jdbc;
    private final GatewayProperties properties;

    public ArchiveService(JdbcTemplate jdbc, GatewayProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    private static DocumentView view(java.sql.ResultSet rs) throws java.sql.SQLException {
        return new DocumentView(rs.getString(1), rs.getString(2), rs.getString(3), rs.getString(4), rs.getString(5),
                rs.getInt(6), rs.getInt(7), rs.getString(8), rs.getLong(9),
                rs.getObject(10, OffsetDateTime.class), rs.getObject(11, OffsetDateTime.class), rs.getString(13));
    }

    public DocumentPage list(GatewayPrincipal principal, int limit, String after) {
        int size = Math.max(1, Math.min(limit, 100));
        if (after != null && !DOCUMENT_ID.matcher(after).matches())
            throw new ApiException(400, "INVALID_CURSOR", "Cursor is not a document ID");
        // Documents without a scan row (rebuilt from the archive alone) are visible to administrators only.
        List<DocumentView> rows = jdbc.query("SELECT " + COLUMNS + """
                  FROM archive_documents d LEFT JOIN scan_jobs s ON s.id=d.scan_id
                 WHERE (s.owner_id=? OR ?) AND (?::text IS NULL OR d.document_id > ?)
                 ORDER BY d.document_id LIMIT ?""", (rs, row) -> view(rs),
                principal.userId(), principal.admin(), after, after, size + 1);
        String next = rows.size() > size ? rows.get(size - 1).documentId() : null;
        return new DocumentPage(rows.subList(0, Math.min(size, rows.size())), next);
    }

    public Located locate(GatewayPrincipal principal, String documentId) {
        if (!DOCUMENT_ID.matcher(documentId).matches()) throw notFound();
        List<Located> rows = jdbc.query("SELECT " + COLUMNS + """
                  FROM archive_documents d LEFT JOIN scan_jobs s ON s.id=d.scan_id
                 WHERE d.document_id=? AND (s.owner_id=? OR ?)""",
                (rs, row) -> new Located(view(rs), null, rs.getString(12)),
                documentId, principal.userId(), principal.admin());
        if (rows.isEmpty()) throw notFound();
        Located row = rows.getFirst();
        try {
            Path archive = properties.archiveRoot().resolve("archive").toRealPath();
            Path directory = archive.resolve(row.view().archivePath()).normalize();
            // Confine to the archive tree even if the index or a directory were tampered with.
            if (!directory.startsWith(archive) || !directory.toRealPath().equals(directory))
                throw new ApiException(409, "ARCHIVE_INTEGRITY", "Archive location is outside the archive");
            return new Located(row.view(), directory, row.pdfFile());
        } catch (IOException missing) {
            throw new ApiException(409, "ARCHIVE_INTEGRITY", "Indexed document is missing from the archive");
        }
    }

    public Sidecar sidecar(GatewayPrincipal principal, String documentId) throws IOException {
        Located located = locate(principal, documentId);
        Path file = regularFile(located.directory().resolve("metadata.json"));
        byte[] bytes = Files.readAllBytes(file);
        Matcher revision = SIDECAR_REVISION.matcher(new String(bytes, StandardCharsets.UTF_8));
        if (!revision.find() || !new String(bytes, StandardCharsets.UTF_8).contains("\"documentId\": \"" + documentId + "\""))
            throw new ApiException(409, "ARCHIVE_INTEGRITY", "Sidecar does not describe this document");
        return new Sidecar(bytes, Integer.parseInt(revision.group(1)));
    }

    /** Returns the PDF path only after its bytes match the indexed SHA-256. */
    public Path verifiedPdf(GatewayPrincipal principal, String documentId) throws IOException {
        Located located = locate(principal, documentId);
        Path file = regularFile(located.directory().resolve(located.pdfFile()));
        MessageDigest digest = sha256();
        try (InputStream input = Files.newInputStream(file, LinkOption.NOFOLLOW_LINKS)) {
            byte[] buffer = new byte[1 << 16];
            for (int read; (read = input.read(buffer)) > 0; ) digest.update(buffer, 0, read);
        }
        if (!HexFormat.of().formatHex(digest.digest()).equals(located.view().sha256()))
            throw new ApiException(409, "ARCHIVE_INTEGRITY", "Archived bytes do not match the index");
        return file;
    }

    @Transactional
    public EditView requestEdit(GatewayPrincipal principal, String documentId, String ifMatch, Map<String, Object> changes) throws IOException {
        if (ifMatch == null || ifMatch.isBlank())
            throw new ApiException(428, "PRECONDITION_REQUIRED", "If-Match with the current ETag is required");
        Matcher etag = ETAG.matcher(ifMatch.strip());
        if (!etag.matches()) throw new ApiException(400, "INVALID_ETAG", "If-Match must be an archive revision ETag");
        int expected = Integer.parseInt(etag.group(1));
        Sidecar current = sidecar(principal, documentId);
        if (current.revision() != expected)
            throw new ApiException(409, "REVISION_CONFLICT", "The metadata changed; reload and retry");
        if (changes == null || changes.isEmpty() || !EDITABLE.containsAll(changes.keySet()))
            throw new ApiException(400, "FIELD_NOT_EDITABLE", "Only title and tags can be edited");
        String title = null;
        String[] tags = null;
        if (changes.containsKey("title")) {
            if (!(changes.get("title") instanceof String value) || value.isBlank() || value.length() > 200
                    || value.codePoints().anyMatch(Character::isISOControl))
                throw new ApiException(400, "INVALID_TITLE", "Title must be 1 to 200 printable characters");
            title = value.strip();
        }
        if (changes.containsKey("tags")) {
            if (!(changes.get("tags") instanceof List<?> list) || list.size() > 64
                    || !list.stream().allMatch(tag -> tag instanceof String text && TAG.matcher(text).matches())
                    || list.stream().distinct().count() != list.size())
                throw new ApiException(400, "INVALID_TAGS", "Tags must be unique lowercase identifiers");
            tags = list.stream().map(String.class::cast).toArray(String[]::new);
        }
        UUID editId = UUID.randomUUID();
        jdbc.update("""
                INSERT INTO metadata_edits(id,document_id,actor_id,expected_revision,changes)
                VALUES (?,?,?,?,jsonb_strip_nulls(jsonb_build_object('title',?::text,'tags',to_jsonb(?::text[]))))""",
                editId, documentId, principal.userId(), expected, title, tags);
        jdbc.update("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (?,'DOCUMENT',?,'METADATA_EDIT_QUEUED',jsonb_build_object('editId',?::text))",
                UUID.randomUUID(), documentId, editId.toString());
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id,details) VALUES (?,'METADATA_EDIT_REQUESTED','DOCUMENT',?,'SUCCESS',?,jsonb_build_object('editId',?::text,'expectedRevision',?))",
                principal.userId(), documentId, editId.toString(), editId.toString(), expected);
        return edit(principal, documentId, editId.toString());
    }

    public EditView edit(GatewayPrincipal principal, String documentId, String editId) {
        locate(principal, documentId);
        UUID id;
        try { id = UUID.fromString(editId); } catch (IllegalArgumentException invalid) { throw notFound(); }
        List<EditView> rows = jdbc.query("SELECT id,document_id,status,expected_revision,result_revision,error_code FROM metadata_edits WHERE id=? AND document_id=?",
                (rs, row) -> new EditView(rs.getString(1), rs.getString(2), rs.getString(3), rs.getInt(4),
                        (Integer) rs.getObject(5), rs.getString(6),
                        "/api/v1/archive/" + rs.getString(2) + "/edits/" + rs.getString(1)),
                id, documentId);
        if (rows.isEmpty()) throw notFound();
        return rows.getFirst();
    }

    private static Path regularFile(Path path) {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS))
            throw new ApiException(409, "ARCHIVE_INTEGRITY", "Archived file is missing or not a regular file");
        return path;
    }

    private static ApiException notFound() {
        return new ApiException(404, "DOCUMENT_NOT_FOUND", "Document not found");
    }

    private static MessageDigest sha256() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }
}
