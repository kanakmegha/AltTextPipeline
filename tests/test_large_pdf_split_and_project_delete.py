import io
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

import pymupdf as fitz
import pytest
from PIL import Image
from starlette.testclient import TestClient

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.base_storage import CleanupBlockedError, DeletionResult, StorageError, get_max_upload_bytes
from src.storage.filestore import FileStore
from src.web import auth
from src.web.app import create_app
from src.worker.extraction_worker import (
    _extract_pdf_batch,
    _split_pdf_batch_to_temp_file,
    process_extraction_job,
)


def _make_synthetic_pdf_with_images(num_pages: int, image_pages: list[int]) -> bytes:
    """Create a multi-page synthetic PDF with real PNG images on specified 1-based pages."""
    doc = fitz.open()
    for page_num in range(1, num_pages + 1):
        page = doc.new_page(width=400, height=400)
        page.insert_text((50, 50), f"Synthetic Page {page_num}")
        if page_num in image_pages:
            img = Image.new("RGB", (60, 60), color=(page_num * 3 % 255, 120, 200))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(50, 80, 170, 200), stream=buf.getvalue())
            page.insert_text((50, 220), f"Figure {page_num}: Synthetic diagram on page {page_num}.")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def env_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET", "test-secret-key-for-remediation")
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("MAX_OBJECT_BYTES", raising=False)
    monkeypatch.delenv("MAX_PARALLEL_BATCHES", raising=False)

    db_path = str(tmp_path / "test.db")
    storage_root = str(tmp_path / "storage")
    os.makedirs(storage_root, exist_ok=True)
    app = create_app(db_path=db_path, storage_dir=storage_root)
    client = TestClient(app)
    return client, app.state.repo, app.state.filestore, tmp_path


