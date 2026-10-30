"""
Database Connection Factory
---------------------------
Selects between SQLite (local development) and PostgreSQL (Vercel production)
database connections based on the configured backend.

Both backends expose the same interface:
  - get_connection() -> connection object
  - session() -> context manager with automatic commit/rollback
"""

from typing import Optional, Any
from .connection import DatabaseConnection as SQLiteConnection
from .postgres_connection import PostgresConnection


def create_connection(
    backend: str = "sqlite",
    db_path: Optional[str] = None,
    database_url: Optional[str] = None,
) -> Any:
    """
    Create a database connection for the specified backend.

    Parameters
    ----------
    backend : str
        "sqlite" for local development, "postgres" for Vercel production.
    db_path : str, optional
        Path to the SQLite database file (used when backend="sqlite").
    database_url : str, optional
        PostgreSQL connection string (used when backend="postgres").

    Returns
    -------
    A connection object implementing the shared DatabaseConnection interface.
    """
    if backend == "postgres":
        if not database_url:
            database_url = os_get("DATABASE_URL")
        if not database_url:
            raise ValueError(
                "DATABASE_URL is required when backend='postgres'. "
                "Set it to your Vercel Postgres connection string."
            )
        return PostgresConnection(database_url=database_url)

    if backend == "sqlite":
        if not db_path:
            db_path = os_get("DB_PATH", default="./data/pipeline.db")
        return SQLiteConnection(db_path=db_path)

    raise ValueError(f"Unknown database backend: {backend!r}. Use 'sqlite' or 'postgres'.")


def os_get(key: str, default: Optional[str] = None) -> Optional[str]:
    """Read an environment variable (kept local to avoid import cycles)."""
    import os

    return os.environ.get(key, default)
