"""
Storage Factory
---------------
Selects between local filesystem storage (development) and Vercel Blob
storage (production) based on environment configuration.
"""

import os
from typing import Any, Optional
from .base_storage import BaseStorage
from .blob_storage import BlobStorage


def resolve_storage_backend(backend: Optional[str] = None) -> str:
    """Resolve an explicit backend, deployment setting, or safe local default."""
    if os.environ.get("VERCEL", "").strip().lower() in {"1", "true", "yes"}:
        return "blob"

    configured = backend or os.environ.get("STORAGE_BACKEND")
    if configured:
        return configured.strip().lower()
    return "local"


def create_storage(
    backend: Optional[str] = None,
    base_dir: Optional[str] = None,
    blob_token: Optional[str] = None,
    blob_store_id: Optional[str] = None,
) -> BaseStorage:
    """
    Create a storage backend instance.

    Parameters
    ----------
    backend : str, optional
        "local" for filesystem storage, "blob" for Vercel Blob.
        If omitted, uses STORAGE_BACKEND, selects "blob" on Vercel, and
        otherwise defaults to "local".
    base_dir : str, optional
        Base directory for local storage (defaults to STORAGE_DIR env or ./data/storage).
    blob_token : str, optional
        Vercel Blob read/write token (defaults to BLOB_READ_WRITE_TOKEN env).
    blob_store_id : str, optional
        Vercel Blob store ID (defaults to BLOB_STORE_ID env).

    Returns
    -------
    A BaseStorage implementation.
    """
    backend = resolve_storage_backend(backend)

    if backend == "blob":
        token = blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN")
        if not token:
            raise ValueError(
                "BLOB_READ_WRITE_TOKEN is required for backend='blob'. "
                "Set it to your Vercel Blob read/write token."
            )
        store_id = blob_store_id or os.environ.get("BLOB_STORE_ID")
        return BlobStorage(blob_token=token, blob_store_id=store_id)

    if backend == "local":
        from .filestore import FileStore

        directory = base_dir or os.environ.get("STORAGE_DIR", "./data/storage")
        return FileStore(base_dir=directory)

    raise ValueError(
        f"Unknown storage backend: {backend!r}. Use 'local' or 'blob'."
    )