def test_upload_limit_consistency_and_distinct_errors(env_setup):
    client, _, _, _ = env_setup
    expected_bytes = get_max_upload_bytes()
    assert expected_bytes == 500 * 1024 * 1024

    cfg_res = client.get("/api/uploads/config")
    assert cfg_res.status_code == 200
    assert cfg_res.json()["max_upload_bytes"] == expected_bytes

    # Empty file -> 400 invalid PDF
    empty_res = client.post("/api/uploads/authorize", json={
        "filename": "empty.pdf",
        "content_type": "application/pdf",
        "size_bytes": 0,
    })
    assert empty_res.status_code == 400
    assert "empty" in empty_res.json()["detail"].lower()

    # 104.5 MB PDF passes authorization and claim-token
    size_104_5_mb = int(104.5 * 1024 * 1024)
    ok_res = client.post("/api/uploads/authorize", json={
        "filename": "large_104_5mb.pdf",
        "content_type": "application/pdf",
        "size_bytes": size_104_5_mb,
    })
    assert ok_res.status_code == 200
    data = ok_res.json()
    assert data["max_size_bytes"] == expected_bytes

    # Claim token issues single-use token with exact expected_size
    csrf_res = client.get("/api/auth/csrf-token")
    csrf_token = csrf_res.json()["csrf_token"]
    claim_res = client.post(
        f"/api/uploads/{data['upload_key']}/claim-token",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert claim_res.status_code == 200
    assert claim_res.json()["max_size_bytes"] == size_104_5_mb

    # Over 500 MB -> 413 exceeding upload limit
    over_res = client.post(
        "/api/uploads/authorize",
        json={
            "filename": "huge.pdf",
            "content_type": "application/pdf",
            "size_bytes": expected_bytes + 1,
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert over_res.status_code == 413
    assert "upload limit" in over_res.json()["detail"].lower()


def test_temporary_pdf_splitting_and_sequential_worker_execution(env_setup, tmp_path):
    _, repo, store, _ = env_setup
    pid = f"proj_split_{uuid.uuid4().hex[:8]}"
    jid = f"job_split_{uuid.uuid4().hex[:8]}"

    # 55-page synthetic PDF with images on pages 1, 27, 53 (spans 3 batches of 25, 25, 5)
    pdf_bytes = _make_synthetic_pdf_with_images(55, [1, 27, 53])
    store.init_project_storage(pid)
    src_key, chk, _ = store.save_source_pdf(pid, pdf_bytes, "book55.pdf")
    repo.create_project(pid, "Book 55", "book55.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")

    # Verify _split_pdf_batch_to_temp_file produces a standalone PDF for pages 26..50
    split_path = str(tmp_path / "split_26_50.pdf")
    _split_pdf_batch_to_temp_file(str(store.resolve_path(pid, src_key)), list(range(26, 51)), split_path)
    split_doc = fitz.open(split_path)
    assert len(split_doc) == 25
    split_doc.close()

    # Verify _extract_pdf_batch remaps local page numbers back to original [26..50]
    res_b2, b2_temp = _extract_pdf_batch(str(store.resolve_path(pid, src_key)), list(range(26, 51)))
    try:
        assert len(res_b2.images) == 1
        assert res_b2.images[0].page_number == 27
        assert os.path.isfile(os.path.join(b2_temp, "batch.pdf"))
    finally:
        import shutil
        shutil.rmtree(b2_temp, ignore_errors=True)

    # Full sequential worker execution (default MAX_PARALLEL_BATCHES=1)
    assert process_extraction_job(jid, repo, store) is True
    assert repo.get_job(jid)["status"] == "completed"
    batches = repo.list_job_batches(jid)
    assert [len(b["page_numbers"]) for b in batches] == [25, 25, 5]
    assert all(b["status"] == "completed" for b in batches)

    images = repo.list_project_images(pid)
    assert sorted(img["page_number"] for img in images) == [1, 27, 53]
    # Original source PDF remains intact after all batches finish
    assert store.exists(pid, src_key)


def test_failed_batch_recovery_no_duplicate_images(env_setup, monkeypatch):
    _, repo, store, _ = env_setup
    pid = f"proj_retry_{uuid.uuid4().hex[:8]}"
    jid = f"job_retry_{uuid.uuid4().hex[:8]}"

    pdf_bytes = _make_synthetic_pdf_with_images(30, [2, 28])
    store.init_project_storage(pid)
    src_key, chk, _ = store.save_source_pdf(pid, pdf_bytes, "retry30.pdf")
    repo.create_project(pid, "Retry 30", "retry30.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")

    orig_extract = _extract_pdf_batch
    calls = {"count": 0}

    def flaky_extract(pdf_path, page_numbers):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("Simulated crash on batch 2")
        return orig_extract(pdf_path, page_numbers)

    monkeypatch.setattr("src.worker.extraction_worker._extract_pdf_batch", flaky_extract)

    # First run: batch 1 persists, batch 2 fails -> job is queued for retry
    assert process_extraction_job(jid, repo, store) is False
    assert repo.get_job(jid)["status"] == "queued"
    assert len(repo.list_project_images(pid)) == 1
    assert repo.list_project_images(pid)[0]["page_number"] == 2

    # Restore real _extract_pdf_batch and resume
    monkeypatch.setattr("src.worker.extraction_worker._extract_pdf_batch", orig_extract)
    assert process_extraction_job(jid, repo, store) is True
    assert repo.get_job(jid)["status"] == "completed"
    images = repo.list_project_images(pid)
    # Exactly 2 images (no duplicate from batch 1)
    assert sorted(img["page_number"] for img in images) == [2, 28]


def _seed_full_project(repo: Repository, store: FileStore, pid: str, name: str, owner_id: str | None = None):
    store.init_project_storage(pid)
    src_key, src_chk, _ = store.save_source_pdf(pid, b"%PDF-1.4\n%%EOF", f"{pid}.pdf")
    repo.create_project(pid, name, f"{pid}.pdf", src_key, src_chk, owner_id=owner_id)
    img_id = f"img_{pid}"
    img_key, img_chk, _ = store.save_image(pid, img_id, b"\x89PNG\r\n\x1a\n", "png")
    repo.create_image_asset(
        image_id=img_id, project_id=pid, page_number=1, xref=1, placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 10, "y1": 10}, original_caption="Cap",
        caption_position="below", needs_caption_review=False, review_reasons=[],
        suggested_filename="fig1.png", file_storage_key=img_key,
        file_checksum=img_chk, image_format="png",
    )
    grp_id = f"grp_{pid}"
    repo.create_figure_group(grp_id, pid, "Cap", 1, "below", "Figure 1", False, "approved", [])
    ver_id = f"ver_{pid}"
    repo.create_alt_text_version(version_id=ver_id, image_id=img_id, version_number=1, text="Alt text")
    repo.create_approval(f"app_{pid}", img_id, ver_id, "approved")
    job_id = f"job_{pid}"
    repo.create_job(job_id, pid, "extraction")
    repo.initialize_job_batches(job_id, [[1]], 1)
    repo.update_job_status(job_id, "completed")
    exp_id = f"exp_{pid}"
    exp_key, exp_chk, _ = store.save_export(pid, exp_id, b"PK\x03\x04docx", f"{pid}.docx")
    repo.create_export(exp_id, pid, "docx", exp_key, exp_chk)
    repo.update_export_status(exp_id, "verified")
    repo.record_audit_event(f"aud_{pid}", pid, "approved", "image", img_id, "user", "{}")
    repo.record_ai_usage(f"ai_{pid}", pid, img_id, "openai", "gpt-4o", 10, 5, 0.01)
    exp_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    uk = f"uk_{pid}"
    repo.create_upload_key(uk, "application/pdf", exp_at, owner_id=owner_id, blob_key=f"{pid}/{src_key}")
    repo.update_upload_key(uk, status="completed", project_id=pid)
    # Extra unlisted artifact file under {pid}/artifacts/
    art_dir = store.get_project_dir(pid) / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "temp_batch.pdf").write_bytes(b"%PDF-1.4\n")
    return {"src_key": src_key, "img_key": img_key, "exp_key": exp_key, "img_id": img_id, "job_id": job_id}


def test_complete_project_deletion_and_cross_project_isolation(env_setup):
    client, repo, store, _ = env_setup
    p_del = f"proj_del_{uuid.uuid4().hex[:6]}"
    p_keep = f"proj_keep_{uuid.uuid4().hex[:6]}"
    _seed_full_project(repo, store, p_del, "Delete Me")
    keep_info = _seed_full_project(repo, store, p_keep, "Keep Me")

    prev_res = client.get(f"/api/projects/{p_del}/delete/preview")
    assert prev_res.status_code == 200
    preview = prev_res.json()["preview"]
    assert preview["can_delete"] is True
    assert "artifacts/temp_batch.pdf" in preview["target_blob_keys"]
    assert any(k.startswith("exports/") for k in preview["target_blob_keys"])

    # Wrong project name rejected
    bad_res = client.post(f"/api/projects/{p_del}/delete", json={"confirm_project_name": "Wrong Name"})
    assert bad_res.status_code == 400
    assert repo.get_project(p_del) is not None

    # Exact project name deletes everything including DOCX exports and all child tables
    ok_res = client.post(f"/api/projects/{p_del}/delete", json={"confirm_project_name": "Delete Me"})
    assert ok_res.status_code == 200
    assert ok_res.json()["status"] == "deleted"

    assert repo.get_project(p_del) is None
    assert repo.list_project_images(p_del) == []
    assert not store.get_project_dir(p_del).exists()
    with repo.db.session() as conn:
        for table, col in [
            ("projects", "id"), ("image_assets", "project_id"), ("figure_groups", "project_id"),
            ("jobs", "project_id"), ("exports", "project_id"), ("audit_events", "project_id"),
            ("ai_usage_ledger", "project_id"), ("upload_keys", "project_id"), ("cleanup_operations", "project_id"),
        ]:
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?;", (p_del,)).fetchone()[0] == 0

    # Sibling project and its DOCX export are completely untouched
    assert repo.get_project(p_keep) is not None
    assert store.exists(p_keep, keep_info["src_key"])
    assert store.exists(p_keep, keep_info["img_key"])
    assert store.exists(p_keep, keep_info["exp_key"])


def test_complete_project_deletion_partial_failure_retry_and_fencing(env_setup):
    client, repo, store, _ = env_setup
    pid = f"proj_fail_{uuid.uuid4().hex[:6]}"
    _seed_full_project(repo, store, pid, "Retry Delete")

    # Active job blocks deletion
    active_job = f"job_act_{uuid.uuid4().hex[:6]}"
    repo.create_job(active_job, pid, "extraction")
    blk_res = client.post(f"/api/projects/{pid}/delete", json={"confirm_project_name": "Retry Delete"})
    assert blk_res.status_code == 409
    repo.update_job_status(active_job, "completed")

    # Simulate partial Blob failure on first delete_objects call
    orig_delete_objects = store.delete_objects
    calls = {"n": 0}

    def flaky_delete(project_id, keys):
        calls["n"] += 1
        if calls["n"] == 1:
            return DeletionResult(deleted=[], missing=[], failed={keys[0]: "Simulated 503"})
        return orig_delete_objects(project_id, keys)

    store.delete_objects = flaky_delete
    part_res = client.post(f"/api/projects/{pid}/delete", json={"confirm_project_name": "Retry Delete"})
    assert part_res.status_code == 200
    assert part_res.json()["status"] == "partial_failure"
    op_id = part_res.json()["operation"]["id"]
    # Project & exports remain while deletion is incomplete, but new jobs/cleanups are fenced
    assert repo.get_project(pid) is not None
    with pytest.raises(CleanupBlockedError):
        repo.create_job("blocked_job", pid, "extraction")
    with pytest.raises(CleanupBlockedError):
        repo.prepare_cleanup_operation(pid, "extracted")

    # Retry succeeds and purges remaining storage + DB records
    retry_res = client.post(f"/api/deletions/{op_id}/retry")
    assert retry_res.status_code == 200
    assert retry_res.json()["status"] == "deleted"
    assert repo.get_project(pid) is None


def test_delete_project_ownership_and_csrf_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SESSION_SECRET_KEY", "multi-user-secret-key-for-delete-test-0123456789abcdef")
    db_path = str(tmp_path / "auth_del.db")
    storage_root = str(tmp_path / "auth_store")
    os.makedirs(storage_root, exist_ok=True)
    app = create_app(db_path=db_path, storage_dir=storage_root)
    repo = app.state.repo
    store = app.state.filestore
    client = TestClient(app)

    u1 = repo.create_user("u_owner", "owner@example.com", "Password123!")
    u2 = repo.create_user("u_other", "other@example.com", "Password123!")
    pid = f"proj_own_{uuid.uuid4().hex[:6]}"
    _seed_full_project(repo, store, pid, "Owned Book", owner_id=u1["id"])

    # Login as u2
    csrf_pre = client.get("/api/auth/csrf-token").json()["csrf_token"]
    login2 = client.post(
        "/api/auth/login",
        json={"email": "other@example.com", "password": "Password123!"},
        headers={"X-CSRF-Token": csrf_pre},
    )
    assert login2.status_code == 200
    csrf2 = client.get("/api/auth/csrf-token").json()["csrf_token"]

    # u2 cannot preview or delete u1's project
    assert client.get(f"/api/projects/{pid}/delete/preview").status_code in (403, 404)
    assert client.post(
        f"/api/projects/{pid}/delete",
        json={"confirm_project_name": "Owned Book"},
        headers={"X-CSRF-Token": csrf2},
    ).status_code in (403, 404)

    # Login as u1 without CSRF -> 403
    csrf1 = client.get("/api/auth/csrf-token").json()["csrf_token"]
    assert client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "Password123!"},
        headers={"X-CSRF-Token": csrf1},
    ).status_code == 200
    assert client.post(
        f"/api/projects/{pid}/delete",
        json={"confirm_project_name": "Owned Book"},
    ).status_code == 403
    assert repo.get_project(pid) is not None


