"""
Unit and integration tests for Database Referential Cleanup Methods (Stage 2).
Covers:
  - Cleanup preview calculations and fencing (unverified export, active jobs, active cleanups).
  - Mode A (extracted assets) deletion and preservation guarantees.
  - Mode B (all source + extracted assets) deletion and preservation guarantees.
  - Export and DOCX storage key absolute preservation.
  - Approved alt-text audit snapshot persistence in cleanup manifest and audit events.
  - AI token usage ledger retention (image_id disassociation without dropping ledger rows).
  - Partial failure handling (DB records stay intact on storage failure).
  - Durable retry behavior (retrying only unresolved keys to final completion).
  - Project storage summary observability.
"""

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Dict, Any

import pytest

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.base_storage import CleanupBlockedError, StorageError, DeletionResult
from src.storage.filestore import FileStore


@pytest.fixture
def temp_db_and_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        storage_root = os.path.join(tmpdir, "storage")
        os.makedirs(storage_root, exist_ok=True)

        db_conn = DatabaseConnection(db_path)
        apply_migrations(db_conn)
        repo = Repository(db_conn)
        filestore = FileStore(storage_root)

        yield repo, filestore, tmpdir


def _seed_test_project(
    repo: Repository,
    filestore: FileStore,
    project_id: str = "proj_test_123",
    num_images: int = 3,
    with_verified_export: bool = True,
    active_job_status: str = None,
) -> Dict[str, Any]:
    """Helper to seed a complete project with source PDF, figures, images, alt text, jobs, and exports."""
    # 1. Project
    source_bytes = b"%PDF-1.4 test document content"
    source_key, source_checksum, _ = filestore.save_source_pdf(
        project_id=project_id,
        source_path_or_bytes=source_bytes,
        original_filename="sample.pdf"
    )

    project = repo.create_project(
        project_id=project_id,
        name="Test Project",
        source_filename="sample.pdf",
        source_storage_key=source_key,
        source_checksum=source_checksum,
    )

    # 2. Figure group
    fg = repo.create_figure_group(
        group_id=f"fg_{project_id}",
        project_id=project_id,
        caption_text="Figure 1: Test architecture overview",
        caption_page_number=1,
    )

    # 3. Image assets, alt text, approvals
    image_ids = []
    image_keys = []
    for i in range(1, num_images + 1):
        img_id = f"img_{project_id}_{i}"
        img_bytes = f"fake png data {i}".encode("utf-8")
        img_key, img_checksum, _ = filestore.save_image(
            project_id=project_id,
            image_id=img_id,
            image_bytes=img_bytes,
            ext="png"
        )
        repo.create_image_asset(
            image_id=img_id,
            project_id=project_id,
            page_number=1,
            xref=100 + i,
            placement_index=i,
            bbox={"x0": 10.0, "y0": 10.0, "x1": 100.0, "y1": 100.0},
            original_caption=f"Figure 1.{i} caption",
            caption_position="bottom",
            needs_caption_review=False,
            review_reasons=[],
            suggested_filename=f"fig_{i}.png",
            file_storage_key=img_key,
            file_checksum=img_checksum,
            image_format="png",
            group_id=fg["id"],
        )
        # Create draft versions
        v1 = repo.create_alt_text_version(
            version_id=f"v1_{img_id}",
            image_id=img_id,
            version_number=1,
            text=f"AI draft alt text for image {i}",
        )
        v2 = repo.create_alt_text_version(
            version_id=f"v2_{img_id}",
            image_id=img_id,
            version_number=2,
            text=f"Approved human alt text for image {i}",
        )
        # Approve v2
        repo.create_approval(
            approval_id=f"app_{img_id}",
            image_id=img_id,
            alt_text_version_id=v2["id"],
            reviewer_id="editor_user",
        )
        # AI usage record
        repo.record_ai_usage(
            ledger_id=f"ai_{img_id}",
            project_id=project_id,
            image_id=img_id,
            provider="gemini",
            model="gemini-2.5",
            input_tokens=100,
            output_tokens=50,
            estimated_cost_usd=0.001,
        )
        image_ids.append(img_id)
        image_keys.append(img_key)

    # 4. Jobs & Batches
    job = repo.create_job(
        job_id=f"job_{project_id}",
        project_id=project_id,
        job_type="extraction",
        page_numbers=[1],
    )
    repo.initialize_job_batches(job["id"], [[1]], total_document_pages=1)
    if active_job_status:
        repo.update_job_status(job["id"], status=active_job_status)
    else:
        repo.update_job_status(job["id"], status="completed")
        repo.set_job_batch_status(job["id"], 0, status="completed")

    # 5. Export
    export_record = None
    if with_verified_export:
        export_bytes = b"PK\x03\x04 fake docx content"
        exp_id = f"exp_{project_id}"
        exp_key, exp_checksum, _ = filestore.save_export(
            project_id=project_id,
            export_id=exp_id,
            export_bytes=export_bytes,
            filename="Test_Project_alt_text.docx",
        )
        export_record = repo.create_export(
            export_id=exp_id,
            project_id=project_id,
            export_type="docx",
            file_storage_key=exp_key,
            file_checksum=exp_checksum,
            status="verified",
            figure_count=num_images,
        )
        repo.update_export_status(exp_id, status="verified", mark_verified=True)

    return {
        "project_id": project_id,
        "source_key": source_key,
        "image_ids": image_ids,
        "image_keys": image_keys,
        "export_record": export_record,
    }


