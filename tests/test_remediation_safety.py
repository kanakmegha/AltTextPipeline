"""
Regression tests for Production Safety Remediation (Priorities 1-6).
"""

import hashlib
import io
import os
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.base_storage import (
    CleanupBlockedError,
    DeletionResult,
    StorageError,
    get_max_upload_bytes,
)
from src.storage.blob_storage import BlobStorage
from src.storage.filestore import FileStore
from src.web.app import create_app
from src.worker.extraction_worker import (
    _extract_pdf_batch,
    _persist_batch_images,
    process_extraction_job,
)


@pytest.fixture
def env_setup(tmp_path):
    db_path = str(tmp_path / "remediation.db")
    storage_root = str(tmp_path / "storage")
    os.makedirs(storage_root, exist_ok=True)
    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    store = FileStore(storage_root)
    app = create_app(db_path=db_path, storage_dir=storage_root)
    client = TestClient(app)
    return client, repo, app.state.filestore, tmp_path


def _make_pdf(num_pages=2, with_image=True, img_size=(64, 64)) -> bytes:
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Figure {i + 1}. Sample diagram on page {i + 1}")
        if with_image:
            img = Image.new("RGB", img_size, color="green")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(72, 100, 200, 220), stream=buf.getvalue())
    return doc.tobytes()


def _seed_cleanable_project(repo: Repository, store: FileStore, project_id: str):
    store.init_project_storage(project_id)
    pdf_bytes = _make_pdf(num_pages=1, with_image=True)
    pdf_key, pdf_hash, _ = store.save_source_pdf(project_id, pdf_bytes, "doc.pdf")
    repo.create_project(project_id, "Test", "doc.pdf", pdf_key, pdf_hash)
    img_key, img_hash, _ = store.save_image(project_id, "img1", b"\x89PNG\r\n\x1a\nfake", "png")
    repo.create_image_asset(
        image_id="img1",
        project_id=project_id,
        page_number=1,
        xref=1,
        placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        original_caption="Fig 1",
        caption_position="bottom",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="fig1.png",
        file_storage_key=img_key,
        file_checksum=img_hash,
        image_format="png",
    )
    exp_key, exp_hash, _ = store.save_export(project_id, "exp1", b"PK\x03\x04docx", "out.docx")
    repo.create_export("exp1", project_id, "docx", exp_key, exp_hash, status="verified", figure_count=1)
    repo.update_export_status("exp1", status="verified", mark_verified=True)
    return pdf_key, img_key, exp_key


# ==============================================================================
# Priority 1: Transactional Cleanup & Job/Export Fencing
# ==============================================================================

def test_concurrent_cleanup_prepare_only_one_succeeds(env_setup):
    _, repo, store, _ = env_setup
    project_id = f"proj_race_{uuid.uuid4().hex[:8]}"
    _seed_cleanable_project(repo, store, project_id)

    barrier = threading.Barrier(2)
    outcomes = []

    def try_prepare():
        barrier.wait()
        try:
            op = repo.prepare_cleanup_operation(project_id, "extracted")
            outcomes.append(("ok", op["id"]))
        except CleanupBlockedError as exc:
            outcomes.append(("blocked", str(exc)))

    t1 = threading.Thread(target=try_prepare)
    t2 = threading.Thread(target=try_prepare)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    ok_count = sum(1 for k, _ in outcomes if k == "ok")
    blocked_count = sum(1 for k, _ in outcomes if k == "blocked")
    assert ok_count == 1
    assert blocked_count == 1


def test_job_and_export_creation_fenced_during_cleanup(env_setup):
    _, repo, store, _ = env_setup
    project_id = f"proj_fence_{uuid.uuid4().hex[:8]}"
    _seed_cleanable_project(repo, store, project_id)

    repo.prepare_cleanup_operation(project_id, "extracted")

    with pytest.raises(CleanupBlockedError):
        repo.create_job("job_blocked", project_id, "extraction")

    with pytest.raises(CleanupBlockedError):
        repo.create_export("exp_blocked", project_id, "docx", "exports/b.docx", "abc")


# ==============================================================================
# Priority 2: Stream PDF Downloads & Incremental SHA-256
# ==============================================================================

