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
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Tuple, Optional, Any, List, Dict, Sequence


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class PathTraversalError(StorageError):
    """Raised when an unsafe path attempts to escape the project directory."""
    pass


class CleanupBlockedError(StorageError):
    """Raised when a cleanup is attempted on active or unverified project assets."""
    pass


STANDARD_STORAGE_DIRS = frozenset({"source", "images", "artifacts", "exports"})
DEFAULT_MAX_UPLOAD_MB = 500


def get_max_upload_bytes() -> int:
    """
    Authoritative maximum PDF upload/object size in bytes (default: 500 MB).
    Reads MAX_UPLOAD_MB (preferred) or MAX_OBJECT_BYTES (legacy fallback).
    """
    raw_mb = os.environ.get("MAX_UPLOAD_MB")
    if raw_mb is not None and str(raw_mb).strip():
        mb = int(str(raw_mb).strip())
        if mb <= 0:
            raise ValueError("MAX_UPLOAD_MB must be positive.")
        return mb * 1024 * 1024

    raw_bytes = os.environ.get("MAX_OBJECT_BYTES")
    if raw_bytes is not None and str(raw_bytes).strip():
        size = int(str(raw_bytes).strip())
        if size <= 0:
            raise ValueError("MAX_OBJECT_BYTES must be positive.")
        return size

    return DEFAULT_MAX_UPLOAD_MB * 1024 * 1024


def validate_storage_key(project_id: str, relative_key: str) -> str:
    """
    Validates that relative_key is safe and belongs strictly to project_id.

    Returns the normalized relative key within the project (e.g. 'images/img.png').
    Raises PathTraversalError if unsafe, cross-project, or attempting directory traversal.
    """
    if not isinstance(project_id, str) or not project_id.strip():
        raise PathTraversalError("Project ID must be a non-empty string.")

    project_id = project_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", project_id):
        raise PathTraversalError(f"Invalid project ID: {project_id}")

    if not isinstance(relative_key, str) or not relative_key.strip():
        raise PathTraversalError("Storage key must be a non-empty string.")

    normalized = relative_key.replace("\\", "/").strip()
    if normalized.startswith("/"):
        raise PathTraversalError(f"Absolute storage paths are forbidden: {relative_key}")

    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PathTraversalError(f"Unsafe storage key: {relative_key}")

    # If key starts with this project's ID:
    if parts[0] == project_id:
        if len(parts) < 2:
            raise PathTraversalError(
                f"Storage key cannot target the project root directory: {relative_key}"
            )
        parts = parts[1:]

    # Key must now target one of the standard project subdirectories
    if not parts or parts[0] not in STANDARD_STORAGE_DIRS:
        raise PathTraversalError(
            f"Cross-project access forbidden or invalid root directory: key '{relative_key}' does not belong to project '{project_id}'"
        )

    return "/".join(parts)


@dataclass
class DeletionResult:
    """
    Structured outcome of an object deletion operation.
    - deleted: list of relative keys that existed and were successfully removed.
    - missing: list of relative keys not found (already deleted or never created); treated idempotently as success.
    - failed: mapping of relative key -> error message for keys that encountered an error during deletion.
    """
    deleted: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """True if no objects failed deletion."""
        return len(self.failed) == 0

    @property
    def has_failures(self) -> bool:
        """True if any objects failed deletion."""
        return len(self.failed) > 0

    @property
    def total_processed(self) -> int:
        return len(self.deleted) + len(self.missing) + len(self.failed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deleted": list(self.deleted),
            "missing": list(self.missing),
            "failed": dict(self.failed),
            "is_success": self.is_success,
            "total_processed": self.total_processed,
        }


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

    def download_to_file(
        self,
        project_id: str,
        relative_key: str,
        destination_path: str,
    ) -> Tuple[int, str]:
        """
        Stream a stored object into destination_path without materializing the full
        payload in memory. Cleans up incomplete destination_path on error.
        Returns: (byte_size, sha256_checksum)
        """
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

    def delete_object(self, project_id: str, relative_key: str) -> DeletionResult:
        """
        Delete a single object identified by relative_key within project_id.
        Idempotent: if the object does not exist, it is recorded in result.missing.
        Validates project_id and key to prevent cross-project or path traversal access.
        """
        raise NotImplementedError

    def delete_objects(self, project_id: str, relative_keys: Sequence[str]) -> DeletionResult:
        """
        Delete multiple objects identified by relative_keys within project_id.
        Idempotent: missing objects are listed in result.missing.
        Failed deletions are recorded in result.failed with error details.
        """
        raise NotImplementedError

    def list_project_keys(self, project_id: str) -> List[str]:
        """List all relative storage keys owned by project_id."""
        return []