def test_preview_project_cleanup_verified_export_requirement(temp_db_and_store):
    repo, filestore, _ = temp_db_and_store
    seeded = _seed_test_project(repo, filestore, project_id="proj_no_exp", with_verified_export=False)

    preview = repo.preview_project_cleanup(seeded["project_id"], "extracted")
    assert preview["can_cleanup"] is False
    assert any("verified export" in r.lower() for r in preview["blocking_reasons"])

    with pytest.raises(CleanupBlockedError) as exc_info:
        repo.prepare_cleanup_operation(seeded["project_id"], "extracted")
    assert "verified export" in str(exc_info.value).lower()


def test_preview_project_cleanup_active_job_blocking(temp_db_and_store):
    repo, filestore, _ = temp_db_and_store
    seeded = _seed_test_project(repo, filestore, project_id="proj_active_job", active_job_status="running")

    preview = repo.preview_project_cleanup(seeded["project_id"], "extracted")
    assert preview["can_cleanup"] is False
    assert any("background job" in r.lower() for r in preview["blocking_reasons"])

    with pytest.raises(CleanupBlockedError) as exc_info:
        repo.prepare_cleanup_operation(seeded["project_id"], "extracted")
    assert "active background job" in str(exc_info.value).lower()


def test_mode_a_cleanup_execution_and_preservation(temp_db_and_store):
    repo, filestore, _ = temp_db_and_store
    pid = "proj_mode_a"
    seeded = _seed_test_project(repo, filestore, project_id=pid, num_images=3)

    # Verify initial assets in storage
    assert filestore.exists(pid, seeded["source_key"])
    for k in seeded["image_keys"]:
        assert filestore.exists(pid, k)
    assert filestore.exists(pid, seeded["export_record"]["file_storage_key"])

    # Preview Mode A
    preview = repo.preview_project_cleanup(pid, "extracted")
    assert preview["can_cleanup"] is True
    assert preview["target_blob_count"] == 3
    assert seeded["source_key"] not in preview["target_blob_keys"]
    assert seeded["export_record"]["file_storage_key"] not in preview["target_blob_keys"]
    assert preview["preserved_assets"]["source_pdf_preserved"] is True
    assert preview["preserved_assets"]["exports_count"] == 1

    # Execute Mode A cleanup
    op = repo.execute_project_cleanup(pid, "extracted", filestore)
    assert op["status"] == "completed"
    assert op["cleanup_mode"] == "extracted"
    assert len(op["deleted_blob_keys"]) == 3
    assert len(op["failed_blob_keys"]) == 0

    # 1. Storage checks: Images deleted, source PDF and DOCX preserved
    for k in seeded["image_keys"]:
        assert not filestore.exists(pid, k)
    assert filestore.exists(pid, seeded["source_key"]), "Original source PDF must be preserved in Mode A"
    assert filestore.exists(pid, seeded["export_record"]["file_storage_key"]), "Export DOCX must NEVER be deleted"

    # 2. Database checks: image records, versions, approvals, figure groups, jobs deleted
    assert repo.list_project_images(pid) == []
    assert repo.get_figure_group(f"fg_{pid}") is None
    assert repo.list_project_jobs(pid) == []
    for img_id in seeded["image_ids"]:
        assert repo.list_image_versions(img_id) == []
        assert repo.get_approval_for_image(img_id) is None

    # 3. AI Usage preserved (image_id set to None)
    ai_usage = repo.get_project_ai_usage(pid)
    assert ai_usage["request_count"] == 3
    assert ai_usage["total_input_tokens"] == 300
    with repo.db.session() as conn:
        cur = conn.execute("SELECT image_id FROM ai_usage_ledger WHERE project_id = ?;", (pid,))
        image_ids_in_ledger = [r[0] for r in cur.fetchall()]
        assert all(img_id is None for img_id in image_ids_in_ledger)

    # 4. Export record preserved
    exports = repo.list_project_exports(pid)
    assert len(exports) == 1
    assert exports[0]["id"] == seeded["export_record"]["id"]
    assert exports[0]["figure_count"] == 3

    # 5. Project record preserved with updated cleanup_status
    proj = repo.get_project(pid)
    assert proj["cleanup_status"] == "cleaned_extracted"
    assert proj["source_storage_key"] == seeded["source_key"]

    # 6. Audit snapshot in manifest contains approved alt text
    audit_summary = op["audit_summary"]
    assert len(audit_summary) == 3
    for entry in audit_summary:
        assert entry["approved_version"] is not None
        assert "Approved human alt text" in entry["approved_version"]["text"]
        assert entry["approved_version"]["reviewer_id"] == "editor_user"

    # 7. Audit events contain started and completed
    events = repo.list_project_audit_events(pid)
    event_types = [e["event_type"] for e in events]
    assert "cleanup_started" in event_types
    assert "cleanup_completed" in event_types


