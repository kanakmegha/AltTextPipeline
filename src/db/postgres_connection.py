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
import threading
from contextlib import contextmanager
from typing import Generator, Optional, Any

import psycopg2
import psycopg2.extensions
import psycopg2.extras
import psycopg2.pool
from psycopg2.extensions import connection as pg_connection


class PostgresConnection:
    """
    Manages PostgreSQL connections with thread-safe, fork-safe connection pooling,
    dict-row access, and transaction context management, mirroring the SQLite
    DatabaseConnection interface.
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        minconn: Optional[int] = None,
        maxconn: Optional[int] = None,
    ):
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
        raw_min = minconn if minconn is not None else int(os.environ.get("PG_POOL_MIN_CONN", "1"))
        raw_max = maxconn if maxconn is not None else int(os.environ.get("PG_POOL_MAX_CONN", "5"))
        self.minconn = max(1, int(raw_min))
        self.maxconn = max(self.minconn, min(20, int(raw_max)))
        self._pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
        self._pool_pid: Optional[int] = None
        self._pool_lock = threading.Lock()

    def _get_pool(self) -> psycopg2.pool.ThreadedConnectionPool:
        """
        Return the process-local connection pool, lazily creating it if needed.
        If the current PID differs from the PID that created the pool (after fork),
        discard the inherited reference without closing the parent's sockets.
        """
        pid = os.getpid()
        with self._pool_lock:
            if self._pool is None or self._pool_pid != pid:
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    self.minconn,
                    self.maxconn,
                    self.database_url,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
                self._pool_pid = pid
            return self._pool

    def close_pool(self) -> None:
        """Close all pooled connections created by the current process."""
        pid = os.getpid()
        with self._pool_lock:
            if self._pool is not None and self._pool_pid == pid:
                try:
                    self._pool.closeall()
                finally:
                    self._pool = None
                    self._pool_pid = None
            elif self._pool is not None:
                self._pool = None
                self._pool_pid = None

    close = close_pool

    def get_connection(self) -> pg_connection:
        """Create a configured standalone connection with RealDictCursor row factory."""
        conn = psycopg2.connect(
            self.database_url,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        conn.autocommit = False
        return conn

    @contextmanager
    def session(self) -> Generator[pg_connection, None, None]:
        """
        Context manager for pooled connections with automatic commit, rollback,
        and broken-connection disposal.
        """
        pool = self._get_pool()
        conn = pool.getconn()
        if getattr(conn, "closed", 0):
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        conn.autocommit = False

        discard = False
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                if not getattr(conn, "closed", 0):
                    conn.rollback()
                else:
                    discard = True
            except Exception:
                discard = True
            raise
        finally:
            if not discard and not getattr(conn, "closed", 0):
                try:
                    if conn.status != psycopg2.extensions.STATUS_READY:
                        conn.rollback()
                except Exception:
                    discard = True
            else:
                discard = True
            pool.putconn(conn, close=discard)

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
