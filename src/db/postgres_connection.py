"""
PostgreSQL Database Connection
------------------------------
Provides a connection interface compatible with the existing SQLite
DatabaseConnection, using psycopg2 against a managed PostgreSQL instance.

Key differences from SQLite:
  - Uses %s parameter placeholders (psycopg2 style)
  - Returns RealDictRow rows (dict-like access by column name)
  - No PRAGMA statements (foreign keys are always enforced in PostgreSQL)
  - JSONB columns for structured metadata (review_reasons, details, etc.)
  - UUID primary keys (application-generated, no SERIAL dependency)
  - TIMESTAMPTZ for timestamps
"""

import os
from contextlib import contextmanager
from typing import Generator, Optional, Any

import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as pg_connection


class PostgresConnection:
    """
    Manages PostgreSQL connections with dict-row access and transaction
    context management, mirroring the SQLite DatabaseConnection interface.
    """

    def __init__(self, database_url: Optional[str] = None):
        self.database_url = (
            database_url
            or os.environ.get("DATABASE_URL")
            or os.environ.get("NEON_DB_URL")
            or os.environ.get("POSTGRES_URL")
        )
        if not self.database_url:
            raise ValueError(
                "DATABASE_URL, NEON_DB_URL, or POSTGRES_URL is required for PostgreSQL connections. "
                "Set it to your PostgreSQL connection string."
            )

    def get_connection(self) -> pg_connection:
        """Create a configured connection with RealDictCursor row factory."""
        conn = psycopg2.connect(
            self.database_url,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        conn.autocommit = False
        return conn

    @contextmanager
    def session(self) -> Generator[pg_connection, None, None]:
        """
        Context manager for automatic commit and rollback.

        Usage:
            with db.session() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT ...", (...))
        """
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute_script(self, sql: str) -> None:
        """
        Execute a multi-statement SQL script. psycopg2 does not provide
        executescript, so we split on semicolons and execute each statement.

        NOTE: This is a best-effort splitter. It does not handle semicolons
        inside string literals or dollar-quoted blocks. For production
        migrations, each migration step should be executed as a separate
        statement via execute().
        """
        with self.session() as conn:
            with conn.cursor() as cur:
                for statement in sql.split(";"):
                    stmt = statement.strip()
                    if stmt:
                        cur.execute(stmt)
