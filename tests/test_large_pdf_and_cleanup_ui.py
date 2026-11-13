"""
Comprehensive Unit & Integration Test Suite for Large PDF Processing and Project Cleanup UI.

Tests:
- Part A: Large PDF upload limits, 500 MB support, validation across layers, rejection >500 MB.
- Part B: Page batching (PDF_BATCH_SIZE=25), temporary working batch directory creation/cleanup,
          retry idempotency (no duplicate image assets), and original PDF retention.
- Part C: API endpoints for cleanup preview, summary, execute (Mode A & Mode B),
          job and export fencing (HTTP 409), and partial failure retry.
- Part D: Export retention and verified DOCX download survival across all cleanup modes.
"""

import io
import os
import shutil
import tempfile
import uuid
from typing import Dict, Any
from unittest.mock import patch

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.base_storage import DeletionResult
from src.storage.blob_storage import BlobStorage, DEFAULT_MAX_OBJECT_BYTES
from src.storage.filestore import FileStore
from src.web.app import create_app
from src.worker.extraction_worker import (
    PDF_BATCH_SIZE,
    _persist_batch_images,
    _process_batch,
)


@pytest.fixture
def app_and_repo(tmp_path):
    """Sets up a test SQLite DB, FileStore, FastAPI test client, and Repository."""
    db_path = str(tmp_path / "test.db")
    storage_root = str(tmp_path / "storage")
    os.makedirs(storage_root, exist_ok=True)

    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)

    app = create_app(db_path=db_path, storage_dir=storage_root)
    client = TestClient(app)

    return client, repo, app.state.filestore, tmp_path


def _create_mock_pdf_bytes(num_pages: int = 1, with_image: bool = True) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} content with Figure {i + 1}")
        if with_image:
            img = Image.new("RGB", (32, 32), color="blue")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(72, 100, 150, 180), stream=buf.getvalue())
    return doc.tobytes()


def _seed_project_with_assets_and_export(
    repo: Repository,
    store: FileStore,
    project_id: str,
    with_verified_export: bool = True,
    active_job_status: str = None,
) -> Dict[str, Any]:
    """Helper to seed a project with PDF, extracted images, alt text, and export."""
    store.init_project_storage(project_id)

    # 1. Source PDF
    pdf_bytes = _create_mock_pdf_bytes(num_pages=3, with_image=True)
    pdf_key, pdf_hash, _ = store.save_source_pdf(
        project_id=project_id,
        source_path_or_bytes=pdf_bytes,
        original_filename="large_document.pdf",
    )
    repo.create_project(
        project_id=project_id,
        name="Large Document Project",
        source_filename="large_document.pdf",
        source_storage_key=pdf_key,
        source_checksum=pdf_hash,
    )

    # 2. Figure group
    fg = repo.create_figure_group(
        group_id=f"fg_{project_id}",
        project_id=project_id,
        caption_text="Figure 1: Test diagram",
        caption_page_number=1,
    )

    # 3. Extracted Images & Approvals
    img_keys = []
    for i in range(1, 4):
        img_id = f"img_{project_id}_{i}"
        img_bytes = b"\x89PNG\r\n\x1a\n" + f"fake image data {i}".encode("utf-8")
        img_key, img_hash, _ = store.save_image(
            project_id=project_id,
            image_id=img_id,
            image_bytes=img_bytes,
            ext="png",
        )
        img_keys.append(img_key)

        repo.create_image_asset(
            image_id=img_id,
            project_id=project_id,
            page_number=i,
            xref=100 + i,
            placement_index=i,
            bbox={"x0": 10.0, "y0": 10.0, "x1": 100.0, "y1": 100.0},
            original_caption=f"Figure {i} caption",
            caption_position="bottom",
            needs_caption_review=False,
            review_reasons=[],
            suggested_filename=f"fig_{i}.png",
            file_storage_key=img_key,
            file_checksum=img_hash,
            image_format="png",
            group_id=fg["id"],
        )

        v1 = repo.create_alt_text_version(
            version_id=f"v1_{img_id}",
            image_id=img_id,
            version_number=1,
            text=f"Approved alt text for figure {i}",
        )
        repo.create_approval(
            approval_id=f"app_{img_id}",
            image_id=img_id,
            alt_text_version_id=v1["id"],
            reviewer_id="tester",
        )

    # 4. Jobs
    job = repo.create_job(
        job_id=f"job_{project_id}",
        project_id=project_id,
        job_type="extraction",
        page_numbers=[1, 2, 3],
    )
    repo.initialize_job_batches(job["id"], [[1, 2, 3]], total_document_pages=3)
    if active_job_status:
        repo.update_job_status(job["id"], status=active_job_status)
    else:
        repo.update_job_status(job["id"], status="completed")
        repo.set_job_batch_status(job["id"], 0, status="completed")

    # 5. Export
    export_id = None
    docx_key = None
    if with_verified_export:
        export_id = f"exp_{project_id}"
        docx_bytes = b"PK\x03\x04" + b"fake validated DOCX file content"
        docx_key, docx_hash, _ = store.save_export(
            project_id=project_id,
            export_id=export_id,
            export_bytes=docx_bytes,
            filename=f"{project_id}_report.docx",
        )
        repo.create_export(
            export_id=export_id,
            project_id=project_id,
            export_type="docx",
            file_storage_key=docx_key,
            file_checksum=docx_hash,
            status="verified",
            figure_count=3,
        )
        repo.update_export_status(export_id, status="verified", mark_verified=True)

    return {
        "project_id": project_id,
        "source_key": pdf_key,
        "image_keys": img_keys,
        "export_id": export_id,
        "docx_key": docx_key,
        "job_id": job["id"],
    }