def test_sqlite_deletion_crash_recovery_lease_fencing_and_partial_db(env_setup, monkeypatch):
    client, repo, store, _ = env_setup
    monkeypatch.setenv("DELETION_LEASE_SECONDS", "300")
    pid_del = f"proj_crash_{uuid.uuid4().hex[:6]}"
    pid_keep = f"proj_safe_{uuid.uuid4().hex[:6]}"
    _seed_full_project(repo, store, pid_del, "Crash Book")
    keep_info = _seed_full_project(repo, store, pid_keep, "Safe Book")

    # 1. Unexpected Blob deletion exception preserves manifest, records error, stays recoverable
    op = repo.prepare_project_deletion(pid_del, storage=store)
    op_id = op["id"]
    orig_delete = store.delete_objects

    def boom_delete(project_id, keys):
        raise RuntimeError("Simulated Blob client crash")

    store.delete_objects = boom_delete
    with pytest.raises(StorageError):
        repo._run_project_deletion_operation(op, store)
    failed_op = repo.get_cleanup_operation(op_id)
    assert failed_op["status"] == "failed"
    assert "Simulated Blob client crash" in (failed_op["error_message"] or "")
    assert failed_op["target_blob_keys"] == op["target_blob_keys"]
    assert repo.get_project(pid_del) is not None

    # 2. Simulate crash after entering deleting_blobs with a stale lease
    store.delete_objects = orig_delete
    stale_exp = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    with repo.db.session() as conn:
        conn.execute(
            "UPDATE cleanup_operations SET status = 'deleting_blobs', lease_token = 'dead-worker', lease_expires_at = ? WHERE id = ?;",
            (stale_exp, op_id),
        )
    prev = client.get(f"/api/projects/{pid_del}/delete/preview").json()["preview"]
    assert prev["existing_operation"]["id"] == op_id
    assert prev["can_resume"] is True
    assert prev["can_delete"] is False

    # 3. Fresh active lease cannot be reclaimed
    fresh_exp = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    with repo.db.session() as conn:
        conn.execute(
            "UPDATE cleanup_operations SET status = 'deleting_blobs', lease_token = 'live-worker', lease_expires_at = ? WHERE id = ?;",
            (fresh_exp, op_id),
        )
    with pytest.raises(CleanupBlockedError):
        repo.retry_project_deletion(op_id, storage=store)

    # 4. Old worker cannot update after its lease is replaced
    with repo.db.session() as conn:
        conn.execute(
            "UPDATE cleanup_operations SET lease_expires_at = ? WHERE id = ?;",
            (stale_exp, op_id),
        )
    claimed_op, new_token = repo._claim_project_deletion_operation(op_id)
    assert new_token and new_token != "live-worker"
    with pytest.raises(CleanupBlockedError):
        repo._renew_deletion_lease(op_id, "live-worker")
    assert repo._record_deletion_failure(op_id, "live-worker", [], [], {}, "stale") is False

    # 5. Concurrent recovery workers: only one succeeds, the other gets CleanupBlockedError
    with repo.db.session() as conn:
        conn.execute(
            "UPDATE cleanup_operations SET lease_expires_at = ? WHERE id = ?;",
            (stale_exp, op_id),
        )
    import threading
    barrier = threading.Barrier(2)
    results = []

    def _compete():
        barrier.wait()
        try:
            _, tok = repo._claim_project_deletion_operation(op_id)
            results.append(("ok", tok))
        except CleanupBlockedError:
            results.append(("blocked", None))

    t1 = threading.Thread(target=_compete)
    t2 = threading.Thread(target=_compete)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(r[0] for r in results) == ["blocked", "ok"]

    # 6. Crash during deleting_records with partial DB deletion already applied -> retry completes cleanly
    with repo.db.session() as conn:
        conn.execute("DELETE FROM approvals WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);", (pid_del,))
        conn.execute("DELETE FROM alt_text_versions WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = ?);", (pid_del,))
        conn.execute(
            "UPDATE cleanup_operations SET status = 'deleting_records', deleted_blob_keys = target_blob_keys, lease_token = 'crashed-rec', lease_expires_at = ? WHERE id = ?;",
            (stale_exp, op_id),
        )
    res = client.post(f"/api/deletions/{op_id}/retry")
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"
    assert repo.get_project(pid_del) is None
    assert repo.get_cleanup_operation(op_id) is None

    # 7. Cross-project isolation intact
    assert repo.get_project(pid_keep) is not None
    assert store.exists(pid_keep, keep_info["exp_key"])

