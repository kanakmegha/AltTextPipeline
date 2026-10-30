"""
Database Schema Migrations (PostgreSQL)
---------------------------------------
Handles initialization and incremental schema migrations for PostgreSQL.

Adapted from src/db/migrations.py (SQLite). Key differences:
  - Uses information_schema.columns instead of PRAGMA table_info
  - Executes migration SQL statement-by-statement (no executescript)
  - Uses ALTER TABLE ... ADD COLUMN IF NOT EXISTS (PostgreSQL 9.6+)
  - Boolean flags are native BOOLEAN (not INTEGER 0/1)
  - Timestamps are TIMESTAMPTZ with server-side defaults
"""

from datetime import datetime, timezone
from pathlib import Path
from .postgres_connection import PostgresConnection


SCHEMA_FILE = Path(__file__).parent / "postgres_schema.sql"


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check whether a column exists in a PostgreSQL table."""
    with conn.session() as c:
        with c.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s;
                """,
                (table_name, column_name),
            )
            return cur.fetchone() is not None


def _execute_sql_script(conn, sql: str) -> None:
    """Execute a multi-statement SQL script, stripping comments and empties."""
    cleaned_lines = []
    for line in sql.split("\n"):
        stripped = line.strip()
        # Skip full-line comments
        if stripped.startswith("--"):
            continue
        # Remove inline comments (everything after --)
        if "--" in line and not stripped.startswith("--"):
            line = line.split("--", 1)[0]
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    with conn.session() as c:
        with c.cursor() as cur:
            for statement in cleaned.split(";"):
                stmt = statement.strip()
                if stmt:
                    cur.execute(stmt)


def _current_version(conn) -> int:
    """Return the current schema migration version."""
    try:
        with conn.session() as c:
            with c.cursor() as cur:
                cur.execute("SELECT MAX(version) FROM schema_migrations;")
                row = cur.fetchone()
                return int(row[0]) if (row and row[0] is not None) else 0
    except Exception:
        # schema_migrations table doesn't exist yet (fresh database)
        return 0


def _record_version(conn, version: int) -> None:
    """Record a completed migration version."""
    with conn.session() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s);",
                (version, datetime.now(timezone.utc).isoformat()),
            )


def apply_migrations(db: PostgresConnection) -> int:
    """
    Applies baseline schema and any incremental migrations.
    Returns the current migration version.
    """
    current_version = _current_version(db)

    # Migration 1: Baseline schema
    if current_version < 1:
        schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
        _execute_sql_script(db, schema_sql)
        _record_version(db, 1)
        current_version = 1

    # Migration 2: AI Generation audit columns and ai_usage_ledger table
    if current_version < 2:
        with db.session() as c:
            with c.cursor() as cur:
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
                    if not _column_exists(db, "alt_text_versions", col_name):
                        cur.execute(
                            f"ALTER TABLE alt_text_versions ADD COLUMN IF NOT EXISTS {col_name} {col_def};"
                        )

                cur.execute(
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
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ai_usage_project ON ai_usage_ledger(project_id);"
                )
        _record_version(db, 2)
        current_version = 2

    # Migration 3: Figure Groups & Multi-Image Caption Association (Phase 4.1)
    if current_version < 3:
        with db.session() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS figure_groups (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        caption_text TEXT NOT NULL,
                        caption_page_number INTEGER NOT NULL,
                        caption_position TEXT,
                        figure_label TEXT,
                        is_cross_page BOOLEAN NOT NULL DEFAULT FALSE,
                        review_status TEXT NOT NULL DEFAULT 'candidate',
                        review_reasons TEXT NOT NULL DEFAULT '[]',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_figure_groups_project ON figure_groups(project_id);"
                )

                image_group_cols = [
                    ("group_id", "TEXT REFERENCES figure_groups(id) ON DELETE SET NULL"),
                    ("association_type", "TEXT NOT NULL DEFAULT 'single'"),
                    ("association_confidence", "REAL NOT NULL DEFAULT 1.0"),
                    ("panel_label", "TEXT"),
                ]
                for col_name, col_def in image_group_cols:
                    if not _column_exists(db, "image_assets", col_name):
                        cur.execute(
                            f"ALTER TABLE image_assets ADD COLUMN IF NOT EXISTS {col_name} {col_def};"
                        )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_image_assets_group ON image_assets(group_id);"
                )
        _record_version(db, 3)
        current_version = 3

    # Migration 4: Audit Events Table (Editor Workflow)
    if current_version < 4:
        with db.session() as c:
            with c.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                        event_type TEXT NOT NULL,
                        target_type TEXT NOT NULL,
                        target_id TEXT NOT NULL,
                        details TEXT NOT NULL DEFAULT '{}',
                        actor_id TEXT DEFAULT 'editor',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_audit_events_project ON audit_events(project_id);"
                )
        _record_version(db, 4)
        current_version = 4

    # Migration 5: Add figure_count column to exports table and backfill
    if current_version < 5:
        with db.session() as c:
            with c.cursor() as cur:
                if not _column_exists(db, "exports", "figure_count"):
                    cur.execute(
                        "ALTER TABLE exports ADD COLUMN IF NOT EXISTS figure_count INTEGER NOT NULL DEFAULT 0;"
                    )
                # Backfill existing export figure_counts from audit_events
                cur.execute(
                    """
                    SELECT target_id, details FROM audit_events
                    WHERE target_type = 'export' AND event_type = 'docx_exported';
                    """
                )
                import json

                for row in cur.fetchall():
                    target_id = row["target_id"]
                    details_str = row["details"]
                    try:
                        details = json.loads(details_str) if isinstance(details_str, str) else details_str or {}
                        fc = details.get("figure_count")
                        if fc is not None:
                            cur.execute(
                                "UPDATE exports SET figure_count = %s WHERE id = %s;",
                                (int(fc), target_id),
                            )
                    except Exception:
                        pass
        _record_version(db, 5)
        current_version = 5

    return current_version