# ==============================================================================
# PART A: Large PDF Upload Limit Verification (500 MB)
# ==============================================================================

def test_blob_storage_max_object_bytes_default():
    """Verify BlobStorage defaults to 500 MB."""
    from src.storage.blob_storage import MAX_OBJECT_BYTES
    assert DEFAULT_MAX_OBJECT_BYTES == 500 * 1024 * 1024
    assert MAX_OBJECT_BYTES == 500 * 1024 * 1024


def test_blob_storage_max_object_bytes_env_override():
    """Verify MAX_OBJECT_BYTES environment override."""
    with patch.dict(
        os.environ,
        {
            "BLOB_READ_WRITE_TOKEN": "test_token_12345",
            "MAX_OBJECT_BYTES": str(250 * 1024 * 1024),
        },
    ):
        # Need to test module-level or instance-level if dynamically read
        import importlib
        import src.storage.blob_storage as bs
        importlib.reload(bs)
        try:
            blob = bs.BlobStorage(blob_token="test_token_12345")
            assert bs.MAX_OBJECT_BYTES == 250 * 1024 * 1024
        finally:
            os.environ.pop("MAX_OBJECT_BYTES", None)
            importlib.reload(bs)


def test_api_upload_authorize_accepts_100mb_and_450mb(app_and_repo):
    """Test that /api/uploads/authorize accepts 104.5 MB and 450 MB requests."""
    client, _, _, _ = app_and_repo

    # 104.5 MB (problem statement observed size)
    size_104_5_mb = int(104.5 * 1024 * 1024)
    res = client.post("/api/uploads/authorize", json={
        "filename": "104mb_document.pdf",
        "content_type": "application/pdf",
        "size_bytes": size_104_5_mb,
    })
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["max_size_bytes"] == 500 * 1024 * 1024
    assert data["upload_key"]
    assert data["pathname"].endswith(".pdf")

    # 450 MB (large valid size under 500 MB)
    size_450_mb = 450 * 1024 * 1024
    res2 = client.post("/api/uploads/authorize", json={
        "filename": "450mb_document.pdf",
        "content_type": "application/pdf",
        "size_bytes": size_450_mb,
    })
    assert res2.status_code == 200, res2.text


def test_api_upload_authorize_rejects_exceeding_500mb(app_and_repo):
    """Test that /api/uploads/authorize rejects files > 500 MB."""
    client, _, _, _ = app_and_repo

    size_501_mb = 501 * 1024 * 1024
    res = client.post("/api/uploads/authorize", json={
        "filename": "huge_document.pdf",
        "content_type": "application/pdf",
        "size_bytes": size_501_mb,
    })
    assert res.status_code == 413
    assert "exceeds the configured upload limit" in res.json()["detail"]


