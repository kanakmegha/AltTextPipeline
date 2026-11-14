"""
Export Service for AltTextPipeline.
----------------------------------
Orchestrates eligibility verification, approved record retrieval,
DOCX generation, verification, and file persistence.
"""

import uuid
from typing import Any, Dict, List, Optional
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.ai.cleaner import clean_alt_text_response, is_contaminated_alt_text
from .docx_builder import DocxBuilder
from .design_config import FIELD_CATALOG, validate_design_config


def check_export_eligibility(repo: Repository, project_id: str) -> Dict[str, Any]:
    """
    Evaluates whether a project is eligible for final DOCX export.

    Requirements for export:
    1. Project must exist.
    2. Must contain at least one extracted image.
    3. Every image must be in 'approved' status with an associated approval record.
    4. No images may have unresolved 'needs_caption_review' flags.
    """
    project = repo.get_project(project_id)
    if not project:
        return {
            "can_export": False,
            "total_images": 0,
            "approved_count": 0,
            "blocking_reasons": ["Project not found"],
            "unapproved_images": [],
            "review_needed_images": [],
        }

    images = repo.list_project_images(project_id)
    if not images:
        return {
            "can_export": False,
            "total_images": 0,
            "approved_count": 0,
            "blocking_reasons": ["Project contains no extracted images to export"],
            "unapproved_images": [],
            "review_needed_images": [],
        }

    unapproved = []
    review_needed = []
    blocking_reasons = []

    for img in images:
        is_approved = img.get("processing_status") == "approved"
        approval = repo.get_approval_for_image(img["id"]) if is_approved else None
        approved_version = (
            repo.get_alt_text_version(approval["alt_text_version_id"])
            if (approval and approval.get("alt_text_version_id"))
            else None
        )

        if not is_approved or not approval or not approved_version:
            unapproved.append({
                "id": img["id"],
                "filename": img["suggested_filename"],
                "page_number": img["page_number"],
                "status": img.get("processing_status", "extracted"),
            })

        if img.get("needs_caption_review"):
            review_needed.append({
                "id": img["id"],
                "filename": img["suggested_filename"],
                "page_number": img["page_number"],
                "reasons": img.get("review_reasons", []),
            })

    if unapproved:
        blocking_reasons.append(
            f"{len(unapproved)} of {len(images)} images are unapproved (require alt-text approval before export)"
        )

    if review_needed:
        blocking_reasons.append(
            f"{len(review_needed)} images have unresolved caption review flags"
        )

    return {
        "can_export": len(blocking_reasons) == 0,
        "total_images": len(images),
        "approved_count": len(images) - len(unapproved),
        "unapproved_count": len(unapproved),
        "unresolved_reviews": len(review_needed),
        "blocking_reasons": blocking_reasons,
        "unapproved_images": unapproved,
        "review_needed_images": review_needed,
    }