def test_filestore_download_to_file_and_error_cleanup(tmp_path):
    store = FileStore(str(tmp_path / "storage"))
    pid = "proj_stream_local"
    store.init_project_storage(pid)
    payload = _make_pdf(num_pages=2)
    key, expected_hash, expected_size = store.save_source_pdf(pid, payload, "stream.pdf")

    dest = str(tmp_path / "downloaded.pdf")
    size, digest = store.download_to_file(pid, key, dest)
    assert size == expected_size
    assert digest == expected_hash
    assert os.path.isfile(dest)

    # Interrupted write cleans up partial destination file
    dest_fail = str(tmp_path / "broken.pdf")
    with patch("builtins.open", side_effect=[open(store.resolve_path(pid, key), "rb"), OSError("disk full")]):
        with pytest.raises(OSError):
            store.download_to_file(pid, key, dest_fail)
    assert not os.path.exists(dest_fail)


def test_blob_storage_download_to_file_streams_and_cleans_on_interrupt(tmp_path):
    with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "tok_test_123"}):
        blob_store = BlobStorage(blob_token="tok_test_123")

    chunks = [b"%PDF-1.4\n", b"chunk-one-" * 100, b"chunk-two-" * 100]
    expected = b"".join(chunks)
    expected_hash = hashlib.sha256(expected).hexdigest()

    class FakeStream:
        def __init__(self, items, fail_after=None):
            self.items = items
            self.fail_after = fail_after

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            for idx, item in enumerate(self.items):
                if self.fail_after is not None and idx >= self.fail_after:
                    raise RuntimeError("mid-stream network reset")
                yield item

    dest_ok = str(tmp_path / "blob_ok.pdf")
    with patch("src.storage.blob_storage.blob.stream_blob", return_value=FakeStream(chunks)):
        size, digest = blob_store.download_to_file("proj1", "source/a.pdf", dest_ok)
    assert size == len(expected)
    assert digest == expected_hash

    dest_err = str(tmp_path / "blob_err.pdf")
    with patch("src.storage.blob_storage.blob.stream_blob", return_value=FakeStream(chunks, fail_after=2)):
        with pytest.raises(StorageError):
            blob_store.download_to_file("proj1", "source/a.pdf", dest_err)
    assert not os.path.exists(dest_err)


def test_worker_streams_30_page_pdf_in_25_page_batches(env_setup):
    _, repo, store, _ = env_setup
    pid = f"proj_30p_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(pid)
    pdf_bytes = _make_pdf(num_pages=30, with_image=True)
    key, checksum, _ = store.save_source_pdf(pid, pdf_bytes, "30pages.pdf")
    repo.create_project(pid, "30 Pages", "30pages.pdf", key, checksum)
    job = repo.create_job(f"job_{pid}", pid, "extraction")

    with patch.dict(os.environ, {"MAX_PARALLEL_BATCHES": "1"}):
        ok = process_extraction_job(job["id"], repo, store)
    assert ok is True
    batches = repo.list_job_batches(job["id"])
    assert len(batches) == 2
    assert len(batches[0]["page_numbers"]) == 25
    assert len(batches[1]["page_numbers"]) == 5


# ==============================================================================
# Priority 3: Orphan Upload Recovery & Race Safety
# ==============================================================================

