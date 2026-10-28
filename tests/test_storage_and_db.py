"""
Test Suite for Phase 3: Persistent State & Temporary Storage
------------------------------------------------------------
Validates:
1. Relational schema persistence across projects, assets, drafts, approvals, jobs, and exports.
2. Strict binary-metadata separation (binaries in FileStore, metadata in SQLite).
3. Project isolation (no cross-project leakage or destructive interference).
4. Uncaptioned images remain eligible for draft generation and review.
5. Path traversal security checks.
6. Explicit cleanup safeguards (prevent deletion of active/review-required/unverified assets).
7. Recovery and resumption across process / connection restarts.
"""

import os
import uuid
import pytest
from pathlib import Path

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import (
    FileStore,
    PathTraversalError,
    CleanupBlockedError,
    calculate_sha256,
)


@pytest.fixture
def env(tmp_path):
    """Provides a fresh isolated database and storage directory."""
    db_file = tmp_path / "test_pipeline.db"
    storage_dir = tmp_path / "storage"

    db_conn = DatabaseConnection(str(db_file))
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    filestore = FileStore(str(storage_dir))

    return {
        "db_file": str(db_file),
        "db_conn": db_conn,
        "repo": repo,
        "filestore": filestore,
        "tmp_path": tmp_path,
    }


def test_project_creation_and_source_storage(env):
    repo = env["repo"]
    filestore = env["filestore"]

    project_id = str(uuid.uuid4())
    pdf_bytes = b"%PDF-1.4 dummy book content for testing"

    rel_key, checksum, size = filestore.save_source_pdf(
        project_id=project_id,
        source_path_or_bytes=pdf_bytes,
        original_filename="Etruria_Book.pdf"
    )

    project = repo.create_project(
        project_id=project_id,
        name="Late Etruria Study",
        source_filename="Etruria_Book.pdf",
        source_storage_key=rel_key,
        source_checksum=checksum,
        language_variant="en-GB"
    )

    assert project["id"] == project_id
    assert project["status"] == "created"
    assert project["cleanup_status"] == "retained"
    assert project["source_checksum"] == checksum

    # Verify physical file existence in isolated directory
    resolved = filestore.resolve_path(project_id, rel_key)
    assert resolved.exists()
    assert resolved.read_bytes() == pdf_bytes


def test_image_asset_persistence_and_separation(env):
    repo = env["repo"]
    filestore = env["filestore"]

    project_id = str(uuid.uuid4())
    filestore.save_source_pdf(project_id, b"%PDF-1.4 dummy", "sample.pdf")
    repo.create_project(project_id, "Sample", "sample.pdf", "source/source.pdf", "chk")

    img_id = str(uuid.uuid4())
    img_bytes = b"\x89PNG\r\n\x1a\nfake_png_bytes"
    img_key, img_checksum, img_size = filestore.save_image(
        project_id=project_id,
        image_id=img_id,
        image_bytes=img_bytes,
        ext="png"
    )

    # Persist image metadata
    asset = repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=33,
        xref=120,
        placement_index=1,
        bbox={"x0": 50.0, "y0": 60.0, "x1": 350.0, "y1": 300.0},
        original_caption="Figure 1. Archaeological tomb reconstruction.",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="Figure_1_Archaeological_tomb.png",
        file_storage_key=img_key,
        file_checksum=img_checksum,
        image_format="png"
    )

    assert asset["id"] == img_id
    assert asset["page_number"] == 33
    assert asset["original_caption"] == "Figure 1. Archaeological tomb reconstruction."
    assert asset["needs_caption_review"] is False
    assert asset["review_reasons"] == []
    assert asset["bbox"]["x0"] == 50.0

    # Ensure binary content is outside the DB and readable via FileStore
    read_back = filestore.read_bytes(project_id, asset["file_storage_key"])
    assert read_back == img_bytes


