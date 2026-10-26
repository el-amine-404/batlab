package net.batlab.pdfgateway.security;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.OffsetDateTime;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import net.batlab.pdfgateway.api.ApiException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.crypto.password.DelegatingPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.crypto.password.Pbkdf2PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Accounts: people sign in on the page with a name and a password; apps and
 * scripts (Homepage, the laptop's agent, an iOS Shortcut) use an API key of an
 * account of their own. Until an admin can sign in with a password, the page
 * asks for a setup code: random, kept in the database and written to the log,
 * so only someone with access to the server makes the first admin.
 */
@Component
public class Accounts {
    private static final Logger log = LoggerFactory.getLogger(Accounts.class);
    public static final Set<String> PERSON_ROLES = Set.of("ADMIN", "USER", "READ_ONLY");
    public static final Set<String> KEY_ROLES = Set.of("ADMIN", "USER", "READ_ONLY", "ENRICHMENT");
    public static final int PASSWORD_MIN = 10;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final String CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    // PBKDF2-HMAC-SHA256, 310 000 rounds, salted; the prefix leaves room for another scheme later.
    private static final PasswordEncoder PASSWORDS = new DelegatingPasswordEncoder("pbkdf2",
            Map.of("pbkdf2", Pbkdf2PasswordEncoder.defaultsForSpringSecurity_v5_8()));
    // Checked when the name is unknown, so a wrong name takes as long as a wrong password.
    private static final String DUMMY = PASSWORDS.encode(UUID.randomUUID().toString());

    private final JdbcTemplate jdbc;
    private final Throttle throttle = new Throttle();

    public Accounts(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public record Account(UUID id, String username, String role, boolean enabled, boolean signsIn,
                          OffsetDateTime createdAt, OffsetDateTime lastLoginAt, List<Key> keys) {}
    public record Key(UUID id, String label, OffsetDateTime createdAt, OffsetDateTime lastUsedAt) {}
    public record NewKey(UUID id, String label, String token, Account account) {}

    // ---- the first admin ---------------------------------------------------------------------

    public boolean needsSetup() {
        Integer admins = jdbc.queryForObject(
                "SELECT count(*) FROM users WHERE role='ADMIN' AND enabled AND password_hash IS NOT NULL", Integer.class);
        return admins == null || admins == 0;
    }

    /** The name the setup form offers: an admin that only had a token (its documents stay its own). */
    public Optional<String> suggestedAdmin() {
        return jdbc.query("SELECT username FROM users WHERE role='ADMIN' AND password_hash IS NULL ORDER BY created_at LIMIT 1",
                (rs, row) -> rs.getString(1)).stream().findFirst();
    }

    /** The setup code while it is needed (a new one when there is none); empty afterwards. */
    @Transactional
    public Optional<String> setupCode() {
        if (!needsSetup()) {
            jdbc.update("DELETE FROM account_state WHERE key='setup_code'");
            return Optional.empty();
        }
        Optional<String> found = jdbc.query("SELECT value FROM account_state WHERE key='setup_code'",
                (rs, row) -> rs.getString(1)).stream().findFirst();
        if (found.isPresent()) return found;
        StringBuilder raw = new StringBuilder();
        for (int i = 0; i < 12; i++) raw.append(CODE_ALPHABET.charAt(RANDOM.nextInt(CODE_ALPHABET.length())));
        String code = raw.substring(0, 4) + "-" + raw.substring(4, 8) + "-" + raw.substring(8);
        jdbc.update("INSERT INTO account_state(key,value) VALUES ('setup_code',?) ON CONFLICT (key) DO NOTHING", code);
        return jdbc.query("SELECT value FROM account_state WHERE key='setup_code'", (rs, row) -> rs.getString(1)).stream().findFirst();
    }

    /** The first admin with a password, when the code is right. An existing admin of that name keeps its documents. */
    @Transactional
    public UUID setup(String code, String username, String password, String address) {
        String key = "setup:" + address;
        throttle.check(key);
        if (!needsSetup()) throw new ApiException(409, "ALREADY_SET_UP", "The gateway is set up already: sign in.");
        String expected = setupCode().orElse("");
        if (!MessageDigest.isEqual(letters(code).getBytes(StandardCharsets.UTF_8), letters(expected).getBytes(StandardCharsets.UTF_8))) {
            throttle.failed(key);
            log.warn("a wrong setup code from {}", address);
            throw new ApiException(422, "WRONG_SETUP_CODE", "That isn't the setup code.");
        }
        String name = cleanName(username);
        checkPassword(password, name);
        Optional<Account> existing = byName(name);
        UUID id;
        if (existing.isPresent()) {
            if (!"ADMIN".equals(existing.get().role()) || existing.get().signsIn())
                throw new ApiException(409, "NAME_TAKEN", "Someone is called '" + name + "' already: choose another name.");
            id = existing.get().id();
            jdbc.update("UPDATE users SET password_hash=?, password_changed_at=now(), last_login_at=now(), enabled=true WHERE id=?", PASSWORDS.encode(password), id);
        } else {
            id = UUID.randomUUID();
            jdbc.update("INSERT INTO users(id,username,role,password_hash,password_changed_at,last_login_at) VALUES (?,?,'ADMIN',?,now(),now())",
                    id, name, PASSWORDS.encode(password));
        }
        jdbc.update("DELETE FROM account_state WHERE key='setup_code'");
        audit(id, "ACCOUNT_SETUP", id.toString(), "SUCCESS");
        log.warn("set up: {} is the first admin with a password, from {}", name, address);
        return id;
    }

    // ---- signing in --------------------------------------------------------------------------

    /** The account a name and password belong to; after 5 wrong tries in 15 minutes each try waits longer. */
    public UUID signIn(String username, String password, String address) {
        String name = username == null ? "" : username.strip().toLowerCase(Locale.ROOT);
        throttle.check("ip:" + address, "name:" + name);
        List<String[]> found = jdbc.query("SELECT id, password_hash FROM users WHERE lower(username)=? AND enabled AND password_hash IS NOT NULL",
                (rs, row) -> new String[] {rs.getString(1), rs.getString(2)}, name);
        boolean right = password != null && password.length() <= 200
                && PASSWORDS.matches(password, found.isEmpty() ? DUMMY : found.getFirst()[1]) && !found.isEmpty();
        if (!right) {
            throttle.failed("ip:" + address, "name:" + name);
            log.warn("a failed sign-in as '{}' from {}", name.length() > 80 ? name.substring(0, 80) : name, address);
            audit(null, "SESSION_CREATE", "-", "FAILURE");
            throw new ApiException(401, "WRONG_PASSWORD", "That name and password don't match.");
        }
        throttle.succeeded("name:" + name);
        UUID id = UUID.fromString(found.getFirst()[0]);
        jdbc.update("UPDATE users SET last_login_at=now() WHERE id=?", id);
        return id;
    }

    @Transactional
    public void changeOwnPassword(UUID id, String current, String next, UUID keepSession) {
        String stored = jdbc.query("SELECT password_hash FROM users WHERE id=?", (rs, row) -> rs.getString(1), id)
                .stream().findFirst().orElse(null);
        if (stored == null || current == null || !PASSWORDS.matches(current, stored))
            throw new ApiException(422, "WRONG_PASSWORD", "The current password isn't right.");
        setPassword(id, next, keepSession);
    }

    // ---- accounts (admins) -------------------------------------------------------------------

    public List<Account> list() {
        Map<UUID, List<Key>> keys = new ConcurrentHashMap<>();
        jdbc.query("SELECT id, user_id, label, created_at, last_used_at FROM api_tokens WHERE revoked_at IS NULL ORDER BY created_at",
                rs -> { keys.computeIfAbsent(rs.getObject(2, UUID.class), k -> new java.util.ArrayList<>())
                        .add(new Key(rs.getObject(1, UUID.class), rs.getString(3), rs.getObject(4, OffsetDateTime.class),
                                rs.getObject(5, OffsetDateTime.class))); });
        return jdbc.query("SELECT id, username, role, enabled, password_hash IS NOT NULL, created_at, last_login_at FROM users ORDER BY lower(username)",
                (rs, row) -> {
                    UUID id = rs.getObject(1, UUID.class);
                    return new Account(id, rs.getString(2), rs.getString(3), rs.getBoolean(4), rs.getBoolean(5),
                            rs.getObject(6, OffsetDateTime.class), rs.getObject(7, OffsetDateTime.class), keys.getOrDefault(id, List.of()));
                });
    }

    public Account get(UUID id) {
        return list().stream().filter(a -> a.id().equals(id)).findFirst()
                .orElseThrow(() -> new ApiException(404, "NO_SUCH_ACCOUNT", "There's no such account."));
    }

    private Optional<Account> byName(String name) {
        return list().stream().filter(a -> a.username().equalsIgnoreCase(name)).findFirst();
    }

    /** A person (with a password) or an app (without: it uses keys). */
    @Transactional
    public Account create(UUID actor, String username, String role, String password) {
        String name = cleanName(username);
        boolean person = password != null && !password.isEmpty();
        if (!(person ? PERSON_ROLES : KEY_ROLES).contains(role))
            throw new ApiException(422, "BAD_ROLE", person ? "A person is an admin, a user or read-only." : "Unknown role.");
        if (person) checkPassword(password, name);
        if (byName(name).isPresent()) throw new ApiException(409, "NAME_TAKEN", "Someone is called '" + name + "' already.");
        UUID id = UUID.randomUUID();
        jdbc.update("INSERT INTO users(id,username,role,password_hash,password_changed_at) VALUES (?,?,?,?,?)",
                id, name, role, person ? PASSWORDS.encode(password) : null, person ? OffsetDateTime.now() : null);
        audit(actor, "ACCOUNT_CREATE", id.toString(), "SUCCESS");
        return get(id);
    }

    @Transactional
    public Account update(UUID actor, UUID id, String role, Boolean enabled, String password) {
        Account account = get(id);
        if (role != null && !(account.signsIn() || password != null ? PERSON_ROLES : KEY_ROLES).contains(role))
            throw new ApiException(422, "BAD_ROLE", "A person is an admin, a user or read-only.");
        if (actor.equals(id) && Boolean.FALSE.equals(enabled)) throw new ApiException(409, "SELF", "You can't turn yourself off.");
        String futureRole = role != null ? role : account.role();
        boolean futureEnabled = enabled != null ? enabled : account.enabled();
        boolean futureSignsIn = account.signsIn() || (password != null && !password.isEmpty());
        if (!("ADMIN".equals(futureRole) && futureEnabled && futureSignsIn) && signInAdmins(id) == 0)
            throw new ApiException(409, "LAST_ADMIN", "Keep at least one admin who can sign in: make another first.");
        if (role != null) jdbc.update("UPDATE users SET role=? WHERE id=?", role, id);
        if (enabled != null) {
            jdbc.update("UPDATE users SET enabled=? WHERE id=?", enabled, id);
            if (!enabled) jdbc.update("UPDATE web_sessions SET revoked_at=now() WHERE user_id=? AND revoked_at IS NULL", id);
        }
        if (password != null && !password.isEmpty()) setPassword(id, password, null);
        // A new role takes effect at once: sessions carry none, they read it on each request.
        audit(actor, "ACCOUNT_UPDATE", id.toString(), "SUCCESS");
        return get(id);
    }

    @Transactional
    public NewKey addKey(UUID actor, UUID id, String label) {
        Account account = get(id);
        String name = label == null || label.isBlank() ? "key" : label.strip();
        if (name.length() > 100) throw new ApiException(422, "BAD_LABEL", "A key's name is at most 100 characters.");
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        String token = "pgw_" + java.util.HexFormat.of().formatHex(bytes);
        UUID keyId = UUID.randomUUID();
        jdbc.update("INSERT INTO api_tokens(id,user_id,token_hash,label) VALUES (?,?,?,?)", keyId, id, TokenHasher.sha256(token), name);
        audit(actor, "API_KEY_CREATE", keyId.toString(), "SUCCESS");
        return new NewKey(keyId, name, token, get(account.id()));
    }

    @Transactional
    public void revokeKey(UUID actor, UUID id, UUID keyId) {
        if (jdbc.update("UPDATE api_tokens SET revoked_at=now() WHERE id=? AND user_id=? AND revoked_at IS NULL", keyId, id) == 0)
            throw new ApiException(404, "NO_SUCH_KEY", "There's no such key.");
        audit(actor, "API_KEY_REVOKE", keyId.toString(), "SUCCESS");
    }

    // ---- helpers -----------------------------------------------------------------------------

    private void setPassword(UUID id, String password, UUID keepSession) {
        String name = jdbc.queryForObject("SELECT username FROM users WHERE id=?", String.class, id);
        checkPassword(password, name);
        jdbc.update("UPDATE users SET password_hash=?, password_changed_at=now() WHERE id=?", PASSWORDS.encode(password), id);
        // Every other session of theirs ends.
        jdbc.update("UPDATE web_sessions SET revoked_at=now() WHERE user_id=? AND revoked_at IS NULL AND id IS DISTINCT FROM ?", id, keepSession);
    }

    private int signInAdmins(UUID excluding) {
        Integer count = jdbc.queryForObject("SELECT count(*) FROM users WHERE role='ADMIN' AND enabled AND password_hash IS NOT NULL AND id<>?",
                Integer.class, excluding);
        return count == null ? 0 : count;
    }

    private static String cleanName(String username) {
        String name = username == null ? "" : username.strip();
        if (name.isEmpty() || name.length() > 40 || !name.matches("[A-Za-z0-9._-]+"))
            throw new ApiException(422, "BAD_NAME", "A user name is letters, digits, . _ and -, up to 40.");
        return name;
    }

    private static void checkPassword(String password, String username) {
        if (password == null || password.length() < PASSWORD_MIN)
            throw new ApiException(422, "WEAK_PASSWORD", "A password has at least " + PASSWORD_MIN + " characters.");
        if (password.length() > 200) throw new ApiException(422, "WEAK_PASSWORD", "A password has at most 200 characters.");
        if (password.equalsIgnoreCase(username)) throw new ApiException(422, "WEAK_PASSWORD", "The password can't be the user name.");
    }

    private static String letters(String code) {
        return code == null ? "" : code.toUpperCase(Locale.ROOT).replaceAll("[^A-Z0-9]", "");
    }

    private void audit(UUID actor, String action, String resource, String outcome) {
        jdbc.update("INSERT INTO audit_events(actor_id,action,resource_type,resource_id,outcome,trace_id) VALUES (?,?,'ACCOUNT',?,?,?)",
                actor, action, resource, outcome, UUID.randomUUID().toString());
    }

    /**
     * Failed tries by address and by name: after 5 in 15 minutes, each try waits
     * twice as long as the last (30 s, 1 min… up to 15 min). In memory: a restart forgets.
     */
    static final class Throttle {
        private static final int FREE = 5;
        private static final long WINDOW = 900_000, LONGEST = 900_000;
        private final Map<String, Deque<Long>> failures = new ConcurrentHashMap<>();

        void check(String... keys) {
            long now = System.currentTimeMillis(), wait = 0;
            for (String key : keys) {
                Deque<Long> found = failures.get(key);
                if (found == null) continue;
                synchronized (found) {
                    while (!found.isEmpty() && found.peekFirst() < now - WINDOW) found.pollFirst();
                    if (found.size() >= FREE) {
                        long pause = Math.min(LONGEST, 30_000L << Math.min(10, found.size() - FREE));
                        wait = Math.max(wait, found.peekLast() + pause - now);
                    }
                }
            }
            if (wait > 0) throw new ApiException(429, "TOO_MANY_TRIES",
                    "Too many wrong tries: try again in " + ((wait + 999) / 1000) + " s.");
        }

        void failed(String... keys) {
            if (failures.size() > 10_000) failures.clear();
            for (String key : keys) {
                Deque<Long> found = failures.computeIfAbsent(key, k -> new ArrayDeque<>());
                synchronized (found) {
                    found.addLast(System.currentTimeMillis());
                    if (found.size() > 64) found.pollFirst();
                }
            }
        }

        void succeeded(String key) { failures.remove(key); }
    }
}