def test_orphan_upload_recovery_and_race_with_complete(env_setup):
    client, repo, store, _ = env_setup
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    # 1. Expired incomplete upload with actual Blob on disk
    p_expired = f"proj_exp_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(p_expired)
    expired_key, _, _ = store.save_source_pdf(p_expired, b"%PDF-1.4 orphan", "orphan.pdf")
    repo.create_upload_key(
        upload_key="uk_expired",
        content_type="application/pdf",
        expires_at=past,
        owner_id=None,
        filename="orphan.pdf",
        expected_size=15,
        blob_key=f"{p_expired}/{expired_key}",
    )

    # 2. Active non-expired upload (must NOT be touched)
    p_active = f"proj_act_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(p_active)
    active_key, _, _ = store.save_source_pdf(p_active, b"%PDF-1.4 active", "active.pdf")
    repo.create_upload_key(
        upload_key="uk_active",
        content_type="application/pdf",
        expires_at=future,
        owner_id=None,
        filename="active.pdf",
        expected_size=15,
        blob_key=f"{p_active}/{active_key}",
    )

    # 3. Upload currently finalizing (must NOT be touched even if past expiry)
    p_fin = f"proj_fin_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(p_fin)
    fin_key, _, _ = store.save_source_pdf(p_fin, b"%PDF-1.4 finalizing", "fin.pdf")
    repo.create_upload_key(
        upload_key="uk_finalizing",
        content_type="application/pdf",
        expires_at=past,
        owner_id=None,
        filename="fin.pdf",
        expected_size=19,
        blob_key=f"{p_fin}/{fin_key}",
    )
    repo.update_upload_key("uk_finalizing", status="finalizing")

    res = client.post("/api/uploads/cleanup-orphans")
    assert res.status_code == 200
    recovery = res.json()["recovery"]
    assert "uk_expired" in recovery["cleaned"]
    assert not store.exists(p_expired, expired_key)
    assert store.exists(p_active, active_key)
    assert store.exists(p_fin, fin_key)

    # Idempotent re-run
    res2 = client.post("/api/uploads/cleanup-orphans")
    assert res2.status_code == 200
    assert res2.json()["recovery"]["processed"] == 0


def test_orphan_upload_failure_and_retry(env_setup):
    _, repo, store, _ = env_setup
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    pid = f"proj_fail_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(pid)
    src_key, _, _ = store.save_source_pdf(pid, b"%PDF-1.4 retry", "retry.pdf")
    repo.create_upload_key(
        upload_key="uk_fail",
        content_type="application/pdf",
        expires_at=past,
        owner_id=None,
        filename="retry.pdf",
        expected_size=14,
        blob_key=f"{pid}/{src_key}",
    )

    with patch.object(
        store,
        "delete_object",
        return_value=DeletionResult(failed={src_key: "transient error"}),
    ):
        r1 = repo.recover_orphan_uploads(store)
        assert "uk_fail" in r1["failed"]
        assert repo.get_upload_key("uk_fail")["status"] == "orphan_cleanup_failed"

    r2 = repo.recover_orphan_uploads(store)
    assert "uk_fail" in r2["cleaned"]
    assert repo.get_upload_key("uk_fail")["status"] == "cleaned_orphan"


# ==============================================================================
# Priority 4: Single Authoritative Upload Limit Config
# ==============================================================================

def test_authoritative_max_upload_mb_config(env_setup):
    client, _, _, _ = env_setup
    assert get_max_upload_bytes() == 500 * 1024 * 1024

    with patch.dict(os.environ, {"MAX_UPLOAD_MB": "150", "BLOB_READ_WRITE_TOKEN": "tok_1"}):
        assert get_max_upload_bytes() == 150 * 1024 * 1024
        res_ok = client.post("/api/uploads/authorize", json={
            "filename": "ok.pdf", "content_type": "application/pdf", "size_bytes": 120 * 1024 * 1024,
        })
        assert res_ok.status_code == 200
        assert res_ok.json()["max_size_bytes"] == 150 * 1024 * 1024

        res_big = client.post("/api/uploads/authorize", json={
            "filename": "big.pdf", "content_type": "application/pdf", "size_bytes": 151 * 1024 * 1024,
        })
        assert res_big.status_code == 413

        blob = BlobStorage(blob_token="tok_1")
        # Ensure _validate_bytes uses get_max_upload_bytes() dynamically
        with patch.dict(os.environ, {"MAX_UPLOAD_MB": "1"}):
            with pytest.raises(StorageError, match="1048576 bytes"):
                blob._validate_bytes(b"x" * (2 * 1024 * 1024), "oversize")


# ==============================================================================
# Priority 5: Parallel Worker IPC Memory Reduction
# ==============================================================================

