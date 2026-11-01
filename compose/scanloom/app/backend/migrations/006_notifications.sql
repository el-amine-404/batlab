-- What the admin is told: asked for and finished scans, in an outbox a small
-- service with network access delivers, so the image worker needs none.
ALTER TABLE projects ADD COLUMN owner_name varchar(160);
-- A scan was asked for and its end hasn't been told: an edit's render is no such end.
ALTER TABLE projects ADD COLUMN scan_pending boolean NOT NULL DEFAULT false;

CREATE TABLE notify_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    values jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    id bigserial PRIMARY KEY,
    project_id uuid REFERENCES projects(id) ON DELETE CASCADE,
    kind varchar(16) NOT NULL CHECK (kind IN ('requested', 'finished', 'test')),
    status varchar(12) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'failed', 'skipped')),
    title varchar(250),
    error varchar(500),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz
);
CREATE INDEX notifications_queue_idx ON notifications (available_at, id) WHERE status = 'queued';
CREATE INDEX notifications_recent_idx ON notifications (id DESC);

CREATE FUNCTION notifications_notify() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('scanloom_notify', '');
    RETURN NEW;
END $$;
CREATE TRIGGER notifications_notify AFTER INSERT ON notifications
    FOR EACH ROW EXECUTE FUNCTION notifications_notify();