def test_mode_b_cleanup_execution_and_preservation(temp_db_and_store):
    repo, filestore, _ = temp_db_and_store
    pid = "proj_mode_b"
    seeded = _seed_test_project(repo, filestore, project_id=pid, num_images=2)

    preview = repo.preview_project_cleanup(pid, "all_source")
    assert preview["can_cleanup"] is True
    assert preview["target_blob_count"] == 3  # 2 images + 1 source PDF
    assert seeded["source_key"] in preview["target_blob_keys"]
    assert seeded["export_record"]["file_storage_key"] not in preview["target_blob_keys"]
    assert preview["preserved_assets"]["source_pdf_preserved"] is False

    # Execute Mode B cleanup
    op = repo.execute_project_cleanup(pid, "all_source", filestore)
    assert op["status"] == "completed"
    assert op["cleanup_mode"] == "all_source"
    assert len(op["deleted_blob_keys"]) == 3

    # 1. Storage checks: Images deleted, source PDF deleted, DOCX preserved
    for k in seeded["image_keys"]:
        assert not filestore.exists(pid, k)
    assert not filestore.exists(pid, seeded["source_key"]), "Source PDF must be deleted in Mode B"
    assert filestore.exists(pid, seeded["export_record"]["file_storage_key"]), "DOCX export must NEVER be deleted"

    # 2. Project row preserved, source_storage_key cleared, cleanup_status = 'cleaned_all_source'
    proj = repo.get_project(pid)
    assert proj["cleanup_status"] == "cleaned_all_source"
    assert proj["source_storage_key"] == ""
    assert proj["name"] == "Test Project"

    # 3. Export record preserved
    exports = repo.list_project_exports(pid)
    assert len(exports) == 1