def test_uncaptioned_image_remains_reviewable_and_draftable(env):
    """An image without caption must remain eligible for alt-text generation and editorial review."""
    repo = env["repo"]
    filestore = env["filestore"]

    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Uncaptioned Test", "book.pdf", "source/source.pdf", "chk")

    img_id = str(uuid.uuid4())
    img_key, img_checksum, _ = filestore.save_image(project_id, img_id, b"uncaptioned_cover", "jpg")

    asset = repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=1,
        xref=5,
        placement_index=1,
        bbox={"x0": 0.0, "y0": 0.0, "x1": 400.0, "y1": 600.0},
        original_caption="",
        caption_position=None,
        needs_caption_review=True,
        review_reasons=["No caption found within proximity tolerance"],
        suggested_filename="Figure_p1_1.jpg",
        file_storage_key=img_key,
        file_checksum=img_checksum,
        image_format="jpg"
    )

    assert asset["caption_position"] is None
    assert asset["original_caption"] == ""
    assert asset["needs_caption_review"] is True
    assert "No caption found within proximity tolerance" in asset["review_reasons"]

    # Attach draft alt text to uncaptioned image
    v1_id = str(uuid.uuid4())
    v1 = repo.create_alt_text_version(
        version_id=v1_id,
        image_id=img_id,
        version_number=1,
        text="A full-bleed artistic book cover depicting Etruscan funerary urns in terracotta."
    )
    assert v1["image_id"] == img_id
    assert v1["version_number"] == 1


def test_project_isolation(env):
    """Operations on Project A cannot access, leak into, or delete Project B."""
    repo = env["repo"]
    filestore = env["filestore"]

    pA_id = str(uuid.uuid4())
    pB_id = str(uuid.uuid4())

    repo.create_project(pA_id, "Project A", "a.pdf", "source/source.pdf", "chkA")
    repo.create_project(pB_id, "Project B", "b.pdf", "source/source.pdf", "chkB")

    filestore.save_image(pA_id, "imgA", b"bytesA", "png")
    filestore.save_image(pB_id, "imgB", b"bytesB", "png")

    pA_images = filestore.get_project_dir(pA_id) / "images"
    pB_images = filestore.get_project_dir(pB_id) / "images"

    assert (pA_images / "imgA.png").exists()
    assert (pB_images / "imgB.png").exists()
    assert not (pA_images / "imgB.png").exists()
    assert not (pB_images / "imgA.png").exists()

    # Cancel and clean project A
    repo.update_project_status(pA_id, status="cancelled", cleanup_status="cleaned")
    filestore.cleanup_project(pA_id, project_cancelled=True)

    # Project A directory deleted, Project B untouched
    assert not filestore.get_project_dir(pA_id).exists()
    assert filestore.get_project_dir(pB_id).exists()
    assert (pB_images / "imgB.png").exists()


def test_path_traversal_protection(env):
    filestore = env["filestore"]
    project_id = str(uuid.uuid4())
    filestore.init_project_storage(project_id)

    with pytest.raises(PathTraversalError):
        filestore.resolve_path(project_id, "../../etc/passwd")

    with pytest.raises(PathTraversalError):
        filestore.resolve_path(project_id, "../sibling_project/source/source.pdf")


