"""
Vercel Blob Storage Implementation
----------------------------------
Production storage backend for AltTextPipeline on Vercel. Stores all binary
assets (source PDFs, extracted images, generated DOCX exports) in private
Vercel Blob buckets, with object keys recorded in PostgreSQL.

Uses the Vercel Blob REST API via httpx (already a project dependency).
No binary content is ever stored in database rows.

Blob key layout (project-isolated):
  {project_id}/source/source.pdf
  {project_id}/images/{image_id}.{ext}
  {project_id}/exports/{export_id}_{filename}
"""

import hashlib
import logging
from pathlib import Path
from typing import Tuple, Optional
import httpx

from .base_storage import (
    BaseStorage,
    StorageError,
    PathTraversalError,
    CleanupBlockedError,
    calculate_sha256,
)

logger = logging.getLogger("alttext.storage.blob")

# Maximum upload size per object (50 MB). Vercel Blob supports larger, but
# we cap at 50 MB to stay within reasonable function memory limits.
MAX_OBJECT_BYTES = 50 * 1024 * 1024


class BlobStorage(BaseStorage):
    """
    Vercel Blob-backed storage. All operations go through the Vercel Blob
    REST API using server-side BLOB_READ_WRITE_TOKEN.
    """

    def __init__(
        self,
        blob_token: str,
        blob_store_id: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        if not blob_token:
            raise StorageError("BLOB_READ_WRITE_TOKEN is required for BlobStorage")
        self.blob_token = blob_token
        self.blob_store_id = blob_store_id
        self.base_url = base_url or "https://blob.vercel-storage.com"
        self._client = httpx.Client(timeout=60.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _object_url(self, key: str) -> str:
        """Build the public object URL for a blob key."""
        store = self.blob_store_id or ""
        if store:
            return f"{self.base_url}/{store}/{key}"
        # Fallback: use the blob.vercel-storage.com API with x-blob-store-id header
        return f"{self.base_url}/{key}"

    def _headers(self, content_type: str = "application/octet-stream") -> dict:
        headers = {
            "Authorization": f"Bearer {self.blob_token}",
            "x-blob-content-type": content_type,
            "Content-Type": content_type,
        }
        if self.blob_store_id:
            headers["x-blob-store-id"] = self.blob_store_id
        return headers

    @staticmethod
    def _safe_key(project_id: str, relative_key: str) -> str:
        """Build a project-isolated blob key and validate it is safe."""
        # Normalize and reject path traversal
        key = str(Path(project_id) / relative_key).replace("\\", "/")
        if ".." in key.split("/") or key.startswith("/") or key.startswith("../"):
            raise PathTraversalError(f"Unsafe storage key: {relative_key}")
        return key

    def _validate_bytes(self, data: bytes, label: str) -> None:
        if not data:
            raise StorageError(f"Empty bytes provided for {label}")
        if len(data) > MAX_OBJECT_BYTES:
            raise StorageError(
                f"{label} exceeds maximum object size ({len(data)} bytes > {MAX_OBJECT_BYTES})"
            )

    # ------------------------------------------------------------------
    # BaseStorage interface
    # ------------------------------------------------------------------

    def init_project_storage(self, project_id: str) -> str:
        """Blob is flat; no directory creation needed. Return project prefix."""
        prefix = f"{project_id}/"
        logger.debug("Initialized Blob project prefix: %s", prefix)
        return prefix

    def save_source_pdf(
        self,
        project_id: str,
        source_path_or_bytes: Path | str | bytes,
        original_filename: str = "source.pdf",
    ) -> Tuple[str, str, int]:
        key = self._safe_key(project_id, f"source/{original_filename}")

        if isinstance(source_path_or_bytes, bytes):
            data = source_path_or_bytes
        else:
            src = Path(source_path_or_bytes)
            if not src.exists():
                raise FileNotFoundError(f"Source file not found: {src}")
            data = src.read_bytes()

        self._validate_bytes(data, "source PDF")
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
        clean_ext = ext.lower().lstrip(".")
        filename = f"{image_id}.{clean_ext}"
        key = self._safe_key(project_id, f"images/{filename}")

        self._validate_bytes(image_bytes, f"image {image_id}")
        checksum = calculate_sha256(image_bytes)
        size = len(image_bytes)

        content_type = f"image/{clean_ext}" if clean_ext != "jpg" else "image/jpeg"
        self._upload_bytes(key, image_bytes, content_type)
        return key, checksum, size

    def save_export(
        self,
        project_id: str,
        export_id: str,
        export_bytes: bytes,
        filename: str,
    ) -> Tuple[str, str, int]:
        safe_name = Path(filename).name
        key = self._safe_key(project_id, f"exports/{export_id}_{safe_name}")

        self._validate_bytes(export_bytes, "DOCX export")
        checksum = calculate_sha256(export_bytes)
        size = len(export_bytes)

        self._upload_bytes(key, export_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        return key, checksum, size

    def _upload_bytes(self, key: str, data: bytes, content_type: str) -> None:
        url = self._object_url(key)
        try:
            resp = self._client.put(url, content=data, headers=self._headers(content_type))
            if resp.status_code not in (200, 201):
                raise StorageError(
                    f"Blob upload failed for {key}: HTTP {resp.status_code} {resp.text[:200]}"
                )
        except httpx.HTTPError as exc:
            raise StorageError(f"Blob upload error for {key}: {exc}") from exc

    def resolve_path(self, project_id: str, relative_key: str) -> str:
        """Return the blob key after safety validation (no local path)."""
        key = self._safe_key(project_id, relative_key)
        return key

    def read_bytes(self, project_id: str, relative_key: str) -> bytes:
        key = self._safe_key(project_id, relative_key)
        url = self._object_url(key)
        try:
            resp = self._client.get(url, headers=self._headers())
            if resp.status_code == 404:
                raise FileNotFoundError(f"Storage asset not found: {relative_key}")
            if resp.status_code != 200:
                raise StorageError(
                    f"Blob download failed for {key}: HTTP {resp.status_code} {resp.text[:200]}"
                )
            return resp.content
        except httpx.HTTPError as exc:
            if isinstance(exc, StorageError):
                raise
            raise StorageError(f"Blob download error for {key}: {exc}") from exc

    read_file = read_bytes

    def exists(self, project_id: str, relative_key: str) -> bool:
        key = self._safe_key(project_id, relative_key)
        url = self._object_url(key)
        try:
            resp = self._client.head(url, headers=self._headers())
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def cleanup_project(
        self,
        project_id: str,
        export_verified_and_delivered: bool = False,
        project_cancelled: bool = False,
        active_jobs_running: bool = False,
    ) -> bool:
        if active_jobs_running:
            raise CleanupBlockedError(
                f"Cannot clean up project {project_id}: active worker jobs are still running."
            )
        if not (export_verified_and_delivered or project_cancelled):
            raise CleanupBlockedError(
                f"Cannot clean up project {project_id}: export has not been verified and delivered, "
                "and project is not cancelled."
            )

        prefix = f"{project_id}/"
        deleted = 0
        try:
            # List objects with this prefix
            list_url = f"{self.base_url}/?storeId={self.blob_store_id}&prefix={prefix}"
            resp = self._client.get(list_url, headers=self._headers())
            if resp.status_code == 200:
                data = resp.json()
                blobs = data.get("blobs", [])
                for blob in blobs:
                    blob_url = blob.get("url") or blob.get("pathname")
                    if blob_url:
                        # Extract the key from the URL
                        key = blob_url.split(f"{self.base_url}/")[-1]
                        del_url = self._object_url(key)
                        del_resp = self._client.delete(del_url, headers=self._headers())
                        if del_resp.status_code in (200, 204):
                            deleted += 1
        except Exception as exc:
            logger.warning("Blob cleanup partial failure for %s: %s", project_id, exc)

        logger.info("Cleaned up %d objects for project %s", deleted, project_id)
        return True

    def get_project_dir(self, project_id: str) -> str:
        """Return the project prefix (Blob has no real directory)."""
        return f"{project_id}/"

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
