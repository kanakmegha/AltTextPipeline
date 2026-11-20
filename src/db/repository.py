"""
Database Repository Layer
-------------------------
Type-safe, relational data access repository for projects, image assets,
draft versions, approvals, background jobs, and exports.
"""

import json
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Set
from .connection import DatabaseConnection
from src.storage.base_storage import CleanupBlockedError, StorageError


def get_deletion_lease_seconds() -> int:
    try:
        return max(1, int(os.getenv("DELETION_LEASE_SECONDS", "300")))
    except (TypeError, ValueError):
        return 300


def _parse_utc_dt(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


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
        status: str = "created",
        owner_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, name, source_filename, source_storage_key,
                    source_checksum, language_variant, status,
                    cleanup_status, owner_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'retained', ?, ?, ?);
                """,
                (
                    project_id, name, source_filename, source_storage_key,
                    source_checksum, language_variant, status, owner_id, now, now
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

        if status is not None and cleanup_status is None:
            query = f"UPDATE projects SET {', '.join(fields)} WHERE id = ? AND COALESCE(cleanup_status, 'retained') != 'deleting';"
        else:
            query = f"UPDATE projects SET {', '.join(fields)} WHERE id = ?;"
        with self.db.session() as conn:
            conn.execute(query, tuple(values))

        return self.get_project(project_id)

    def update_project_source_checksum(self, project_id: str, checksum: str):
        with self.db.session() as conn:
            conn.execute("UPDATE projects SET source_checksum=?, updated_at=? WHERE id=?;",
                         (checksum, utc_now_iso(), project_id))
        return self.get_project(project_id)

    def list_projects(self, owner_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            if owner_id is not None:
                cursor = conn.execute(
                    "SELECT * FROM projects WHERE owner_id = ? ORDER BY created_at DESC;",
                    (owner_id,),
                )
            else:
                cursor = conn.execute("SELECT * FROM projects ORDER BY created_at DESC;")
            return [dict(r) for r in cursor.fetchall()]

    def delete_project(self, project_id: str) -> bool:
        with self.db.session() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE id = ?;", (project_id,))
            return (cursor.rowcount or 0) > 0

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
        review_reasons: Optional[List[str]] = None,
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
        if review_reasons is not None:
            fields.append("review_reasons = ?")
            values.append(json.dumps(review_reasons))

        values.append(image_id)
        query = f"UPDATE image_assets SET {', '.join(fields)} WHERE id = ?;"
        with self.db.session() as conn:
            conn.execute(query, tuple(values))
        return self.get_image_asset(image_id)

    def list_image_asset_source_keys(self, project_id: str) -> Dict[tuple, str]:
        """Return {(page_number, xref, placement_index): image_id} for a project in one query."""
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT id, page_number, xref, placement_index FROM image_assets WHERE project_id = ?;",
                (project_id,),
            )
            return {
                (int(row["page_number"]), int(row["xref"]), int(row["placement_index"])): row["id"]
                for row in cursor.fetchall()
            }

    def create_image_assets_batch(self, items: List[Dict[str, Any]]) -> int:
        """Idempotently insert image_assets rows in a single transaction."""
        if not items:
            return 0
        now = utc_now_iso()
        project_ids = {item["project_id"] for item in items}
        with self.db.session() as conn:
            existing_keys = set()
            blocked_projects = set()
            for pid in project_ids:
                prow = conn.execute("SELECT cleanup_status FROM projects WHERE id = ?;", (pid,)).fetchone()
                if not prow or (prow["cleanup_status"] or "retained") in ("cleaning", "deleting"):
                    blocked_projects.add(pid)
                    continue
                cur = conn.execute(
                    "SELECT page_number, xref, placement_index FROM image_assets WHERE project_id = ?;",
                    (pid,),
                )
                for row in cur.fetchall():
                    existing_keys.add((pid, int(row["page_number"]), int(row["xref"]), int(row["placement_index"])))

            rows_to_insert = []
            for item in items:
                if item["project_id"] in blocked_projects:
                    continue
                key = (
                    item["project_id"],
                    int(item["page_number"]),
                    int(item["xref"]),
                    int(item["placement_index"]),
                )
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                bbox = item["bbox"]
                rows_to_insert.append((
                    item["image_id"],
                    item["project_id"],
                    int(item["page_number"]),
                    int(item["xref"]),
                    int(item["placement_index"]),
                    bbox["x0"],
                    bbox["y0"],
                    bbox["x1"],
                    bbox["y1"],
                    item.get("original_caption", ""),
                    item.get("caption_position"),
                    1 if item.get("needs_caption_review") else 0,
                    json.dumps(item.get("review_reasons") or []),
                    item["suggested_filename"],
                    item["file_storage_key"],
                    item["file_checksum"],
                    item["image_format"],
                    item.get("processing_status", "extracted"),
                    item.get("group_id"),
                    item.get("association_type", "single"),
                    float(item.get("association_confidence", 1.0)),
                    item.get("panel_label"),
                    now,
                    now,
                ))
            if not rows_to_insert:
                return 0
            before_changes = conn.total_changes
            conn.executemany(
                """
                INSERT INTO image_assets (
                    id, project_id, page_number, xref, placement_index,
                    bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                    original_caption, caption_position, needs_caption_review,
                    review_reasons, suggested_filename, file_storage_key,
                    file_checksum, image_format, processing_status,
                    group_id, association_type, association_confidence, panel_label,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING;
                """,
                rows_to_insert,
            )
            return conn.total_changes - before_changes

    def create_figure_groups_batch(self, groups: List[Dict[str, Any]]) -> int:
        """Idempotently insert figure_groups rows in a single transaction."""
        if not groups:
            return 0
        now = utc_now_iso()
        project_ids = {g["project_id"] for g in groups}
        with self.db.session() as conn:
            existing_ids = set()
            blocked_projects = set()
            for pid in project_ids:
                prow = conn.execute("SELECT cleanup_status FROM projects WHERE id = ?;", (pid,)).fetchone()
                if not prow or (prow["cleanup_status"] or "retained") in ("cleaning", "deleting"):
                    blocked_projects.add(pid)
                    continue
                cur = conn.execute("SELECT id FROM figure_groups WHERE project_id = ?;", (pid,))
                for row in cur.fetchall():
                    existing_ids.add(row["id"])
            rows_to_insert = []
            for g in groups:
                if g["project_id"] in blocked_projects:
                    continue
                gid = g["group_id"]
                if gid in existing_ids:
                    continue
                existing_ids.add(gid)
                rows_to_insert.append((
                    gid,
                    g["project_id"],
                    g["caption_text"],
                    int(g["caption_page_number"]),
                    g.get("caption_position", "below"),
                    g.get("figure_label"),
                    1 if g.get("is_cross_page") else 0,
                    g.get("review_status", "candidate"),
                    json.dumps(g.get("review_reasons") or []),
                    now,
                    now,
                ))
            if not rows_to_insert:
                return 0
            conn.executemany(
                """
                INSERT OR IGNORE INTO figure_groups (
                    id, project_id, caption_text, caption_page_number,
                    caption_position, figure_label, is_cross_page,
                    review_status, review_reasons, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rows_to_insert,
            )
            return len(rows_to_insert)

    def update_image_group_associations_batch(self, updates: List[Dict[str, Any]]) -> int:
        """Batch-update image_assets group/caption metadata in a single transaction."""
        if not updates:
            return 0
        now = utc_now_iso()
        rows = [
            (
                u.get("group_id"),
                u.get("association_type", "single"),
                float(u.get("association_confidence", 1.0)),
                u.get("panel_label"),
                u.get("caption", ""),
                u.get("caption_position"),
                1 if u.get("needs_caption_review") else 0,
                json.dumps(u.get("review_reasons") or []),
                now,
                u["image_id"],
            )
            for u in updates
        ]
        with self.db.session() as conn:
            conn.executemany(
                """
                UPDATE image_assets
                SET group_id = ?,
                    association_type = ?,
                    association_confidence = ?,
                    panel_label = ?,
                    original_caption = ?,
                    caption_position = ?,
                    needs_caption_review = ?,
                    review_reasons = ?,
                    updated_at = ?
                WHERE id = ?
                  AND EXISTS (
                      SELECT 1 FROM projects p
                      WHERE p.id = image_assets.project_id
                        AND COALESCE(p.cleanup_status, 'retained') NOT IN ('cleaning', 'deleting')
                  );
                """,
                rows,
            )
        return len(rows)

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
                WHERE id = ?
                  AND EXISTS (
                      SELECT 1 FROM projects p
                      WHERE p.id = image_assets.project_id
                        AND COALESCE(p.cleanup_status, 'retained') NOT IN ('cleaning', 'deleting')
                  );
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

    def approve_alt_text(
        self,
        approval_id: str,
        image_id: str,
        reviewer_id: Optional[str] = None,
        alt_text: Optional[str] = None,
        version_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomically approves alt-text for an image.
        If submitted text differs from the selected/latest version, a new manual
        alt_text_version is created and approved within the same transaction.
        """
        now = utc_now_iso()
        with self.db.session() as conn:
            cur = conn.execute("SELECT id, project_id FROM image_assets WHERE id = ?;", (image_id,))
            asset = cur.fetchone()
            if not asset:
                raise ValueError(f"Image asset '{image_id}' not found.")

            target_version_id = None
            submitted_text = alt_text.strip() if (alt_text is not None and alt_text.strip() != "") else None

            selected_version = None
            if version_id:
                cur = conn.execute(
                    "SELECT * FROM alt_text_versions WHERE id = ? AND image_id = ?;",
                    (version_id, image_id)
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Version '{version_id}' not found for image '{image_id}'.")
                selected_version = dict(row)

            if submitted_text:
                base_version = selected_version
                if not base_version:
                    cur = conn.execute(
                        "SELECT * FROM alt_text_versions WHERE image_id = ? ORDER BY version_number DESC LIMIT 1;",
                        (image_id,)
                    )
                    row = cur.fetchone()
                    base_version = dict(row) if row else None

                if base_version and (base_version.get("text") or "").strip() == submitted_text:
                    target_version_id = base_version["id"]
                else:
                    cur = conn.execute(
                        "SELECT COALESCE(MAX(version_number), 0) FROM alt_text_versions WHERE image_id = ?;",
                        (image_id,)
                    )
                    max_ver = cur.fetchone()[0]
                    next_ver = max_ver + 1
                    new_vid = str(uuid.uuid4())
                    lang = (base_version.get("language") if base_version else "en-GB") or "en-GB"
                    conn.execute(
                        """
                        INSERT INTO alt_text_versions (
                            id, image_id, version_number, text, revision_instruction,
                            provider, model, language, status, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            new_vid, image_id, next_ver, submitted_text,
                            "Approved client edit", "manual", "human_editor",
                            lang, "success", now
                        )
                    )
                    target_version_id = new_vid
            else:
                if selected_version:
                    target_version_id = selected_version["id"]
                else:
                    cur = conn.execute(
                        "SELECT id FROM alt_text_versions WHERE image_id = ? ORDER BY version_number DESC LIMIT 1;",
                        (image_id,)
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"Cannot approve: No draft version exists for image '{image_id}'.")
                    target_version_id = row[0]

            conn.execute(
                """
                INSERT INTO approvals (id, image_id, alt_text_version_id, reviewer_id, approved_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (approval_id, image_id, target_version_id, reviewer_id or "editor", now)
            )

            conn.execute(
                "UPDATE image_assets SET processing_status = 'approved', updated_at = ? WHERE id = ?;",
                (now, image_id)
            )

        return self.get_approval(approval_id)

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

    def _assert_not_cleaning_in_tx(
        self,
        conn: Any,
        project_id: str,
        operation_label: str,
        disallow_cleaned_extracted: bool = False,
    ) -> None:
        """Verify inside an active transaction that cleanup does not block job/export creation."""
        cur = conn.execute("SELECT cleanup_status FROM projects WHERE id = ?;", (project_id,))
        row = cur.fetchone()
        if row:
            cstatus = row["cleanup_status"] or "retained"
            if cstatus in ("cleaning", "deleting"):
                raise CleanupBlockedError(
                    f"Cannot {operation_label} while project '{project_id}' {'deletion' if cstatus == 'deleting' else 'cleanup'} is in progress."
                )
            if cstatus == "cleaned_all_source":
                raise CleanupBlockedError(
                    f"Cannot {operation_label}: project '{project_id}' source and extracted assets have been cleaned."
                )
            if disallow_cleaned_extracted and cstatus == "cleaned_extracted":
                raise CleanupBlockedError(
                    f"Cannot {operation_label}: project '{project_id}' extracted assets have been cleaned."
                )
        cur = conn.execute(
            """
            SELECT id FROM cleanup_operations
            WHERE project_id = ? AND status IN ('pending', 'deleting_blobs', 'deleting_records')
            LIMIT 1;
            """,
            (project_id,),
        )
        active = cur.fetchone()
        if active:
            raise CleanupBlockedError(
                f"Cannot {operation_label}: cleanup operation ({active['id']}) is in progress."
            )

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
            conn.execute("BEGIN IMMEDIATE;")
            self._assert_not_cleaning_in_tx(
                conn,
                project_id,
                operation_label=f"create {job_type} job",
                disallow_cleaned_extracted=(job_type != "extraction"),
            )
            conn.execute(
                """
                INSERT INTO jobs (
                    id, project_id, image_id, job_type, page_start, page_end, page_numbers,
                    status, attempt_count, max_attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?);
                """,
                (job_id, project_id, image_id, job_type, page_start, page_end,
                 json.dumps(page_numbers) if page_numbers else None, max_attempts, now)
            )
        return self.get_job(job_id)

    def claim_job(self, job_id: str, stale_before: str, now: str, worker_id: Optional[str] = None,
                  max_attempts: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Atomically claim a queued job or reclaim an abandoned running job."""
        with self.db.session() as conn:
            cursor = conn.execute(
                """UPDATE jobs SET status='running', attempt_count=attempt_count+1,
                   started_at=COALESCE(started_at, ?), lease_updated_at=?, error_message=NULL,
                   worker_id=?,max_attempts=COALESCE(?,max_attempts)
                   WHERE id=? AND attempt_count < COALESCE(?,max_attempts)
                   AND (status='queued' OR (status='running' AND COALESCE(lease_updated_at, started_at) < ?))
                   AND EXISTS (
                       SELECT 1 FROM projects p
                       WHERE p.id = jobs.project_id
                         AND COALESCE(p.cleanup_status, 'retained') NOT IN ('cleaning', 'deleting')
                   );""",
                (now, now, worker_id, max_attempts, job_id, max_attempts, stale_before),
            )
            if not cursor.rowcount:
                return None
        return self.get_job(job_id)

    def renew_job_lease(self, job_id: str, worker_id: str, now: str) -> bool:
        with self.db.session() as conn:
            cur = conn.execute(
                "UPDATE jobs SET lease_updated_at=? WHERE id=? AND status='running' AND worker_id=?;",
                (now, job_id, worker_id),
            )
            return cur.rowcount == 1

    def initialize_job_batches(self, job_id: str, page_batches: List[List[int]], total_document_pages: int,
                               max_attempts: int = 3):
        now = utc_now_iso()
        with self.db.session() as conn:
            for index, pages in enumerate(page_batches):
                conn.execute(
                    """INSERT INTO job_batches (job_id,batch_index,page_numbers,status,max_attempts,created_at,updated_at)
                       VALUES (?,?,?,'queued',?,?,?) ON CONFLICT(job_id,batch_index) DO NOTHING;""",
                    (job_id, index, json.dumps(pages), max_attempts, now, now),
                )
            conn.execute(
                "UPDATE jobs SET total_pages=?,pages_to_process=?,total_batches=? WHERE id=?;",
                (total_document_pages, sum(len(pages) for pages in page_batches), len(page_batches), job_id),
            )
            self._refresh_job_batch_counts(conn, job_id)

    @staticmethod
    def _refresh_job_batch_counts(conn, job_id: str):
        rows = conn.execute(
            "SELECT page_numbers FROM job_batches WHERE job_id=? AND status='completed';", (job_id,)
        ).fetchall()
        completed_pages = sum(len(json.loads(row[0])) for row in rows)
        conn.execute(
            "UPDATE jobs SET completed_batches=?,completed_pages=? WHERE id=?;",
            (len(rows), completed_pages, job_id),
        )

    def list_job_batches(self, job_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM job_batches WHERE job_id=? ORDER BY batch_index;", (job_id,)
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["page_numbers"] = json.loads(data["page_numbers"])
            result.append(data)
        return result

    def start_job_batch(self, job_id: str, batch_index: int, now: str, worker_id: Optional[str] = None) -> bool:
        with self.db.session() as conn:
            cur = conn.execute(
                """UPDATE job_batches SET status='running',attempt_count=attempt_count+1,
                   started_at=COALESCE(started_at,?),completed_at=NULL,lease_updated_at=?,
                   error_message=NULL,updated_at=? WHERE job_id=? AND batch_index=?
                   AND status!='completed' AND attempt_count<max_attempts
                   AND EXISTS (SELECT 1 FROM jobs WHERE id=? AND status='running'
                               AND (? IS NULL OR worker_id=?));""",
                (now, now, now, job_id, batch_index, job_id, worker_id, worker_id),
            )
            return cur.rowcount == 1

    def set_job_batch_status(self, job_id: str, batch_index: int, status: str,
                             error_message: Optional[str] = None, worker_id: Optional[str] = None) -> bool:
        now = utc_now_iso()
        completed_at = now if status in {"completed", "failed"} else None
        with self.db.session() as conn:
            cur = conn.execute(
                """UPDATE job_batches SET status=?,error_message=?,completed_at=?,lease_updated_at=?,updated_at=?
                   WHERE job_id=? AND batch_index=?
                   AND (? IS NULL OR EXISTS (SELECT 1 FROM jobs WHERE id=? AND status='running' AND worker_id=?));""",
                (status, error_message, completed_at, now if status == "running" else None,
                 now, job_id, batch_index, worker_id, job_id, worker_id),
            )
            if cur.rowcount != 1:
                return False
            self._refresh_job_batch_counts(conn, job_id)
            return True

    def update_claimed_job_status(self, job_id: str, worker_id: str, status: str,
                                  error_message: Optional[str] = None) -> bool:
        now = utc_now_iso()
        completed_at = now if status in {"completed", "failed", "cancelled"} else None
        with self.db.session() as conn:
            cur = conn.execute(
                """UPDATE jobs SET status=?,error_message=?,completed_at=?,
                   lease_updated_at=CASE WHEN ?='running' THEN ? ELSE lease_updated_at END
                   WHERE id=? AND status='running' AND worker_id=?;""",
                (status, error_message, completed_at, status, now, job_id, worker_id),
            )
            return cur.rowcount == 1

    def list_recoverable_jobs(self, stale_before: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                """SELECT id FROM jobs WHERE job_type='extraction' AND attempt_count < max_attempts
                   AND (status='queued' OR (status='running' AND COALESCE(lease_updated_at, started_at) < ?))
                   AND EXISTS (
                       SELECT 1 FROM projects p
                       WHERE p.id = jobs.project_id
                         AND COALESCE(p.cleanup_status, 'retained') NOT IN ('cleaning', 'deleting')
                   )
                   ORDER BY created_at LIMIT ?;""", (stale_before, limit)
            ).fetchall()
        return [job for row in rows if (job := self.get_job(row[0]))]

    def is_job_cancelled_or_project_deleting(self, job_id: str, project_id: str) -> bool:
        with self.db.session() as conn:
            prow = conn.execute("SELECT cleanup_status FROM projects WHERE id = ?;", (project_id,)).fetchone()
            if not prow or (prow["cleanup_status"] or "retained") in ("cleaning", "deleting"):
                return True
            jrow = conn.execute("SELECT status FROM jobs WHERE id = ?;", (job_id,)).fetchone()
            if not jrow or jrow["status"] == "cancelled":
                return True
        return False

    def fail_exhausted_jobs(self, stale_before: str) -> List[str]:
        now = utc_now_iso()
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT project_id FROM jobs WHERE job_type='extraction' AND status='running' AND attempt_count >= max_attempts AND COALESCE(lease_updated_at, started_at) < ?;",
                (stale_before,),
            ).fetchall()
            conn.execute(
                "UPDATE jobs SET status='failed', error_message='Worker stopped during final retry', completed_at=? WHERE job_type='extraction' AND status='running' AND attempt_count >= max_attempts AND COALESCE(lease_updated_at, started_at) < ?;",
                (now, stale_before),
            )
        projects = sorted({row[0] for row in rows})
        for project_id in projects:
            self.update_project_status(project_id, status="failed")
        return projects

    def find_image_asset_by_source(self, project_id: str, page_number: int, xref: int, placement_index: int):
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT id FROM image_assets WHERE project_id=? AND page_number=? AND xref=? AND placement_index=? LIMIT 1;",
                (project_id, page_number, xref, placement_index),
            ).fetchone()
        return self.get_image_asset(row[0]) if row else None

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM jobs WHERE id = ?;", (job_id,))
            row = cursor.fetchone()
            data = dict(row) if row else None
            if data and data.get("page_numbers"):
                data["page_numbers"] = json.loads(data["page_numbers"])
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
                conn.execute(
                    """UPDATE jobs SET status='running', error_message=NULL,
                       started_at=COALESCE(started_at, ?), lease_updated_at=?
                       WHERE id=? AND status IN ('queued','running');""",
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
            conn.execute(
                """
                    UPDATE jobs SET
                    status = ?,
                    attempt_count = ?,
                    error_message = ?,
                    started_at = COALESCE(started_at, ?),
                        completed_at = ?,
                        lease_updated_at = CASE WHEN ? = 'running' THEN ? ELSE lease_updated_at END
                WHERE id = ? AND status NOT IN ('completed','cancelled');
                """,
                    (status, attempts, error_message, started_at, completed_at, status, now, job_id)
            )
        return self.get_job(job_id)

    def list_project_jobs(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at ASC;", (project_id,))
            rows = [dict(r) for r in cursor.fetchall()]
            for data in rows:
                if data.get("page_numbers"):
                    data["page_numbers"] = json.loads(data["page_numbers"])
            return rows

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
            conn.execute("BEGIN IMMEDIATE;")
            self._assert_not_cleaning_in_tx(
                conn,
                project_id,
                operation_label="create export",
                disallow_cleaned_extracted=True,
            )
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
        is_active: bool = True,
    ) -> Dict[str, Any]:
        if not password or len(password) > 256:
            raise ValueError("Password must be between 1 and 256 characters.")
        now = utc_now_iso()
        password_hash, password_salt = _hash_password(password)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO users (id, email, password_hash, password_salt, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (user_id, email, password_hash, password_salt, 1 if is_active else 0, now)
            )
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?;", (user_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["is_active"] = bool(data.get("is_active"))
            return data

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE email = ?;", (email,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            data["is_active"] = bool(data.get("is_active"))
            return data

    def set_user_active(self, user_id: str, is_active: bool) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            conn.execute(
                "UPDATE users SET is_active = ? WHERE id = ?;",
                (1 if is_active else 0, user_id),
            )
        return self.get_user(user_id)

    def verify_user_password(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        if not email or not password or len(password) > 256:
            return None
        user = self.get_user_by_email(email)
        if not user:
            # Perform dummy hash computation to mitigate timing attacks
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
            conn.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?);
                """,
                (session_id, user_id, token_hash, expires_at, now)
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

    def delete_user_sessions(self, user_id: str) -> int:
        with self.db.session() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE user_id = ?;",
                (user_id,)
            )
            return cursor.rowcount or 0

    def cleanup_expired_sessions(self) -> int:
        now = utc_now_iso()
        with self.db.session() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?;",
                (now,)
            )
            return cursor.rowcount or 0

    def record_failed_login(self, identifier: str, ip_address: str) -> None:
        import uuid
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO login_attempts (id, identifier, ip_address, attempted_at)
                VALUES (?, ?, ?, ?);
                """,
                (str(uuid.uuid4()), identifier.lower().strip(), ip_address, now),
            )

    def count_failed_logins(self, identifier: str, ip_address: str, since_iso: str) -> int:
        with self.db.session() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM login_attempts
                WHERE (identifier = ? OR ip_address = ?) AND attempted_at >= ?;
                """,
                (identifier.lower().strip(), ip_address, since_iso),
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def clear_failed_logins(self, identifier: str, ip_address: str) -> None:
        with self.db.session() as conn:
            conn.execute(
                "DELETE FROM login_attempts WHERE identifier = ? OR ip_address = ?;",
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
            conn.execute(
                """
                INSERT INTO upload_keys (
                    id, upload_key, project_id, status, content_type,
                    owner_id, filename, expected_size, blob_key,
                    source_storage_key, source_checksum, source_size,
                    total_chunks, chunk_size, uploaded_chunks, temp_dir,
                    expires_at, error_message, created_at, updated_at
                ) VALUES (?, ?, NULL, 'uploading', ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, 0, NULL, ?, NULL, ?, ?);
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
            cur = conn.execute(
                "UPDATE upload_keys SET token_issued=1, updated_at=? WHERE upload_key=? AND status='uploading' AND token_issued=0 AND expires_at>? AND owner_id IS ?;",
                (now, upload_key, now, owner_id),
            )
            return cur.rowcount == 1

    def claim_upload_finalization(self, upload_key: str, owner_id: Optional[str], now: str) -> bool:
        with self.db.session() as conn:
            cur = conn.execute(
                "UPDATE upload_keys SET status='finalizing', updated_at=? WHERE upload_key=? AND status='uploading' AND token_issued=1 AND owner_id IS ? AND expires_at>?;",
                (now, upload_key, owner_id, now),
            )
            return cur.rowcount == 1

    def finalize_upload_project(self, *, upload_key, project_id, job_id, owner_id,
                                name, source_filename, source_storage_key,
                                source_checksum, source_size, page_start=None, page_end=None, page_numbers=None):
        """Atomically create project/job and consume the upload intent."""
        now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute(
                """INSERT INTO projects (id,name,source_filename,source_storage_key,source_checksum,
                   language_variant,status,cleanup_status,owner_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,'en-GB','queued','retained',?,?,?) ON CONFLICT(id) DO NOTHING;""",
                (project_id, name, source_filename, source_storage_key, source_checksum, owner_id, now, now),
            )
            conn.execute(
                """INSERT INTO jobs (id,project_id,job_type,page_start,page_end,page_numbers,status,attempt_count,max_attempts,created_at)
                   VALUES (?,?, 'extraction', ?, ?, ?, 'queued',0,3,?) ON CONFLICT(id) DO NOTHING;""",
                (job_id, project_id, page_start, page_end, json.dumps(page_numbers) if page_numbers else None, now),
            )
            conn.execute(
                """UPDATE upload_keys SET status='completed',project_id=?,source_storage_key=?,
                   source_checksum=?,source_size=?,updated_at=? WHERE upload_key=? AND owner_id IS ?
                   AND status IN ('finalizing','completed');""",
                (project_id, source_storage_key, source_checksum, source_size, now, upload_key, owner_id),
            )
        return {"project": self.get_project(project_id), "job": self.get_job(job_id)}

    def get_upload_key(self, upload_key: str) -> Optional[Dict[str, Any]]:
        """Get upload key record by upload_key."""
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM upload_keys WHERE upload_key = ?;",
                (upload_key,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

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
            fields.append("status = ?")
            values.append(status)
        if source_storage_key is not None:
            fields.append("source_storage_key = ?")
            values.append(source_storage_key)
        if source_checksum is not None:
            fields.append("source_checksum = ?")
            values.append(source_checksum)
        if source_size is not None:
            fields.append("source_size = ?")
            values.append(source_size)
        if total_chunks is not None:
            fields.append("total_chunks = ?")
            values.append(total_chunks)
        if chunk_size is not None:
            fields.append("chunk_size = ?")
            values.append(chunk_size)
        if uploaded_chunks is not None:
            fields.append("uploaded_chunks = ?")
            values.append(uploaded_chunks)
        if temp_dir is not None:
            fields.append("temp_dir = ?")
            values.append(temp_dir)
        if error_message is not None:
            fields.append("error_message = ?")
            values.append(error_message)
        if project_id is not None:
            fields.append("project_id = ?")
            values.append(project_id)
        if token_issued is not None:
            fields.append("token_issued = ?")
            values.append(1 if token_issued else 0)
        if not fields:
            return self.get_upload_key(upload_key)

        fields.append("updated_at = ?")
        values.append(now)
        values.append(upload_key)

        query = f"UPDATE upload_keys SET {', '.join(fields)} WHERE upload_key = ?;"
        with self.db.session() as conn:
            conn.execute(query, tuple(values))
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
            conn.execute(
                """
                UPDATE upload_keys
                SET status = 'completed',
                    project_id = ?,
                    source_storage_key = ?,
                    source_checksum = ?,
                    source_size = ?,
                    updated_at = ?
                WHERE upload_key = ?;
                """,
                (project_id, source_storage_key, source_checksum, source_size, now, upload_key)
            )
        return self.get_upload_key(upload_key)

    def expire_upload_keys(self) -> int:
        """Mark expired upload keys as 'expired'. Returns count of expired keys."""
        now = utc_now_iso()
        with self.db.session() as conn:
            cursor = conn.execute(
                "UPDATE upload_keys SET status = 'expired', updated_at = ? WHERE status = 'uploading' AND expires_at < ?;",
                (now, now)
            )
            return cursor.rowcount or 0

    def list_upload_keys_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        """List upload keys associated with a project."""
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM upload_keys WHERE project_id = ? ORDER BY created_at DESC;",
                (project_id,)
            )
            return [dict(r) for r in cursor.fetchall()]

    # ============================================================
    # Storage Lifecycle Management & Post-Export Project Cleanup
    # ============================================================

    @staticmethod
    def _normalize_cleanup_mode(cleanup_mode: str) -> str:
        m = (cleanup_mode or "").strip().lower()
        if m in ("extracted", "mode_a", "extracted_only"):
            return "extracted"
        if m in ("all_source", "mode_b", "all", "source_and_extracted"):
            return "all_source"
        raise ValueError(f"Invalid cleanup mode '{cleanup_mode}'. Allowed modes: 'extracted', 'all_source'.")

    def _format_cleanup_operation_row(self, row: Any) -> Optional[Dict[str, Any]]:
        if not row:
            return None
        data = dict(row)
        for json_col in ("target_blob_keys", "deleted_blob_keys", "missing_blob_keys"):
            val = data.get(json_col)
            if isinstance(val, str):
                try:
                    data[json_col] = json.loads(val)
                except Exception:
                    data[json_col] = []
            elif val is None:
                data[json_col] = []

        for dict_col in ("failed_blob_keys", "db_records_summary"):
            val = data.get(dict_col)
            if isinstance(val, str):
                try:
                    data[dict_col] = json.loads(val)
                except Exception:
                    data[dict_col] = {}
            elif val is None:
                data[dict_col] = {}

        val = data.get("audit_summary")
        if isinstance(val, str):
            try:
                data["audit_summary"] = json.loads(val)
            except Exception:
                data["audit_summary"] = []
        elif val is None:
            data["audit_summary"] = []

        return data

    def get_cleanup_operation(self, operation_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute("SELECT * FROM cleanup_operations WHERE id = ?;", (operation_id,))
            return self._format_cleanup_operation_row(cursor.fetchone())

    def get_active_cleanup_operation(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM cleanup_operations
                WHERE project_id = ? AND status IN ('pending', 'deleting_blobs', 'deleting_records')
                ORDER BY created_at DESC LIMIT 1;
                """,
                (project_id,)
            )
            return self._format_cleanup_operation_row(cursor.fetchone())

    def get_latest_cleanup_operation(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM cleanup_operations WHERE project_id = ? ORDER BY created_at DESC LIMIT 1;",
                (project_id,)
            )
            return self._format_cleanup_operation_row(cursor.fetchone())

    def list_cleanup_operations(self, project_id: str) -> List[Dict[str, Any]]:
        with self.db.session() as conn:
            cursor = conn.execute(
                "SELECT * FROM cleanup_operations WHERE project_id = ? ORDER BY created_at DESC;",
                (project_id,)
            )
            return [self._format_cleanup_operation_row(r) for r in cursor.fetchall()]

    def _build_cleanup_preview_in_tx(
        self, conn: Any, project_id: str, mode: str
    ) -> Dict[str, Any]:
        """Compute cleanup preview & blocking reasons using an existing connection/transaction."""
        cur = conn.execute("SELECT * FROM projects WHERE id = ?;", (project_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found.")
        project = dict(row)

        blocking_reasons: List[str] = []

        # 1. Verified export check
        cur = conn.execute(
            """
            SELECT id, file_storage_key, status, verified_at, created_at FROM exports
            WHERE project_id = ? AND (status IN ('verified', 'delivered') OR verified_at IS NOT NULL)
            ORDER BY created_at DESC;
            """,
            (project_id,)
        )
        verified_export_rows = [dict(r) for r in cur.fetchall()]
        if not verified_export_rows:
            blocking_reasons.append(
                "Project has no verified export. A verified DOCX export is required before cleanup."
            )

        # All exports (to protect from deletion)
        cur = conn.execute("SELECT file_storage_key FROM exports WHERE project_id = ?;", (project_id,))
        export_storage_keys = {r["file_storage_key"] for r in cur.fetchall() if r["file_storage_key"]}

        # 2. Active background job fencing
        cur = conn.execute(
            "SELECT id, status FROM jobs WHERE project_id = ? AND status IN ('queued', 'running', 'processing');",
            (project_id,)
        )
        active_jobs = [dict(r) for r in cur.fetchall()]
        if active_jobs:
            blocking_reasons.append(
                f"Project has active background job(s) in progress (e.g. job {active_jobs[0]['id']})."
            )

        # 3. Active cleanup operation fencing
        cur = conn.execute(
            """
            SELECT id, status FROM cleanup_operations
            WHERE project_id = ? AND status IN ('pending', 'deleting_blobs', 'deleting_records');
            """,
            (project_id,)
        )
        active_cleanups = [dict(r) for r in cur.fetchall()]
        if active_cleanups:
            blocking_reasons.append(
                f"A cleanup operation ({active_cleanups[0]['id']}) is already in progress for this project."
            )

        # 4. Check if already cleaned or currently cleaning
        curr_cleanup_status = project.get("cleanup_status", "retained")
        if curr_cleanup_status == "cleaning":
            blocking_reasons.append("Project cleanup is currently in progress.")
        elif curr_cleanup_status == "deleting":
            blocking_reasons.append("Project permanent deletion is currently in progress.")
        elif curr_cleanup_status == "cleaned_all_source":
            blocking_reasons.append(
                "Project has already undergone full cleanup (both source PDF and extracted assets)."
            )
        elif curr_cleanup_status == "cleaned_extracted" and mode == "extracted":
            blocking_reasons.append(
                "Extracted assets have already been cleaned for this project."
            )

        # 5. Determine target blob keys (strictly images + source PDF if Mode B; NEVER exports)
        cur = conn.execute(
            "SELECT file_storage_key FROM image_assets WHERE project_id = ? ORDER BY page_number ASC, placement_index ASC;",
            (project_id,)
        )
        target_blob_keys: List[str] = []
        for r in cur.fetchall():
            k = r["file_storage_key"]
            if k and k not in export_storage_keys and not k.startswith("exports/"):
                target_blob_keys.append(k)

        source_key = project.get("source_storage_key", "")
        if mode == "all_source" and source_key and source_key not in export_storage_keys:
            if source_key not in target_blob_keys:
                target_blob_keys.append(source_key)

        # 6. Count database records to be removed
        cur = conn.execute("SELECT COUNT(*) FROM jobs WHERE project_id = ?;", (project_id,))
        jobs_count = cur.fetchone()[0]

        cur = conn.execute(
            "SELECT COUNT(*) FROM job_batches WHERE job_id IN (SELECT id FROM jobs WHERE project_id = ?);",
            (project_id,)
        )
        job_batches_count = cur.fetchone()[0]

        cur = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);",
            (project_id,)
        )
        approvals_count = cur.fetchone()[0]

        cur = conn.execute(
            "SELECT COUNT(*) FROM alt_text_versions WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);",
            (project_id,)
        )
        alt_text_versions_count = cur.fetchone()[0]

        cur = conn.execute("SELECT COUNT(*) FROM image_assets WHERE project_id = ?;", (project_id,))
        image_assets_count = cur.fetchone()[0]

        cur = conn.execute("SELECT COUNT(*) FROM figure_groups WHERE project_id = ?;", (project_id,))
        figure_groups_count = cur.fetchone()[0]

        cur = conn.execute("SELECT COUNT(*) FROM ai_usage_ledger WHERE project_id = ?;", (project_id,))
        ai_usage_count = cur.fetchone()[0]

        cur = conn.execute("SELECT COUNT(*) FROM audit_events WHERE project_id = ?;", (project_id,))
        audit_events_count = cur.fetchone()[0]

        return {
            "project_id": project_id,
            "cleanup_mode": mode,
            "can_cleanup": len(blocking_reasons) == 0,
            "blocking_reasons": blocking_reasons,
            "target_blob_keys": target_blob_keys,
            "target_blob_count": len(target_blob_keys),
            "records_to_delete": {
                "jobs": jobs_count,
                "job_batches": job_batches_count,
                "approvals": approvals_count,
                "alt_text_versions": alt_text_versions_count,
                "image_assets": image_assets_count,
                "figure_groups": figure_groups_count,
            },
            "preserved_assets": {
                "exports_count": len(export_storage_keys),
                "export_storage_keys": sorted(list(export_storage_keys)),
                "source_pdf_preserved": (mode != "all_source"),
                "source_storage_key": source_key,
                "ai_usage_count": ai_usage_count,
                "audit_events_count": audit_events_count,
            },
        }

    def preview_project_cleanup(self, project_id: str, cleanup_mode: str = "extracted") -> Dict[str, Any]:
        """
        Inspect project assets and dependencies to produce a dry-run preview
        of what post-export cleanup will delete and preserve.
        Enforces verified export requirement, active job fencing, and active cleanup fencing.
        """
        mode = self._normalize_cleanup_mode(cleanup_mode)
        with self.db.session() as conn:
            return self._build_cleanup_preview_in_tx(conn, project_id, mode)

    def prepare_cleanup_operation(self, project_id: str, cleanup_mode: str = "extracted") -> Dict[str, Any]:
        """
        Validates cleanup eligibility inside an immediate write lock, creates a durable
        pending cleanup manifest row, captures approved alt text in an audit snapshot,
        and sets project status to 'cleaning'.
        """
        mode = self._normalize_cleanup_mode(cleanup_mode)
        audit_summary = []
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            preview = self._build_cleanup_preview_in_tx(conn, project_id, mode)
            if not preview["can_cleanup"]:
                raise CleanupBlockedError("; ".join(preview["blocking_reasons"]))

            target_keys = preview["target_blob_keys"]
            records_summary = preview["records_to_delete"]
            cur = conn.execute(
                """
                SELECT
                    ia.id as image_id,
                    ia.page_number,
                    ia.suggested_filename,
                    ia.original_caption,
                    ia.file_storage_key,
                    app.id as approval_id,
                    app.reviewer_id,
                    app.approved_at,
                    v.version_number as approved_version_number,
                    v.text as approved_text
                FROM image_assets ia
                LEFT JOIN approvals app ON app.image_id = ia.id
                LEFT JOIN alt_text_versions v ON v.id = app.alt_text_version_id
                WHERE ia.project_id = ?
                ORDER BY ia.page_number ASC, ia.placement_index ASC;
                """,
                (project_id,)
            )
            for r in cur.fetchall():
                row_dict = dict(r)
                app_dict = None
                if row_dict.get("approval_id"):
                    app_dict = {
                        "approval_id": row_dict["approval_id"],
                        "version_number": row_dict.get("approved_version_number"),
                        "text": row_dict.get("approved_text"),
                        "reviewer_id": row_dict.get("reviewer_id"),
                        "approved_at": row_dict.get("approved_at"),
                    }
                audit_summary.append({
                    "image_id": row_dict["image_id"],
                    "page_number": row_dict["page_number"],
                    "suggested_filename": row_dict["suggested_filename"],
                    "original_caption": row_dict.get("original_caption", ""),
                    "file_storage_key": row_dict["file_storage_key"],
                    "approved_version": app_dict,
                })

            operation_id = str(uuid.uuid4())
            now = utc_now_iso()

            conn.execute(
                """
                INSERT INTO cleanup_operations (
                    id, project_id, cleanup_mode, status, target_blob_keys,
                    deleted_blob_keys, missing_blob_keys, failed_blob_keys,
                    db_records_summary, audit_summary, error_message, retry_count,
                    started_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, '[]', '[]', '{}', ?, ?, NULL, 0, ?, ?, ?);
                """,
                (
                    operation_id, project_id, mode,
                    json.dumps(target_keys),
                    json.dumps(records_summary),
                    json.dumps(audit_summary),
                    now, now, now
                )
            )
            conn.execute(
                "UPDATE projects SET cleanup_status = 'cleaning', updated_at = ? WHERE id = ?;",
                (now, project_id)
            )

        self.record_audit_event(
            event_id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="cleanup_started",
            target_type="project",
            target_id=project_id,
            details={
                "cleanup_operation_id": operation_id,
                "cleanup_mode": mode,
                "target_blob_count": len(target_keys),
            },
            actor_id="editor",
        )

        return self.get_cleanup_operation(operation_id)

    def update_cleanup_operation_blobs(
        self,
        operation_id: str,
        status: str,
        deleted_keys: List[str],
        missing_keys: List[str],
        failed_keys: Dict[str, str],
        error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Records intermediate progress of storage object deletions into the manifest.
        If status is 'failed', resets project cleanup_status to 'retained' and logs an audit event.
        """
        now = utc_now_iso()
        op = self.get_cleanup_operation(operation_id)
        if not op:
            raise ValueError(f"Cleanup operation '{operation_id}' not found.")

        project_id = op["project_id"]

        with self.db.session() as conn:
            conn.execute(
                """
                UPDATE cleanup_operations
                SET status = ?,
                    deleted_blob_keys = ?,
                    missing_blob_keys = ?,
                    failed_blob_keys = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (
                    status,
                    json.dumps(deleted_keys),
                    json.dumps(missing_keys),
                    json.dumps(failed_keys),
                    error_message,
                    now,
                    operation_id
                )
            )
            if status == "failed":
                conn.execute(
                    "UPDATE projects SET cleanup_status = 'retained', updated_at = ? WHERE id = ?;",
                    (now, project_id)
                )

        if status == "failed":
            self.record_audit_event(
                event_id=str(uuid.uuid4()),
                project_id=project_id,
                event_type="cleanup_failed",
                target_type="cleanup_operation",
                target_id=operation_id,
                details={
                    "error_message": error_message,
                    "failed_blob_count": len(failed_keys),
                },
                actor_id="editor",
            )

        return self.get_cleanup_operation(operation_id)

    def finalize_cleanup_records(self, operation_id: str) -> Dict[str, Any]:
        """
        Within an atomic transaction, validates that all target blob deletions have succeeded,
        then deletes dependent database records in referential foreign-key order, updates project
        cleanup status, marks the cleanup operation as completed, and appends a final audit event.
        """
        op = self.get_cleanup_operation(operation_id)
        if not op:
            raise ValueError(f"Cleanup operation '{operation_id}' not found.")

        if op["status"] == "completed":
            return op

        project_id = op["project_id"]
        mode = op["cleanup_mode"]
        target_keys = set(op.get("target_blob_keys") or [])
        deleted_keys = set(op.get("deleted_blob_keys") or [])
        missing_keys = set(op.get("missing_blob_keys") or [])
        failed_keys = dict(op.get("failed_blob_keys") or {})

        unresolved = target_keys - deleted_keys - missing_keys
        if unresolved or failed_keys:
            raise CleanupBlockedError(
                f"Cannot finalize database records: {len(unresolved)} target keys unresolved, "
                f"{len(failed_keys)} failed keys present."
            )

        now = utc_now_iso()

        with self.db.session() as conn:
            # 1. Job batches and jobs
            cur = conn.execute("SELECT id FROM jobs WHERE project_id = ?;", (project_id,))
            job_ids = [r[0] for r in cur.fetchall()]
            if job_ids:
                ph = ",".join("?" for _ in job_ids)
                conn.execute(f"DELETE FROM job_batches WHERE job_id IN ({ph});", tuple(job_ids))
            conn.execute("DELETE FROM jobs WHERE project_id = ?;", (project_id,))

            # 2. Approvals and alt text versions
            cur = conn.execute("SELECT id FROM image_assets WHERE project_id = ?;", (project_id,))
            image_ids = [r[0] for r in cur.fetchall()]
            if image_ids:
                ph = ",".join("?" for _ in image_ids)
                conn.execute(f"DELETE FROM approvals WHERE image_id IN ({ph});", tuple(image_ids))
                conn.execute(f"DELETE FROM alt_text_versions WHERE image_id IN ({ph});", tuple(image_ids))

            # 3. Disassociate image_id in AI usage ledger while preserving token/cost history
            conn.execute("UPDATE ai_usage_ledger SET image_id = NULL WHERE project_id = ?;", (project_id,))

            # 4. Image assets
            conn.execute("DELETE FROM image_assets WHERE project_id = ?;", (project_id,))

            # 5. Figure groups
            conn.execute("DELETE FROM figure_groups WHERE project_id = ?;", (project_id,))

            # 6. Update project status and source storage key if Mode B
            new_cleanup_status = "cleaned_all_source" if mode == "all_source" else "cleaned_extracted"
            if mode == "all_source":
                conn.execute(
                    "UPDATE projects SET source_storage_key = '', cleanup_status = ?, updated_at = ? WHERE id = ?;",
                    (new_cleanup_status, now, project_id)
                )
            else:
                conn.execute(
                    "UPDATE projects SET cleanup_status = ?, updated_at = ? WHERE id = ?;",
                    (new_cleanup_status, now, project_id)
                )

            # 7. Mark cleanup operation as completed
            conn.execute(
                """
                UPDATE cleanup_operations
                SET status = 'completed', completed_at = ?, updated_at = ?
                WHERE id = ?;
                """,
                (now, now, operation_id)
            )

        self.record_audit_event(
            event_id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="cleanup_completed",
            target_type="project",
            target_id=project_id,
            details={
                "cleanup_operation_id": operation_id,
                "cleanup_mode": mode,
                "deleted_records": op.get("db_records_summary") or {},
                "deleted_blob_count": len(deleted_keys),
                "missing_blob_count": len(missing_keys),
            },
            actor_id="editor",
        )

        return self.get_cleanup_operation(operation_id)

    def execute_project_cleanup(self, project_id: str, cleanup_mode: str, storage: Any) -> Dict[str, Any]:
        """
        Coordinates the complete safe post-export cleanup workflow:
          1. Pre-validation and manifest initialization (pending)
          2. Storage object deletion with individual failure tracking (deleting_blobs)
          3. Atomic referential database record purging (deleting_records -> completed)
        If blob deletions fail, database records are left completely intact.
        """
        op = self.prepare_cleanup_operation(project_id, cleanup_mode)
        op_id = op["id"]
        target_keys = op.get("target_blob_keys") or []

        self.update_cleanup_operation_blobs(
            operation_id=op_id,
            status="deleting_blobs",
            deleted_keys=[],
            missing_keys=[],
            failed_keys={},
        )

        try:
            deletion_result = storage.delete_objects(project_id, target_keys)
        except Exception as exc:
            self.update_cleanup_operation_blobs(
                operation_id=op_id,
                status="failed",
                deleted_keys=[],
                missing_keys=[],
                failed_keys={k: str(exc) for k in target_keys},
                error_message=f"Storage deletion exception: {str(exc)}",
            )
            raise

        if deletion_result.has_failures:
            self.update_cleanup_operation_blobs(
                operation_id=op_id,
                status="failed",
                deleted_keys=deletion_result.deleted,
                missing_keys=deletion_result.missing,
                failed_keys=deletion_result.failed,
                error_message=f"Storage deletion failed for {len(deletion_result.failed)} object(s).",
            )
            raise StorageError(
                f"Storage deletion partially failed for project {project_id}: {deletion_result.failed}"
            )

        self.update_cleanup_operation_blobs(
            operation_id=op_id,
            status="deleting_records",
            deleted_keys=deletion_result.deleted,
            missing_keys=deletion_result.missing,
            failed_keys={},
        )

        return self.finalize_cleanup_records(op_id)

    def retry_cleanup_operation(self, operation_id: str, storage: Any) -> Dict[str, Any]:
        """
        Retries an incomplete or failed cleanup operation for any unresolved target blob keys.
        """
        op = self.get_cleanup_operation(operation_id)
        if not op:
            raise ValueError(f"Cleanup operation '{operation_id}' not found.")

        if op["status"] == "completed":
            return op

        project_id = op["project_id"]
        target_keys = op.get("target_blob_keys") or []
        deleted_keys = set(op.get("deleted_blob_keys") or [])
        missing_keys = set(op.get("missing_blob_keys") or [])

        unresolved = [k for k in target_keys if k not in deleted_keys and k not in missing_keys]

        with self.db.session() as conn:
            conn.execute(
                "UPDATE cleanup_operations SET retry_count = retry_count + 1 WHERE id = ?;",
                (operation_id,)
            )

        if unresolved:
            self.update_cleanup_operation_blobs(
                operation_id=operation_id,
                status="deleting_blobs",
                deleted_keys=list(deleted_keys),
                missing_keys=list(missing_keys),
                failed_keys={},
            )
            try:
                res = storage.delete_objects(project_id, unresolved)
            except Exception as exc:
                new_failed = dict(op.get("failed_blob_keys") or {})
                for k in unresolved:
                    new_failed[k] = str(exc)
                self.update_cleanup_operation_blobs(
                    operation_id=operation_id,
                    status="failed",
                    deleted_keys=list(deleted_keys),
                    missing_keys=list(missing_keys),
                    failed_keys=new_failed,
                    error_message=f"Storage deletion retry exception: {str(exc)}",
                )
                raise

            deleted_keys.update(res.deleted)
            missing_keys.update(res.missing)

            if res.has_failures:
                self.update_cleanup_operation_blobs(
                    operation_id=operation_id,
                    status="failed",
                    deleted_keys=list(deleted_keys),
                    missing_keys=list(missing_keys),
                    failed_keys=res.failed,
                    error_message=f"Storage deletion retry failed for {len(res.failed)} object(s).",
                )
                raise StorageError(f"Storage deletion retry failed: {res.failed}")

        self.update_cleanup_operation_blobs(
            operation_id=operation_id,
            status="deleting_records",
            deleted_keys=list(deleted_keys),
            missing_keys=list(missing_keys),
            failed_keys={},
        )

        return self.finalize_cleanup_records(operation_id)

    def get_project_storage_summary(self, project_id: str) -> Dict[str, Any]:
        """
        Returns estimated project storage observability: counts of source, extracted,
        and export assets, along with cleanup eligibility and latest operation status.
        """
        project = self.get_project(project_id)
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        images = self.list_project_images(project_id)
        exports = self.list_project_exports(project_id)
        latest_op = self.get_latest_cleanup_operation(project_id)

        source_key = project.get("source_storage_key", "")
        has_source = bool(source_key)

        image_keys = [img["file_storage_key"] for img in images if img.get("file_storage_key")]
        export_keys = [exp["file_storage_key"] for exp in exports if exp.get("file_storage_key")]

        verified_exports = [
            exp for exp in exports
            if exp.get("status") in ("verified", "delivered") or exp.get("verified_at")
        ]

        curr_cleanup_status = project.get("cleanup_status", "retained")
        is_eligible = (
            len(verified_exports) > 0
            and curr_cleanup_status not in ("cleaned_all_source", "cleaning")
        )

        return {
            "project_id": project_id,
            "project_name": project.get("name", ""),
            "cleanup_status": curr_cleanup_status,
            "has_source_file": has_source,
            "source_storage_key": source_key,
            "image_count": len(images),
            "image_storage_keys": image_keys,
            "export_count": len(exports),
            "export_storage_keys": export_keys,
            "verified_export_count": len(verified_exports),
            "is_cleanup_eligible": is_eligible,
            "latest_cleanup_operation": latest_op,
        }

    # ============================================================
    # Orphan Upload Recovery
    # ============================================================

    ORPHAN_CLAIM_TTL_SECONDS = 300

    def list_expired_incomplete_uploads(
        self,
        limit: int = 50,
        claim_ttl_seconds: int = ORPHAN_CLAIM_TTL_SECONDS,
    ) -> List[Dict[str, Any]]:
        """Return expired upload intents that never completed or became a project source."""
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(seconds=max(1, int(claim_ttl_seconds)))).isoformat()
        with self.db.session() as conn:
            cur = conn.execute(
                """
                SELECT * FROM upload_keys
                WHERE (
                    status IN ('uploading', 'expired', 'orphan_cleanup_failed')
                    OR (status = 'cleaning_orphan' AND updated_at < ?)
                )
                  AND expires_at < ?
                  AND project_id IS NULL
                ORDER BY expires_at ASC
                LIMIT ?;
                """,
                (stale_before, now, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def claim_orphan_upload_cleanup(
        self,
        upload_key: str,
        claim_ttl_seconds: int = ORPHAN_CLAIM_TTL_SECONDS,
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically claim an expired incomplete upload for orphan Blob cleanup.
        Reclaims stale 'cleaning_orphan' rows only after claim_ttl_seconds has elapsed.
        Returns None if it is currently finalizing, already completed, freshly claimed, or not expired.
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(seconds=max(1, int(claim_ttl_seconds)))).isoformat()
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """
                UPDATE upload_keys
                SET status = 'cleaning_orphan', updated_at = ?
                WHERE upload_key = ?
                  AND (
                      status IN ('uploading', 'expired', 'orphan_cleanup_failed')
                      OR (status = 'cleaning_orphan' AND updated_at < ?)
                  )
                  AND expires_at < ?
                  AND project_id IS NULL;
                """,
                (now, upload_key, stale_before, now),
            )
            if not cur.rowcount:
                return None
            row = conn.execute("SELECT * FROM upload_keys WHERE upload_key = ?;", (upload_key,)).fetchone()
            return dict(row) if row else None

    def mark_orphan_upload_cleaned(self, upload_key: str) -> None:
        with self.db.session() as conn:
            conn.execute(
                "UPDATE upload_keys SET status = 'cleaned_orphan', updated_at = ? WHERE upload_key = ? AND status = 'cleaning_orphan';",
                (utc_now_iso(), upload_key),
            )

    def mark_orphan_upload_cleanup_failed(self, upload_key: str) -> None:
        with self.db.session() as conn:
            conn.execute(
                "UPDATE upload_keys SET status = 'orphan_cleanup_failed', updated_at = ? WHERE upload_key = ? AND status = 'cleaning_orphan';",
                (utc_now_iso(), upload_key),
            )

    def recover_orphan_uploads(
        self,
        storage: Any,
        limit: int = 50,
        claim_ttl_seconds: int = ORPHAN_CLAIM_TTL_SECONDS,
    ) -> Dict[str, Any]:
        """
        Safely delete orphaned Blob objects from expired, incomplete uploads.
        Idempotent and retry-safe; never deletes objects belonging to finalizing or completed projects.
        """
        candidates = self.list_expired_incomplete_uploads(
            limit=limit,
            claim_ttl_seconds=claim_ttl_seconds,
        )
        cleaned: List[str] = []
        missing: List[str] = []
        failed: Dict[str, str] = {}
        skipped: List[str] = []

        for candidate in candidates:
            upload_key = candidate["upload_key"]
            claimed = self.claim_orphan_upload_cleanup(
                upload_key,
                claim_ttl_seconds=claim_ttl_seconds,
            )
            if not claimed:
                skipped.append(upload_key)
                continue

            blob_key = (claimed.get("blob_key") or "").strip()
            if not blob_key or "/" not in blob_key:
                self.mark_orphan_upload_cleaned(upload_key)
                missing.append(upload_key)
                continue

            proj_prefix, rel_key = blob_key.split("/", 1)
            # Double-check no project exists for this source prefix
            if self.get_project(proj_prefix):
                self.update_upload_key(upload_key, status="completed", project_id=proj_prefix)
                skipped.append(upload_key)
                continue

            try:
                res = storage.delete_object(proj_prefix, rel_key)
                if res.has_failures:
                    self.mark_orphan_upload_cleanup_failed(upload_key)
                    failed[upload_key] = "; ".join(res.failed.values())
                else:
                    self.mark_orphan_upload_cleaned(upload_key)
                    if res.deleted:
                        cleaned.append(upload_key)
                    else:
                        missing.append(upload_key)
            except Exception as exc:
                self.mark_orphan_upload_cleanup_failed(upload_key)
                failed[upload_key] = str(exc)

        return {
            "processed": len(candidates),
            "cleaned": cleaned,
            "missing": missing,
            "failed": failed,
            "skipped": skipped,
        }

    # ============================================================
    # Permanent Project Deletion (All Assets, Exports & History)
    # ============================================================

    def _build_project_deletion_preview_in_tx(
        self,
        conn: Any,
        project_id: str,
        storage: Any = None,
    ) -> Dict[str, Any]:
        row = conn.execute("SELECT * FROM projects WHERE id = ?;", (project_id,)).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found.")
        project = dict(row)

        blocking_reasons: List[str] = []

        active_jobs = [
            dict(r)
            for r in conn.execute(
                "SELECT id, status FROM jobs WHERE project_id = ? AND status IN ('queued', 'running', 'processing');",
                (project_id,),
            ).fetchall()
        ]

        active_exports = [
            dict(r)
            for r in conn.execute(
                "SELECT id, status FROM exports WHERE project_id = ? AND status IN ('queued', 'running', 'processing');",
                (project_id,),
            ).fetchall()
        ]
        if active_exports:
            blocking_reasons.append(
                f"Project has active export job(s) in progress (e.g. export {active_exports[0]['id']})."
            )

        active_ops = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, cleanup_mode, status FROM cleanup_operations
                WHERE project_id = ? AND status IN ('pending', 'deleting_blobs', 'deleting_records');
                """,
                (project_id,),
            ).fetchall()
        ]
        if active_ops:
            blocking_reasons.append(
                f"Another storage operation ({active_ops[0]['id']}) is already in progress for this project."
            )

        cstatus = project.get("cleanup_status") or "retained"
        if cstatus in ("cleaning", "deleting"):
            blocking_reasons.append(f"Project is currently in '{cstatus}' state.")

        seen_keys: Set[str] = set()
        target_blob_keys: List[str] = []

        def _add_key(raw_key: Optional[str]) -> None:
            k = (raw_key or "").strip().lstrip("/")
            if not k:
                return
            prefix = f"{project_id}/"
            if k.startswith(prefix):
                k = k[len(prefix):]
            if k and k not in seen_keys:
                seen_keys.add(k)
                target_blob_keys.append(k)

        _add_key(project.get("source_storage_key"))
        for r in conn.execute(
            "SELECT file_storage_key FROM image_assets WHERE project_id = ? ORDER BY page_number ASC, placement_index ASC;",
            (project_id,),
        ).fetchall():
            _add_key(r["file_storage_key"])
        for r in conn.execute(
            "SELECT file_storage_key FROM exports WHERE project_id = ?;",
            (project_id,),
        ).fetchall():
            _add_key(r["file_storage_key"])
        for r in conn.execute(
            "SELECT blob_key FROM upload_keys WHERE project_id = ? OR blob_key LIKE ?;",
            (project_id, f"{project_id}/%"),
        ).fetchall():
            _add_key(r["blob_key"])

        if storage is not None and hasattr(storage, "list_project_keys"):
            for k in storage.list_project_keys(project_id):
                _add_key(k)

        records_summary = {
            "jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE project_id = ?;", (project_id,)).fetchone()[0],
            "job_batches": conn.execute(
                "SELECT COUNT(*) FROM job_batches WHERE job_id IN (SELECT id FROM jobs WHERE project_id = ?);",
                (project_id,),
            ).fetchone()[0],
            "image_assets": conn.execute("SELECT COUNT(*) FROM image_assets WHERE project_id = ?;", (project_id,)).fetchone()[0],
            "alt_text_versions": conn.execute(
                "SELECT COUNT(*) FROM alt_text_versions WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);",
                (project_id,),
            ).fetchone()[0],
            "approvals": conn.execute(
                "SELECT COUNT(*) FROM approvals WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);",
                (project_id,),
            ).fetchone()[0],
            "figure_groups": conn.execute("SELECT COUNT(*) FROM figure_groups WHERE project_id = ?;", (project_id,)).fetchone()[0],
            "exports": conn.execute("SELECT COUNT(*) FROM exports WHERE project_id = ?;", (project_id,)).fetchone()[0],
            "audit_events": conn.execute("SELECT COUNT(*) FROM audit_events WHERE project_id = ?;", (project_id,)).fetchone()[0],
            "ai_usage_ledger": conn.execute("SELECT COUNT(*) FROM ai_usage_ledger WHERE project_id = ?;", (project_id,)).fetchone()[0],
            "upload_keys": conn.execute(
                "SELECT COUNT(*) FROM upload_keys WHERE project_id = ? OR blob_key LIKE ?;",
                (project_id, f"{project_id}/%"),
            ).fetchone()[0],
        }

        existing_op_row = conn.execute(
            """
            SELECT * FROM cleanup_operations
            WHERE project_id = ? AND cleanup_mode = 'delete_project' AND status != 'completed'
            ORDER BY created_at DESC LIMIT 1;
            """,
            (project_id,),
        ).fetchone()
        existing_operation = self._format_cleanup_operation_row(existing_op_row) if existing_op_row else None
        now_dt = datetime.now(timezone.utc)
        can_resume = False
        is_active_deletion = False
        if existing_operation:
            op_status = existing_operation.get("status")
            if op_status in ("pending", "failed"):
                can_resume = True
            elif op_status in ("deleting_blobs", "deleting_records"):
                exp_dt = _parse_utc_dt(existing_operation.get("lease_expires_at"))
                if exp_dt is None or exp_dt <= now_dt:
                    can_resume = True
                else:
                    is_active_deletion = True

        return {
            "project_id": project_id,
            "project_name": project.get("name") or "",
            "can_delete": len(blocking_reasons) == 0,
            "blocking_reasons": blocking_reasons,
            "target_blob_keys": target_blob_keys,
            "target_blob_count": len(target_blob_keys),
            "records_to_delete": records_summary,
            "existing_operation": existing_operation,
            "can_resume": can_resume,
            "is_active_deletion": is_active_deletion,
            "active_extraction_jobs": active_jobs,
        }

    def preview_project_deletion(self, project_id: str, storage: Any = None) -> Dict[str, Any]:
        with self.db.session() as conn:
            return self._build_project_deletion_preview_in_tx(conn, project_id, storage=storage)

    def prepare_project_deletion(self, project_id: str, storage: Any = None) -> Dict[str, Any]:
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            preview = self._build_project_deletion_preview_in_tx(conn, project_id, storage=storage)
            if not preview["can_delete"]:
                raise CleanupBlockedError("; ".join(preview["blocking_reasons"]))

            operation_id = str(uuid.uuid4())
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO cleanup_operations (
                    id, project_id, cleanup_mode, status,
                    target_blob_keys, deleted_blob_keys, missing_blob_keys, failed_blob_keys,
                    db_records_summary, created_at, updated_at
                ) VALUES (?, ?, 'delete_project', 'pending', ?, '[]', '[]', '{}', ?, ?, ?);
                """,
                (
                    operation_id,
                    project_id,
                    json.dumps(preview["target_blob_keys"]),
                    json.dumps(preview["records_to_delete"]),
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE projects SET cleanup_status = 'deleting', updated_at = ? WHERE id = ?;",
                (now, project_id),
            )
            conn.execute(
                """
                UPDATE jobs
                SET status = 'cancelled',
                    error_message = 'Cancelled because project is being deleted',
                    completed_at = COALESCE(completed_at, ?),
                    lease_updated_at = NULL,
                    worker_id = NULL
                WHERE project_id = ? AND status IN ('queued', 'running', 'processing');
                """,
                (now, project_id),
            )
            conn.execute(
                """
                UPDATE job_batches
                SET status = 'cancelled',
                    error_message = 'Cancelled because project is being deleted',
                    completed_at = COALESCE(completed_at, ?)
                WHERE job_id IN (SELECT id FROM jobs WHERE project_id = ?)
                  AND status IN ('queued', 'running');
                """,
                (now, project_id),
            )
            row = conn.execute("SELECT * FROM cleanup_operations WHERE id = ?;", (operation_id,)).fetchone()
            return self._format_cleanup_operation_row(row)

    def _claim_project_deletion_operation(self, operation_id: str) -> tuple[Dict[str, Any], Optional[str]]:
        lease_seconds = get_deletion_lease_seconds()
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires_at = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        lease_token = str(uuid.uuid4())

        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            row = conn.execute(
                "SELECT * FROM cleanup_operations WHERE id = ?;",
                (operation_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"Project deletion operation '{operation_id}' not found.")
            op = self._format_cleanup_operation_row(row)
            if op.get("cleanup_mode") != "delete_project":
                raise ValueError(f"Project deletion operation '{operation_id}' not found.")
            status = op["status"]
            if status == "completed":
                return op, None
            if status in ("deleting_blobs", "deleting_records"):
                exp_dt = _parse_utc_dt(op.get("lease_expires_at"))
                if exp_dt is not None and exp_dt > now_dt:
                    raise CleanupBlockedError(
                        f"Project deletion operation '{operation_id}' is currently active (lease expires at {op.get('lease_expires_at')})."
                    )
            elif status not in ("pending", "failed"):
                raise CleanupBlockedError(
                    f"Cannot execute project deletion operation '{operation_id}' in state '{status}'."
                )

            next_status = "deleting_records" if status == "deleting_records" else "deleting_blobs"
            retry_inc = 0 if status == "pending" else 1
            conn.execute(
                """
                UPDATE cleanup_operations
                SET status = ?,
                    lease_token = ?,
                    lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?),
                    retry_count = retry_count + ?,
                    updated_at = ?
                WHERE id = ?;
                """,
                (next_status, lease_token, lease_expires_at, now, retry_inc, now, operation_id),
            )
            updated_row = conn.execute(
                "SELECT * FROM cleanup_operations WHERE id = ?;",
                (operation_id,),
            ).fetchone()
            return self._format_cleanup_operation_row(updated_row), lease_token

    def _renew_deletion_lease(self, operation_id: str, lease_token: str) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_expires_at = (now_dt + timedelta(seconds=get_deletion_lease_seconds())).isoformat()
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """
                UPDATE cleanup_operations
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND lease_token = ?;
                """,
                (lease_expires_at, now, operation_id, lease_token),
            )
            if cur.rowcount == 0:
                raise CleanupBlockedError(
                    f"Deletion lease lost for operation '{operation_id}' (reclaimed by another worker)."
                )

    def _record_deletion_failure(
        self,
        operation_id: str,
        lease_token: str,
        deleted_all: List[str],
        missing_all: List[str],
        failed_map: Dict[str, str],
        err_msg: str,
    ) -> bool:
        fail_now = utc_now_iso()
        with self.db.session() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """
                UPDATE cleanup_operations
                SET status = 'failed',
                    deleted_blob_keys = ?,
                    missing_blob_keys = ?,
                    failed_blob_keys = ?,
                    error_message = ?,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                WHERE id = ? AND lease_token = ?;
                """,
                (
                    json.dumps(deleted_all),
                    json.dumps(missing_all),
                    json.dumps(failed_map),
                    err_msg[:1000],
                    fail_now,
                    operation_id,
                    lease_token,
                ),
            )
            return cur.rowcount > 0

    def _run_project_deletion_operation(self, op: Dict[str, Any], storage: Any) -> Dict[str, Any]:
        operation_id = op["id"]
        project_id = op["project_id"]

        if op["status"] == "completed":
            return op

        claimed_op, lease_token = self._claim_project_deletion_operation(operation_id)
        if claimed_op["status"] == "completed" or lease_token is None:
            return claimed_op

        deleted_all = list(claimed_op.get("deleted_blob_keys") or [])
        missing_all = list(claimed_op.get("missing_blob_keys") or [])
        already_done = set(deleted_all) | set(missing_all)
        target_keys = list(claimed_op.get("target_blob_keys") or [])
        remaining_keys = [k for k in target_keys if k not in already_done]

        failed_map: Dict[str, str] = {}

        try:
            if remaining_keys and claimed_op["status"] != "deleting_records":
                chunk_size = 50
                for start in range(0, len(remaining_keys), chunk_size):
                    self._renew_deletion_lease(operation_id, lease_token)
                    chunk = remaining_keys[start:start + chunk_size]
                    del_res = storage.delete_objects(project_id, chunk)
                    for k in del_res.deleted:
                        if k not in deleted_all:
                            deleted_all.append(k)
                    for k in del_res.missing:
                        if k not in missing_all:
                            missing_all.append(k)
                    for k, reason in (del_res.failed or {}).items():
                        failed_map[k] = reason

                if failed_map:
                    err_msg = f"Failed to delete {len(failed_map)} storage object(s)."
                    if not self._record_deletion_failure(
                        operation_id, lease_token, deleted_all, missing_all, failed_map, err_msg
                    ):
                        raise CleanupBlockedError(
                            f"Deletion lease lost for operation '{operation_id}' (reclaimed by another worker)."
                        )
                    raise StorageError(err_msg)

            self._renew_deletion_lease(operation_id, lease_token)

            # Final sweep for any Blobs uploaded by an in-flight worker racing with cancellation
            if storage is not None and hasattr(storage, "list_project_keys") and claimed_op["status"] != "deleting_records":
                already_handled = set(deleted_all) | set(missing_all)
                extra_keys = []
                for raw_k in storage.list_project_keys(project_id):
                    k = (raw_k or "").strip().lstrip("/")
                    if k.startswith(f"{project_id}/"):
                        k = k[len(f"{project_id}/"):]
                    if k and k not in already_handled and k not in extra_keys:
                        extra_keys.append(k)
                if extra_keys:
                    extra_res = storage.delete_objects(project_id, extra_keys)
                    for k in extra_res.deleted:
                        if k not in deleted_all:
                            deleted_all.append(k)
                    for k in extra_res.missing:
                        if k not in missing_all:
                            missing_all.append(k)
                    if extra_res.failed:
                        failed_map.update(extra_res.failed)
                        err_msg = f"Failed to delete {len(failed_map)} storage object(s)."
                        if not self._record_deletion_failure(
                            operation_id, lease_token, deleted_all, missing_all, failed_map, err_msg
                        ):
                            raise CleanupBlockedError(
                                f"Deletion lease lost for operation '{operation_id}' (reclaimed by another worker)."
                            )
                        raise StorageError(err_msg)

            # Remove project directory on local FileStore if empty/cleaned
            if hasattr(storage, "get_project_dir"):
                p_dir = storage.get_project_dir(project_id)
                if hasattr(p_dir, "exists") and p_dir.exists():
                    import shutil as _shutil
                    _shutil.rmtree(p_dir, ignore_errors=True)

            rec_now_dt = datetime.now(timezone.utc)
            rec_now = rec_now_dt.isoformat()
            rec_lease_exp = (rec_now_dt + timedelta(seconds=get_deletion_lease_seconds())).isoformat()
            with self.db.session() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                cur = conn.execute(
                    """
                    UPDATE cleanup_operations
                    SET status = 'deleting_records',
                        deleted_blob_keys = ?,
                        missing_blob_keys = ?,
                        failed_blob_keys = '{}',
                        error_message = NULL,
                        lease_expires_at = ?,
                        updated_at = ?
                    WHERE id = ? AND lease_token = ?;
                    """,
                    (json.dumps(deleted_all), json.dumps(missing_all), rec_lease_exp, rec_now, operation_id, lease_token),
                )
                if cur.rowcount == 0:
                    raise CleanupBlockedError(
                        f"Deletion lease lost for operation '{operation_id}' before database finalization."
                    )
                conn.execute(
                    "DELETE FROM approvals WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);",
                    (project_id,),
                )
                conn.execute(
                    "DELETE FROM alt_text_versions WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);",
                    (project_id,),
                )
                conn.execute("DELETE FROM ai_usage_ledger WHERE project_id = ?;", (project_id,))
                conn.execute(
                    "DELETE FROM job_batches WHERE job_id IN (SELECT id FROM jobs WHERE project_id = ?);",
                    (project_id,),
                )
                conn.execute("DELETE FROM jobs WHERE project_id = ?;", (project_id,))
                conn.execute("DELETE FROM exports WHERE project_id = ?;", (project_id,))
                conn.execute("DELETE FROM image_assets WHERE project_id = ?;", (project_id,))
                conn.execute("DELETE FROM figure_groups WHERE project_id = ?;", (project_id,))
                conn.execute("DELETE FROM audit_events WHERE project_id = ?;", (project_id,))
                conn.execute(
                    "DELETE FROM upload_keys WHERE project_id = ? OR blob_key LIKE ?;",
                    (project_id, f"{project_id}/%"),
                )
                conn.execute("DELETE FROM cleanup_operations WHERE project_id = ?;", (project_id,))
                conn.execute("DELETE FROM projects WHERE id = ?;", (project_id,))

            return {
                "id": operation_id,
                "project_id": project_id,
                "cleanup_mode": "delete_project",
                "status": "completed",
                "target_blob_keys": target_keys,
                "deleted_blob_keys": deleted_all,
                "missing_blob_keys": missing_all,
                "failed_blob_keys": {},
                "db_records_summary": claimed_op.get("db_records_summary") or {},
                "completed_at": rec_now,
            }
        except (CleanupBlockedError, StorageError):
            raise
        except Exception as exc:
            self._record_deletion_failure(
                operation_id, lease_token, deleted_all, missing_all, failed_map, str(exc)
            )
            raise StorageError(f"Project deletion failed: {exc}") from exc

    def execute_project_deletion(self, project_id: str, storage: Any) -> Dict[str, Any]:
        op = self.prepare_project_deletion(project_id, storage=storage)
        return self._run_project_deletion_operation(op, storage)

    def retry_project_deletion(self, operation_id: str, storage: Any) -> Dict[str, Any]:
        op = self.get_cleanup_operation(operation_id)
        if not op or op.get("cleanup_mode") != "delete_project":
            raise ValueError(f"Project deletion operation '{operation_id}' not found.")
        if op["status"] == "completed":
            return op
        return self._run_project_deletion_operation(op, storage)

    # ============================================================
    # Word Document Design Configuration
    # ============================================================

    def get_project_doc_design(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT doc_design_config FROM projects WHERE id = ?;",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            raw = row["doc_design_config"]
            if not raw:
                return None
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None

    def save_project_doc_design(self, project_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now_iso()
        serialized = json.dumps(config)
        with self.db.session() as conn:
            cur = conn.execute(
                "UPDATE projects SET doc_design_config = ?, updated_at = ? WHERE id = ?;",
                (serialized, now, project_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Project '{project_id}' not found.")
        return {"config": config, "saved_at": now}

    def reset_project_doc_design(self, project_id: str) -> None:
        now = utc_now_iso()
        with self.db.session() as conn:
            cur = conn.execute(
                "UPDATE projects SET doc_design_config = NULL, updated_at = ? WHERE id = ?;",
                (now, project_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Project '{project_id}' not found.")

    def get_user_doc_design(self, user_id: str) -> Optional[Dict[str, Any]]:
        uid = (user_id or "").strip()
        if not uid:
            return None
        with self.db.session() as conn:
            row = conn.execute(
                "SELECT doc_design_config FROM user_doc_preferences WHERE user_id = ?;",
                (uid,),
            ).fetchone()
            if not row or not row["doc_design_config"]:
                return None
            try:
                parsed = json.loads(row["doc_design_config"]) if isinstance(row["doc_design_config"], str) else row["doc_design_config"]
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None

    def save_user_doc_design(self, user_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        uid = (user_id or "").strip()
        if not uid:
            raise ValueError("Authenticated user_id is required to save Word document preferences.")
        now = utc_now_iso()
        serialized = json.dumps(config)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO user_doc_preferences (user_id, doc_design_config, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    doc_design_config = excluded.doc_design_config,
                    updated_at = excluded.updated_at;
                """,
                (uid, serialized, now),
            )
        return {"user_id": uid, "config": config, "saved_at": now}

    def reset_user_doc_design(self, user_id: str) -> None:
        uid = (user_id or "").strip()
        if not uid:
            return
        with self.db.session() as conn:
            conn.execute(
                "DELETE FROM user_doc_preferences WHERE user_id = ?;",
                (uid,),
            )
