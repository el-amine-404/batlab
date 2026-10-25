-- Embedded-metadata versions: a new unsigned PDF written from a validated CDR
-- output. It is a different set of bytes, so it needs its own final validation.
ALTER TABLE artifacts ADD COLUMN parent_artifact_id varchar(80) REFERENCES artifacts(id);

ALTER TABLE artifacts ADD CONSTRAINT artifacts_metadata_version_has_parent
    CHECK (kind <> 'EMBEDDED_METADATA' OR parent_artifact_id IS NOT NULL);

-- A PASS must describe either a successful CDR output of this scan's exact
-- source, or an embedded-metadata child of such an output that itself passed.
CREATE OR REPLACE FUNCTION final_validations_pass_requires_cdr() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.verdict <> 'PASS' THEN
        RETURN NEW;
    END IF;
    IF NEW.output_sha256 = NEW.source_sha256 OR NOT EXISTS (
        SELECT 1 FROM scan_jobs s WHERE s.id = NEW.scan_id AND s.source_sha256 = NEW.source_sha256) THEN
        RAISE EXCEPTION 'final validation PASS for % does not match the scan source', NEW.scan_id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM artifacts a
          JOIN cdr_results c ON c.output_artifact_id = a.id AND c.status = 'SUCCEEDED'
         WHERE a.id = NEW.artifact_id AND a.scan_id = NEW.scan_id AND a.kind = 'CDR_OUTPUT'
           AND a.sha256 = NEW.output_sha256 AND c.output_sha256 = NEW.output_sha256
           AND c.source_sha256 = NEW.source_sha256) THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1 FROM artifacts child
          JOIN artifacts parent ON parent.id = child.parent_artifact_id
          JOIN final_validations pf ON pf.artifact_id = parent.id AND pf.verdict = 'PASS' AND pf.output_sha256 = parent.sha256
          JOIN cdr_results c ON c.output_artifact_id = parent.id AND c.status = 'SUCCEEDED'
         WHERE child.id = NEW.artifact_id AND child.scan_id = NEW.scan_id AND child.kind = 'EMBEDDED_METADATA'
           AND child.sha256 = NEW.output_sha256 AND child.sha256 <> parent.sha256
           AND parent.kind = 'CDR_OUTPUT' AND c.source_sha256 = NEW.source_sha256) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'final validation PASS for % does not match a successful CDR output or its metadata version', NEW.scan_id;
END $$;

-- Metadata-version bytes are identified by hash, like CDR output.
CREATE OR REPLACE FUNCTION artifacts_release_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.kind IN ('CDR_OUTPUT', 'EMBEDDED_METADATA')
       AND (NEW.sha256 <> OLD.sha256 OR NEW.storage_object <> OLD.storage_object OR NEW.kind <> OLD.kind
            OR NEW.parent_artifact_id IS DISTINCT FROM OLD.parent_artifact_id) THEN
        RAISE EXCEPTION 'artifact identity is immutable for %', OLD.id;
    END IF;
    IF NEW.state IN ('FINAL_VALIDATED', 'PUBLISHED') AND NOT EXISTS (
        SELECT 1 FROM final_validations f
         WHERE f.artifact_id = NEW.id AND f.verdict = 'PASS' AND f.output_sha256 = NEW.sha256) THEN
        RAISE EXCEPTION 'artifact % has no passing final validation', NEW.id;
    END IF;
    RETURN NEW;
END $$;
