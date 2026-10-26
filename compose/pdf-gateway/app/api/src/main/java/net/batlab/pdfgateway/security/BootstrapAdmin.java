package net.batlab.pdfgateway.security;

import java.util.UUID;
import net.batlab.pdfgateway.config.GatewayProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
public class BootstrapAdmin implements ApplicationRunner {
    private static final Logger log = LoggerFactory.getLogger(BootstrapAdmin.class);
    private final JdbcTemplate jdbc;
    private final GatewayProperties properties;
    private final Accounts accounts;

    public BootstrapAdmin(JdbcTemplate jdbc, GatewayProperties properties, Accounts accounts) {
        this.jdbc = jdbc;
        this.properties = properties;
        this.accounts = accounts;
    }

    @Override
    @Transactional
    public void run(ApplicationArguments args) {
        bootstrapToken();
        // Until an admin can sign in with a password, whoever runs the server makes one with this code.
        accounts.setupCode().ifPresent(code -> log.warn("No admin can sign in yet: open the page and make one with "
                + "the setup code {} (the dashes are only for reading). Find it again with: "
                + "docker logs pdf-gateway-api 2>&1 | grep 'setup code'", code));
    }

    /** An API key for the admin from the environment (scripts made before passwords); optional. */
    private void bootstrapToken() {
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
