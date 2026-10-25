package net.batlab.pdfgateway.security;

import java.util.UUID;
import net.batlab.pdfgateway.config.GatewayProperties;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class BootstrapAdmin implements ApplicationRunner {
    private final JdbcTemplate jdbc;
    private final GatewayProperties properties;

    public BootstrapAdmin(JdbcTemplate jdbc, GatewayProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        String token = properties.bootstrapAdminToken();
        if (token == null || token.isBlank()) return;
        if (token.length() < 32) throw new IllegalStateException("bootstrap admin token must contain at least 32 characters");
        UUID userId = jdbc.query("SELECT id FROM users WHERE username = ?",
                rs -> rs.next() ? rs.getObject(1, UUID.class) : null, properties.bootstrapAdminName());
        if (userId == null) {
            userId = UUID.randomUUID();
            jdbc.update("INSERT INTO users(id, username, role) VALUES (?, ?, 'ADMIN')", userId, properties.bootstrapAdminName());
        }
        String hash = TokenHasher.sha256(token);
        Integer exists = jdbc.queryForObject("SELECT count(*) FROM api_tokens WHERE token_hash = ?", Integer.class, hash);
        if (exists != null && exists == 0) {
            jdbc.update("INSERT INTO api_tokens(id, user_id, token_hash, label) VALUES (?, ?, ?, ?)",
                    UUID.randomUUID(), userId, hash, "bootstrap");
        }
    }
}