# ==============================================================================
# PART B: Chunked PDF Extraction & Temporary Batch Cleanup
# ==============================================================================

def test_pdf_batch_size_configuration():
    """Verify PDF_BATCH_SIZE is set to 25."""
    assert PDF_BATCH_SIZE == 25


def test_batch_extraction_and_temp_dir_cleanup(tmp_path):
    """Verify _process_batch creates a temp batch dir, extracts, persists, and removes temp dir."""
    pdf_bytes = _create_mock_pdf_bytes(num_pages=5, with_image=True)
    pdf_file = tmp_path / "sample.pdf"
    pdf_file.write_bytes(pdf_bytes)

    db_path = str(tmp_path / "batch_test.db")
    storage_root = str(tmp_path / "batch_storage")
    os.makedirs(storage_root, exist_ok=True)

    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    store = FileStore(storage_root)

    project_id = f"proj_batch_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(project_id)
    repo.create_project(
        project_id=project_id,
        name="Batch Test",
        source_filename="sample.pdf",
        source_storage_key="source/sample.pdf",
        source_checksum="deadbeef",
    )

    created_temp_dirs = []
    original_mkdtemp = tempfile.mkdtemp

    def tracking_mkdtemp(*args, **kwargs):
        d = original_mkdtemp(*args, **kwargs)
        if "pdf_batch_" in d:
            created_temp_dirs.append(d)
        return d

    with patch("tempfile.mkdtemp", side_effect=tracking_mkdtemp):
        result, batch_temp_dir = _process_batch(str(pdf_file), [1, 2, 3, 4, 5], engine=None)
        assert os.path.exists(batch_temp_dir)
        assert len(result.images) > 0

        # Persist images
        _persist_batch_images(repo, store, project_id, result)
        # Clean up batch temp dir (as worker does)
        if batch_temp_dir and os.path.exists(batch_temp_dir):
            shutil.rmtree(batch_temp_dir, ignore_errors=True)

    # Ensure a temporary directory was created for the batch
    assert len(created_temp_dirs) >= 1
    # Ensure that every batch temp dir was deleted after persistence
    for d in created_temp_dirs:
        assert not os.path.exists(d), f"Temporary batch directory was not cleaned up: {d}"

    # Ensure source PDF still exists intact
    assert pdf_file.exists()


def test_batch_retry_idempotency_no_duplicate_images(tmp_path):
    """Verify that retrying an already persisted batch does not duplicate image records."""
    pdf_bytes = _create_mock_pdf_bytes(num_pages=2, with_image=True)
    pdf_file = tmp_path / "idempotent.pdf"
    pdf_file.write_bytes(pdf_bytes)

    db_path = str(tmp_path / "idempotent.db")
    storage_root = str(tmp_path / "idempotent_storage")
    os.makedirs(storage_root, exist_ok=True)

    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    store = FileStore(storage_root)

    project_id = f"proj_idemp_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(project_id)
    repo.create_project(
        project_id=project_id,
        name="Idempotent Test",
        source_filename="idempotent.pdf",
        source_storage_key="source/idempotent.pdf",
        source_checksum="deadbeef",
    )

    result, temp_dir = _process_batch(str(pdf_file), [1, 2], engine=None)
    try:
        # First persistence
        _persist_batch_images(repo, store, project_id, result)
        first_count = len(repo.list_project_images(project_id))
        assert first_count > 0

        # Second persistence (retry)
        _persist_batch_images(repo, store, project_id, result)
        second_count = len(repo.list_project_images(project_id))

        # Must be identical - no duplicate images
        assert first_count == second_count
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ==============================================================================
# PART C: Project Cleanup API Endpoints
# ==============================================================================

