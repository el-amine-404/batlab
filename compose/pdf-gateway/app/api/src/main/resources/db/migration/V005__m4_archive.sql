-- Operational index of the portable archive. The archive's sidecars are the
-- source of truth; these rows are rebuildable from them (pdf-archive reindex).

CREATE TABLE archive_documents (
    document_id varchar(30) PRIMARY KEY CHECK (document_id ~ '^doc-[0-9A-HJKMNP-TV-Z]{26}$'),
    scan_id varchar(31) UNIQUE REFERENCES scan_jobs(id) ON DELETE SET NULL,
    archive_path varchar(240) NOT NULL UNIQUE,
    pdf_file varchar(240) NOT NULL,
    title varchar(200) NOT NULL,
    category varchar(32) NOT NULL,
    review_state varchar(32) NOT NULL,
    current_version integer NOT NULL CHECK (current_version > 0),
    metadata_revision integer NOT NULL CHECK (metadata_revision > 0),
    sha256 char(64) NOT NULL,
    bytes bigint NOT NULL CHECK (bytes > 0),
    sidecar jsonb NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE archive_versions (
    document_id varchar(30) NOT NULL REFERENCES archive_documents(document_id) ON DELETE CASCADE,
    artifact_version integer NOT NULL CHECK (artifact_version > 0),
    artifact_id varchar(80) REFERENCES artifacts(id),
    sha256 char(64) NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (document_id, artifact_version)
);

CREATE INDEX archive_documents_category_idx ON archive_documents (category, review_state);

-- A scan is READY, and an artifact PUBLISHED, only when the archive index holds
-- exactly the bytes that passed final validation. (PL/pgSQL resolves NEW fields
-- per table, so each table has its own function.)
CREATE FUNCTION scan_jobs_archive_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.status = 'READY' OR NEW.artifact_state = 'PUBLISHED') AND NOT EXISTS (
        SELECT 1 FROM archive_documents d
          JOIN archive_versions v ON v.document_id = d.document_id AND v.artifact_version = d.current_version
          JOIN final_validations f ON f.artifact_id = v.artifact_id AND f.verdict = 'PASS' AND f.output_sha256 = v.sha256
         WHERE d.scan_id = NEW.id AND d.sha256 = v.sha256) THEN
        RAISE EXCEPTION 'scan % is not published in the archive index', NEW.id;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION artifacts_archive_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state = 'PUBLISHED' AND NOT EXISTS (
        SELECT 1 FROM archive_versions v WHERE v.artifact_id = NEW.id AND v.sha256 = NEW.sha256) THEN
        RAISE EXCEPTION 'artifact % has no archive version', NEW.id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER scan_jobs_archive_guard
    BEFORE INSERT OR UPDATE ON scan_jobs
    FOR EACH ROW EXECUTE FUNCTION scan_jobs_archive_guard();

CREATE TRIGGER artifacts_archive_guard
    BEFORE INSERT OR UPDATE ON artifacts
    FOR EACH ROW EXECUTE FUNCTION artifacts_archive_guard();
