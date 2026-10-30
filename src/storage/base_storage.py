"""
Storage Interface
-----------------
Abstract base for object storage backends. The application stores object
keys in PostgreSQL (not binary contents in database rows) and keeps binary
assets in either local filesystem storage (development) or Vercel Blob
(production).

All implementations must be project-isolated: a key for project A must never
be readable by project B.
"""

import hashlib
import os
from pathlib import Path
from typing import Tuple, Optional, Any


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class PathTraversalError(StorageError):
    """Raised when an unsafe path attempts to escape the project directory."""
    pass


class CleanupBlockedError(StorageError):
    """Raised when a cleanup is attempted on active or unverified project assets."""
    pass


def calculate_sha256(data_bytes: bytes) -> str:
    """Computes SHA-256 hash of bytes."""
    return hashlib.sha256(data_bytes).hexdigest()


def calculate_file_sha256(filepath: str) -> str:
    """Computes SHA-256 hash of a file on disk."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


class BaseStorage:
    """
    Abstract storage interface. Concrete implementations:

    - LocalFileStore (src/storage/filestore.py) - local disk, development
    - BlobStorage (src/storage/blob_storage.py) - Vercel Blob, production
    """

    def init_project_storage(self, project_id: str) -> Any:
        """Initialize standard directory layout for a project. Returns a project handle."""
        raise NotImplementedError

    def save_source_pdf(
        self,
        project_id: str,
        source_path_or_bytes: Path | str | bytes,
        original_filename: str = "source.pdf",
    ) -> Tuple[str, str, int]:
        """Store source PDF. Returns (relative_storage_key, sha256_checksum, byte_size)."""
        raise NotImplementedError

    def save_image(
        self,
        project_id: str,
        image_id: str,
        image_bytes: bytes,
        ext: str,
    ) -> Tuple[str, str, int]:
        """Store extracted image binary. Returns (relative_storage_key, sha256_checksum, byte_size)."""
        raise NotImplementedError

    def save_export(
        self,
        project_id: str,
        export_id: str,
        export_bytes: bytes,
        filename: str,
    ) -> Tuple[str, str, int]:
        """Store generated export file. Returns (relative_storage_key, sha256_checksum, byte_size)."""
        raise NotImplementedError

    def resolve_path(self, project_id: str, relative_key: str) -> Any:
        """Safely resolve a relative storage key within the project's namespace."""
        raise NotImplementedError

    def read_bytes(self, project_id: str, relative_key: str) -> bytes:
        """Read file bytes for a verified project storage key."""
        raise NotImplementedError

    def cleanup_project(
        self,
        project_id: str,
        export_verified_and_delivered: bool = False,
        project_cancelled: bool = False,
        active_jobs_running: bool = False,
    ) -> bool:
        """Clean up temporary files for a project with policy safeguards."""
        raise NotImplementedError

    def get_project_dir(self, project_id: str) -> Any:
        """Return a project directory handle (Path for local, str prefix for Blob)."""
        raise NotImplementedError

    def exists(self, project_id: str, relative_key: str) -> bool:
        """Check whether a stored object exists."""
        raise NotImplementedError
