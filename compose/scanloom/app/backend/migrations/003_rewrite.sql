-- Scanloom 2: scans are kept until deleted, belong to someone, and report
-- their pipeline. Every change is additive or widens a check, and existing
-- scans survive: no scan keeps the expiry the first release gave it.

ALTER TABLE projects ALTER COLUMN expires_at DROP NOT NULL;
UPDATE projects SET expires_at = NULL WHERE status <> 'deleting';
ALTER TABLE projects ADD COLUMN owner varchar(80);
ALTER TABLE projects ADD COLUMN failed_count integer NOT NULL DEFAULT 0;
ALTER TABLE projects DROP CONSTRAINT projects_status_check;
ALTER TABLE projects ADD CONSTRAINT projects_status_check
    CHECK (status IN ('empty', 'queued', 'processing', 'ready', 'failed', 'deleting'));
CREATE INDEX projects_owner_created_idx ON projects (owner, created_at DESC);
CREATE INDEX projects_created_idx ON projects (created_at DESC);

ALTER TABLE pages ADD COLUMN thumb_object varchar(96);
ALTER TABLE pages ADD COLUMN preview_object varchar(96);
ALTER TABLE pages ADD COLUMN jpeg_object varchar(96);
ALTER TABLE pages ADD COLUMN source_preview_object varchar(96);
ALTER TABLE pages ADD COLUMN jpeg_bytes bigint;
ALTER TABLE pages ADD COLUMN stage_started_at timestamptz;
ALTER TABLE pages ADD COLUMN timings jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE pages ADD COLUMN reused_from uuid REFERENCES pages(id) ON DELETE SET NULL;
ALTER TABLE pages ADD COLUMN corners_hint jsonb;
-- Raised by every edit; a render only publishes if it saw the latest one.
ALTER TABLE pages ADD COLUMN edit_rev bigint NOT NULL DEFAULT 0;

ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('process_page', 'render_page', 'derive_page', 'build_export', 'cleanup', 'remove_objects'));
ALTER TABLE jobs ADD COLUMN priority smallint NOT NULL DEFAULT 0;
DROP INDEX jobs_claim_idx;
CREATE INDEX jobs_claim_idx ON jobs (priority DESC, available_at, id) WHERE status = 'queued';
-- A running render may be joined by one queued for a newer edit, never by two.
DROP INDEX jobs_one_page_work_idx;
CREATE UNIQUE INDEX jobs_one_page_work_idx ON jobs (page_id, kind)
    WHERE status = 'queued' AND page_id IS NOT NULL;
CREATE INDEX jobs_finished_idx ON jobs (finished_at) WHERE status IN ('done', 'failed');

ALTER TABLE exports DROP COLUMN token_hash;
ALTER TABLE exports ADD COLUMN cache_key char(64);
ALTER TABLE exports ADD COLUMN page_count integer NOT NULL DEFAULT 0;
CREATE INDEX exports_cache_idx ON exports (project_id, cache_key) WHERE status IN ('queued', 'building', 'ready');
ALTER TABLE project_events ADD COLUMN public_id varchar(32);
ALTER TABLE project_events ADD COLUMN owner varchar(80);
CREATE INDEX project_events_created_idx ON project_events (created_at);

-- A project's counts and status are always worked out from its pages, in one place.
CREATE FUNCTION project_refresh(target uuid) RETURNS void LANGUAGE sql AS $$
    UPDATE projects pr SET
        page_count = counts.total,
        completed_count = counts.ready,
        failed_count = counts.bad,
        warning_count = counts.warned,
        status = CASE
            WHEN pr.status = 'deleting' THEN 'deleting'
            WHEN counts.total = 0 THEN 'empty'
            WHEN counts.ready + counts.bad < counts.total THEN 'processing'
            WHEN counts.ready = 0 THEN 'failed'
            ELSE 'ready' END,
        error = CASE WHEN counts.bad = 0 THEN NULL ELSE pr.error END,
        version = pr.version + 1
    FROM (
        SELECT count(*)::int AS total,
               (count(*) FILTER (WHERE status = 'ready'))::int AS ready,
               (count(*) FILTER (WHERE status IN ('failed', 'blocked')))::int AS bad,
               (count(*) FILTER (WHERE jsonb_array_length(COALESCE(quality->'warnings', '[]'::jsonb)) > 0))::int AS warned
        FROM pages WHERE project_id = target
    ) counts
    WHERE pr.id = target;
$$;

-- Every event reaches the API's listeners when its transaction commits, which
-- then tell the browsers: no polling of this table by anyone.
CREATE FUNCTION project_events_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('scanloom_events', json_build_object(
        'project', NEW.public_id, 'owner', NEW.owner, 'event', NEW.event,
        'page', NEW.data->>'page_id')::text);
    RETURN NEW;
END $$;
CREATE TRIGGER project_events_notify AFTER INSERT ON project_events
    FOR EACH ROW EXECUTE FUNCTION project_events_notify();

SELECT project_refresh(id) FROM projects;

-- Pages made before this release have no thumbnails yet: the worker makes them,
-- at the lowest priority.
INSERT INTO jobs (project_id, page_id, kind, priority)
    SELECT project_id, id, 'derive_page', -10 FROM pages WHERE result_object IS NOT NULL;

-- A new job wakes the worker at once, instead of its next look.
CREATE FUNCTION jobs_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('scanloom_jobs', '');
    RETURN NEW;
END $$;
CREATE TRIGGER jobs_notify AFTER INSERT ON jobs
    FOR EACH ROW EXECUTE FUNCTION jobs_notify();

UPDATE project_events ev SET public_id = pr.public_id, owner = pr.owner FROM projects pr WHERE pr.id = ev.project_id;

-- The limits were set for short-lived, small scans; the settings of that time go.
UPDATE settings SET values = (values - 'retention_hours' - 'link_ttl_minutes' - 'preview_long_edge' - 'auto_accept_confidence')
    || '{"max_files_per_project": 500, "max_upload_bytes": 52428800, "max_pixels": 60000000}'::jsonb;
