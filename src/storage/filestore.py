"""
Per-Project Isolated Temporary File Storage
------------------------------------------
Manages temporary file persistence for uploaded PDFs, extracted image assets,
intermediate diagnostics, and export artifacts.
Enforces safe path resolution and explicit cleanup safeguards.
"""

import os
import shutil
import hashlib
from pathlib import Path
from typing import Tuple, Optional


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


class FileStore:
    """
    Manages per-project isolated temporary storage on the filesystem.
    """

    def __init__(self, base_dir: str):
        self.base_dir = Path(os.path.abspath(base_dir))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_project_dir(self, project_id: str) -> Path:
        """Returns the canonical root directory for a project."""
        p_dir = (self.base_dir / project_id).resolve()
        if not str(p_dir).startswith(str(self.base_dir)):
            raise PathTraversalError(f"Invalid project ID traversal attempt: {project_id}")
        return p_dir

    def init_project_storage(self, project_id: str) -> Path:
        """Initializes standard directory layout for a project."""
        p_dir = self.get_project_dir(project_id)
        (p_dir / "source").mkdir(parents=True, exist_ok=True)
        (p_dir / "images").mkdir(parents=True, exist_ok=True)
        (p_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (p_dir / "exports").mkdir(parents=True, exist_ok=True)
        return p_dir

    def save_source_pdf(
        self,
        project_id: str,
        source_path_or_bytes: Path | str | bytes,
        original_filename: str = "source.pdf"
    ) -> Tuple[str, str, int]:
        """
        Stores source PDF under project's source directory using a safe internal name.
        Returns: (relative_storage_key, sha256_checksum, byte_size)
        """
        p_dir = self.init_project_storage(project_id)
        safe_name = "source.pdf"
        target_path = p_dir / "source" / safe_name

        if isinstance(source_path_or_bytes, bytes):
            data = source_path_or_bytes
            target_path.write_bytes(data)
            checksum = calculate_sha256(data)
            size = len(data)
        else:
            src = Path(source_path_or_bytes)
            if not src.exists():
                raise FileNotFoundError(f"Source file not found: {src}")
            shutil.copy2(src, target_path)
            checksum = calculate_file_sha256(str(target_path))
            size = target_path.stat().st_size

        rel_key = f"source/{safe_name}"
        return rel_key, checksum, size

    def save_source_pdf_from_file(
        self,
        project_id: str,
        file_path: Path | str,
        original_filename: str = "source.pdf",
    ) -> Tuple[str, str, int]:
        """
        Store a source PDF from a file path on disk.
        The file is copied from the temp path to project storage.
        Returns (relative_storage_key, sha256_checksum, byte_size).
        """
        p_dir = self.init_project_storage(project_id)
        # Sanitize filename: only keep the basename, remove path components
        safe_name = Path(original_filename).name
        if not safe_name or safe_name in (".", ".."):
            safe_name = "source.pdf"
        target_path = p_dir / "source" / safe_name

        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"Source file not found: {src}")

        shutil.copy2(src, target_path)
        checksum = calculate_file_sha256(str(target_path))
        size = target_path.stat().st_size

        rel_key = f"source/{safe_name}"
        return rel_key, checksum, size

    def save_image(
        self,
        project_id: str,
        image_id: str,
        image_bytes: bytes,
        ext: str
    ) -> Tuple[str, str, int]:
        """
        Stores an extracted image binary under project's images directory.
        Uses the immutable image UUID for the filename to guarantee collision freedom.
        Returns: (relative_storage_key, sha256_checksum, byte_size)
        """
        p_dir = self.init_project_storage(project_id)
        clean_ext = ext.lower().lstrip(".")
        filename = f"{image_id}.{clean_ext}"
        target_path = p_dir / "images" / filename

        target_path.write_bytes(image_bytes)
        checksum = calculate_sha256(image_bytes)
        size = len(image_bytes)

        rel_key = f"images/{filename}"
        return rel_key, checksum, size

    def save_export(
        self,
        project_id: str,
        export_id: str,
        export_bytes: bytes,
        filename: str
    ) -> Tuple[str, str, int]:
        """
        Stores a generated export file (DOCX or ZIP) under exports directory.
        Returns: (relative_storage_key, sha256_checksum, byte_size)
        """
        p_dir = self.init_project_storage(project_id)
        # Sanitize filename
        safe_name = Path(filename).name
        target_path = p_dir / "exports" / f"{export_id}_{safe_name}"

        target_path.write_bytes(export_bytes)
        checksum = calculate_sha256(export_bytes)
        size = len(export_bytes)

        rel_key = f"exports/{target_path.name}"
        return rel_key, checksum, size

    def resolve_path(self, project_id: str, relative_key: str) -> Path:
        """
        Safely resolves a relative storage key within the project's directory.
        Prevents path traversal attacks.
        """
        p_dir = self.get_project_dir(project_id)
        target = (p_dir / relative_key).resolve()

        if not str(target).startswith(str(p_dir)):
            raise PathTraversalError(f"Unsafe path traversal detected: {relative_key}")

        return target

    def read_bytes(self, project_id: str, relative_key: str) -> bytes:
        """Reads file bytes for a verified project storage key."""
        path = self.resolve_path(project_id, relative_key)
        if not path.exists():
            raise FileNotFoundError(f"Storage asset not found: {relative_key}")
        return path.read_bytes()

    def head(self, project_id: str, relative_key: str):
        """Return local file metadata for the same verification interface as Blob."""
        path = self.resolve_path(project_id, relative_key)
        if not path.is_file():
            raise FileNotFoundError(f"Storage asset not found: {relative_key}")
        content_type = "application/pdf" if path.suffix.lower() == ".pdf" else None
        return {"pathname": f"{project_id}/{relative_key}", "size": path.stat().st_size,
                "content_type": content_type, "etag": None}

    read_file = read_bytes

    def cleanup_project(
        self,
        project_id: str,
        export_verified_and_delivered: bool = False,
        project_cancelled: bool = False,
        active_jobs_running: bool = False
    ) -> bool:
        """
        Cleans up temporary files for a project with strict policy safeguards:
        - NEVER deletes if active jobs are running.
        - NEVER deletes unless export is verified & delivered OR project is cancelled.
        - NEVER affects neighboring project directories.
        """
        if active_jobs_running:
            raise CleanupBlockedError(
                f"Cannot clean up project {project_id}: active worker jobs are still running."
            )

        if not (export_verified_and_delivered or project_cancelled):
            raise CleanupBlockedError(
                f"Cannot clean up project {project_id}: export has not been verified and delivered, "
                "and project is not cancelled."
            )

        p_dir = self.get_project_dir(project_id)
        if p_dir.exists():
            shutil.rmtree(p_dir)
            return True
        return False
