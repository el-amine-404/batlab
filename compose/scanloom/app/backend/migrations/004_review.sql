-- Photos can wait, checked and shown, until someone says to scan them.
ALTER TABLE pages ADD COLUMN hold boolean NOT NULL DEFAULT false;
ALTER TABLE pages DROP CONSTRAINT pages_status_check;
ALTER TABLE pages ADD CONSTRAINT pages_status_check
    CHECK (status IN ('queued', 'scanning', 'decoding', 'held', 'detecting', 'rendering', 'ready', 'blocked', 'failed'));
ALTER TABLE projects ADD COLUMN held_count integer NOT NULL DEFAULT 0;
ALTER TABLE projects DROP CONSTRAINT projects_status_check;
ALTER TABLE projects ADD CONSTRAINT projects_status_check
    CHECK (status IN ('empty', 'draft', 'queued', 'processing', 'ready', 'failed', 'deleting'));
ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check
    CHECK (kind IN ('prepare_page', 'process_page', 'render_page', 'derive_page', 'build_export', 'cleanup', 'remove_objects'));
CREATE INDEX pages_owner_hash_idx ON pages (source_sha256, status);

CREATE OR REPLACE FUNCTION project_refresh(target uuid) RETURNS void LANGUAGE sql AS $$
    UPDATE projects pr SET
        page_count = counts.total,
        completed_count = counts.ready,
        failed_count = counts.bad,
        warning_count = counts.warned,
        held_count = counts.waiting,
        status = CASE
            WHEN pr.status = 'deleting' THEN 'deleting'
            WHEN counts.total = 0 THEN 'empty'
            WHEN counts.ready + counts.bad + counts.idle < counts.total THEN 'processing'
            WHEN counts.ready = 0 AND counts.idle > 0 THEN 'draft'
            WHEN counts.ready = 0 THEN 'failed'
            ELSE 'ready' END,
        error = CASE WHEN counts.bad = 0 THEN NULL ELSE pr.error END,
        version = pr.version + 1
    FROM (
        SELECT count(*)::int AS total,
               (count(*) FILTER (WHERE status = 'ready'))::int AS ready,
               (count(*) FILTER (WHERE status IN ('failed', 'blocked')))::int AS bad,
               (count(*) FILTER (WHERE status = 'held'))::int AS idle,
               (count(*) FILTER (WHERE hold))::int AS waiting,
               (count(*) FILTER (WHERE jsonb_array_length(COALESCE(quality->'warnings', '[]'::jsonb)) > 0))::int AS warned
        FROM pages WHERE project_id = target
    ) counts
    WHERE pr.id = target;
$$;
