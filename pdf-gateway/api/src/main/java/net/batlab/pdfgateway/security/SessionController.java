package net.batlab.pdfgateway.security;

import jakarta.servlet.http.HttpServletRequest;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Exchanges an API token for a browser session. The token is used once and is
 * never stored by the browser; the session cookie is HttpOnly, Secure,
 * SameSite=Strict and host-only (__Host- prefix).
 */
@RestController
@RequestMapping("/api/v1/session")
public class SessionController {
    private static final Duration LIFETIME = Duration.ofHours(8);
    private static final SecureRandom RANDOM = new SecureRandom();
    private final JdbcTemplate jdbc;

    public SessionController(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public record SessionView(String username, String role, String csrfToken, OffsetDateTime expiresAt) {}

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<?> create(@RequestBody Map<String, Object> body) {
        Object token = body == null ? null : body.get("token");
        if (!(token instanceof String raw) || raw.length() < 32 || raw.length() > 512)
            return problem(401, "AUTH_REQUIRED", "A valid API token is required");
        List<UUID> users = jdbc.query("""
                SELECT u.id FROM api_tokens t JOIN users u ON u.id = t.user_id
                 WHERE t.token_hash = ? AND t.revoked_at IS NULL AND u.enabled
                   AND (t.expires_at IS NULL OR t.expires_at > now())""",
                (rs, row) -> rs.getObject(1, UUID.class), TokenHasher.sha256(raw));
        if (users.size() != 1) {
            jdbc.update("INSERT INTO audit_events(action,resource_type,resource_id,outcome,trace_id) VALUES ('SESSION_CREATE','SESSION','-','FAILURE',?)", UUID.randomUUID().toString());
            return problem(401, "AUTH_REQUIRED", "A valid API token is required");
        }
        String cookie = randomHex();
        String csrf = randomHex();
        OffsetDateTime expires = OffsetDateTime.now().plus(LIFETIME);
        UUID id = UUID.randomUUID();
        jdbc.update("INSERT INTO web_sessions(id,session_hash,csrf_token,user_id,expires_at) VALUES (?,?,?,?,?)",
                id, TokenHasher.sha256(cookie), csrf, users.getFirst(), expires);
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id) VALUES (?,'SESSION_CREATE','SESSION',?,'SUCCESS',?)",
                users.getFirst(), id.toString(), UUID.randomUUID().toString());
        SessionView view = jdbc.queryForObject("SELECT username, role FROM users WHERE id=?",
                (rs, row) -> new SessionView(rs.getString(1), rs.getString(2), csrf, expires), users.getFirst());
        return ResponseEntity.status(201).header(HttpHeaders.SET_COOKIE, sessionCookie(cookie, LIFETIME).toString())
                .header(HttpHeaders.CACHE_CONTROL, "no-store").body(view);
    }

    @GetMapping
    ResponseEntity<?> current(@AuthenticationPrincipal GatewayPrincipal principal, HttpServletRequest request) {
        Object attribute = request.getAttribute(BearerTokenFilter.SessionPrincipal.class.getName());
        if (!(attribute instanceof BearerTokenFilter.SessionPrincipal session))
            return problem(400, "NOT_A_SESSION", "Bearer-token requests have no browser session");
        OffsetDateTime expires = jdbc.queryForObject("SELECT expires_at FROM web_sessions WHERE id=?", OffsetDateTime.class, session.sessionId());
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(new SessionView(principal.username(), principal.role(), session.csrfToken(), expires));
    }

    @DeleteMapping
    ResponseEntity<Void> delete(HttpServletRequest request) {
        Object attribute = request.getAttribute(BearerTokenFilter.SessionPrincipal.class.getName());
        if (attribute instanceof BearerTokenFilter.SessionPrincipal session)
            jdbc.update("UPDATE web_sessions SET revoked_at = now() WHERE id = ?", session.sessionId());
        return ResponseEntity.noContent().header(HttpHeaders.SET_COOKIE, sessionCookie("", Duration.ZERO).toString()).build();
    }

    private static ResponseCookie sessionCookie(String value, Duration maxAge) {
        return ResponseCookie.from(BearerTokenFilter.SESSION_COOKIE, value)
                .httpOnly(true).secure(true).sameSite("Strict").path("/").maxAge(maxAge).build();
    }

    private static String randomHex() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return HexFormat.of().formatHex(bytes);
    }

    private static ResponseEntity<Map<String, Object>> problem(int status, String code, String detail) {
        return ResponseEntity.status(status).contentType(MediaType.APPLICATION_PROBLEM_JSON)
                .body(Map.of("type", "about:blank", "title", code, "status", status, "detail", detail, "code", code, "traceId", "unavailable"));
    }
}
