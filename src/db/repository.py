"""
Database Repository Layer
-------------------------
Type-safe, relational data access repository for projects, image assets,
draft versions, approvals, background jobs, and exports.
"""

import json
import hashlib
import secrets
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from .connection import DatabaseConnection


def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Password & session token hashing (stdlib, no extra dependency)
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return dk.hex(), salt.hex()


def _verify_password(password: str, expected_hash_hex: str, salt_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return secrets.compare_digest(dk.hex(), expected_hash_hex)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
        processing_status: str = "extracted",
        group_id: Optional[str] = None,
        association_type: str = "single",
        association_confidence: float = 1.0,
        panel_label: Optional[str] = None,
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
                    group_id, association_type, association_confidence, panel_label,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    image_id, project_id, page_number, xref, placement_index,
                    bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"],
                    original_caption, caption_position, 1 if needs_caption_review else 0,
                    reasons_json, suggested_filename, file_storage_key,
                    file_checksum, image_format, processing_status,
                    group_id, association_type, float(association_confidence), panel_label,
                    now, now
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
    # Figure Groups (Phase 4.1)
    # ============================================================

    def create_figure_group(
        self,
        group_id: str,
        project_id: str,
        caption_text: str,
        caption_page_number: int,
        caption_position: Optional[str] = "below",
        figure_label: Optional[str] = None,
        is_cross_page: bool = False,
        review_status: str = "candidate",
        review_reasons: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        reasons_json = json.dumps(review_reasons or [])
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO figure_groups (
                    id, project_id, caption_text, caption_page_number,
                    caption_position, figure_label, is_cross_page,
                    review_status, review_reasons, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    group_id, project_id, caption_text, caption_page_number,
                    caption_position, figure_label, 1 if is_cross_page else 0,
                    review_status, reasons_json, now, now
                )
            )
        return self.get_figure_group(group_id)

    def get_figure_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM figure_groups WHERE id = ?;", (group_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["is_cross_page"] = bool(data["is_cross_page"])
            data["review_reasons"] = json.loads(data["review_reasons"] or "[]")
            img_cursor = conn.execute(
                """
                SELECT id, page_number, suggested_filename, panel_label, original_caption,
                       caption_position, needs_caption_review, review_reasons,
                       association_confidence, file_storage_key
                FROM image_assets
                WHERE group_id = ?
                ORDER BY page_number ASC, placement_index ASC;
                """,
                (group_id,)
            )
            member_images = []
            for img_row in img_cursor.fetchall():
                item = dict(img_row)
                item["needs_caption_review"] = bool(item["needs_caption_review"])
                item["review_reasons"] = json.loads(item["review_reasons"] or "[]")
                member_images.append(item)
            data["images"] = member_images
            data["image_count"] = len(member_images)
            return data

    def list_project_groups(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM figure_groups WHERE project_id = ? ORDER BY caption_page_number ASC, created_at ASC;",
                (project_id,)
            )
            groups = []
            for row in cursor.fetchall():
                data = dict(row)
                data["is_cross_page"] = bool(data["is_cross_page"])
                data["review_reasons"] = json.loads(data["review_reasons"] or "[]")
                # Attach member images
                img_cursor = conn.execute(
                    """
                    SELECT id, page_number, suggested_filename, panel_label, original_caption,
                           caption_position, needs_caption_review, review_reasons,
                           association_confidence, file_storage_key
                    FROM image_assets
                    WHERE group_id = ?
                    ORDER BY page_number ASC, placement_index ASC;
                    """,
                    (data["id"],)
                )
                member_images = []
                for img_row in img_cursor.fetchall():
                    item = dict(img_row)
                    item["needs_caption_review"] = bool(item["needs_caption_review"])
                    item["review_reasons"] = json.loads(item["review_reasons"] or "[]")
                    member_images.append(item)
                data["images"] = member_images
                data["image_count"] = len(member_images)
                groups.append(data)
            return groups

    def update_group_review_status(
        self,
        group_id: str,
        review_status: str,
        review_reasons: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        with self.db.session() as conn:
            if review_reasons is not None:
                reasons_json = json.dumps(review_reasons)
                conn.execute(
                    "UPDATE figure_groups SET review_status = ?, review_reasons = ?, updated_at = ? WHERE id = ?;",
                    (review_status, reasons_json, now, group_id)
                )
            else:
                conn.execute(
                    "UPDATE figure_groups SET review_status = ?, updated_at = ? WHERE id = ?;",
                    (review_status, now, group_id)
                )
            # If confirmed, clear group-level review flag on member images
            if review_status == "confirmed":
                conn.execute(
                    "UPDATE image_assets SET needs_caption_review = 0, updated_at = ? WHERE group_id = ?;",
                    (now, group_id)
                )
        return self.get_figure_group(group_id)

    def update_image_group_association(
        self,
        image_id: str,
        group_id: Optional[str],
        association_type: str = "single",
        association_confidence: float = 1.0,
        panel_label: Optional[str] = None,
        caption: Optional[str] = None,
        caption_position: Optional[str] = None,
        needs_review: Optional[bool] = None,
        needs_caption_review: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        fields = [
            "group_id = ?",
            "association_type = ?",
            "association_confidence = ?",
            "panel_label = ?",
            "updated_at = ?"
        ]
        values = [group_id, association_type, float(association_confidence), panel_label, now]
        if caption is not None:
            fields.append("original_caption = ?")
            values.append(caption)
        if caption_position is not None:
            fields.append("caption_position = ?")
            values.append(caption_position)
        review_flag = needs_caption_review if needs_caption_review is not None else needs_review
        if review_flag is not None:
            fields.append("needs_caption_review = ?")
            values.append(1 if review_flag else 0)

        values.append(image_id)
        query = f"UPDATE image_assets SET {', '.join(fields)} WHERE id = ?;"
        with self.db.session() as conn:
            conn.execute(query, tuple(values))
        return self.get_image_asset(image_id)

    def remove_image_from_group(self, image_id: str) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        reasons_json = json.dumps(["Removed from figure group by editor"])
        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE image_assets
                SET group_id = NULL,
                    association_type = 'uncaptioned',
                    original_caption = '',
                    caption_position = NULL,
                    needs_caption_review = 1,
                    review_reasons = ?,
                    panel_label = NULL,
                    updated_at = ?
                WHERE id = ?;
                """,
                (reasons_json, now, image_id)
            )
        return self.get_image_asset(image_id)

    def update_image_caption(
        self,
        image_id: str,
        caption: str,
        caption_position: Optional[str] = None,
        needs_review: bool = False,
        review_reasons: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        reasons_json = json.dumps(review_reasons or [])
        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE image_assets
                SET original_caption = ?,
                    caption_position = ?,
                    needs_caption_review = ?,
                    review_reasons = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (caption, caption_position, 1 if needs_review else 0, reasons_json, now, image_id)
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
        model: Optional[str] = None,
        language: str = "en-GB",
        prompt_text: Optional[str] = None,
        prompt_hash: Optional[str] = None,
        caption_snapshot: Optional[str] = None,
        context_snapshot: Optional[str] = None,
        status: str = "success",
        error_message: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        duration_seconds: float = 0.0,
        provider_request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO alt_text_versions (
                    id, image_id, version_number, text, revision_instruction,
                    prompt_version_id, provider, model, language, prompt_text,
                    prompt_hash, caption_snapshot, context_snapshot, status,
                    error_message, input_tokens, output_tokens, estimated_cost_usd,
                    duration_seconds, provider_request_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    version_id, image_id, version_number, text, revision_instruction,
                    prompt_version_id, provider, model, language, prompt_text,
                    prompt_hash, caption_snapshot, context_snapshot, status,
                    error_message, input_tokens, output_tokens, estimated_cost_usd,
                    duration_seconds, provider_request_id, now
                )
            )
        return self.get_alt_text_version(version_id)

    def get_alt_text_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM alt_text_versions WHERE id = ?;", (version_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    get_version = get_alt_text_version

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
    # AI Usage Ledger
    # ============================================================

    def record_ai_usage(
        self,
        ledger_id: str,
        project_id: str,
        image_id: Optional[str],
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO ai_usage_ledger (
                    id, project_id, image_id, provider, model,
                    input_tokens, output_tokens, estimated_cost_usd, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    ledger_id, project_id, image_id, provider, model,
                    input_tokens, output_tokens, estimated_cost_usd, now
                )
            )
        return {
            "id": ledger_id,
            "project_id": project_id,
            "image_id": image_id,
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost_usd": estimated_cost_usd,
            "created_at": now,
        }

    def get_project_ai_usage(self, project_id: str) -> Dict[str, Any]:
        with self.db.session() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as request_count,
                    COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost_usd
                FROM ai_usage_ledger
                WHERE project_id = ?;
                """,
                (project_id,)
            )
            row = dict(cursor.fetchone())
            row["total_tokens"] = row["total_input_tokens"] + row["total_output_tokens"]
            return row

    def get_total_ai_usage(self) -> Dict[str, Any]:
        with self.db.session() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as request_count,
                    COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                    COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost_usd
                FROM ai_usage_ledger;
                """
            )
            row = dict(cursor.fetchone())
            row["total_tokens"] = row["total_input_tokens"] + row["total_output_tokens"]
            return row

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

    def _format_export_row(self, row: Any) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        data = dict(row)
        fc = data.get("figure_count")
        if fc is None:
            fc = 0
        data["figure_count"] = int(fc)
        data["total_figures_exported"] = int(fc)
        return data

    def create_export(
        self,
        export_id: str,
        project_id: str,
        export_type: str,
        file_storage_key: str,
        file_checksum: str,
        status: str = "pending",
        figure_count: int = 0,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO exports (
                    id, project_id, export_type, file_storage_key,
                    file_checksum, status, figure_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (export_id, project_id, export_type, file_storage_key, file_checksum, status, figure_count, now)
            )
        return self.get_export(export_id)

    def get_export(self, export_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM exports WHERE id = ?;", (export_id,))
            row = cursor.fetchone()
            return self._format_export_row(row)

    def update_export_figure_count(self, export_id: str, figure_count: int) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            conn.execute("UPDATE exports SET figure_count = ? WHERE id = ?;", (figure_count, export_id))
        return self.get_export(export_id)

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

    def list_project_exports(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM exports WHERE project_id = ? ORDER BY created_at DESC;",
                (project_id,)
            )
            return [self._format_export_row(r) for r in cursor.fetchall()]

    def get_latest_project_export(self, project_id: str, export_type: str = "docx") -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM exports
                WHERE project_id = ? AND export_type = ?
                ORDER BY created_at DESC LIMIT 1;
                """,
                (project_id, export_type)
            )
            row = cursor.fetchone()
            return self._format_export_row(row)

    # ============================================================
    # Audit Events & Editor Workflow Grouping
    # ============================================================

    def record_audit_event(
        self,
        event_id: str,
        project_id: str,
        event_type: str,
        target_type: str,
        target_id: str,
        details: Optional[Dict[str, Any]] = None,
        actor_id: str = "editor",
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        details_json = json.dumps(details or {})
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                    id, project_id, event_type, target_type, target_id,
                    details, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (event_id, project_id, event_type, target_type, target_id, details_json, actor_id, now)
            )
        return self.get_audit_event(event_id)

    def get_audit_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM audit_events WHERE id = ?;", (event_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["details"] = json.loads(data["details"] or "{}")
            return data

    def list_project_audit_events(
        self,
        project_id: str,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            query = "SELECT * FROM audit_events WHERE project_id = ?"
            params = [project_id]
            if target_type is not None:
                query += " AND target_type = ?"
                params.append(target_type)
            if target_id is not None:
                query += " AND target_id = ?"
                params.append(target_id)
            query += " ORDER BY created_at ASC;"
            cursor = conn.execute(query, tuple(params))
            events = []
            for row in cursor.fetchall():
                data = dict(row)
                data["details"] = json.loads(data["details"] or "{}")
                events.append(data)
            return events

    # ============================================================
    # Users & Sessions (Authentication)
    # ============================================================

    def create_user(
        self,
        user_id: str,
        email: str,
        password: str,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        password_hash, password_salt = _hash_password(password)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, password_salt, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?);
                """,
                (user_id, email, password_hash, password_salt, now)
            )
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?;", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE email = ?;", (email,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def verify_user_password(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        user = self.get_user_by_email(email)
        if not user:
            return None
        if _verify_password(password, user["password_hash"], user["password_salt"]):
            return user
        return None

    def create_session(
        self,
        session_id: str,
        user_id: str,
        token: str,
        expires_at: str,
    ) -> Dict[str, Any]:
        token_hash = _hash_token(token)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (session_id, user_id, token_hash, expires_at, now := utc_now_iso())
            )
        return self.get_session_by_token(token)

    def get_session_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        token_hash = _hash_token(token)
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?;",
                (token_hash,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_session(self, token: str) -> bool:
        token_hash = _hash_token(token)
        with self.db.session() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?;",
                (token_hash,)
            )
            return cursor.rowcount > 0

    def cleanup_expired_sessions(self) -> int:
        now = utc_now_iso()
        with self.db.session() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE expires_at < ?;",
                (now,)
            )
            return cursor.rowcount or 0

    # ============================================================
    # Figure Grouping (existing)
    # ============================================================

    def create_group_from_selection(
        self,
        project_id: str,
        image_ids: List[str],
        caption: str,
        caption_position: Optional[str] = "below",
        figure_label: Optional[str] = None,
        actor_id: str = "editor",
        allow_reassignment: bool = False,
        decision_mode: Optional[str] = None,
        group_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomically creates a figure group from an editor's selection of image IDs,
        applying the confirmed shared caption, flagging approved drafts whose caption changed,
        and writing audit entries. All operations occur in a single transaction.
        """
        import uuid

        if not image_ids:
            raise ValueError("At least one image ID must be provided to form a group.")

        if len(image_ids) != len(set(image_ids)):
            raise ValueError("Duplicate image IDs provided in selection.")

        if not caption or not caption.strip():
            raise ValueError("A non-empty caption is required to form a figure group.")

        assigned_group_id = group_id or str(uuid.uuid4())
        now = utc_now_iso()

        with self.db.session() as conn:
            # 1. Validate project existence
            p_cursor = conn.execute("SELECT id FROM projects WHERE id = ?;", (project_id,))
            if not p_cursor.fetchone():
                raise ValueError(f"Project '{project_id}' not found.")

            # 2. Validate all image assets and project ownership
            images_data = []
            for img_id in image_ids:
                i_cursor = conn.execute("SELECT * FROM image_assets WHERE id = ?;", (img_id,))
                row = i_cursor.fetchone()
                if not row:
                    raise ValueError(f"Image asset '{img_id}' not found.")
                img_dict = dict(row)
                if img_dict["project_id"] != project_id:
                    raise ValueError(f"Image asset '{img_id}' does not belong to project '{project_id}'.")

                # Group reassignment policy check
                if img_dict["group_id"] and img_dict["group_id"] != assigned_group_id:
                    if not allow_reassignment:
                        raise ValueError(
                            f"Image '{img_id}' is already assigned to group '{img_dict['group_id']}'. "
                            "Explicit reassignment required."
                        )

                images_data.append(img_dict)

            # 3. Determine group attributes
            pages = sorted({img["page_number"] for img in images_data})
            is_cross_page = len(pages) > 1
            caption_page = pages[0]

            # 4. Insert into figure_groups
            conn.execute(
                """
                INSERT INTO figure_groups (
                    id, project_id, caption_text, caption_page_number,
                    caption_position, figure_label, is_cross_page,
                    review_status, review_reasons, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', '[]', ?, ?);
                """,
                (
                    assigned_group_id, project_id, caption, caption_page,
                    caption_position, figure_label, 1 if is_cross_page else 0,
                    now, now
                )
            )

            # 5. Process each image asset: update caption, group_id, review flags if approved
            member_audit_records = []
            flagged_image_ids = []

            for img in images_data:
                img_id = img["id"]
                prior_caption = img["original_caption"] or ""
                prior_group_id = img["group_id"]
                caption_changed = (prior_caption.strip() != caption.strip())

                # Check if image has an approval record
                appr_cursor = conn.execute(
                    "SELECT id, alt_text_version_id, approved_at FROM approvals WHERE image_id = ? ORDER BY approved_at DESC LIMIT 1;",
                    (img_id,)
                )
                appr_row = appr_cursor.fetchone()
                has_approved_draft = appr_row is not None

                # Determine review status
                needs_caption_review = False
                existing_reasons = json.loads(img["review_reasons"] or "[]")
                review_reasons = list(existing_reasons)

                if has_approved_draft and caption_changed:
                    # Caption change post-approval: preserve approval record, flag for review
                    needs_caption_review = True
                    reason_msg = "Caption modified after alt-text approval; review required"
                    if reason_msg not in review_reasons:
                        review_reasons.append(reason_msg)
                    flagged_image_ids.append(img_id)
                else:
                    # For newly grouped images with editor-confirmed caption, clear previous uncertain caption flags
                    review_reasons = [r for r in review_reasons if "uncertain" not in r.lower() and "unmatched" not in r.lower()]

                reasons_json = json.dumps(review_reasons)

                conn.execute(
                    """
                    UPDATE image_assets
                    SET group_id = ?,
                        original_caption = ?,
                        caption_position = ?,
                        association_type = 'group_matched',
                        association_confidence = 1.0,
                        needs_caption_review = ?,
                        review_reasons = ?,
                        updated_at = ?
                    WHERE id = ?;
                    """,
                    (
                        assigned_group_id,
                        caption,
                        caption_position,
                        1 if needs_caption_review else 0,
                        reasons_json,
                        now,
                        img_id
                    )
                )

                member_audit = {
                    "image_id": img_id,
                    "page_number": img["page_number"],
                    "prior_caption": prior_caption,
                    "new_caption": caption,
                    "prior_group_id": prior_group_id,
                    "new_group_id": assigned_group_id,
                    "has_approved_draft": has_approved_draft,
                    "flagged_for_review": needs_caption_review
                }
                member_audit_records.append(member_audit)

                # Write individual image audit entry
                img_audit_id = str(uuid.uuid4())
                img_audit_details = json.dumps({
                    "action": "image_grouped",
                    "group_id": assigned_group_id,
                    "prior_caption": prior_caption,
                    "new_caption": caption,
                    "prior_group_id": prior_group_id,
                    "has_approved_draft": has_approved_draft,
                    "flagged_for_review": needs_caption_review,
                    "decision_mode": decision_mode,
                })
                conn.execute(
                    """
                    INSERT INTO audit_events (
                        id, project_id, event_type, target_type, target_id,
                        details, actor_id, created_at
                    ) VALUES (?, ?, 'image_grouped', 'image_asset', ?, ?, ?, ?);
                    """,
                    (img_audit_id, project_id, img_id, img_audit_details, actor_id, now)
                )

            # 6. Write group audit entry
            grp_audit_id = str(uuid.uuid4())
            grp_audit_details = json.dumps({
                "action": "create_group_from_selection",
                "group_id": assigned_group_id,
                "figure_label": figure_label,
                "caption": caption,
                "caption_position": caption_position,
                "decision_mode": decision_mode,
                "image_ids": image_ids,
                "pages": pages,
                "is_cross_page": is_cross_page,
                "flagged_for_review": flagged_image_ids,
                "member_changes": member_audit_records,
            })
            conn.execute(
                """
                INSERT INTO audit_events (
                    id, project_id, event_type, target_type, target_id,
                    details, actor_id, created_at
                ) VALUES (?, ?, 'group_created_from_selection', 'figure_group', ?, ?, ?, ?);
                """,
                (grp_audit_id, project_id, assigned_group_id, grp_audit_details, actor_id, now)
            )

        # Retrieve and return created group with members
        created_group = self.get_figure_group(assigned_group_id)
        return {
            "group": created_group,
            "group_id": assigned_group_id,
            "member_image_ids": image_ids,
            "flagged_for_review": flagged_image_ids,
            "audit_event_id": grp_audit_id,
        }
