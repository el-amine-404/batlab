-- API 2: edits can be conditional on what the editor last saw; a batch export belongs
-- to nobody's scan, so deleting one of its scans leaves it.
ALTER TABLE projects ADD COLUMN rev bigint NOT NULL DEFAULT 1;
ALTER TABLE exports ALTER COLUMN project_id DROP NOT NULL;
ALTER TABLE exports ADD COLUMN owner varchar(80);
ALTER TABLE jobs ALTER COLUMN project_id DROP NOT NULL;
