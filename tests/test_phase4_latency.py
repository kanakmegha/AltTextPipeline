import io
import os
import threading
import uuid

import pymupdf as fitz
import pytest
from PIL import Image

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.worker import extraction_worker
from src.worker.extraction_worker import _extract_pdf_batch, process_extraction_job


def _make_pdf(pages_with_images: list[int], total_pages: int = 6) -> bytes:
    doc = fitz.open()
    for p in range(1, total_pages + 1):
        page = doc.new_page(width=450, height=550)
        page.insert_text((40, 30), f"Page {p}")
        if p in pages_with_images:
            img = Image.new("RGB", (60, 60), color=(p * 30 % 255, 110, 180))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(60, 80, 180, 200), stream=buf.getvalue())
            page.insert_text((60, 220), f"Figure {p}: Diagram on page {p}.")
    data = doc.tobytes()
    doc.close()
    return data


def _setup_env(tmp_path, name: str, pages_with_images=None, total_pages=6):
    db_path = str(tmp_path / f"{name}.db")
    storage_dir = str(tmp_path / f"storage_{name}")
    os.makedirs(storage_dir, exist_ok=True)
    conn = DatabaseConnection(db_path)
    apply_migrations(conn)
    repo = Repository(conn)
    filestore = FileStore(base_dir=storage_dir)
    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    pdf_bytes = _make_pdf(pages_with_images or [1, 3, 5], total_pages=total_pages)
    src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, f"{name}.pdf")
    repo.create_project(pid, f"Proj {name}", f"{name}.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")
    return repo, filestore, pid, jid


def test_worker_termination_during_batch_and_retry_after_partial_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    monkeypatch.setenv("MAX_RETRIES", "2")
    repo, filestore, pid, jid = _setup_env(tmp_path, "term", [1, 3, 5], total_pages=6)

    orig_process_batch = extraction_worker._process_batch
    killed = {"done": False}

    def crash_on_batch_2(pdf_path, pages, engine):
        if 3 in pages and not killed["done"]:
            killed["done"] = True
            raise RuntimeError("simulated worker termination")
        return orig_process_batch(pdf_path, pages, engine)

    monkeypatch.setattr(extraction_worker, "_process_batch", crash_on_batch_2)
    assert process_extraction_job(jid, repo, filestore) is False
    assert repo.get_job(jid)["status"] == "queued"
    # Batch 1 (page 1) already persisted
    assert len(repo.list_project_images(pid)) == 1

    # Retry completes remaining batches without duplicating page 1
    monkeypatch.setattr(extraction_worker, "_process_batch", orig_process_batch)
    assert process_extraction_job(jid, repo, filestore) is True
    assert repo.get_job(jid)["status"] == "completed"
    assert len(repo.list_project_images(pid)) == 3


def test_lease_expiry_prevents_stale_worker_from_completing_job(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    repo, filestore, pid, jid = _setup_env(tmp_path, "lease", [1, 3, 5], total_pages=6)

    orig_process_batch = extraction_worker._process_batch
    stolen = {"done": False}

    def steal_lease_mid_job(pdf_path, pages, engine):
        if 3 in pages and not stolen["done"]:
            stolen["done"] = True
            with repo.db.session() as conn:
                conn.execute("UPDATE jobs SET worker_id = 'other-worker' WHERE id = ?;", (jid,))
        return orig_process_batch(pdf_path, pages, engine)

    monkeypatch.setattr(extraction_worker, "_process_batch", steal_lease_mid_job)
    assert process_extraction_job(jid, repo, filestore, worker_id="stale-worker") is False

    # Stale worker must NOT have marked the job completed
    job = repo.get_job(jid)
    assert job["worker_id"] == "other-worker"
    assert job["status"] != "completed"


def test_known_source_map_not_poisoned_on_db_failure_and_partial_blob_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    monkeypatch.setenv("MAX_RETRIES", "3")
    repo, filestore, pid, jid = _setup_env(tmp_path, "cache_fail", [1, 2], total_pages=2)

    orig_insert = repo.create_image_assets_batch
    fail_db = {"done": False}

    def flaky_insert(items):
        if not fail_db["done"]:
            fail_db["done"] = True
            raise RuntimeError("transient db error")
        return orig_insert(items)

    monkeypatch.setattr(repo, "create_image_assets_batch", flaky_insert)
    assert process_extraction_job(jid, repo, filestore) is False
    assert len(repo.list_project_images(pid)) == 0

    # Retry succeeds and inserts both images
    assert process_extraction_job(jid, repo, filestore) is True
    assert len(repo.list_project_images(pid)) == 2


def test_concurrent_jobs_for_different_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")

    db_path = str(tmp_path / "shared.db")
    storage_dir = str(tmp_path / "shared_storage")
    os.makedirs(storage_dir, exist_ok=True)
    conn = DatabaseConnection(db_path)
    apply_migrations(conn)
    repo = Repository(conn)
    filestore = FileStore(base_dir=storage_dir)

    projects = []
    for idx, img_pages in enumerate(([1, 3], [2, 4, 6])):
        pid = str(uuid.uuid4())
        jid = str(uuid.uuid4())
        pdf_bytes = _make_pdf(img_pages, total_pages=6)
        src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, f"p{idx}.pdf")
        repo.create_project(pid, f"Proj {idx}", f"p{idx}.pdf", src_key, chk)
        repo.create_job(jid, pid, "extraction")
        projects.append((pid, jid, len(img_pages)))

    barrier = threading.Barrier(2)
    outcomes = []

    def run_one(jid):
        barrier.wait()
        outcomes.append(process_extraction_job(jid, repo, filestore))

    t1 = threading.Thread(target=run_one, args=(projects[0][1],))
    t2 = threading.Thread(target=run_one, args=(projects[1][1],))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert outcomes == [True, True]
    for pid, jid, expected_count in projects:
        assert repo.get_job(jid)["status"] == "completed"
        assert len(repo.list_project_images(pid)) == expected_count


