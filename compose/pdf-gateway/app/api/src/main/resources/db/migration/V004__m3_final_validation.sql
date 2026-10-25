-- Final validation of exact CDR output bytes, plus database-enforced release
-- invariants. Workers already follow these rules; the triggers make sure no
-- other writer (including a manual SQL shortcut from an initial CLEAN scan) can
-- reach a release state without a passing validation of a CDR artifact.

CREATE TABLE final_validations (
    id uuid PRIMARY KEY,
    scan_id varchar(31) NOT NULL REFERENCES scan_jobs(id) ON DELETE CASCADE,
    attempt integer NOT NULL CHECK (attempt > 0),
    artifact_id varchar(80) NOT NULL REFERENCES artifacts(id),
    source_sha256 char(64) NOT NULL,
    output_sha256 char(64) NOT NULL,
    verdict varchar(8) NOT NULL CHECK (verdict IN ('PASS', 'FAIL', 'UNKNOWN')),
    detection varchar(32) NOT NULL,
    policy_decision varchar(32) NOT NULL CHECK (policy_decision IN ('ALLOW_SANITIZED', 'REVIEW_REQUIRED', 'BLOCK')),
    failure_codes jsonb NOT NULL,
    policy_version varchar(40) NOT NULL,
    engines jsonb NOT NULL,
    report_sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (scan_id, attempt),
    CHECK ((verdict = 'PASS') = (policy_decision = 'ALLOW_SANITIZED')),
    CHECK (verdict <> 'PASS' OR (failure_codes = '[]'::jsonb AND detection = 'NO_THREATS_DETECTED'))
);

CREATE INDEX final_validations_scan_idx ON final_validations (scan_id, attempt DESC);

-- A PASS must describe a successful CDR output of this scan's exact source.
CREATE FUNCTION final_validations_pass_requires_cdr() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.verdict = 'PASS' AND NOT EXISTS (
        SELECT 1
          FROM artifacts a
          JOIN cdr_results c ON c.output_artifact_id = a.id AND c.status = 'SUCCEEDED'
          JOIN scan_jobs s ON s.id = a.scan_id
         WHERE a.id = NEW.artifact_id
           AND a.scan_id = NEW.scan_id
           AND a.kind = 'CDR_OUTPUT'
           AND a.sha256 = NEW.output_sha256
           AND c.output_sha256 = NEW.output_sha256
           AND c.source_sha256 = NEW.source_sha256
           AND s.source_sha256 = NEW.source_sha256
           AND NEW.output_sha256 <> NEW.source_sha256) THEN
        RAISE EXCEPTION 'final validation PASS for % does not match a successful CDR output', NEW.scan_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER final_validations_pass_requires_cdr
    BEFORE INSERT ON final_validations
    FOR EACH ROW EXECUTE FUNCTION final_validations_pass_requires_cdr();

CREATE FUNCTION final_validations_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'final validation records are append-only';
END $$;

CREATE TRIGGER final_validations_immutable
    BEFORE UPDATE ON final_validations
    FOR EACH ROW EXECUTE FUNCTION final_validations_immutable();

-- CDR output bytes are identified by hash; the hash can never be swapped.
CREATE FUNCTION artifacts_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.kind = 'CDR_OUTPUT'
       AND (NEW.sha256 <> OLD.sha256 OR NEW.storage_object <> OLD.storage_object OR NEW.kind <> OLD.kind) THEN
        RAISE EXCEPTION 'CDR output identity is immutable for %', OLD.id;
    END IF;
    IF NEW.state IN ('FINAL_VALIDATED', 'PUBLISHED') AND NOT EXISTS (
        SELECT 1 FROM final_validations f
         WHERE f.artifact_id = NEW.id AND f.verdict = 'PASS' AND f.output_sha256 = NEW.sha256) THEN
        RAISE EXCEPTION 'artifact % has no passing final validation', NEW.id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER artifacts_release_guard
    BEFORE INSERT OR UPDATE ON artifacts
    FOR EACH ROW EXECUTE FUNCTION artifacts_release_guard();

-- No status, policy or artifact state that implies release without evidence.
CREATE FUNCTION scan_jobs_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.status IN ('AWAITING_ARCHIVE', 'ARCHIVING', 'READY')
        OR NEW.policy = 'ALLOW_SANITIZED'
        OR NEW.artifact_state IN ('FINAL_VALIDATED', 'PUBLISHED'))
       AND NOT EXISTS (
        SELECT 1
          FROM final_validations f
          JOIN artifacts a ON a.id = f.artifact_id
         WHERE f.scan_id = NEW.id
           AND f.verdict = 'PASS'
           AND f.source_sha256 = NEW.source_sha256
           AND a.state IN ('FINAL_VALIDATED', 'PUBLISHED')
           AND a.sha256 = f.output_sha256) THEN
        RAISE EXCEPTION 'scan % cannot enter a release state without a passing final validation', NEW.id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER scan_jobs_release_guard
    BEFORE INSERT OR UPDATE ON scan_jobs
    FOR EACH ROW EXECUTE FUNCTION scan_jobs_release_guard();
