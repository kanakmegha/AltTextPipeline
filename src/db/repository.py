"""
Database Repository Layer
-------------------------
Type-safe, relational data access repository for projects, image assets,
draft versions, approvals, background jobs, and exports.
"""

import json
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from .connection import DatabaseConnection


def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class Repository:
    """
    CRUD repository operating over SQLite via DatabaseConnection.
    """

    def __init__(self, db: DatabaseConnection):
        self.db = db

    # ============================================================
    # Projects
    # ============================================================

    def create_project(
        self,
        project_id: str,
        name: str,
        source_filename: str,
        source_storage_key: str,
        source_checksum: str,
        language_variant: str = "en-GB",
        status: str = "created"
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, name, source_filename, source_storage_key,
                    source_checksum, language_variant, status,
                    cleanup_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'retained', ?, ?);
                """,
                (
                    project_id, name, source_filename, source_storage_key,
                    source_checksum, language_variant, status, now, now
                )
            )
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM projects WHERE id = ?;", (project_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_project_status(
        self,
        project_id: str,
        status: Optional[str] = None,
        cleanup_status: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        fields = []
        values = []
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if cleanup_status is not None:
            fields.append("cleanup_status = ?")
            values.append(cleanup_status)
        if not fields:
            return self.get_project(project_id)

        fields.append("updated_at = ?")
        values.append(now)
        values.append(project_id)

        query = f"UPDATE projects SET {', '.join(fields)} WHERE id = ?;"
        with self.db.session() as conn:
            conn.execute(query, tuple(values))

        return self.get_project(project_id)

    def list_projects(self) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM projects ORDER BY created_at DESC;")
            return [dict(r) for r in cursor.fetchall()]

    # ============================================================
    # Image Assets
    # ============================================================

    def create_image_asset(
        self,
        image_id: str,
        project_id: str,
        page_number: int,
        xref: int,
        placement_index: int,
        bbox: Dict[str, float],
        original_caption: str,
        caption_position: Optional[str],
        needs_caption_review: bool,
        review_reasons: List[str],
        suggested_filename: str,
        file_storage_key: str,
        file_checksum: str,
        image_format: str,
        processing_status: str = "extracted"
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        reasons_json = json.dumps(review_reasons or [])
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO image_assets (
                    id, project_id, page_number, xref, placement_index,
                    bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                    original_caption, caption_position, needs_caption_review,
                    review_reasons, suggested_filename, file_storage_key,
                    file_checksum, image_format, processing_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    image_id, project_id, page_number, xref, placement_index,
                    bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"],
                    original_caption, caption_position, 1 if needs_caption_review else 0,
                    reasons_json, suggested_filename, file_storage_key,
                    file_checksum, image_format, processing_status, now, now
                )
            )
        return self.get_image_asset(image_id)

    def get_image_asset(self, image_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM image_assets WHERE id = ?;", (image_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["needs_caption_review"] = bool(data["needs_caption_review"])
            data["review_reasons"] = json.loads(data["review_reasons"] or "[]")
            data["bbox"] = {
                "x0": data.pop("bbox_x0"),
                "y0": data.pop("bbox_y0"),
                "x1": data.pop("bbox_x1"),
                "y1": data.pop("bbox_y1"),
            }
            return data

    def list_project_images(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM image_assets
                WHERE project_id = ?
                ORDER BY page_number ASC, placement_index ASC;
                """,
                (project_id,)
            )
            images = []
            for row in cursor.fetchall():
                data = dict(row)
                data["needs_caption_review"] = bool(data["needs_caption_review"])
                data["review_reasons"] = json.loads(data["review_reasons"] or "[]")
                data["bbox"] = {
                    "x0": data.pop("bbox_x0"),
                    "y0": data.pop("bbox_y0"),
                    "x1": data.pop("bbox_x1"),
                    "y1": data.pop("bbox_y1"),
                }
                images.append(data)
            return images

    def update_image_status(
        self,
        image_id: str,
        processing_status: str
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                "UPDATE image_assets SET processing_status = ?, updated_at = ? WHERE id = ?;",
                (processing_status, now, image_id)
            )
        return self.get_image_asset(image_id)

    # ============================================================
    # Alt Text Versions
    # ============================================================

    def create_alt_text_version(
        self,
        version_id: str,
        image_id: str,
        version_number: int,
        text: str,
        revision_instruction: Optional[str] = None,
        prompt_version_id: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO alt_text_versions (
                    id, image_id, version_number, text, revision_instruction,
                    prompt_version_id, provider, model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    version_id, image_id, version_number, text, revision_instruction,
                    prompt_version_id, provider, model, now
                )
            )
        return self.get_alt_text_version(version_id)

    def get_alt_text_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM alt_text_versions WHERE id = ?;", (version_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_image_versions(self, image_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM alt_text_versions WHERE image_id = ? ORDER BY version_number ASC;",
                (image_id,)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_latest_version(self, image_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM alt_text_versions WHERE image_id = ? ORDER BY version_number DESC LIMIT 1;",
                (image_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ============================================================
    # Approvals
    # ============================================================

    def create_approval(
        self,
        approval_id: str,
        image_id: str,
        alt_text_version_id: str,
        reviewer_id: Optional[str] = None
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO approvals (id, image_id, alt_text_version_id, reviewer_id, approved_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (approval_id, image_id, alt_text_version_id, reviewer_id, now)
            )
            # Update image processing status to approved
            conn.execute(
                "UPDATE image_assets SET processing_status = 'approved', updated_at = ? WHERE id = ?;",
                (now, image_id)
            )

        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM approvals WHERE id = ?;", (approval_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_approval_for_image(self, image_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM approvals WHERE image_id = ? ORDER BY approved_at DESC LIMIT 1;",
                (image_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ============================================================
    # Jobs
    # ============================================================

    def create_job(
        self,
        job_id: str,
        project_id: str,
        job_type: str,
        image_id: Optional[str] = None,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
        max_attempts: int = 3
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, project_id, image_id, job_type, page_start, page_end,
                    status, attempt_count, max_attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?);
                """,
                (job_id, project_id, image_id, job_type, page_start, page_end, max_attempts, now)
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM jobs WHERE id = ?;", (job_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_job_status(
        self,
        job_id: str,
        status: str,
        increment_attempt: bool = False,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        job = self.get_job(job_id)
        if not job:
            return None

        attempts = job["attempt_count"] + (1 if increment_attempt else 0)
        started_at = job["started_at"] or (now if status == "running" else None)
        completed_at = now if status in {"completed", "failed", "cancelled"} else None

        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE jobs SET
                    status = ?,
                    attempt_count = ?,
                    error_message = ?,
                    started_at = COALESCE(started_at, ?),
                    completed_at = ?
                WHERE id = ?;
                """,
                (status, attempts, error_message, started_at, completed_at, job_id)
            )
        return self.get_job(job_id)

    def list_project_jobs(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at ASC;", (project_id,))
            return [dict(r) for r in cursor.fetchall()]

    # ============================================================
    # Exports
    # ============================================================

    def create_export(
        self,
        export_id: str,
        project_id: str,
        export_type: str,
        file_storage_key: str,
        file_checksum: str,
        status: str = "pending"
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO exports (
                    id, project_id, export_type, file_storage_key,
                    file_checksum, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (export_id, project_id, export_type, file_storage_key, file_checksum, status, now)
            )
        return self.get_export(export_id)

    def get_export(self, export_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM exports WHERE id = ?;", (export_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_export_status(
        self,
        export_id: str,
        status: str,
        mark_verified: bool = False,
        mark_delivered: bool = False
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        with self.db.session() as conn:
            verified_clause = ", verified_at = ?" if mark_verified else ""
            delivered_clause = ", delivered_at = ?" if mark_delivered else ""

            params = [status]
            if mark_verified:
                params.append(now)
            if mark_delivered:
                params.append(now)
            params.append(export_id)

            query = f"UPDATE exports SET status = ?{verified_clause}{delivered_clause} WHERE id = ?;"
            conn.execute(query, tuple(params))

        return self.get_export(export_id)
