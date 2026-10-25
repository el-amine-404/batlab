CREATE TABLE signature_verifications (
    id uuid PRIMARY KEY,
    scan_id varchar(31) NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
    attempt integer NOT NULL CHECK (attempt > 0),
    source_sha256 char(64) NOT NULL,
    state varchar(40) NOT NULL,
    policy_decision varchar(48) NOT NULL,
    preserve_original boolean NOT NULL,
    signature_count integer NOT NULL CHECK (signature_count >= 0),
    report jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scan_id, attempt)
);

CREATE INDEX signature_verifications_scan_idx
    ON signature_verifications (scan_id, created_at DESC);
