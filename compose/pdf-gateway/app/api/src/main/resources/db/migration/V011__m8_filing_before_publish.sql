-- The AI files a document before it is archived: a SUGGEST job may exist for a
-- scan that has no archive document yet. It carries the text it may read
-- (bounded, the same untrusted text the rules see) and a deadline after which
-- the archive stops waiting for it. The job's own document link is set once the
-- document is published. Security state is still never touched by a job.

ALTER TABLE enrichment_jobs ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE enrichment_jobs ADD COLUMN scan_id varchar(31) REFERENCES scan_jobs(id) ON DELETE CASCADE;
ALTER TABLE enrichment_jobs ADD COLUMN context jsonb;
ALTER TABLE enrichment_jobs ADD COLUMN deadline_at timestamptz;
-- The checked fields the archive applies (what survived validation of the result).
ALTER TABLE enrichment_jobs ADD COLUMN applied jsonb;
ALTER TABLE enrichment_jobs ADD CONSTRAINT enrichment_jobs_subject_check CHECK (document_id IS NOT NULL OR scan_id IS NOT NULL);
ALTER TABLE enrichment_jobs ADD CONSTRAINT enrichment_jobs_context_size CHECK (context IS NULL OR octet_length(context::text) <= 65536);
CREATE UNIQUE INDEX enrichment_jobs_scan_kind_idx ON enrichment_jobs (scan_id, kind) WHERE scan_id IS NOT NULL;
