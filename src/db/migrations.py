"""
Database Schema Migrations
--------------------------
Handles initialization and incremental schema migrations.
"""

import os
from datetime import datetime, timezone
from pathlib import Path
from .connection import DatabaseConnection


SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def apply_migrations(db: DatabaseConnection) -> int:
    """
    Applies baseline schema if not already applied.
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
        current_version = cursor.fetchone()[0] or 0

        if current_version < 1:
            schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
            conn.executescript(schema_sql)

            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?);",
                (1, now_iso)
            )
            current_version = 1

    return current_version
