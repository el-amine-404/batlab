package net.batlab.pdfgateway.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Authenticates either an API bearer token (scripts, iOS Shortcut) or the web
 * UI's session cookie. Cookie-authenticated unsafe requests must carry the
 * session's CSRF token and, when the browser sends one, a same-origin Origin;
 * bearer requests need neither because browsers never attach them ambiently.
 */
@Component
public class BearerTokenFilter extends OncePerRequestFilter {
    public static final String SESSION_COOKIE = "__Host-pgw_session";
    public static final String CSRF_HEADER = "X-CSRF-Token";
    private static final Set<String> SAFE_METHODS = Set.of("GET", "HEAD", "OPTIONS");
    private final JdbcTemplate jdbc;

    public BearerTokenFilter(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public record SessionPrincipal(GatewayPrincipal principal, UUID sessionId, String csrfToken) {}

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String authorization = request.getHeader("Authorization");
        if (authorization != null && authorization.startsWith("Bearer ") && authorization.length() <= 1024) {
            String raw = authorization.substring(7);
            if (raw.length() >= 32) authenticate(TokenHasher.sha256(raw)).ifPresent(this::signIn);
        } else {
            Optional<String> cookie = sessionCookie(request);
            if (cookie.isPresent()) {
                Optional<SessionPrincipal> session = session(TokenHasher.sha256(cookie.get()));
                if (session.isPresent()) {
                    if (!SAFE_METHODS.contains(request.getMethod())) {
                        if (!sameOrigin(request) || !constantTimeEquals(request.getHeader(CSRF_HEADER), session.get().csrfToken())) {
                            reject(response);
                            return;
                        }
                    }
                    request.setAttribute(SessionPrincipal.class.getName(), session.get());
                    signIn(session.get().principal());
                }
            }
        }
        chain.doFilter(request, response);
    }

    private void signIn(GatewayPrincipal principal) {
        var auth = new UsernamePasswordAuthenticationToken(principal, null,
                List.of(new SimpleGrantedAuthority("ROLE_" + principal.role())));
        SecurityContextHolder.getContext().setAuthentication(auth);
    }

    public static Optional<String> sessionCookie(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) return Optional.empty();
        for (Cookie cookie : cookies)
            if (SESSION_COOKIE.equals(cookie.getName()) && cookie.getValue().matches("^[0-9a-f]{64}$"))
                return Optional.of(cookie.getValue());
        return Optional.empty();
    }

    /** A present Origin must match the request's own host and scheme (the UI is same-origin). */
    private static boolean sameOrigin(HttpServletRequest request) {
        String origin = request.getHeader("Origin");
        if (origin == null) return true;
        try {
            URI uri = URI.create(origin);
            return uri.getHost() != null && uri.getHost().equalsIgnoreCase(request.getServerName())
                    && uri.getScheme() != null && uri.getScheme().equalsIgnoreCase(request.getScheme());
        } catch (IllegalArgumentException invalid) {
            return false;
        }
    }

    private static boolean constantTimeEquals(String supplied, String expected) {
        if (supplied == null || expected == null) return false;
        return MessageDigest.isEqual(supplied.getBytes(StandardCharsets.UTF_8), expected.getBytes(StandardCharsets.UTF_8));
    }

    private static void reject(HttpServletResponse response) throws IOException {
        response.setStatus(HttpServletResponse.SC_FORBIDDEN);
        response.setContentType("application/problem+json");
        response.getWriter().write("{\"type\":\"about:blank\",\"title\":\"Forbidden\",\"status\":403,\"code\":\"CSRF_REJECTED\",\"traceId\":\"unavailable\"}");
    }

    private Optional<GatewayPrincipal> authenticate(String hash) {
        List<GatewayPrincipal> found = jdbc.query("""
                SELECT u.id, u.username, u.role
                  FROM api_tokens t JOIN users u ON u.id = t.user_id
                 WHERE t.token_hash = ? AND t.revoked_at IS NULL AND u.enabled
                   AND (t.expires_at IS NULL OR t.expires_at > now())
                """, (rs, row) -> new GatewayPrincipal(rs.getObject(1, UUID.class), rs.getString(2), rs.getString(3)), hash);
        if (found.size() == 1) {
            jdbc.update("UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?", OffsetDateTime.now(), hash);
            return Optional.of(found.getFirst());
        }
        return Optional.empty();
    }

    private Optional<SessionPrincipal> session(String hash) {
        // Absolute expiry is stored; idle expiry is two hours since the last request.
        List<SessionPrincipal> found = jdbc.query("""
                SELECT u.id, u.username, u.role, s.id, s.csrf_token
                  FROM web_sessions s JOIN users u ON u.id = s.user_id
                 WHERE s.session_hash = ? AND s.revoked_at IS NULL AND u.enabled AND s.expires_at > now()
                   AND s.last_seen_at > now() - interval '2 hours'
                """, (rs, row) -> new SessionPrincipal(new GatewayPrincipal(rs.getObject(1, UUID.class), rs.getString(2), rs.getString(3)),
                rs.getObject(4, UUID.class), rs.getString(5)), hash);
        if (found.size() != 1) return Optional.empty();
        jdbc.update("UPDATE web_sessions SET last_seen_at = now() WHERE id = ?", found.getFirst().sessionId());
        return Optional.of(found.getFirst());
    }
}