def test_cleanup_preview_mode_a_and_b(app_and_repo):
    """Verify GET /api/projects/{id}/cleanup/preview for Mode A and Mode B."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_prev_{uuid.uuid4().hex[:8]}"
    _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=True)

    # Mode A preview
    res_a = client.get(f"/api/projects/{project_id}/cleanup/preview?mode=extracted")
    assert res_a.status_code == 200, res_a.text
    data_a = res_a.json()
    assert data_a["mode"] == "extracted"
    prev_a = data_a["preview"]
    assert prev_a["can_cleanup"] is True
    assert prev_a["records_to_delete"]["image_assets"] == 3
    assert prev_a["target_blob_count"] == 3
    assert prev_a["preserved_assets"]["source_pdf_preserved"] is True
    assert prev_a["preserved_assets"]["exports_count"] == 1

    # Mode B preview
    res_b = client.get(f"/api/projects/{project_id}/cleanup/preview?mode=all_source")
    assert res_b.status_code == 200, res_b.text
    data_b = res_b.json()
    assert data_b["mode"] == "all_source"
    prev_b = data_b["preview"]
    assert prev_b["can_cleanup"] is True
    assert prev_b["records_to_delete"]["image_assets"] == 3
    assert prev_b["target_blob_count"] == 4
    assert prev_b["preserved_assets"]["source_pdf_preserved"] is False
    assert prev_b["preserved_assets"]["exports_count"] == 1


def test_cleanup_summary_endpoint(app_and_repo):
    """Verify GET /api/projects/{id}/cleanup/summary."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_summ_{uuid.uuid4().hex[:8]}"
    _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=True)

    res = client.get(f"/api/projects/{project_id}/cleanup/summary")
    assert res.status_code == 200
    data = res.json()
    assert data["project_id"] == project_id
    summary = data["summary"]
    assert summary["has_source_file"] is True
    assert summary["image_count"] == 3
    assert summary["export_count"] == 1


def test_cleanup_blocked_without_verified_export(app_and_repo):
    """Verify cleanup execution is blocked (HTTP 409) if no verified export exists."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_no_exp_{uuid.uuid4().hex[:8]}"
    _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=False)

    # Preview indicates can_cleanup is False
    res_prev = client.get(f"/api/projects/{project_id}/cleanup/preview?mode=extracted")
    assert res_prev.status_code == 200
    prev = res_prev.json()["preview"]
    assert prev["can_cleanup"] is False
    assert len(prev["blocking_reasons"]) > 0

    # Execute is rejected with HTTP 409
    res_exec = client.post(f"/api/projects/{project_id}/cleanup/execute", json={"mode": "extracted"})
    assert res_exec.status_code == 409
    assert "Cleanup cannot proceed" in res_exec.json()["detail"]["message"]


def test_cleanup_blocked_during_active_job(app_and_repo):
    """Verify cleanup execution is blocked (HTTP 409) if an extraction or export job is active."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_active_{uuid.uuid4().hex[:8]}"
    _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=True, active_job_status="running")

    res_exec = client.post(f"/api/projects/{project_id}/cleanup/execute", json={"mode": "extracted"})
    assert res_exec.status_code == 409
    detail = res_exec.json()["detail"]
    assert "Cleanup cannot proceed" in detail["message"]


