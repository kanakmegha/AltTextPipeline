"""
PostgreSQL Repository Layer
---------------------------
Type-safe, relational data access repository for PostgreSQL.

Mirrors the SQLite repository (src/db/repository.py) in interface and
behavior, adapted for PostgreSQL:
  - %s parameter placeholders (psycopg2 style)
  - RealDictRow rows (dict-like access; dict(row) works the same)
  - Native BOOLEAN for needs_caption_review, is_cross_page
  - TIMESTAMPTZ columns returned as datetime -> converted to ISO strings
  - JSON metadata stored as TEXT (review_reasons, details), handled with json.loads/dumps
"""

import json
import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from .postgres_connection import PostgresConnection


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


def _to_iso(val: Any) -> Any:
    """Convert a datetime (from PostgreSQL TIMESTAMPTZ) to an ISO string;
    pass through other values unchanged."""
    if isinstance(val, datetime):
        return val.isoformat()
    return val


class PostgresRepository:
    """
    CRUD repository operating over PostgreSQL via PostgresConnection.
    Interface is compatible with the SQLite Repository.
    """

    def __init__(self, db: PostgresConnection):
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
        status: str = "created",
        owner_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO projects (
                        id, name, source_filename, source_storage_key,
                        source_checksum, language_variant, status,
                        cleanup_status, owner_id, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'retained', %s, %s, %s);
                    """,
                    (
                        project_id, name, source_filename, source_storage_key,
                        source_checksum, language_variant, status, owner_id, now, now
                    )
                )
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM projects WHERE id = %s;", (project_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["created_at"] = _to_iso(data.get("created_at"))
                data["updated_at"] = _to_iso(data.get("updated_at"))
                return data

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
            fields.append("status = %s")
            values.append(status)
        if cleanup_status is not None:
            fields.append("cleanup_status = %s")
            values.append(cleanup_status)
        if not fields:
            return self.get_project(project_id)

        fields.append("updated_at = %s")
        values.append(now)
        values.append(project_id)

        query = f"UPDATE projects SET {', '.join(fields)} WHERE id = %s;"
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
        return self.get_project(project_id)

    def update_project_source_checksum(self, project_id: str, checksum: str):
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE projects SET source_checksum=%s, updated_at=%s WHERE id=%s;",
                            (checksum, utc_now_iso(), project_id))
        return self.get_project(project_id)

    def list_projects(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                if owner_id is not None:
                    cur.execute(
                        "SELECT * FROM projects WHERE owner_id = %s ORDER BY created_at DESC;",
                        (owner_id,),
                    )
                else:
                    cur.execute("SELECT * FROM projects ORDER BY created_at DESC;")
                rows = cur.fetchall()
                result = []
                for r in rows:
                    data = dict(r)
                    data["created_at"] = _to_iso(data.get("created_at"))
                    data["updated_at"] = _to_iso(data.get("updated_at"))
                    result.append(data)
                return result

    def delete_project(self, project_id: str) -> bool:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM projects WHERE id = %s;", (project_id,))
                return (cur.rowcount or 0) > 0

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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO image_assets (
                        id, project_id, page_number, xref, placement_index,
                        bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                        original_caption, caption_position, needs_caption_review,
                        review_reasons, suggested_filename, file_storage_key,
                        file_checksum, image_format, processing_status,
                        group_id, association_type, association_confidence, panel_label,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        image_id, project_id, page_number, xref, placement_index,
                        bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"],
                        original_caption, caption_position, needs_caption_review,
                        reasons_json, suggested_filename, file_storage_key,
                        file_checksum, image_format, processing_status,
                        group_id, association_type, float(association_confidence), panel_label,
                        now, now
                    )
                )
        return self.get_image_asset(image_id)

    def get_image_asset(self, image_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM image_assets WHERE id = %s;", (image_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._format_image_asset(dict(row))

    def _format_image_asset(self, data: Dict[str, Any]) -> Dict[str, Any]:
        data["needs_caption_review"] = bool(data.get("needs_caption_review"))
        data["review_reasons"] = json.loads(data["review_reasons"] or "[]")
        data["bbox"] = {
            "x0": data.pop("bbox_x0"),
            "y0": data.pop("bbox_y0"),
            "x1": data.pop("bbox_x1"),
            "y1": data.pop("bbox_y1"),
        }
        data["created_at"] = _to_iso(data.get("created_at"))
        data["updated_at"] = _to_iso(data.get("updated_at"))
        return data

    def list_project_images(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM image_assets
                    WHERE project_id = %s
                    ORDER BY page_number ASC, placement_index ASC;
                    """,
                    (project_id,)
                )
                images = []
                for row in cur.fetchall():
                    data = self._format_image_asset(dict(row))
                    images.append(data)
                return images

    def update_image_status(
        self,
        image_id: str,
        processing_status: str
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE image_assets SET processing_status = %s, updated_at = %s WHERE id = %s;",
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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO figure_groups (
                        id, project_id, caption_text, caption_page_number,
                        caption_position, figure_label, is_cross_page,
                        review_status, review_reasons, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        group_id, project_id, caption_text, caption_page_number,
                        caption_position, figure_label, is_cross_page,
                        review_status, reasons_json, now, now
                    )
                )
        return self.get_figure_group(group_id)

    def get_figure_group(self, group_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM figure_groups WHERE id = %s;", (group_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["is_cross_page"] = bool(data["is_cross_page"])
                data["review_reasons"] = json.loads(data["review_reasons"] or "[]")
                data["created_at"] = _to_iso(data.get("created_at"))
                data["updated_at"] = _to_iso(data.get("updated_at"))
                # Attach member images
                img_cur = conn.cursor()
                img_cur.execute(
                    """
                    SELECT id, page_number, suggested_filename, panel_label, original_caption,
                           caption_position, needs_caption_review, review_reasons,
                           association_confidence, file_storage_key
                    FROM image_assets
                    WHERE group_id = %s
                    ORDER BY page_number ASC, placement_index ASC;
                    """,
                    (group_id,)
                )
                member_images = []
                for img_row in img_cur.fetchall():
                    item = dict(img_row)
                    item["needs_caption_review"] = bool(item["needs_caption_review"])
                    item["review_reasons"] = json.loads(item["review_reasons"] or "[]")
                    member_images.append(item)
                data["images"] = member_images
                data["image_count"] = len(member_images)
                return data

    def list_project_groups(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM figure_groups WHERE project_id = %s ORDER BY caption_page_number ASC, created_at ASC;",
                    (project_id,)
                )
                groups = []
                for row in cur.fetchall():
                    data = dict(row)
                    data["is_cross_page"] = bool(data["is_cross_page"])
                    data["review_reasons"] = json.loads(data["review_reasons"] or "[]")
                    data["created_at"] = _to_iso(data.get("created_at"))
                    data["updated_at"] = _to_iso(data.get("updated_at"))
                    # Attach member images
                    img_cur = conn.cursor()
                    img_cur.execute(
                        """
                        SELECT id, page_number, suggested_filename, panel_label, original_caption,
                               caption_position, needs_caption_review, review_reasons,
                               association_confidence, file_storage_key
                        FROM image_assets
                        WHERE group_id = %s
                        ORDER BY page_number ASC, placement_index ASC;
                        """,
                        (data["id"],)
                    )
                    member_images = []
                    for img_row in img_cur.fetchall():
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
            with conn.cursor() as cur:
                if review_reasons is not None:
                    reasons_json = json.dumps(review_reasons)
                    cur.execute(
                        "UPDATE figure_groups SET review_status = %s, review_reasons = %s, updated_at = %s WHERE id = %s;",
                        (review_status, reasons_json, now, group_id)
                    )
                else:
                    cur.execute(
                        "UPDATE figure_groups SET review_status = %s, updated_at = %s WHERE id = %s;",
                        (review_status, now, group_id)
                    )
                # If confirmed, clear group-level review flag on member images
                if review_status == "confirmed":
                    cur.execute(
                        "UPDATE image_assets SET needs_caption_review = FALSE, updated_at = %s WHERE group_id = %s;",
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
            "group_id = %s",
            "association_type = %s",
            "association_confidence = %s",
            "panel_label = %s",
            "updated_at = %s"
        ]
        values = [group_id, association_type, float(association_confidence), panel_label, now]
        if caption is not None:
            fields.append("original_caption = %s")
            values.append(caption)
        if caption_position is not None:
            fields.append("caption_position = %s")
            values.append(caption_position)
        review_flag = needs_caption_review if needs_caption_review is not None else needs_review
        if review_flag is not None:
            fields.append("needs_caption_review = %s")
            values.append(review_flag)

        values.append(image_id)
        query = f"UPDATE image_assets SET {', '.join(fields)} WHERE id = %s;"
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
        return self.get_image_asset(image_id)

    def remove_image_from_group(self, image_id: str) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        reasons_json = json.dumps(["Removed from figure group by editor"])
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE image_assets
                    SET group_id = NULL,
                        association_type = 'uncaptioned',
                        original_caption = '',
                        caption_position = NULL,
                        needs_caption_review = TRUE,
                        review_reasons = %s,
                        panel_label = NULL,
                        updated_at = %s
                    WHERE id = %s;
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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE image_assets
                    SET original_caption = %s,
                        caption_position = %s,
                        needs_caption_review = %s,
                        review_reasons = %s,
                        updated_at = %s
                    WHERE id = %s;
                    """,
                    (caption, caption_position, needs_review, reasons_json, now, image_id)
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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alt_text_versions (
                        id, image_id, version_number, text, revision_instruction,
                        prompt_version_id, provider, model, language, prompt_text,
                        prompt_hash, caption_snapshot, context_snapshot, status,
                        error_message, input_tokens, output_tokens, estimated_cost_usd,
                        duration_seconds, provider_request_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
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
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM alt_text_versions WHERE id = %s;", (version_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["created_at"] = _to_iso(data.get("created_at"))
                return data

    get_version = get_alt_text_version

    def list_image_versions(self, image_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM alt_text_versions WHERE image_id = %s ORDER BY version_number ASC;",
                    (image_id,)
                )
                rows = cur.fetchall()
                result = []
                for r in rows:
                    data = dict(r)
                    data["created_at"] = _to_iso(data.get("created_at"))
                    result.append(data)
                return result

    def get_latest_version(self, image_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM alt_text_versions WHERE image_id = %s ORDER BY version_number DESC LIMIT 1;",
                    (image_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["created_at"] = _to_iso(data.get("created_at"))
                return data

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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_usage_ledger (
                        id, project_id, image_id, provider, model,
                        input_tokens, output_tokens, estimated_cost_usd, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) as request_count,
                        COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                        COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                        COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost_usd
                    FROM ai_usage_ledger
                    WHERE project_id = %s;
                    """,
                    (project_id,)
                )
                row = dict(cur.fetchone())
                row["total_tokens"] = row["total_input_tokens"] + row["total_output_tokens"]
                return row

    def get_total_ai_usage(self) -> Dict[str, Any]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) as request_count,
                        COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                        COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                        COALESCE(SUM(estimated_cost_usd), 0.0) as total_cost_usd
                    FROM ai_usage_ledger;
                    """
                )
                row = dict(cur.fetchone())
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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO approvals (id, image_id, alt_text_version_id, reviewer_id, approved_at)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (approval_id, image_id, alt_text_version_id, reviewer_id, now)
                )
                # Update image processing status to approved
                cur.execute(
                    "UPDATE image_assets SET processing_status = 'approved', updated_at = %s WHERE id = %s;",
                    (now, image_id)
                )

        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM approvals WHERE id = %s;", (approval_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["approved_at"] = _to_iso(data.get("approved_at"))
                return data

    def get_approval_for_image(self, image_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM approvals WHERE image_id = %s ORDER BY approved_at DESC LIMIT 1;",
                    (image_id,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["approved_at"] = _to_iso(data.get("approved_at"))
                return data

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
        page_numbers: Optional[List[int]] = None,
        max_attempts: int = 3
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO jobs (
                        id, project_id, image_id, job_type, page_start, page_end, page_numbers,
                        status, attempt_count, max_attempts, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', 0, %s, %s);
                    """,
                    (job_id, project_id, image_id, job_type, page_start, page_end,
                     json.dumps(page_numbers) if page_numbers else None, max_attempts, now)
                )
        return self.get_job(job_id)

    def claim_job(self, job_id: str, stale_before: str, now: str) -> Optional[Dict[str, Any]]:
        """Atomically claim a queued job or reclaim an abandoned running job."""
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE jobs SET status='running', attempt_count=attempt_count+1,
                       started_at=COALESCE(started_at, %s), lease_updated_at=%s, error_message=NULL
                       WHERE id=%s AND attempt_count < max_attempts
                       AND (status='queued' OR (status='running' AND COALESCE(lease_updated_at, started_at) < %s));""",
                    (now, now, job_id, stale_before),
                )
                claimed = cur.rowcount == 1
        return self.get_job(job_id) if claimed else None

    def list_recoverable_jobs(self, stale_before: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM jobs WHERE job_type='extraction' AND attempt_count < max_attempts
                       AND (status='queued' OR (status='running' AND COALESCE(lease_updated_at, started_at) < %s))
                       ORDER BY created_at LIMIT %s;""", (stale_before, limit)
                )
                ids = [row["id"] for row in cur.fetchall()]
        return [job for job_id in ids if (job := self.get_job(job_id))]

    def fail_exhausted_jobs(self, stale_before: str) -> List[str]:
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT project_id FROM jobs WHERE job_type='extraction' AND status='running' AND attempt_count >= max_attempts AND COALESCE(lease_updated_at, started_at) < %s;",
                    (stale_before,),
                )
                projects = sorted({row["project_id"] for row in cur.fetchall()})
                cur.execute(
                    "UPDATE jobs SET status='failed', error_message='Worker stopped during final retry', completed_at=%s WHERE job_type='extraction' AND status='running' AND attempt_count >= max_attempts AND COALESCE(lease_updated_at, started_at) < %s;",
                    (now, stale_before),
                )
        for project_id in projects:
            self.update_project_status(project_id, status="failed")
        return projects

    def find_image_asset_by_source(self, project_id: str, page_number: int, xref: int, placement_index: int):
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM image_assets WHERE project_id=%s AND page_number=%s AND xref=%s AND placement_index=%s LIMIT 1;",
                    (project_id, page_number, xref, placement_index),
                )
                row = cur.fetchone()
                image_id = row["id"] if row else None
        return self.get_image_asset(image_id) if image_id else None

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM jobs WHERE id = %s;", (job_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["started_at"] = _to_iso(data.get("started_at"))
                data["completed_at"] = _to_iso(data.get("completed_at"))
                data["lease_updated_at"] = _to_iso(data.get("lease_updated_at"))
                data["created_at"] = _to_iso(data.get("created_at"))
                data["page_numbers"] = json.loads(data["page_numbers"]) if data.get("page_numbers") else None
                return data

    def update_job_status(
        self,
        job_id: str,
        status: str,
        increment_attempt: bool = False,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        now = utc_now_iso()
        if status == "running" and not increment_attempt:
            with self.db.session() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE jobs SET status='running', error_message=NULL,
                           started_at=COALESCE(started_at, %s), lease_updated_at=%s
                           WHERE id=%s AND status IN ('queued','running');""",
                        (now, now, job_id),
                    )
            return self.get_job(job_id)

        job = self.get_job(job_id)
        if not job:
            return None

        attempts = job["attempt_count"] + (1 if increment_attempt else 0)
        started_at = job["started_at"] or (now if status == "running" else None)
        completed_at = now if status in {"completed", "failed", "cancelled"} else None

        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE jobs SET
                        status = %s,
                        attempt_count = %s,
                        error_message = %s,
                        started_at = COALESCE(started_at, %s),
                        completed_at = %s,
                        lease_updated_at = CASE WHEN %s = 'running' THEN %s ELSE lease_updated_at END
                    WHERE id = %s AND status NOT IN ('completed','cancelled');
                    """,
                    (status, attempts, error_message, started_at, completed_at, status, now, job_id)
                )
        return self.get_job(job_id)

    def list_project_jobs(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM jobs WHERE project_id = %s ORDER BY created_at ASC;", (project_id,))
                rows = cur.fetchall()
                result = []
                for r in rows:
                    data = dict(r)
                    data["started_at"] = _to_iso(data.get("started_at"))
                    data["completed_at"] = _to_iso(data.get("completed_at"))
                    data["lease_updated_at"] = _to_iso(data.get("lease_updated_at"))
                    data["created_at"] = _to_iso(data.get("created_at"))
                    data["page_numbers"] = json.loads(data["page_numbers"]) if data.get("page_numbers") else None
                    result.append(data)
                return result

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
        data["created_at"] = _to_iso(data.get("created_at"))
        data["verified_at"] = _to_iso(data.get("verified_at"))
        data["delivered_at"] = _to_iso(data.get("delivered_at"))
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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO exports (
                        id, project_id, export_type, file_storage_key,
                        file_checksum, status, figure_count, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (export_id, project_id, export_type, file_storage_key, file_checksum, status, figure_count, now)
                )
        return self.get_export(export_id)

    def get_export(self, export_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM exports WHERE id = %s;", (export_id,))
                row = cur.fetchone()
                return self._format_export_row(row)

    def update_export_figure_count(self, export_id: str, figure_count: int) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE exports SET figure_count = %s WHERE id = %s;", (figure_count, export_id))
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
            with conn.cursor() as cur:
                params = [status]
                if mark_verified:
                    params.append(now)
                if mark_delivered:
                    params.append(now)
                params.append(export_id)

                set_clause = "status = %s"
                if mark_verified:
                    set_clause += ", verified_at = %s"
                if mark_delivered:
                    set_clause += ", delivered_at = %s"

                query = f"UPDATE exports SET {set_clause} WHERE id = %s;"
                cur.execute(query, tuple(params))
        return self.get_export(export_id)

    def list_project_exports(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM exports WHERE project_id = %s ORDER BY created_at DESC;",
                    (project_id,)
                )
                return [self._format_export_row(r) for r in cur.fetchall()]

    def get_latest_project_export(self, project_id: str, export_type: str = "docx") -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM exports
                    WHERE project_id = %s AND export_type = %s
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    (project_id, export_type)
                )
                row = cur.fetchone()
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
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_events (
                        id, project_id, event_type, target_type, target_id,
                        details, actor_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (event_id, project_id, event_type, target_type, target_id, details_json, actor_id, now)
                )
        return self.get_audit_event(event_id)

    def get_audit_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM audit_events WHERE id = %s;", (event_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["details"] = json.loads(data["details"] or "{}")
                data["created_at"] = _to_iso(data.get("created_at"))
                return data

    def list_project_audit_events(
        self,
        project_id: str,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                query = "SELECT * FROM audit_events WHERE project_id = %s"
                params = [project_id]
                if target_type is not None:
                    query += " AND target_type = %s"
                    params.append(target_type)
                if target_id is not None:
                    query += " AND target_id = %s"
                    params.append(target_id)
                query += " ORDER BY created_at ASC;"
                cur.execute(query, tuple(params))
                events = []
                for row in cur.fetchall():
                    data = dict(row)
                    data["details"] = json.loads(data["details"] or "{}")
                    data["created_at"] = _to_iso(data.get("created_at"))
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
        is_active: bool = True,
    ) -> Dict[str, Any]:
        if not password or len(password) > 256:
            raise ValueError("Password must be between 1 and 256 characters.")
        now = utc_now_iso()
        password_hash, password_salt = _hash_password(password)
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, email, password_hash, password_salt, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s);
                    """,
                    (user_id, email, password_hash, password_salt, bool(is_active), now)
                )
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s;", (user_id,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["is_active"] = bool(data.get("is_active"))
                data["created_at"] = _to_iso(data.get("created_at"))
                return data

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email = %s;", (email,))
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["is_active"] = bool(data.get("is_active"))
                data["created_at"] = _to_iso(data.get("created_at"))
                return data

    def set_user_active(self, user_id: str, is_active: bool) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET is_active = %s WHERE id = %s;",
                    (bool(is_active), user_id),
                )
        return self.get_user(user_id)

    def verify_user_password(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        if not email or not password or len(password) > 256:
            return None
        user = self.get_user_by_email(email)
        if not user:
            _hash_password(password, b"\x00" * 16)
            return None
        if not user.get("is_active"):
            _hash_password(password, b"\x00" * 16)
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
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at)
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (session_id, user_id, token_hash, expires_at, now)
                )
        return self.get_session_by_token(token)

    def get_session_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        token_hash = _hash_token(token)
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM sessions WHERE token_hash = %s;",
                    (token_hash,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["expires_at"] = _to_iso(data.get("expires_at"))
                data["created_at"] = _to_iso(data.get("created_at"))
                return data

    def delete_session(self, token: str) -> bool:
        token_hash = _hash_token(token)
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE token_hash = %s;",
                    (token_hash,)
                )
                return cur.rowcount > 0

    def delete_user_sessions(self, user_id: str) -> int:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE user_id = %s;",
                    (user_id,)
                )
                return cur.rowcount or 0

    def cleanup_expired_sessions(self) -> int:
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sessions WHERE expires_at <= %s;",
                    (now,)
                )
                return cur.rowcount or 0

    def record_failed_login(self, identifier: str, ip_address: str) -> None:
        import uuid
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO login_attempts (id, identifier, ip_address, attempted_at)
                    VALUES (%s, %s, %s, %s);
                    """,
                    (str(uuid.uuid4()), identifier.lower().strip(), ip_address, now),
                )

    def count_failed_logins(self, identifier: str, ip_address: str, since_iso: str) -> int:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM login_attempts
                    WHERE (identifier = %s OR ip_address = %s) AND attempted_at >= %s;
                    """,
                    (identifier.lower().strip(), ip_address, since_iso),
                )
                row = cur.fetchone()
                return int(row["cnt"]) if row and row.get("cnt") is not None else 0

    def clear_failed_logins(self, identifier: str, ip_address: str) -> None:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM login_attempts WHERE identifier = %s OR ip_address = %s;",
                    (identifier.lower().strip(), ip_address),
                )

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
            with conn.cursor() as cur:
                # 1. Validate project existence
                cur.execute("SELECT id FROM projects WHERE id = %s;", (project_id,))
                if not cur.fetchone():
                    raise ValueError(f"Project '{project_id}' not found.")

                # 2. Validate all image assets and project ownership
                images_data = []
                for img_id in image_ids:
                    cur.execute("SELECT * FROM image_assets WHERE id = %s;", (img_id,))
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"Image asset '{img_id}' not found.")
                    img_dict = dict(row)
                    if img_dict["project_id"] != project_id:
                        raise ValueError(
                            f"Image asset '{img_id}' does not belong to project '{project_id}'."
                        )

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
                cur.execute(
                    """
                    INSERT INTO figure_groups (
                        id, project_id, caption_text, caption_page_number,
                        caption_position, figure_label, is_cross_page,
                        review_status, review_reasons, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed', '[]', %s, %s);
                    """,
                    (
                        assigned_group_id, project_id, caption, caption_page,
                        caption_position, figure_label, is_cross_page,
                        now, now
                    )
                )

                # 5. Process each image asset
                member_audit_records = []
                flagged_image_ids = []

                for img in images_data:
                    img_id = img["id"]
                    prior_caption = img["original_caption"] or ""
                    prior_group_id = img["group_id"]
                    caption_changed = (prior_caption.strip() != caption.strip())

                    # Check if image has an approval record
                    cur.execute(
                        "SELECT id, alt_text_version_id, approved_at FROM approvals WHERE image_id = %s ORDER BY approved_at DESC LIMIT 1;",
                        (img_id,)
                    )
                    appr_row = cur.fetchone()
                    has_approved_draft = appr_row is not None

                    # Determine review status
                    needs_caption_review = False
                    existing_reasons = json.loads(img["review_reasons"] or "[]")
                    review_reasons = list(existing_reasons)

                    if has_approved_draft and caption_changed:
                        needs_caption_review = True
                        reason_msg = "Caption modified after alt-text approval; review required"
                        if reason_msg not in review_reasons:
                            review_reasons.append(reason_msg)
                        flagged_image_ids.append(img_id)
                    else:
                        review_reasons = [r for r in review_reasons if "uncertain" not in r.lower() and "unmatched" not in r.lower()]

                    reasons_json = json.dumps(review_reasons)

                    cur.execute(
                        """
                        UPDATE image_assets
                        SET group_id = %s,
                            original_caption = %s,
                            caption_position = %s,
                            association_type = 'group_matched',
                            association_confidence = 1.0,
                            needs_caption_review = %s,
                            review_reasons = %s,
                            updated_at = %s
                        WHERE id = %s;
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
                    cur.execute(
                        """
                        INSERT INTO audit_events (
                            id, project_id, event_type, target_type, target_id,
                            details, actor_id, created_at
                        ) VALUES (%s, %s, 'image_grouped', 'image_asset', %s, %s, %s, %s);
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
                cur.execute(
                    """
                    INSERT INTO audit_events (
                        id, project_id, event_type, target_type, target_id,
                        details, actor_id, created_at
                    ) VALUES (%s, %s, 'group_created_from_selection', 'figure_group', %s, %s, %s, %s);
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

    # ============================================================
    # Upload Keys (Direct-to-Blob Upload Tracking)
    # ============================================================

    def create_upload_key(
        self,
        upload_key: str,
        content_type: str,
        expires_at: str,
        owner_id: Optional[str] = None,
        filename: str = "upload.pdf",
        expected_size: int = 0,
        blob_key: Optional[str] = None,
        total_chunks: Optional[int] = None,
        chunk_size: Optional[int] = None,
        temp_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a pending upload key record for direct-to-Blob upload."""
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO upload_keys (
                        id, upload_key, project_id, status, content_type,
                        owner_id, filename, expected_size, blob_key,
                        source_storage_key, source_checksum, source_size,
                        total_chunks, chunk_size, uploaded_chunks, temp_dir,
                        expires_at, error_message, created_at, updated_at
                    ) VALUES (%s, %s, NULL, 'uploading', %s, %s, %s, %s, %s, NULL, NULL, NULL, %s, %s, 0, NULL, %s, NULL, %s, %s);
                    """,
                    (
                        str(uuid.uuid4()),
                        upload_key,
                        content_type,
                        owner_id,
                        filename,
                        expected_size,
                        blob_key,
                        total_chunks,
                        chunk_size,
                        expires_at,
                        now,
                        now,
                    )
                )
        return self.get_upload_key(upload_key)

    def claim_upload_token(self, upload_key: str, owner_id: Optional[str], now: str) -> bool:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE upload_keys SET token_issued=TRUE, updated_at=%s WHERE upload_key=%s AND status='uploading' AND token_issued=FALSE AND expires_at>%s AND owner_id IS NOT DISTINCT FROM %s;",
                    (now, upload_key, now, owner_id),
                )
                return cur.rowcount == 1

    def claim_upload_finalization(self, upload_key: str, owner_id: Optional[str], now: str) -> bool:
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE upload_keys SET status='finalizing', updated_at=%s WHERE upload_key=%s AND status='uploading' AND token_issued=TRUE AND owner_id IS NOT DISTINCT FROM %s AND expires_at>%s;",
                    (now, upload_key, owner_id, now),
                )
                return cur.rowcount == 1

    def finalize_upload_project(self, *, upload_key, project_id, job_id, owner_id,
                                name, source_filename, source_storage_key,
                                source_checksum, source_size, page_start=None, page_end=None, page_numbers=None):
        """Atomically create project/job and consume the upload intent."""
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO projects (id,name,source_filename,source_storage_key,source_checksum,
                       language_variant,status,cleanup_status,owner_id,created_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s,'en-GB','queued','retained',%s,%s,%s) ON CONFLICT(id) DO NOTHING;""",
                    (project_id, name, source_filename, source_storage_key, source_checksum, owner_id, now, now),
                )
                cur.execute(
                    """INSERT INTO jobs (id,project_id,job_type,page_start,page_end,page_numbers,status,attempt_count,max_attempts,created_at)
                       VALUES (%s,%s,'extraction',%s,%s,%s,'queued',0,3,%s) ON CONFLICT(id) DO NOTHING;""",
                    (job_id, project_id, page_start, page_end,
                     json.dumps(page_numbers) if page_numbers else None, now),
                )
                cur.execute(
                    """UPDATE upload_keys SET status='completed',project_id=%s,source_storage_key=%s,
                       source_checksum=%s,source_size=%s,updated_at=%s WHERE upload_key=%s
                       AND owner_id IS NOT DISTINCT FROM %s AND status IN ('finalizing','completed');""",
                    (project_id, source_storage_key, source_checksum, source_size, now, upload_key, owner_id),
                )
        return {"project": self.get_project(project_id), "job": self.get_job(job_id)}

    def get_upload_key(self, upload_key: str) -> Optional[Dict[str, Any]]:
        """Get upload key record by upload_key."""
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM upload_keys WHERE upload_key = %s;",
                    (upload_key,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["expires_at"] = _to_iso(data.get("expires_at"))
                data["created_at"] = _to_iso(data.get("created_at"))
                data["updated_at"] = _to_iso(data.get("updated_at"))
                return data

    def update_upload_key(
        self,
        upload_key: str,
        status: Optional[str] = None,
        source_storage_key: Optional[str] = None,
        source_checksum: Optional[str] = None,
        source_size: Optional[int] = None,
        total_chunks: Optional[int] = None,
        chunk_size: Optional[int] = None,
        uploaded_chunks: Optional[int] = None,
        temp_dir: Optional[str] = None,
        error_message: Optional[str] = None,
        project_id: Optional[str] = None,
        token_issued: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an upload key record."""
        now = utc_now_iso()
        fields = []
        values = []
        if status is not None:
            fields.append("status = %s")
            values.append(status)
        if source_storage_key is not None:
            fields.append("source_storage_key = %s")
            values.append(source_storage_key)
        if source_checksum is not None:
            fields.append("source_checksum = %s")
            values.append(source_checksum)
        if source_size is not None:
            fields.append("source_size = %s")
            values.append(source_size)
        if total_chunks is not None:
            fields.append("total_chunks = %s")
            values.append(total_chunks)
        if chunk_size is not None:
            fields.append("chunk_size = %s")
            values.append(chunk_size)
        if uploaded_chunks is not None:
            fields.append("uploaded_chunks = %s")
            values.append(uploaded_chunks)
        if temp_dir is not None:
            fields.append("temp_dir = %s")
            values.append(temp_dir)
        if error_message is not None:
            fields.append("error_message = %s")
            values.append(error_message)
        if project_id is not None:
            fields.append("project_id = %s")
            values.append(project_id)
        if token_issued is not None:
            fields.append("token_issued = %s")
            values.append(token_issued)
        if not fields:
            return self.get_upload_key(upload_key)

        fields.append("updated_at = %s")
        values.append(now)
        values.append(upload_key)

        query = f"UPDATE upload_keys SET {', '.join(fields)} WHERE upload_key = %s;"
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
        return self.get_upload_key(upload_key)

    def complete_upload(
        self,
        upload_key: str,
        project_id: str,
        source_storage_key: str,
        source_checksum: str,
        source_size: int,
    ) -> Optional[Dict[str, Any]]:
        """Mark an upload as completed and link it to a project."""
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE upload_keys
                    SET status = 'completed',
                        project_id = %s,
                        source_storage_key = %s,
                        source_checksum = %s,
                        source_size = %s,
                        updated_at = %s
                    WHERE upload_key = %s;
                    """,
                    (project_id, source_storage_key, source_checksum, source_size, now, upload_key)
                )
        return self.get_upload_key(upload_key)

    def expire_upload_keys(self) -> int:
        """Mark expired upload keys as 'expired'. Returns count of expired keys."""
        now = utc_now_iso()
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE upload_keys SET status = 'expired', updated_at = %s WHERE status = 'uploading' AND expires_at < %s;",
                    (now, now)
                )
                return cur.rowcount or 0

    def list_upload_keys_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """List upload keys associated with a project."""
        with self.db.session() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM upload_keys WHERE project_id = %s ORDER BY created_at DESC;",
                    (project_id,)
                )
                rows = cur.fetchall()
                result = []
                for r in rows:
                    data = dict(r)
                    data["expires_at"] = _to_iso(data.get("expires_at"))
                    data["created_at"] = _to_iso(data.get("created_at"))
                    data["updated_at"] = _to_iso(data.get("updated_at"))
                    result.append(data)
                return result
