-- Paperless is a projection of the archive. One row per archived document that
-- should exist in Paperless. Exactly-once relies on a marker stored in Paperless
-- (custom field gateway_document_id) plus the recorded upload/task state here.
CREATE TABLE paperless_imports (
    document_id varchar(30) PRIMARY KEY REFERENCES archive_documents(document_id) ON DELETE CASCADE,
    status varchar(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'IMPORTING', 'IMPORTED', 'FAILED_RETRYABLE', 'FAILED_FINAL')),
    artifact_sha256 char(64) NOT NULL,
    upload_started_at timestamptz,
    task_id varchar(100),
    paperless_document_id integer,
    synced_revision integer,
    attempts integer NOT NULL DEFAULT 0,
    last_error varchar(200),
    requested_by uuid REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status <> 'IMPORTED' OR paperless_document_id IS NOT NULL)
);
CREATE INDEX paperless_imports_status_idx ON paperless_imports (status);

-- Only documents that are actually in the archive can be projected.
CREATE FUNCTION paperless_imports_require_archive() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM archive_documents d WHERE d.document_id = NEW.document_id AND d.sha256 = NEW.artifact_sha256) THEN
        RAISE EXCEPTION 'paperless import for % does not match an archived artifact', NEW.document_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER paperless_imports_require_archive
    BEFORE INSERT OR UPDATE OF artifact_sha256 ON paperless_imports
    FOR EACH ROW EXECUTE FUNCTION paperless_imports_require_archive();