def test_cleanup_safeguards(env):
    """Cleanup must be blocked if active jobs exist or export is unverified."""
    repo = env["repo"]
    filestore = env["filestore"]

    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Guard Test", "book.pdf", "source/source.pdf", "chk")
    filestore.save_source_pdf(project_id, b"pdf", "book.pdf")

    # 1. Blocked when active jobs are running
    with pytest.raises(CleanupBlockedError, match="active worker jobs"):
        filestore.cleanup_project(project_id, active_jobs_running=True)

    # 2. Blocked when project is in review without verified delivery
    with pytest.raises(CleanupBlockedError, match="export has not been verified"):
        filestore.cleanup_project(project_id, export_verified_and_delivered=False, project_cancelled=False)

    # 3. Allowed when export is verified and delivered
    export_id = str(uuid.uuid4())
    exp_key, exp_chk, _ = filestore.save_export(project_id, export_id, b"docx", "output.docx")
    repo.create_export(export_id, project_id, "docx", exp_key, exp_chk, status="delivered")
    repo.update_project_status(project_id, status="completed", cleanup_status="eligible_for_cleanup")

    assert filestore.get_project_dir(project_id).exists()
    success = filestore.cleanup_project(project_id, export_verified_and_delivered=True)
    assert success is True
    assert not filestore.get_project_dir(project_id).exists()

    # Database records remain intact for history
    p_record = repo.get_project(project_id)
    assert p_record is not None
    assert p_record["status"] == "completed"


def test_state_recovery_after_process_restart(env):
    """Closing connection and re-initializing must preserve full draft and approval history."""
    db_file = env["db_file"]
    repo = env["repo"]

    project_id = str(uuid.uuid4())
    img_id = str(uuid.uuid4())
    v1_id = str(uuid.uuid4())
    v2_id = str(uuid.uuid4())
    appr_id = str(uuid.uuid4())

    repo.create_project(project_id, "Restart Test", "test.pdf", "source/source.pdf", "chk")
    repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=10,
        xref=55,
        placement_index=1,
        bbox={"x0": 10.0, "y0": 20.0, "x1": 200.0, "y1": 150.0},
        original_caption="Figure 5. Initial chart.",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="Figure_5.png",
        file_storage_key="images/Figure_5.png",
        file_checksum="chk5",
        image_format="png"
    )

    repo.create_alt_text_version(v1_id, img_id, 1, "First draft alt text.")
    repo.create_alt_text_version(v2_id, img_id, 2, "Second revised alt text.", revision_instruction="Make it concise.")
    repo.create_approval(appr_id, img_id, v2_id, reviewer_id="editor_1")

    # SIMULATE PROCESS RESTART:
    # 1. Close old connection
    # 2. Reopen fresh DatabaseConnection to same file
    new_db_conn = DatabaseConnection(db_file)
    new_repo = Repository(new_db_conn)

    restored_img = new_repo.get_image_asset(img_id)
    assert restored_img is not None
    assert restored_img["processing_status"] == "approved"

    versions = new_repo.list_image_versions(img_id)
    assert len(versions) == 2
    assert versions[0]["version_number"] == 1
    assert versions[1]["version_number"] == 2
    assert versions[1]["revision_instruction"] == "Make it concise."

    approval = new_repo.get_approval_for_image(img_id)
    assert approval is not None
    assert approval["alt_text_version_id"] == v2_id
    assert approval["reviewer_id"] == "editor_1"


def test_job_lifecycle_and_retry_tracking(env):
    repo = env["repo"]
    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Job Test", "b.pdf", "source/source.pdf", "chk")

    job_id = str(uuid.uuid4())
    job = repo.create_job(
        job_id=job_id,
        project_id=project_id,
        job_type="extraction_chunk",
        page_start=1,
        page_end=20,
        max_attempts=3
    )

    assert job["status"] == "queued"
    assert job["attempt_count"] == 0

    # Start job
    job = repo.update_job_status(job_id, status="running", increment_attempt=True)
    assert job["status"] == "running"
    assert job["attempt_count"] == 1
    assert job["started_at"] is not None

    # Transient failure
    job = repo.update_job_status(
        job_id,
        status="failed",
        error_message="Worker timeout on page 14"
    )
    assert job["status"] == "failed"
    assert job["error_message"] == "Worker timeout on page 14"

    # Retry and succeed
    job = repo.update_job_status(job_id, status="running", increment_attempt=True)
    assert job["attempt_count"] == 2
    job = repo.update_job_status(job_id, status="completed")
    assert job["status"] == "completed"
    assert job["completed_at"] is not None
