CREATE TABLE users (
    id uuid PRIMARY KEY,
    username varchar(80) NOT NULL UNIQUE,
    role varchar(20) NOT NULL CHECK (role IN ('ADMIN', 'USER', 'READ_ONLY')),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api_tokens (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE,
    label varchar(100) NOT NULL,
    expires_at timestamptz,
    last_used_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);

CREATE TABLE scan_jobs (
    id varchar(31) PRIMARY KEY CHECK (id ~ '^scan-[0-9A-HJKMNP-TV-Z]{26}$'),
    owner_id uuid NOT NULL REFERENCES users(id),
    status varchar(32) NOT NULL,
    intent varchar(32) NOT NULL,
    cdr_mode varchar(32) NOT NULL,
    display_name varchar(255) NOT NULL,
    source_bytes bigint NOT NULL CHECK (source_bytes > 0),
    source_sha256 char(64) NOT NULL,
    quarantine_object varchar(80) NOT NULL UNIQUE,
    detection varchar(32) NOT NULL DEFAULT 'UNKNOWN',
    policy varchar(32) NOT NULL DEFAULT 'REVIEW_REQUIRED',
    artifact_state varchar(32) NOT NULL DEFAULT 'ORIGINAL_QUARANTINED',
    import_status varchar(32) NOT NULL DEFAULT 'NOT_REQUESTED',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    version bigint NOT NULL DEFAULT 1
);

CREATE TABLE job_steps (
    id uuid PRIMARY KEY,
    scan_id varchar(31) NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
    step_type varchar(40) NOT NULL,
    status varchar(32) NOT NULL,
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    engine_version varchar(160),
    started_at timestamptz,
    finished_at timestamptz,
    error_code varchar(80),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (scan_id, step_type, attempt)
);

CREATE TABLE artifacts (
    id varchar(80) PRIMARY KEY,
    scan_id varchar(31) NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
    kind varchar(40) NOT NULL,
    storage_object varchar(160) NOT NULL UNIQUE,
    sha256 char(64) NOT NULL,
    bytes bigint NOT NULL CHECK (bytes > 0),
    state varchar(32) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_keys (
    user_id uuid NOT NULL REFERENCES users(id),
    operation varchar(80) NOT NULL,
    key_hash char(64) NOT NULL,
    request_hash char(64) NOT NULL,
    resource_id varchar(80) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, operation, key_hash)
);

CREATE TABLE audit_events (
    id bigserial PRIMARY KEY,
    actor_id uuid REFERENCES users(id),
    action varchar(80) NOT NULL,
    resource_type varchar(40) NOT NULL,
    resource_id varchar(80) NOT NULL,
    outcome varchar(20) NOT NULL,
    trace_id varchar(80) NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox_events (
    id uuid PRIMARY KEY,
    aggregate_type varchar(40) NOT NULL,
    aggregate_id varchar(80) NOT NULL,
    event_type varchar(80) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    available_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    last_error varchar(500)
);
CREATE INDEX outbox_unpublished_idx ON outbox_events (available_at, created_at) WHERE published_at IS NULL;
CREATE INDEX scan_jobs_owner_created_idx ON scan_jobs (owner_id, created_at DESC);
CREATE INDEX job_steps_scan_idx ON job_steps (scan_id, step_type);
CREATE INDEX artifacts_scan_idx ON artifacts (scan_id, kind);
