-- Browser sessions for the web UI. The cookie value is never stored, only its
-- SHA-256. The CSRF token is bound to the session and checked on every unsafe
-- request authenticated by the cookie.
CREATE TABLE web_sessions (
    id uuid PRIMARY KEY,
    session_hash char(64) NOT NULL UNIQUE,
    csrf_token char(64) NOT NULL,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX web_sessions_user_idx ON web_sessions (user_id) WHERE revoked_at IS NULL;