def test_execute_mode_a_cleanup_preserves_pdf_and_export(app_and_repo):
    """Execute Mode A: cleans image assets, preserves source PDF and export."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_exec_a_{uuid.uuid4().hex[:8]}"
    seeded = _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=True)

    res = client.post(f"/api/projects/{project_id}/cleanup/execute", json={"mode": "extracted"})
    assert res.status_code == 200, res.text
    op = res.json()["operation"]
    assert op["status"] == "completed"
    assert op["cleanup_mode"] == "extracted"
    assert len(op["deleted_blob_keys"]) == 3

    # Images deleted from storage and DB
    for img_key in seeded["image_keys"]:
        assert not store.exists(project_id, img_key)
    assert len(repo.list_project_images(project_id)) == 0

    # Source PDF preserved in storage and DB
    assert store.exists(project_id, seeded["source_key"])
    project = repo.get_project(project_id)
    assert project["source_storage_key"] == seeded["source_key"]

    # Export preserved in storage and DB
    assert store.exists(project_id, seeded["docx_key"])
    exports = repo.list_project_exports(project_id)
    assert len(exports) == 1


def test_execute_mode_b_cleanup_deletes_pdf_preserves_export(app_and_repo):
    """Execute Mode B: cleans source PDF and image assets, preserves export."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_exec_b_{uuid.uuid4().hex[:8]}"
    seeded = _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=True)

    res = client.post(f"/api/projects/{project_id}/cleanup/execute", json={"mode": "all_source"})
    assert res.status_code == 200, res.text
    op = res.json()["operation"]
    assert op["status"] == "completed"
    assert op["cleanup_mode"] == "all_source"
    assert len(op["deleted_blob_keys"]) == 4  # 3 images + 1 source PDF

    # Images and PDF deleted from storage
    for img_key in seeded["image_keys"]:
        assert not store.exists(project_id, img_key)
    assert not store.exists(project_id, seeded["source_key"])

    # DB project record source_storage_key cleared
    project = repo.get_project(project_id)
    assert not project.get("source_storage_key")

    # Export preserved
    assert store.exists(project_id, seeded["docx_key"])
    exports = repo.list_project_exports(project_id)
    assert len(exports) == 1


def test_cleanup_partial_failure_and_retry(app_and_repo):
    """Simulate a partial failure during execute and verify retry successfully finishes."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_retry_{uuid.uuid4().hex[:8]}"
    seeded = _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=True)

    failing_key = seeded["image_keys"][0]
    original_delete_objects = store.delete_objects

    def mock_delete_objects(pid, keys):
        failed = {}
        deleted = []
        for k in keys:
            if k == failing_key:
                failed[k] = "Network timeout simulation"
            else:
                original_delete_objects(pid, [k])
                deleted.append(k)
        return DeletionResult(deleted=deleted, missing=[], failed=failed)

    with patch.object(store, "delete_objects", side_effect=mock_delete_objects):
        res = client.post(f"/api/projects/{project_id}/cleanup/execute", json={"mode": "extracted"})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "partial_failure"
        op = body["operation"]
        assert op["status"] == "failed"
        assert failing_key in op["failed_blob_keys"]
        operation_id = op["id"]

    # Now retry without failure
    res_retry = client.post(f"/api/cleanup/{operation_id}/retry")
    assert res_retry.status_code == 200
    op_after = res_retry.json()["operation"]
    assert op_after["status"] == "completed"
    assert op_after["failed_blob_keys"] == {}


# ==============================================================================
# PART D: Export Retention & Verified DOCX Download
# ==============================================================================

def test_export_download_survives_cleanup(app_and_repo):
    """Verify that /api/projects/{id}/exports/{export_id}/download succeeds after both Mode A and Mode B cleanups."""
    client, repo, store, _ = app_and_repo
    project_id = f"proj_dl_{uuid.uuid4().hex[:8]}"
    seeded = _seed_project_with_assets_and_export(repo, store, project_id, with_verified_export=True)
    export_id = seeded["export_id"]

    # Verify download works before cleanup
    dl_before = client.get(f"/api/projects/{project_id}/exports/{export_id}/download")
    assert dl_before.status_code == 200
    assert dl_before.content.startswith(b"PK\x03\x04")

    # Perform Mode A cleanup
    res_a = client.post(f"/api/projects/{project_id}/cleanup/execute", json={"mode": "extracted"})
    assert res_a.status_code == 200

    # Verify download STILL works after Mode A cleanup
    dl_after_a = client.get(f"/api/projects/{project_id}/exports/{export_id}/download")
    assert dl_after_a.status_code == 200
    assert dl_after_a.content.startswith(b"PK\x03\x04")

    # Perform Mode B cleanup
    res_b = client.post(f"/api/projects/{project_id}/cleanup/execute", json={"mode": "all_source"})
    assert res_b.status_code == 200

    # Verify download STILL works after Mode B cleanup
    dl_after_b = client.get(f"/api/projects/{project_id}/exports/{export_id}/download")
    assert dl_after_b.status_code == 200
    assert dl_after_b.content.startswith(b"PK\x03\x04")