def _load_figure_groups(repo: Repository, images: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Fetch distinct figure groups referenced by project images."""
    groups: Dict[str, Dict[str, Any]] = {}
    for img in images:
        gid = img.get("group_id")
        if gid and gid not in groups:
            grp = repo.get_figure_group(gid)
            if grp:
                groups[gid] = grp
    return groups


def get_project_design_context(repo: Repository, project_id: str) -> Dict[str, Any]:
    """
    Inspect real project images/versions/groups to determine which fields
    actually have data available and build a real representative sample for preview.
    """
    images = repo.list_project_images(project_id)
    groups = _load_figure_groups(repo, images)

    has_caption = any(bool((img.get("original_caption") or "").strip()) for img in images)
    has_figure_label = False
    has_generated = False
    has_approved = False
    sample_item: Optional[Dict[str, Any]] = None
    preview_items: List[Dict[str, Any]] = []

    for img in images:
        grp = groups.get(img.get("group_id") or "")
        fig_label = (grp.get("figure_label") if grp else None) or None
        if fig_label:
            has_figure_label = True

        latest_ver = repo.get_latest_version(img["id"])
        gen_text = ((latest_ver.get("alt_text") or latest_ver.get("text") or "").strip() if latest_ver else "")
        if gen_text:
            has_generated = True

        approval = repo.get_approval_for_image(img["id"])
        app_text = ""
        if approval and approval.get("alt_text_version_id"):
            av = repo.get_alt_text_version(approval["alt_text_version_id"])
            if av:
                app_text = (av.get("text") or av.get("alt_text") or "").strip()
        if app_text:
            has_approved = True

        entry = {
            "image_id": img["id"],
            "suggested_filename": img.get("suggested_filename") or "",
            "page_number": img.get("page_number"),
            "figure_label": fig_label or "",
            "original_caption": (img.get("original_caption") or "").strip(),
            "generated_alt_text": gen_text,
            "approved_alt_text": app_text or gen_text,
            "review_status": img.get("processing_status") or "extracted",
            "image_url": f"/api/projects/{project_id}/images/{img['id']}/file",
        }
        if len(preview_items) < 3:
            preview_items.append(entry)
        if sample_item is None and (app_text or entry["original_caption"]):
            sample_item = entry

    if sample_item is None and preview_items:
        sample_item = preview_items[0]

    availability_map = {
        "image": len(images) > 0,
        "image_id": len(images) > 0,
        "filename": any(bool(img.get("suggested_filename")) for img in images),
        "page_number": any(img.get("page_number") is not None for img in images),
        "figure_label": has_figure_label,
        "original_caption": has_caption,
        "generated_alt_text": has_generated,
        "approved_alt_text": has_approved or len(images) > 0,
        "review_status": len(images) > 0,
    }
    available_fields = [
        {"key": f["key"], "label": f["label"], "available": availability_map.get(f["key"], False)}
        for f in FIELD_CATALOG
        if availability_map.get(f["key"], False)
    ]

    return {
        "available_fields": available_fields,
        "sample_item": sample_item,
        "preview_items": preview_items,
        "total_images": len(images),
    }


def resolve_effective_doc_design(
    repo: Repository,
    project: Dict[str, Any],
    user_id: Optional[str] = None,
) -> tuple[Optional[Dict[str, Any]], str]:
    """
    Resolve effective Word document design configuration with precedence:
    1. User-level saved preference (user_doc_preferences)
    2. Legacy project-level configuration (projects.doc_design_config)
    3. None (default format)
    """
    project_name = project.get("name", "Document")
    effective_uid = (user_id or project.get("owner_id") or "").strip()
    if effective_uid and hasattr(repo, "get_user_doc_design"):
        user_cfg = repo.get_user_doc_design(effective_uid)
        if user_cfg is not None:
            return validate_design_config(user_cfg, project_name=project_name), "user"

    if hasattr(repo, "get_project_doc_design"):
        proj_cfg = repo.get_project_doc_design(project["id"])
        if proj_cfg is not None:
            return validate_design_config(proj_cfg, project_name=project_name), "project_legacy"

    return None, "default"


def export_project_docx(
    repo: Repository,
    filestore: FileStore,
    project_id: str,
    include_page_breaks: bool = False,
    design_config: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds, validates, and stores a DOCX export using strictly approved records.

    Raises ValueError if the project is not eligible for export.
    """
    eligibility = check_export_eligibility(repo, project_id)
    if not eligibility["can_export"]:
        reasons_str = "; ".join(eligibility["blocking_reasons"])
        raise ValueError(f"Export blocked: {reasons_str}")

    project = repo.get_project(project_id)
    # List images in reading order: page_number ASC, placement_index ASC
    images = repo.list_project_images(project_id)
    groups = _load_figure_groups(repo, images)

    if design_config is not None:
        effective_design = validate_design_config(
            design_config,
            project_name=project.get("name", "Document"),
        )
    else:
        effective_design, _ = resolve_effective_doc_design(repo, project, user_id=user_id)

    builder_items: List[Dict[str, Any]] = []
    for img in images:
        approval = repo.get_approval_for_image(img["id"])
        if not approval:
            raise ValueError(f"Approved image {img['id']} has no approval record")

        approved_version = repo.get_alt_text_version(approval["alt_text_version_id"])
        if not approved_version:
            raise ValueError(
                f"Approved version {approval['alt_text_version_id']} for image {img['id']} not found"
            )

        # Read original image binary from isolated project storage
        img_bytes = filestore.read_bytes(project_id, img["file_storage_key"])

        raw_alt = (approved_version.get("text") or approved_version.get("alt_text", "")).strip()
        # Preserve approved alt text exactly as stored, unless explicitly contaminated
        if is_contaminated_alt_text(raw_alt):
            clean_alt, is_valid, _ = clean_alt_text_response(raw_alt)
            final_alt = clean_alt if is_valid else raw_alt
        else:
            final_alt = raw_alt

        grp = groups.get(img.get("group_id") or "")
        fig_label = (grp.get("figure_label") if grp else None) or ""
        latest_ver = repo.get_latest_version(img["id"])
        gen_alt = ((latest_ver.get("alt_text") or latest_ver.get("text") or "").strip() if latest_ver else "")

        builder_items.append({
            "image_id": img["id"],
            "suggested_filename": img["suggested_filename"],
            "page_number": img["page_number"],
            "figure_label": fig_label,
            "original_caption": img.get("original_caption"),
            "generated_alt_text": gen_alt,
            "approved_alt_text": final_alt,
            "review_status": img.get("processing_status") or "approved",
            "image_bytes": img_bytes,
            "image_format": img.get("image_format", "png"),
            "source_path": img["file_storage_key"],
        })

    # Build and validate DOCX
    docx_bytes = DocxBuilder.build_docx(
        project_name=project.get("name", "Document"),
        items=builder_items,
        include_page_breaks=include_page_breaks,
        design_config=effective_design,
    )

    export_id = str(uuid.uuid4())
    safe_proj_name = "".join(c for c in project.get("name", "project") if c.isalnum() or c in ("-", "_")).strip() or "export"
    filename = f"{safe_proj_name}_alt_text.docx"

    storage_key, checksum, byte_size = filestore.save_export(
        project_id=project_id,
        export_id=export_id,
        export_bytes=docx_bytes,
        filename=filename,
    )

    # Persist export record in database with figure count
    fig_count = len(builder_items)
    export_record = repo.create_export(
        export_id=export_id,
        project_id=project_id,
        export_type="docx",
        file_storage_key=storage_key,
        file_checksum=checksum,
        status="verified",
        figure_count=fig_count,
    )
    repo.update_export_status(export_id, status="verified", mark_verified=True)

    # Record audit event
    repo.record_audit_event(
        event_id=str(uuid.uuid4()),
        project_id=project_id,
        event_type="docx_exported",
        target_type="export",
        target_id=export_id,
        details={
            "filename": filename,
            "byte_size": byte_size,
            "checksum": checksum,
            "figure_count": fig_count,
        },
        actor_id="editor",
    )

    export_record["byte_size"] = byte_size
    export_record["size_bytes"] = byte_size
    export_record["filename"] = filename
    export_record["figure_count"] = fig_count
    export_record["total_figures_exported"] = fig_count
    export_record["download_url"] = f"/api/projects/{project_id}/exports/{export_id}/download"
    return export_record
