"""
Database Connection Manager
---------------------------
Provides thread-safe connection handling for SQLite with WAL mode, foreign keys,
and row factory mapping. Portable architecture designed for easy migration to PostgreSQL.
"""

import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager
from typing import Generator


class DatabaseConnection:
    """
    Manages SQLite database connections with foreign key enforcement and WAL mode.
    """

    def __init__(self, db_path: str):
        self.db_path = str(Path(os.path.abspath(db_path)))
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Creates a configured connection with row dictionary access."""
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        conn.row_factory = sqlite3.Row
        # Enable foreign key support and Write-Ahead Logging
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    @contextmanager
    def session(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for automatic commit and rollback."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
