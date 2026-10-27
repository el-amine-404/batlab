CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    revision bigint NOT NULL DEFAULT 1,
    values jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE projects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id varchar(32) NOT NULL UNIQUE,
    title varchar(160) NOT NULL,
    profile varchar(24) NOT NULL CHECK (profile IN ('faithful', 'paper', 'mono')),
    status varchar(24) NOT NULL CHECK (status IN ('queued', 'processing', 'ready', 'failed', 'deleting')),
    page_count integer NOT NULL DEFAULT 0 CHECK (page_count >= 0),
    completed_count integer NOT NULL DEFAULT 0 CHECK (completed_count >= 0),
    warning_count integer NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    error varchar(500),
    version bigint NOT NULL DEFAULT 1,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX projects_expiry_idx ON projects (expires_at);
CREATE INDEX projects_status_created_idx ON projects (status, created_at DESC);

CREATE TABLE pages (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    position integer NOT NULL CHECK (position >= 0),
    original_name varchar(255) NOT NULL,
    source_object varchar(96) NOT NULL UNIQUE,
    source_sha256 char(64) NOT NULL,
    source_bytes bigint NOT NULL CHECK (source_bytes > 0),
    detected_mime varchar(40),
    width integer,
    height integer,
    corners jsonb,
    rotation smallint NOT NULL DEFAULT 0 CHECK (rotation IN (0, 90, 180, 270)),
    appearance varchar(24) NOT NULL DEFAULT 'auto' CHECK (appearance IN ('auto', 'color', 'gray', 'mono')),
    confidence real,
    quality jsonb NOT NULL DEFAULT '{}'::jsonb,
    integrity jsonb NOT NULL DEFAULT '{}'::jsonb,
    malware_status varchar(16) NOT NULL DEFAULT 'pending' CHECK (malware_status IN ('pending', 'clean', 'blocked', 'error')),
    status varchar(24) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'scanning', 'decoding', 'detecting', 'rendering', 'ready', 'blocked', 'failed')),
    normalized_object varchar(96),
    result_object varchar(96),
    result_sha256 char(64),
    result_bytes bigint,
    error varchar(500),
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_id, position)
);
CREATE INDEX pages_project_order_idx ON pages (project_id, position);
CREATE INDEX pages_status_idx ON pages (status) WHERE status NOT IN ('ready', 'blocked', 'failed');
CREATE INDEX pages_source_hash_idx ON pages (source_sha256);

CREATE TABLE jobs (
    id bigserial PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    page_id uuid REFERENCES pages(id) ON DELETE CASCADE,
    kind varchar(24) NOT NULL CHECK (kind IN ('process_page', 'render_page', 'build_export', 'cleanup')),
    status varchar(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    worker_id varchar(80),
    error varchar(500),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz
);
CREATE INDEX jobs_claim_idx ON jobs (available_at, id)
    WHERE status = 'queued';
CREATE INDEX jobs_lease_idx ON jobs (lease_until)
    WHERE status = 'running';
CREATE UNIQUE INDEX jobs_one_page_work_idx ON jobs (page_id, kind)
    WHERE status IN ('queued', 'running') AND page_id IS NOT NULL;

CREATE TABLE exports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind varchar(16) NOT NULL CHECK (kind IN ('image', 'zip', 'pdf')),
    status varchar(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'building', 'ready', 'failed')),
    token_hash char(64) NOT NULL UNIQUE,
    object_name varchar(96),
    filename varchar(255),
    bytes bigint,
    sha256 char(64),
    error varchar(500),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz
);
CREATE INDEX exports_project_created_idx ON exports (project_id, created_at DESC);
CREATE INDEX exports_expiry_idx ON exports (expires_at);

CREATE TABLE project_events (
    seq bigserial PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event varchar(40) NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX project_events_stream_idx ON project_events (project_id, seq);

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END $$;
CREATE TRIGGER projects_touch BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER pages_touch BEFORE UPDATE ON pages
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
