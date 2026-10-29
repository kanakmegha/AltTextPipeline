"""
Database Schema Migrations
--------------------------
Handles initialization and incremental schema migrations.
"""

from datetime import datetime, timezone
from pathlib import Path
from .connection import DatabaseConnection


SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def apply_migrations(db: DatabaseConnection) -> int:
    """
    Applies baseline schema and any incremental migrations.
    Returns the current migration version.
    """
    with db.session() as conn:
        # 1. Ensure migrations table exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )

        cursor = conn.execute("SELECT MAX(version) FROM schema_migrations;")
        row = cursor.fetchone()
        current_version = row[0] if (row and row[0] is not None) else 0

        # Migration 1: Baseline schema
        if current_version < 1:
            schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
            conn.executescript(schema_sql)

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (1, now_iso)
            )
            current_version = 1

        # Migration 2: AI Generation audit columns and ai_usage_ledger table
        if current_version < 2:
            # Check existing columns in alt_text_versions
            cursor = conn.execute("PRAGMA table_info(alt_text_versions);")
            existing_cols = {col[1] for col in cursor.fetchall()}

            new_columns = [
                ("language", "TEXT NOT NULL DEFAULT 'en-GB'"),
                ("prompt_text", "TEXT"),
                ("prompt_hash", "TEXT"),
                ("caption_snapshot", "TEXT"),
                ("context_snapshot", "TEXT"),
                ("status", "TEXT NOT NULL DEFAULT 'success'"),
                ("error_message", "TEXT"),
                ("input_tokens", "INTEGER NOT NULL DEFAULT 0"),
                ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
                ("estimated_cost_usd", "REAL NOT NULL DEFAULT 0.0"),
                ("duration_seconds", "REAL NOT NULL DEFAULT 0.0"),
                ("provider_request_id", "TEXT"),
            ]

            for col_name, col_def in new_columns:
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE alt_text_versions ADD COLUMN {col_name} {col_def};")

            # Create ai_usage_ledger table
            conn.execute(
                """
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
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_project ON ai_usage_ledger(project_id);")

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (2, now_iso)
            )
            current_version = 2

        # Migration 3: Figure Groups & Multi-Image Caption Association (Phase 4.1)
        if current_version < 3:
            # 1. Create figure_groups table
            conn.execute(
                """
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
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_figure_groups_project ON figure_groups(project_id);")

            # 2. Add group association columns to image_assets if missing
            cursor = conn.execute("PRAGMA table_info(image_assets);")
            existing_image_cols = {col[1] for col in cursor.fetchall()}

            image_group_cols = [
                ("group_id", "TEXT REFERENCES figure_groups(id) ON DELETE SET NULL"),
                ("association_type", "TEXT NOT NULL DEFAULT 'single'"),
                ("association_confidence", "REAL NOT NULL DEFAULT 1.0"),
                ("panel_label", "TEXT"),
            ]

            for col_name, col_def in image_group_cols:
                if col_name not in existing_image_cols:
                    conn.execute(f"ALTER TABLE image_assets ADD COLUMN {col_name} {col_def};")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_image_assets_group ON image_assets(group_id);")

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (3, now_iso)
            )
            current_version = 3

        # Migration 4: Audit Events Table (Editor Workflow)
        if current_version < 4:
            conn.execute(
                """
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
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_project ON audit_events(project_id);")

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (4, now_iso)
            )
            current_version = 4

        # Migration 5: Add figure_count column to exports table and backfill existing records
        if current_version < 5:
            cursor = conn.execute("PRAGMA table_info(exports);")
            existing_export_cols = {col[1] for col in cursor.fetchall()}
            if "figure_count" not in existing_export_cols:
                conn.execute("ALTER TABLE exports ADD COLUMN figure_count INTEGER NOT NULL DEFAULT 0;")

            # Backfill existing export figure_counts from audit_events if available
            cursor = conn.execute(
                "SELECT target_id, details FROM audit_events WHERE target_type = 'export' AND event_type = 'docx_exported';"
            )
            for row in cursor.fetchall():
                target_id, details_str = row[0], row[1]
                try:
                    import json
                    details = json.loads(details_str)
                    fc = details.get("figure_count")
                    if fc is not None:
                        conn.execute("UPDATE exports SET figure_count = ? WHERE id = ?;", (int(fc), target_id))
                except Exception:
                    pass

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (5, now_iso)
            )
            current_version = 5

    return current_version