def test_extract_pdf_batch_strips_ipc_image_bytes_and_persists_from_disk(env_setup):
    _, repo, store, tmp_path = env_setup
    pid = f"proj_ipc_{uuid.uuid4().hex[:8]}"
    store.init_project_storage(pid)
    repo.create_project(pid, "IPC Test", "ipc.pdf", "source/ipc.pdf", "abc")

    pdf_file = tmp_path / "ipc.pdf"
    pdf_file.write_bytes(_make_pdf(num_pages=2, with_image=True, img_size=(256, 256)))

    result, temp_dir = _extract_pdf_batch(str(pdf_file), [1, 2])
    try:
        # Verify IPC payload was stripped while disk files exist
        for img in result.images:
            assert img.image_bytes == b""
            assert os.path.isfile(os.path.join(temp_dir, img.suggested_filename))

        _persist_batch_images(repo, store, pid, result, batch_temp_dir=temp_dir)
        saved = repo.list_project_images(pid)
        assert len(saved) == 2
        for item in saved:
            assert store.exists(pid, item["file_storage_key"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ==============================================================================
# Priority 6: Project List Pagination
# ==============================================================================

def test_projects_list_pagination(env_setup):
    client, repo, store, _ = env_setup
    for i in range(12):
        pid = f"proj_pg_{i:02d}_{uuid.uuid4().hex[:4]}"
        store.init_project_storage(pid)
        repo.create_project(pid, f"Project {i}", f"f{i}.pdf", f"source/f{i}.pdf", "abc")

    res_p1 = client.get("/api/projects?limit=5&offset=0")
    assert res_p1.status_code == 200
    d1 = res_p1.json()
    assert d1["total_count"] == 12
    assert len(d1["projects"]) == 5

    res_p3 = client.get("/api/projects?limit=5&offset=10")
    assert res_p3.status_code == 200
    d3 = res_p3.json()
    assert d3["total_count"] == 12
    assert len(d3["projects"]) == 2


def test_sqlite_orphan_upload_stale_vs_fresh_claim_recovery(env_setup):
    from datetime import datetime, timedelta, timezone
    from concurrent.futures import ThreadPoolExecutor

    _, repo, store, _ = env_setup
    past_exp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    k_fresh = f"uk_sq_fresh_{uuid.uuid4().hex[:6]}"
    k_stale = f"uk_sq_stale_{uuid.uuid4().hex[:6]}"

    store.init_project_storage("proj_sq_fresh")
    store.init_project_storage("proj_sq_stale")
    fresh_key, _, _ = store.save_source_pdf("proj_sq_fresh", b"%PDF-1.4\nfresh", "f.pdf")
    stale_key, _, _ = store.save_source_pdf("proj_sq_stale", b"%PDF-1.4\nstale", "s.pdf")
    repo.create_upload_key(
        upload_key=k_fresh,
        content_type="application/pdf",
        expires_at=past_exp,
        filename="f.pdf",
        expected_size=14,
        blob_key=f"proj_sq_fresh/{fresh_key}",
    )
    repo.create_upload_key(
        upload_key=k_stale,
        content_type="application/pdf",
        expires_at=past_exp,
        filename="s.pdf",
        expected_size=14,
        blob_key=f"proj_sq_stale/{stale_key}",
    )

    assert repo.claim_orphan_upload_cleanup(k_fresh, claim_ttl_seconds=300) is not None
    # Fresh claim must not be stolen
    assert repo.claim_orphan_upload_cleanup(k_fresh, claim_ttl_seconds=300) is None

    assert repo.claim_orphan_upload_cleanup(k_stale, claim_ttl_seconds=300) is not None
    with repo.db.session() as conn:
        conn.execute("UPDATE upload_keys SET updated_at = ? WHERE upload_key = ?;", (stale_ts, k_stale))

    eligible_keys = {r["upload_key"] for r in repo.list_expired_incomplete_uploads(limit=50, claim_ttl_seconds=300)}
    assert k_stale in eligible_keys
    assert k_fresh not in eligible_keys

    # Concurrent reclaim on stale claim: only one worker wins
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: repo.claim_orphan_upload_cleanup(k_stale, claim_ttl_seconds=300), range(4)))
    assert len([c for c in claims if c is not None]) == 1

    # Make stale again and run full recovery
    with repo.db.session() as conn:
        conn.execute("UPDATE upload_keys SET updated_at = ? WHERE upload_key = ?;", (stale_ts, k_stale))
    rec = repo.recover_orphan_uploads(store, limit=50, claim_ttl_seconds=300)
    assert k_stale in rec["cleaned"]
    assert k_fresh not in rec["cleaned"]
    assert not store.exists("proj_sq_stale", stale_key)
    assert store.exists("proj_sq_fresh", fresh_key)
    assert repo.get_upload_key(k_stale)["status"] == "cleaned_orphan"
