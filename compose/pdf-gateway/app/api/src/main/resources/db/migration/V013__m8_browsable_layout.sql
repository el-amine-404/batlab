-- The archive is browsable: several documents share a folder (Section/Correspondent), so a
-- document is identified on disk by its folder and file name together, not its folder alone.
ALTER TABLE archive_documents DROP CONSTRAINT archive_documents_archive_path_key;
ALTER TABLE archive_documents ADD CONSTRAINT archive_documents_location_key UNIQUE (archive_path, pdf_file);
