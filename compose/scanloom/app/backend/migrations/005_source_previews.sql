-- Pages that reused earlier work before this release may lack the photo the crop editor shows.
INSERT INTO jobs (project_id, page_id, kind, priority)
    SELECT project_id, id, 'derive_page', -10 FROM pages
    WHERE source_preview_object IS NULL AND normalized_object IS NOT NULL AND result_object IS NOT NULL
    ON CONFLICT DO NOTHING;
