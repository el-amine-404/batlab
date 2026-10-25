-- Optional OCR/AI enrichment by an agent on a separate machine that pulls work.
-- Enrichment never affects security state: jobs reference only published
-- archive documents, and AI output is stored as proposals.

ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('ADMIN', 'USER', 'READ_ONLY', 'ENRICHMENT'));

CREATE TABLE enrichment_jobs (
    id uuid PRIMARY KEY,
    document_id varchar(30) NOT NULL REFERENCES archive_documents(document_id) ON DELETE CASCADE,
    artifact_sha256 char(64) NOT NULL,
    kind varchar(16) NOT NULL CHECK (kind IN ('OCR', 'SUGGEST')),
    -- SUBMITTED: the agent returned a result; the archive worker validates it
    -- (bounds, vocabulary, evidence quotes present in the document text).
    status varchar(16) NOT NULL DEFAULT 'WAITING_FOR_AI'
        CHECK (status IN ('WAITING_FOR_AI', 'LEASED', 'SUBMITTED', 'COMPLETED', 'FAILED', 'SKIPPED', 'EXPIRED')),
    result jsonb,
    lease_agent varchar(64),
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    error_code varchar(48),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '30 days',
    completed_at timestamptz,
    UNIQUE (document_id, kind, artifact_sha256)
);
CREATE INDEX enrichment_jobs_waiting_idx ON enrichment_jobs (kind, created_at) WHERE status = 'WAITING_FOR_AI';

CREATE TABLE ai_agents (
    agent_id varchar(64) PRIMARY KEY CHECK (agent_id ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
    user_id uuid NOT NULL REFERENCES users(id),
    version varchar(80),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    report jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_success_at timestamptz,
    consecutive_failures integer NOT NULL DEFAULT 0,
    breaker_open_until timestamptz,
    last_error varchar(80)
);

CREATE TABLE ai_suggestions (
    id uuid PRIMARY KEY,
    document_id varchar(30) NOT NULL REFERENCES archive_documents(document_id) ON DELETE CASCADE,
    job_id uuid NOT NULL REFERENCES enrichment_jobs(id),
    model varchar(120) NOT NULL,
    prompt_version varchar(40) NOT NULL,
    fields jsonb NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'APPLIED', 'DISMISSED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    decided_by uuid REFERENCES users(id)
);
CREATE INDEX ai_suggestions_document_idx ON ai_suggestions (document_id, created_at DESC);
