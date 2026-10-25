-- Signed-original retention is independent of the artifact lifecycle; keeping it
-- in artifact_state would be lost when a sanitized copy is staged.
ALTER TABLE scan_jobs
    ADD COLUMN original_retention varchar(32) NOT NULL DEFAULT 'STANDARD'
        CHECK (original_retention IN ('STANDARD', 'SIGNED_RESTRICTED'));

UPDATE scan_jobs
   SET original_retention = 'SIGNED_RESTRICTED', artifact_state = 'ORIGINAL_QUARANTINED'
 WHERE artifact_state = 'SIGNED_ORIGINAL_RESTRICTED';

CREATE FUNCTION scan_jobs_retention_never_lowered() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.original_retention = 'SIGNED_RESTRICTED' AND NEW.original_retention <> 'SIGNED_RESTRICTED' THEN
        RAISE EXCEPTION 'signed original retention cannot be lowered for %', OLD.id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER scan_jobs_retention_never_lowered
    BEFORE UPDATE OF original_retention ON scan_jobs
    FOR EACH ROW EXECUTE FUNCTION scan_jobs_retention_never_lowered();

CREATE TABLE cdr_results (
    id uuid PRIMARY KEY,
    scan_id varchar(31) NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
    attempt integer NOT NULL CHECK (attempt > 0),
    status varchar(16) NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
    failure_code varchar(48),
    retryable boolean NOT NULL DEFAULT false,
    source_sha256 char(64) NOT NULL,
    output_artifact_id varchar(80) REFERENCES artifacts(id),
    output_sha256 char(64),
    output_bytes bigint CHECK (output_bytes > 0),
    provider_version varchar(80),
    dangerzone_version varchar(40),
    image_digest varchar(80),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    duration_ms integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scan_id, attempt),
    CHECK ((status = 'SUCCEEDED') = (output_artifact_id IS NOT NULL AND output_sha256 IS NOT NULL
                                    AND image_digest IS NOT NULL AND failure_code IS NULL))
);

CREATE INDEX cdr_results_scan_idx ON cdr_results (scan_id, attempt DESC);
