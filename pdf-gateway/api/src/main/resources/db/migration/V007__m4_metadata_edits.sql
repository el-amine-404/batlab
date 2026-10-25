-- Sidecar edits requested through the API and applied by the archive worker,
-- the archive's only writer, with compare-and-swap on the sidecar revision.
CREATE TABLE metadata_edits (
    id uuid PRIMARY KEY,
    document_id varchar(30) NOT NULL REFERENCES archive_documents(document_id) ON DELETE CASCADE,
    actor_id uuid REFERENCES users(id),
    expected_revision integer NOT NULL CHECK (expected_revision > 0),
    changes jsonb NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'APPLIED', 'CONFLICT', 'INVALID')),
    result_revision integer,
    error_code varchar(48),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE INDEX metadata_edits_document_idx ON metadata_edits (document_id, created_at DESC);
