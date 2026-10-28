-- ============================================================
-- AI-Assisted Alt-Text Publishing Pipeline Schema
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Projects Table
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    source_storage_key TEXT NOT NULL,
    source_checksum TEXT NOT NULL,
    language_variant TEXT NOT NULL DEFAULT 'en-GB',
    status TEXT NOT NULL DEFAULT 'created',
    cleanup_status TEXT NOT NULL DEFAULT 'retained',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Image Assets Table
CREATE TABLE IF NOT EXISTS image_assets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    xref INTEGER NOT NULL,
    placement_index INTEGER NOT NULL,
    bbox_x0 REAL NOT NULL,
    bbox_y0 REAL NOT NULL,
    bbox_x1 REAL NOT NULL,
    bbox_y1 REAL NOT NULL,
    original_caption TEXT NOT NULL DEFAULT '',
    caption_position TEXT,
    needs_caption_review INTEGER NOT NULL DEFAULT 0,
    review_reasons TEXT NOT NULL DEFAULT '[]',
    suggested_filename TEXT NOT NULL,
    file_storage_key TEXT NOT NULL,
    file_checksum TEXT NOT NULL,
    image_format TEXT NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'extracted',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Alt Text Draft Versions Table
CREATE TABLE IF NOT EXISTS alt_text_versions (
    id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL REFERENCES image_assets(id) ON DELETE CASCADE,
    version_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    revision_instruction TEXT,
    prompt_version_id TEXT,
    provider TEXT,
    model TEXT,
    created_at TEXT NOT NULL
);

-- Approvals Table
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL REFERENCES image_assets(id) ON DELETE CASCADE,
    alt_text_version_id TEXT NOT NULL REFERENCES alt_text_versions(id) ON DELETE CASCADE,
    reviewer_id TEXT,
    approved_at TEXT NOT NULL
);

-- Jobs Table
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    image_id TEXT REFERENCES image_assets(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    page_start INTEGER,
    page_end INTEGER,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL
);

-- Exports Table
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    export_type TEXT NOT NULL,
    file_storage_key TEXT NOT NULL,
    file_checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    verified_at TEXT,
    delivered_at TEXT
);

-- Indexes for performance & quick foreign-key joins
CREATE INDEX IF NOT EXISTS idx_image_assets_project ON image_assets(project_id);
CREATE INDEX IF NOT EXISTS idx_image_assets_status ON image_assets(processing_status);
CREATE INDEX IF NOT EXISTS idx_alt_versions_image ON alt_text_versions(image_id);
CREATE INDEX IF NOT EXISTS idx_approvals_image ON approvals(image_id);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_exports_project ON exports(project_id);
