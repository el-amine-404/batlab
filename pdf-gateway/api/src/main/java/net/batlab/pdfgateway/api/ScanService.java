package net.batlab.pdfgateway.api;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.FileStore;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import net.batlab.pdfgateway.config.GatewayProperties;
import net.batlab.pdfgateway.security.GatewayPrincipal;
import net.batlab.pdfgateway.security.TokenHasher;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.multipart.MultipartFile;

@Service
public class ScanService {
    private final JdbcTemplate jdbc;
    private final GatewayProperties properties;
    private final TransactionTemplate transactions;

    public ScanService(JdbcTemplate jdbc, GatewayProperties properties, PlatformTransactionManager transactionManager) {
        this.jdbc = jdbc; this.properties = properties; this.transactions = new TransactionTemplate(transactionManager);
    }

    public ScanAccepted create(GatewayPrincipal owner, String idempotencyKey, MultipartFile upload,
                               String intent, String cdrMode, String traceId) throws IOException {
        if (idempotencyKey == null || idempotencyKey.length() < 16 || idempotencyKey.length() > 128)
            throw new ApiException(400, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must contain 16 to 128 characters");
        if (!List.of("SCAN_ONLY", "ARCHIVE", "ARCHIVE_AND_IMPORT").contains(intent))
            throw new ApiException(400, "INVALID_INTENT", "Unknown scan intent");
        if (!List.of("RASTER", "RASTER_WITH_OCR").contains(cdrMode))
            throw new ApiException(400, "INVALID_CDR_MODE", "Unknown CDR mode");
        if (upload.isEmpty()) throw new ApiException(400, "EMPTY_UPLOAD", "The PDF is empty");
        if (upload.getSize() > properties.maxUploadBytes()) throw new ApiException(413, "UPLOAD_TOO_LARGE", "Upload exceeds configured byte limit");

        Path quarantine = properties.storageRoot().resolve("quarantine");
        Files.createDirectories(quarantine);
        assertHeadroom(quarantine, upload.getSize());
        String object = Ulid.next("quar-");
        Path temporary = quarantine.resolve("." + object + ".partial");
        Path target = quarantine.resolve(object + ".bin");
        MessageDigest digest = sha256();
        long bytes;
        try (InputStream input = new DigestInputStream(upload.getInputStream(), digest);
             OutputStream output = Files.newOutputStream(temporary, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE)) {
            bytes = input.transferTo(output);
        }
        try {
            if (bytes == 0 || bytes > properties.maxUploadBytes())
                throw new ApiException(bytes == 0 ? 400 : 413, bytes == 0 ? "EMPTY_UPLOAD" : "UPLOAD_TOO_LARGE", "Upload rejected");
            byte[] magic = new byte[5];
            try (InputStream check = Files.newInputStream(temporary)) {
                if (check.read(magic) != 5 || !java.util.Arrays.equals(magic, "%PDF-".getBytes(java.nio.charset.StandardCharsets.US_ASCII)))
                    throw new ApiException(400, "NOT_A_PDF", "File signature is not PDF");
            }
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE);
            String hash = HexFormat.of().formatHex(digest.digest());
            String display = safeDisplayName(upload.getOriginalFilename());
            try {
                ScanPersisted persisted = transactions.execute(status ->
                        persist(owner, idempotencyKey, intent, cdrMode, traceId, object, display, bytes, hash));
                if (persisted == null) throw new IllegalStateException("scan transaction returned no result");
                if (!persisted.created()) Files.deleteIfExists(target);
                return persisted.response();
            } catch (RuntimeException error) {
                Files.deleteIfExists(target);
                throw error;
            }
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    record ScanPersisted(ScanAccepted response, boolean created) {}

    ScanPersisted persist(GatewayPrincipal owner, String key, String intent, String cdrMode, String traceId,
                          String object, String display, long bytes, String sourceHash) {
        String keyHash = TokenHasher.sha256(key);
        String requestHash = TokenHasher.sha256(intent + "\n" + cdrMode + "\n" + sourceHash);
        // pg_advisory_xact_lock returns PostgreSQL void, so execute it without
        // asking the JDBC driver to coerce its empty value into a Java number.
        jdbc.query("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                resultSet -> null, owner.userId() + ":CREATE_SCAN:" + keyHash);
        List<String[]> existing = jdbc.query("SELECT resource_id,request_hash FROM idempotency_keys WHERE user_id=? AND operation='CREATE_SCAN' AND key_hash=?",
                (rs, row) -> new String[]{rs.getString(1), rs.getString(2)}, owner.userId(), keyHash);
        if (!existing.isEmpty()) {
            if (!requestHash.equals(existing.getFirst()[1]))
                throw new ApiException(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key was already used for different content or options");
            return new ScanPersisted(accepted(existing.getFirst()[0]), false);
        }
        String scanId = Ulid.next("scan-");
        jdbc.update("""
                INSERT INTO scan_jobs(id, owner_id, status, intent, cdr_mode, display_name, source_bytes,
                                      source_sha256, quarantine_object, import_status)
                VALUES (?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?)
                """, scanId, owner.userId(), intent, cdrMode, display, bytes, sourceHash, object,
                "ARCHIVE_AND_IMPORT".equals(intent) ? "PENDING" : "NOT_REQUESTED");
        jdbc.update("INSERT INTO idempotency_keys(user_id,operation,key_hash,request_hash,resource_id) VALUES (?,'CREATE_SCAN',?,?,?)",
                owner.userId(), keyHash, requestHash, scanId);
        UUID eventId = UUID.randomUUID();
        jdbc.update("INSERT INTO outbox_events(id,aggregate_type,aggregate_id,event_type,payload) VALUES (?,'SCAN',?,'SCAN_QUEUED',jsonb_build_object('scanId',?,'artifactId',?))",
                eventId, scanId, scanId, object);
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id) VALUES (?,'SCAN_CREATED','SCAN',?,'SUCCESS',?)",
                owner.userId(), scanId, traceId);
        return new ScanPersisted(accepted(scanId), true);
    }

    public ScanView get(GatewayPrincipal principal, String scanId) {
        List<ScanView> rows = jdbc.query("""
                SELECT id,status,detection,policy,artifact_state,original_retention,import_status,source_bytes,source_sha256,created_at,updated_at
                  FROM scan_jobs WHERE id=? AND (owner_id=? OR ?)
                """, (rs, row) -> new ScanView(rs.getString(1), ScanContract.status(rs.getString(2)),
                ScanContract.stage(rs.getString(2)), ScanContract.detection(rs.getString(3)),
                ScanContract.policy(rs.getString(4)), ScanContract.artifact(rs.getString(5)),
                ScanContract.retention(rs.getString(6)), rs.getString(7), rs.getLong(8), rs.getString(9),
                rs.getObject(10, OffsetDateTime.class), rs.getObject(11, OffsetDateTime.class)),
                scanId, principal.userId(), principal.admin());
        if (rows.isEmpty()) throw new ApiException(404, "SCAN_NOT_FOUND", "Scan not found");
        return rows.getFirst();
    }

    private void assertHeadroom(Path path, long incoming) throws IOException {
        FileStore store = Files.getFileStore(path);
        long usableAfter = store.getUsableSpace() - incoming;
        long percentFloor = Math.multiplyExact(store.getTotalSpace(), properties.minimumFreePercent()) / 100;
        if (usableAfter < Math.max(properties.minimumFreeBytes(), percentFloor))
            throw new ApiException(507, "STORAGE_HEADROOM", "Upload paused because storage headroom is below policy");
    }

    private static String safeDisplayName(String original) {
        String value = original == null ? "upload.pdf" : original.replaceAll("[\\p{Cntrl}\\\\/]", "_").strip();
        if (value.isEmpty()) value = "upload.pdf";
        return value.length() <= 255 ? value : value.substring(0, 255);
    }

    private static MessageDigest sha256() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }

    private static ScanAccepted accepted(String id) {
        return new ScanAccepted(id, "QUEUED", "/api/v1/scans/" + id, "/api/v1/scans/" + id + "/events");
    }
}
