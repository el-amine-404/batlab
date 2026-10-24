-- PostgreSQL Schema for PDF Pipeline

CREATE TABLE IF NOT EXISTS documents (
    sha256 CHAR(64) PRIMARY KEY,
    original_filename VARCHAR(255) NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    
    -- OCR & Language Metrics
    is_native_vector BOOLEAN DEFAULT FALSE,
    ocr_applied BOOLEAN DEFAULT FALSE,
    detected_languages VARCHAR(64) DEFAULT 'eng+fra+ara',
    dictionary_validity_ratio REAL DEFAULT 0.0,
    noise_ratio REAL DEFAULT 0.0,
    needs_vision_review BOOLEAN DEFAULT FALSE,
    
    -- Catalog & AI Metadata
    verified_title TEXT,
    verified_author TEXT,
    domain VARCHAR(64),
    subdomain VARCHAR(128),
    tags JSONB DEFAULT '[]'::jsonb,
    metadata_payload JSONB DEFAULT '{}'::jsonb,
    
    -- Routing & Ingestion
    target_system VARCHAR(32), -- 'paperless' | 'calibre' | 'archive'
    lab2_dispatch_id VARCHAR(128),
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Index 1: Instant O(1) Deduplication
CREATE UNIQUE INDEX IF NOT EXISTS idx_docs_sha256 ON documents(sha256);

-- Index 2: State Machine Status Tracking
CREATE INDEX IF NOT EXISTS idx_docs_status ON documents(status);

-- Index 3: High-Speed Night Vision Queue (Partial Index - Zero cost during daytime)
CREATE INDEX IF NOT EXISTS idx_docs_night_vision ON documents(sha256) 
WHERE needs_vision_review = TRUE;

-- Index 4: GIN Index on JSONB Tags for instant search
CREATE INDEX IF NOT EXISTS idx_docs_tags_gin ON documents USING GIN (tags);

-- Index 5: GIN Index on JSONB Payload
CREATE INDEX IF NOT EXISTS idx_docs_payload_gin ON documents USING GIN (metadata_payload);
