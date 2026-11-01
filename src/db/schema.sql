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
    owner_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Figure Groups Table (Supporting multi-image shared captions and cross-page figures)
CREATE TABLE IF NOT EXISTS figure_groups (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    caption_text TEXT NOT NULL,
    caption_page_number INTEGER NOT NULL,
    caption_position TEXT,
    figure_label TEXT,
    is_cross_page INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'candidate',
    review_reasons TEXT NOT NULL DEFAULT '[]',
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
    group_id TEXT REFERENCES figure_groups(id) ON DELETE SET NULL,
    association_type TEXT NOT NULL DEFAULT 'single',
    association_confidence REAL NOT NULL DEFAULT 1.0,
    panel_label TEXT,
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
    language TEXT NOT NULL DEFAULT 'en-GB',
    prompt_text TEXT,
    prompt_hash TEXT,
    caption_snapshot TEXT,
    context_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'success',
    error_message TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    duration_seconds REAL NOT NULL DEFAULT 0.0,
    provider_request_id TEXT,
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
    total_pages INTEGER NOT NULL DEFAULT 0,
    completed_pages INTEGER NOT NULL DEFAULT 0,
    pages_to_process INTEGER NOT NULL DEFAULT 0,
    total_batches INTEGER NOT NULL DEFAULT 0,
    completed_batches INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_batches (
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    batch_index INTEGER NOT NULL,
    page_numbers TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    lease_updated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, batch_index)
);

-- Exports Table
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    export_type TEXT NOT NULL,
    file_storage_key TEXT NOT NULL,
    file_checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    figure_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    verified_at TEXT,
    delivered_at TEXT
);

-- AI Usage Ledger Table (Auditing token consumption and spend per project)
CREATE TABLE IF NOT EXISTS ai_usage_ledger (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    image_id TEXT REFERENCES image_assets(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL
);

-- Audit Events Table (Tracking editor decisions, groupings, and caption changes)
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    actor_id TEXT DEFAULT 'editor',
    created_at TEXT NOT NULL
);

-- Indexes for performance & quick foreign-key joins
CREATE INDEX IF NOT EXISTS idx_image_assets_project ON image_assets(project_id);
CREATE INDEX IF NOT EXISTS idx_image_assets_status ON image_assets(processing_status);
CREATE INDEX IF NOT EXISTS idx_alt_versions_image ON alt_text_versions(image_id);
CREATE INDEX IF NOT EXISTS idx_approvals_image ON approvals(image_id);
CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_exports_project ON exports(project_id);
CREATE INDEX IF NOT EXISTS idx_ai_usage_project ON ai_usage_ledger(project_id);
CREATE INDEX IF NOT EXISTS idx_figure_groups_project ON figure_groups(project_id);
CREATE INDEX IF NOT EXISTS idx_image_assets_group ON image_assets(group_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_project ON audit_events(project_id);
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id);
