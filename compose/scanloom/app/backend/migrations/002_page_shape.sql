ALTER TABLE pages
    ADD COLUMN corner_radius real NOT NULL DEFAULT 0
    CHECK (corner_radius >= 0 AND corner_radius <= 0.25);