def test_no_heartbeat_renewal_or_lost_lease_warning_after_completion(tmp_path, monkeypatch, caplog):
    import logging
    import time

    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    repo, filestore, pid, jid = _setup_env(tmp_path, "hb_stop", [1, 3], total_pages=4)

    events = []
    orig_renew = repo.renew_job_lease
    orig_update = repo.update_claimed_job_status

    def tracked_renew(job_id, worker_id, now):
        events.append(("renew", time.monotonic()))
        return orig_renew(job_id, worker_id, now)

    def tracked_update(job_id, worker_id, status, error_message=None):
        events.append((f"status:{status}", time.monotonic()))
        return orig_update(job_id, worker_id, status, error_message=error_message)

    monkeypatch.setattr(repo, "renew_job_lease", tracked_renew)
    monkeypatch.setattr(repo, "update_claimed_job_status", tracked_update)

    with caplog.at_level(logging.WARNING, logger="alttext.worker"):
        assert process_extraction_job(jid, repo, filestore, heartbeat_seconds=0.05) is True

    time.sleep(0.15)
    assert not any("lost lease" in rec.message for rec in caplog.records)
    completed_idx = next(i for i, (ev, _) in enumerate(events) if ev == "status:completed")
    assert all(ev != "renew" for ev, _ in events[completed_idx + 1:])


def test_in_flight_heartbeat_cannot_modify_completed_job(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    repo, filestore, pid, jid = _setup_env(tmp_path, "hb_inflight", [1], total_pages=2)

    orig_renew = repo.renew_job_lease
    entered = threading.Event()
    release = threading.Event()

    def slow_renew(job_id, worker_id, now):
        entered.set()
        release.wait(timeout=0.5)
        return orig_renew(job_id, worker_id, now)

    monkeypatch.setattr(repo, "renew_job_lease", slow_renew)

    result_box = []
    t = threading.Thread(
        target=lambda: result_box.append(process_extraction_job(jid, repo, filestore, heartbeat_seconds=0.01))
    )
    t.start()
    assert entered.wait(timeout=2.0)
    release.set()
    t.join(timeout=5.0)

    assert result_box == [True]
    job_after = repo.get_job(jid)
    assert job_after["status"] == "completed"
    # Direct late renewal call against completed job must be rejected and leave status completed
    assert orig_renew(jid, job_after["worker_id"], "2099-01-01T00:00:00+00:00") is False
    assert repo.get_job(jid)["status"] == "completed"


def test_genuine_heartbeat_lease_loss_prevents_stale_worker_completion(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    repo, filestore, pid, jid = _setup_env(tmp_path, "hb_loss", [1, 3, 5], total_pages=6)

    orig_process_batch = extraction_worker._process_batch

    def slow_batch(pdf_path, pages, engine):
        time.sleep(0.08)
        return orig_process_batch(pdf_path, pages, engine)

    monkeypatch.setattr(extraction_worker, "_process_batch", slow_batch)
    monkeypatch.setattr(repo, "renew_job_lease", lambda *_args, **_kwargs: False)

    assert process_extraction_job(jid, repo, filestore, heartbeat_seconds=0.02, worker_id="stale-hb") is False
    assert repo.get_job(jid)["status"] != "completed"


def test_worker_exception_shuts_down_heartbeat_cleanly(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    repo, filestore, pid, jid = _setup_env(tmp_path, "hb_exc", [1, 3], total_pages=4)

    monkeypatch.setattr(
        extraction_worker,
        "_process_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    renew_calls_after = []
    finished_flag = {"done": False}
    orig_renew = repo.renew_job_lease

    def counting_renew(job_id, worker_id, now):
        if finished_flag["done"]:
            renew_calls_after.append(now)
        return orig_renew(job_id, worker_id, now)

    monkeypatch.setattr(repo, "renew_job_lease", counting_renew)
    assert process_extraction_job(jid, repo, filestore, heartbeat_seconds=0.02) is False
    finished_flag["done"] = True
    time.sleep(0.12)
    assert renew_calls_after == []