def test_mode_b_after_mode_a_stepwise_cleanup(temp_db_and_store):
    repo, filestore, _ = temp_db_and_store
    pid = "proj_stepwise"
    seeded = _seed_test_project(repo, filestore, project_id=pid, num_images=2)

    # Step 1: Mode A
    op1 = repo.execute_project_cleanup(pid, "extracted", filestore)
    assert op1["status"] == "completed"
    proj = repo.get_project(pid)
    assert proj["cleanup_status"] == "cleaned_extracted"
    assert filestore.exists(pid, seeded["source_key"])

    # Step 2: Attempting Mode A again should be blocked
    preview = repo.preview_project_cleanup(pid, "extracted")
    assert preview["can_cleanup"] is False
    assert any("already been cleaned" in r for r in preview["blocking_reasons"])

    # Step 3: Attempting Mode B should be allowed and should only target the source PDF
    preview_b = repo.preview_project_cleanup(pid, "all_source")
    assert preview_b["can_cleanup"] is True
    assert preview_b["target_blob_keys"] == [seeded["source_key"]]

    op2 = repo.execute_project_cleanup(pid, "all_source", filestore)
    assert op2["status"] == "completed"
    assert not filestore.exists(pid, seeded["source_key"])
    proj = repo.get_project(pid)
    assert proj["cleanup_status"] == "cleaned_all_source"
    assert proj["source_storage_key"] == ""


def test_cleanup_partial_failure_preserves_db_records(temp_db_and_store, monkeypatch):
    repo, filestore, _ = temp_db_and_store
    pid = "proj_partial_fail"
    seeded = _seed_test_project(repo, filestore, project_id=pid, num_images=3)

    # Monkeypatch filestore.delete_objects to simulate failure on the 2nd image
    original_delete = filestore.delete_objects
    failing_key = seeded["image_keys"][1]

    def mock_delete_objects(project_id, relative_keys):
        result = DeletionResult()
        for k in relative_keys:
            if k == failing_key:
                result.failed[k] = "Simulated I/O disk error"
            else:
                filestore.delete_object(project_id, k)
                result.deleted.append(k)
        return result

    monkeypatch.setattr(filestore, "delete_objects", mock_delete_objects)

    with pytest.raises(StorageError) as exc_info:
        repo.execute_project_cleanup(pid, "extracted", filestore)
    assert "partially failed" in str(exc_info.value).lower()

    # Verify Database records are completely INTACT!
    images = repo.list_project_images(pid)
    assert len(images) == 3, "Database records must NOT be deleted when storage deletions fail!"
    assert len(repo.list_project_jobs(pid)) == 1
    assert repo.get_figure_group(f"fg_{pid}") is not None

    # Project cleanup_status reverted to 'retained'
    proj = repo.get_project(pid)
    assert proj["cleanup_status"] == "retained"

    # Operation manifest record is 'failed' with detailed failed_blob_keys
    op = repo.get_latest_cleanup_operation(pid)
    assert op["status"] == "failed"
    assert failing_key in op["failed_blob_keys"]
    assert len(op["deleted_blob_keys"]) == 2

    # Now restore storage functionality and retry
    monkeypatch.setattr(filestore, "delete_objects", original_delete)

    # Retry should only delete unresolved keys and complete
    completed_op = repo.retry_cleanup_operation(op["id"], filestore)
    assert completed_op["status"] == "completed"
    assert completed_op["retry_count"] == 1

    # Now database records are purged cleanly
    assert len(repo.list_project_images(pid)) == 0
    assert repo.get_project(pid)["cleanup_status"] == "cleaned_extracted"
    assert not filestore.exists(pid, failing_key)


def test_concurrent_cleanup_blocking(temp_db_and_store):
    repo, filestore, _ = temp_db_and_store
    pid = "proj_concurrent"
    _seed_test_project(repo, filestore, project_id=pid, num_images=2)

    # Start first cleanup operation
    op1 = repo.prepare_cleanup_operation(pid, "extracted")
    assert op1["status"] == "pending"
    assert repo.get_project(pid)["cleanup_status"] == "cleaning"

    # Second cleanup attempt must be blocked
    preview = repo.preview_project_cleanup(pid, "extracted")
    assert preview["can_cleanup"] is False
    assert any("already in progress" in r for r in preview["blocking_reasons"])

    with pytest.raises(CleanupBlockedError):
        repo.prepare_cleanup_operation(pid, "extracted")


