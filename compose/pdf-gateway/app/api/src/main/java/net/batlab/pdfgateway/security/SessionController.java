package net.batlab.pdfgateway.security;

import jakarta.servlet.http.HttpServletRequest;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.HexFormat;
import java.util.Map;
import java.util.UUID;
import net.batlab.pdfgateway.api.ApiException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Browser sessions: a person signs in with their name and password (or makes
 * the first admin with the setup code). The session cookie is HttpOnly, Secure,
 * SameSite=Strict and host-only (__Host- prefix); apps use API keys instead.
 */
@RestController
@RequestMapping("/api/v1/session")
public class SessionController {
    private static final Duration LIFETIME = Duration.ofHours(8);
    private static final SecureRandom RANDOM = new SecureRandom();
    private final JdbcTemplate jdbc;
    private final Accounts accounts;

    public SessionController(JdbcTemplate jdbc, Accounts accounts) {
        this.jdbc = jdbc;
        this.accounts = accounts;
    }

    public record SessionView(String username, String role, String csrfToken, OffsetDateTime expiresAt) {}

    public record SetupState(boolean needed, String suggestedUsername) {}
    public record SetupIn(String code, String username, String password) {}
    public record LoginIn(String username, String password) {}
    public record PasswordIn(String current, String next) {}

    /** Whether the page shows the setup form (no admin can sign in yet), and the name it offers. */
    @GetMapping("/setup")
    ResponseEntity<SetupState> setupState() {
        boolean needed = accounts.needsSetup();
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(new SetupState(needed, needed ? accounts.suggestedAdmin().orElse(null) : null));
    }

    /** The first admin, with the setup code from the API's log; signs them in. */
    @PostMapping(path = "/setup", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<SessionView> setup(@RequestBody SetupIn body, HttpServletRequest request) {
        if (body == null) throw new ApiException(422, "BAD_REQUEST", "The setup code, a name and a password are needed.");
        return open(accounts.setup(body.code(), body.username(), body.password(), request.getRemoteAddr()));
    }

    /** Signs a person in with their name and password: the session cookie. Apps use an API key instead. */
    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<SessionView> create(@RequestBody LoginIn body, HttpServletRequest request) {
        if (body == null) throw new ApiException(401, "WRONG_PASSWORD", "That name and password don't match.");
        return open(accounts.signIn(body.username(), body.password(), request.getRemoteAddr()));
    }

    /** Changes one's own password; one's other sessions end. */
    @PutMapping(path = "/password", consumes = MediaType.APPLICATION_JSON_VALUE)
    ResponseEntity<Void> password(@AuthenticationPrincipal GatewayPrincipal principal, @RequestBody PasswordIn body, HttpServletRequest request) {
        if (!(request.getAttribute(BearerTokenFilter.SessionPrincipal.class.getName()) instanceof BearerTokenFilter.SessionPrincipal session))
            throw new ApiException(400, "NOT_A_SESSION", "Change a password from the page.");
        accounts.changeOwnPassword(principal.userId(), body == null ? null : body.current(), body == null ? null : body.next(), session.sessionId());
        return ResponseEntity.noContent().build();
    }

    private ResponseEntity<SessionView> open(UUID user) {
        String cookie = randomHex();
        String csrf = randomHex();
        OffsetDateTime expires = OffsetDateTime.now().plus(LIFETIME);
        UUID id = UUID.randomUUID();
        jdbc.update("INSERT INTO web_sessions(id,session_hash,csrf_token,user_id,expires_at) VALUES (?,?,?,?,?)",
                id, TokenHasher.sha256(cookie), csrf, user, expires);
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id) VALUES (?,'SESSION_CREATE','SESSION',?,'SUCCESS',?)",
                user, id.toString(), UUID.randomUUID().toString());
        SessionView view = jdbc.queryForObject("SELECT username, role FROM users WHERE id=?",
                (rs, row) -> new SessionView(rs.getString(1), rs.getString(2), csrf, expires), user);
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
