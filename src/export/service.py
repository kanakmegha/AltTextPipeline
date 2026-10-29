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

        if not is_approved or not approval:
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


def export_project_docx(
    repo: Repository,
    filestore: FileStore,
    project_id: str,
    include_page_breaks: bool = False,
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

        builder_items.append({
            "image_id": img["id"],
            "suggested_filename": img["suggested_filename"],
            "page_number": img["page_number"],
            "original_caption": img.get("original_caption"),
            "approved_alt_text": final_alt,
            "image_bytes": img_bytes,
            "image_format": img.get("image_format", "png"),
            "source_path": img["file_storage_key"],
        })

    # Build and validate DOCX
    docx_bytes = DocxBuilder.build_docx(
        project_name=project.get("name", "Document"),
        items=builder_items,
        include_page_breaks=include_page_breaks,
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