def test_project_storage_summary_observability(temp_db_and_store):
    repo, filestore, _ = temp_db_and_store
    pid = "proj_summary"
    seeded = _seed_test_project(repo, filestore, project_id=pid, num_images=4)

    summary = repo.get_project_storage_summary(pid)
    assert summary["project_id"] == pid
    assert summary["cleanup_status"] == "retained"
    assert summary["has_source_file"] is True
    assert summary["image_count"] == 4
    assert len(summary["image_storage_keys"]) == 4
    assert summary["export_count"] == 1
    assert summary["verified_export_count"] == 1
    assert summary["is_cleanup_eligible"] is True
    assert summary["latest_cleanup_operation"] is None

    # After cleanup
    repo.execute_project_cleanup(pid, "extracted", filestore)
    post_summary = repo.get_project_storage_summary(pid)
    assert post_summary["cleanup_status"] == "cleaned_extracted"
    assert post_summary["image_count"] == 0
    assert post_summary["export_count"] == 1
    assert post_summary["latest_cleanup_operation"] is not None
    assert post_summary["latest_cleanup_operation"]["status"] == "completed"


# ============================================================
# PostgreSQL Parity Tests
# ============================================================

def _get_pg_repo():
    database_url = os.environ.get(
        "PG_TEST_DATABASE_URL",
        "host=/tmp user=postgres dbname=alttext_test",
    )
    try:
        from src.db.postgres_connection import PostgresConnection
        from src.db.postgres_migrations import apply_migrations as pg_apply_migrations
        from src.db.postgres_repository import PostgresRepository
        conn = PostgresConnection(database_url=database_url)
        with conn.session() as c:
            with c.cursor() as cur:
                cur.execute("SELECT 1;")
        pg_apply_migrations(conn)
        return PostgresRepository(conn)
    except Exception:
        return None


def test_postgres_cleanup_parity_mode_a_and_mode_b():
    pg_repo = _get_pg_repo()
    if pg_repo is None:
        pytest.skip("PostgreSQL test database not available in current environment")

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_root = os.path.join(tmpdir, "pg_storage")
        filestore = FileStore(storage_root)

        # 1. Mode A on Postgres
        pid_a = f"pg_proj_a_{uuid.uuid4().hex[:8]}"
        seeded_a = _seed_test_project(pg_repo, filestore, project_id=pid_a, num_images=2)

        preview_a = pg_repo.preview_project_cleanup(pid_a, "extracted")
        assert preview_a["can_cleanup"] is True
        assert preview_a["target_blob_count"] == 2
        assert seeded_a["source_key"] not in preview_a["target_blob_keys"]

        op_a = pg_repo.execute_project_cleanup(pid_a, "extracted", filestore)
        assert op_a["status"] == "completed"
        assert len(op_a["deleted_blob_keys"]) == 2
        assert filestore.exists(pid_a, seeded_a["source_key"])
        assert filestore.exists(pid_a, seeded_a["export_record"]["file_storage_key"])
        assert pg_repo.get_project(pid_a)["cleanup_status"] == "cleaned_extracted"
        assert len(pg_repo.list_project_images(pid_a)) == 0
        assert len(pg_repo.list_project_exports(pid_a)) == 1

        # 2. Mode B on Postgres
        pid_b = f"pg_proj_b_{uuid.uuid4().hex[:8]}"
        seeded_b = _seed_test_project(pg_repo, filestore, project_id=pid_b, num_images=2)

        preview_b = pg_repo.preview_project_cleanup(pid_b, "all_source")
        assert preview_b["can_cleanup"] is True
        assert preview_b["target_blob_count"] == 3
        assert seeded_b["source_key"] in preview_b["target_blob_keys"]

        op_b = pg_repo.execute_project_cleanup(pid_b, "all_source", filestore)
        assert op_b["status"] == "completed"
        assert len(op_b["deleted_blob_keys"]) == 3
        assert not filestore.exists(pid_b, seeded_b["source_key"])
        assert filestore.exists(pid_b, seeded_b["export_record"]["file_storage_key"])
        proj_b = pg_repo.get_project(pid_b)
        assert proj_b["cleanup_status"] == "cleaned_all_source"
        assert proj_b["source_storage_key"] == ""
        assert len(pg_repo.list_project_images(pid_b)) == 0
        assert len(pg_repo.list_project_exports(pid_b)) == 1

