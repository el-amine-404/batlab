-- What the notifier told about each file (a message when it arrives, one when it is done),
-- so a restart repeats nothing. Files already there when this was added count as told.
CREATE TABLE pdf_notifications (
    scan_id varchar(31) PRIMARY KEY REFERENCES scan_jobs(id) ON DELETE CASCADE,
    received_at timestamptz,
    done_at timestamptz
);
INSERT INTO pdf_notifications(scan_id, received_at, done_at) SELECT id, now(), now() FROM scan_jobs;
