package net.batlab.pdfgateway.security;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class BearerTokenFilter extends OncePerRequestFilter {
    private final JdbcTemplate jdbc;

    public BearerTokenFilter(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        String authorization = request.getHeader("Authorization");
        if (authorization != null && authorization.startsWith("Bearer ") && authorization.length() <= 1024) {
            String raw = authorization.substring(7);
            if (raw.length() >= 32) {
                authenticate(TokenHasher.sha256(raw)).ifPresent(principal -> {
                    var auth = new UsernamePasswordAuthenticationToken(principal, null,
                            List.of(new SimpleGrantedAuthority("ROLE_" + principal.role())));
                    SecurityContextHolder.getContext().setAuthentication(auth);
                });
            }
        }
        chain.doFilter(request, response);
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
}
