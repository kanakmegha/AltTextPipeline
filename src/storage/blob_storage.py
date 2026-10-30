
"""
Vercel Blob Storage
-------------------
Private object storage for AltTextPipeline.

Uses the official vercel-blob Python SDK.
All objects are isolated under their project ID.

Key layout:
    {project_id}/source/{filename}
    {project_id}/images/{image_id}.{ext}
    {project_id}/exports/{export_id}_{filename}
"""

import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple

from vercel.blob import sync as blob
from vercel.blob.errors import BlobError, BlobNotFoundError

from .base_storage import (
    BaseStorage,
    StorageError,
    PathTraversalError,
    CleanupBlockedError,
    calculate_sha256,
)

logger = logging.getLogger("alttext.storage.blob")

MAX_OBJECT_BYTES = 50 * 1024 * 1024

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)


class BlobStorage(BaseStorage):
    """Private Vercel Blob implementation of BaseStorage."""

    def __init__(
        self,
        blob_token: str,
        blob_store_id: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        if not blob_token or not blob_token.strip():
            raise StorageError("BLOB_READ_WRITE_TOKEN is required.")

        # The official SDK resolves credentials from environment variables.
        configured_token = (
            os.environ.get("BLOB_READ_WRITE_TOKEN")
            or os.environ.get("VERCEL_BLOB_READ_WRITE_TOKEN")
        )

        if not configured_token:
            raise StorageError(
                "The Blob SDK requires BLOB_READ_WRITE_TOKEN "
                "or VERCEL_BLOB_READ_WRITE_TOKEN in the environment."
            )

        if configured_token.strip() != blob_token.strip():
            raise StorageError(
                "The supplied Blob token does not match the configured "
                "environment token."
            )

        self.blob_token = blob_token
        self.blob_store_id = blob_store_id

        # The SDK derives the store ID from a read-write token.
        self.base_url = base_url

    @staticmethod
    def _validate_project_id(project_id: str) -> str:
        if not isinstance(project_id, str):
            raise PathTraversalError("Project ID must be a string.")

        project_id = project_id.strip()

        if not project_id or not re.fullmatch(
            r"[A-Za-z0-9_-]+", project_id
        ):
            raise PathTraversalError("Invalid project ID.")

        return project_id

    @classmethod
    def _safe_key(cls, project_id: str, relative_key: str) -> str:
        """Create and validate a project-isolated Blob pathname."""

        project_id = cls._validate_project_id(project_id)

        if not isinstance(relative_key, str) or not relative_key:
            raise PathTraversalError("Storage key must be a non-empty string.")

        normalized = relative_key.replace("\\", "/")

        if normalized.startswith("/"):
            raise PathTraversalError("Absolute storage paths are forbidden.")

        parts = normalized.split("/")

        if any(part in ("", ".", "..") for part in parts):
            raise PathTraversalError(
                f"Unsafe storage key: {relative_key}"
            )

        return PurePosixPath(project_id, *parts).as_posix()

    @staticmethod
    def _safe_filename(filename: str) -> str:
        if not isinstance(filename, str) or not filename.strip():
            raise PathTraversalError("Filename cannot be empty.")

        filename = filename.replace("\\", "/")
        safe_name = filename.split("/")[-1]

        if safe_name in ("", ".", ".."):
            raise PathTraversalError("Invalid filename.")

        return safe_name

    @staticmethod
    def _validate_bytes(data: bytes, label: str) -> None:
        if not isinstance(data, bytes):
            raise StorageError(f"{label} must be supplied as bytes.")

        if not data:
            raise StorageError(f"Empty bytes provided for {label}.")

        if len(data) > MAX_OBJECT_BYTES:
            raise StorageError(
                f"{label} exceeds the maximum object size "
                f"of {MAX_OBJECT_BYTES} bytes."
            )

    @staticmethod
    def _sdk_error(operation: str, key: str, exc: Exception):
        """Translate SDK failures without hiding their underlying cause."""
        if isinstance(exc, BlobError):
            raise StorageError(
                f"Vercel Blob {operation} failed for {key}: {exc}"
            ) from exc

        raise StorageError(
            f"Unexpected Vercel Blob {operation} failure for {key}: {exc}"
        ) from exc

    def _upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        try:
            blob.put_blob(
                key,
                data,
                access="private",
                content_length=len(data),
                content_type=content_type,
                allow_overwrite=True,
            )
        except Exception as exc:
            self._sdk_error("upload", key, exc)

    def init_project_storage(self, project_id: str) -> str:
        project_id = self._validate_project_id(project_id)
        return f"{project_id}/"

    def save_source_pdf(
        self,
        project_id: str,
        source_path_or_bytes: Path | str | bytes,
        original_filename: str = "source.pdf",
    ) -> Tuple[str, str, int]:

        safe_name = self._safe_filename(original_filename)
        key = self._safe_key(
            project_id,
            f"source/{safe_name}",
        )

        if isinstance(source_path_or_bytes, bytes):
            data = source_path_or_bytes
        else:
            source_path = Path(source_path_or_bytes)

            if not source_path.is_file():
                raise FileNotFoundError(
                    f"Source file not found: {source_path}"
                )

            data = source_path.read_bytes()

        self._validate_bytes(data, "Source PDF")

        checksum = calculate_sha256(data)
        size = len(data)

        self._upload_bytes(key, data, "application/pdf")

        return key, checksum, size

    def save_image(
        self,
        project_id: str,
        image_id: str,
        image_bytes: bytes,
        ext: str,
    ) -> Tuple[str, str, int]:

        image_id = self._safe_filename(image_id)

        if not re.fullmatch(r"[A-Za-z0-9_-]+", image_id):
            raise PathTraversalError("Invalid image ID.")

        clean_ext = ext.lower().lstrip(".")

        if not re.fullmatch(r"[a-z0-9]+", clean_ext):
            raise PathTraversalError("Invalid image extension.")

        key = self._safe_key(
            project_id,
            f"images/{image_id}.{clean_ext}",
        )

        self._validate_bytes(image_bytes, f"Image {image_id}")

        content_type = (
            "image/jpeg" if clean_ext in ("jpg", "jpeg")
            else f"image/{clean_ext}"
        )

        checksum = calculate_sha256(image_bytes)
        size = len(image_bytes)

        self._upload_bytes(key, image_bytes, content_type)

        return key, checksum, size

    def save_export(
        self,
        project_id: str,
        export_id: str,
        export_bytes: bytes,
        filename: str,
    ) -> Tuple[str, str, int]:

        export_id = self._safe_filename(export_id)

        if not re.fullmatch(r"[A-Za-z0-9_-]+", export_id):
            raise PathTraversalError("Invalid export ID.")

        safe_name = self._safe_filename(filename)

        key = self._safe_key(
            project_id,
            f"exports/{export_id}_{safe_name}",
        )

        self._validate_bytes(export_bytes, "DOCX export")

        checksum = calculate_sha256(export_bytes)
        size = len(export_bytes)

        self._upload_bytes(key, export_bytes, DOCX_CONTENT_TYPE)

        return key, checksum, size

    def resolve_path(
        self,
        project_id: str,
        relative_key: str,
    ) -> str:
        return self._safe_key(project_id, relative_key)

    def read_bytes(
        self,
        project_id: str,
        relative_key: str,
    ) -> bytes:

        key = self._safe_key(project_id, relative_key)

        try:
            result = blob.get_blob(
                key,
                access="private",
            )
            return result.body

        except BlobNotFoundError as exc:
            raise FileNotFoundError(
                f"Storage asset not found: {relative_key}"
            ) from exc

        except Exception as exc:
            self._sdk_error("download", key, exc)

    read_file = read_bytes

    def exists(
        self,
        project_id: str,
        relative_key: str,
    ) -> bool:

        key = self._safe_key(project_id, relative_key)

        try:
            blob.head_blob(key)
            return True

        except BlobNotFoundError:
            return False

        except Exception as exc:
            self._sdk_error("existence check", key, exc)

    def cleanup_project(
        self,
        project_id: str,
        export_verified_and_delivered: bool = False,
        project_cancelled: bool = False,
        active_jobs_running: bool = False,
    ) -> bool:

        project_id = self._validate_project_id(project_id)

        if active_jobs_running:
            raise CleanupBlockedError(
                f"Cannot clean up project {project_id}: "
                "active jobs are still running."
            )

        if not (
            export_verified_and_delivered or project_cancelled
        ):
            raise CleanupBlockedError(
                f"Cannot clean up project {project_id}: "
                "export has not been verified and delivered, "
                "and the project is not cancelled."
            )

        prefix = f"{project_id}/"
        deleted = 0

        try:
            # query_blobs transparently follows pagination.
            for item in blob.query_blobs(
                prefix=prefix,
                page_size=1000,
            ):
                pathname = item.pathname

                # Never delete an object outside this project's prefix.
                if not pathname.startswith(prefix):
                    raise StorageError(
                        f"Blob listing returned an object outside "
                        f"project {project_id}: {pathname}"
                    )

                # Use ETag protection to avoid deleting a changed object.
                blob.delete_blob(
                    pathname,
                    if_match=item.etag,
                )

                deleted += 1

        except Exception as exc:
            logger.exception(
                "Blob cleanup failed for project %s after %d deletions",
                project_id,
                deleted,
            )
            raise StorageError(
                f"Project cleanup was incomplete for {project_id}. "
                f"{deleted} objects were deleted before failure: {exc}"
            ) from exc

        logger.info(
            "Deleted %d Blob objects for project %s",
            deleted,
            project_id,
        )

        return True

    def get_project_dir(self, project_id: str) -> str:
        project_id = self._validate_project_id(project_id)
        return f"{project_id}/"

    def close(self) -> None:
        """The SDK manages its own session and transport."""
        return None
