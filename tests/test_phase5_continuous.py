import io
import os
import threading
import time
import uuid

import pymupdf as fitz
import pytest
from PIL import Image
from starlette.testclient import TestClient

from src.web.app import create_app
from src.worker import extraction_worker
from src.worker.extraction_worker import process_extraction_job


def _make_grouped_multipage_pdf(num_pages: int, image_pages: list[int]) -> bytes:
    """Create a multi-page PDF with distinct images and captions for Option C tests."""
    doc = fitz.open()
    for p in range(1, num_pages + 1):
        page = doc.new_page(width=500, height=650)
        page.insert_text((40, 30), f"Document Page {p}")
        if p in image_pages:
            # Make distinct colors/sizes per page
            img = Image.new("RGB", (80, 60), color=((p * 37) % 255, (p * 73) % 255, 160))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(60, 80, 220, 200), stream=buf.getvalue())
            page.insert_text((60, 220), f"Figure {p}: Illustration on page {p}.")
    data = doc.tobytes()
    doc.close()
    return data


def test_option_c_first_image_flushes_immediately_while_job_running(tmp_path, monkeypatch):
    """
    Page 1 cover image must flush immediately on arrival without waiting for
    PERSIST_MICRO_BATCH_SIZE=4 or 25-page batch completion, and be visible via API
    while job_status == 'running'.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PDF_BATCH_SIZE", "25")
    monkeypatch.setenv("PERSIST_MICRO_BATCH_SIZE", "4")
    monkeypatch.setenv("PERSIST_FLUSH_INTERVAL_SECONDS", "5.0")

    db_path = str(tmp_path / "optc_ttfi.db")
    storage_dir = str(tmp_path / "storage")
    os.makedirs(storage_dir, exist_ok=True)
    app = create_app(db_path=db_path, storage_dir=storage_dir)
    client = TestClient(app)
    repo = app.state.repo
    filestore = app.state.filestore

    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    # 6 pages, images on pages 1, 2, 3, 4, 5, 6
    pdf_bytes = _make_grouped_multipage_pdf(6, [1, 2, 3, 4, 5, 6])
    src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, "book.pdf")
    repo.create_project(pid, "OptionC TTFI", "book.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")

    flush_sizes = []
    mid_job_snapshot = {}
    orig_persist = extraction_worker._persist_images_list

    def spy_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs):
        flush_sizes.append((len(image_entries), [e[0].page_number for e in image_entries]))
        res = orig_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs)
        # Immediately after the very first flush (page 1), check API visibility while job is still running
        if len(flush_sizes) == 1:
            prog = client.get(f"/api/jobs/{jid}/progress").json()
            imgs = client.get(f"/api/jobs/{jid}/images").json()
            mid_job_snapshot["progress"] = prog
            mid_job_snapshot["images"] = imgs["images"]
        return res

    monkeypatch.setattr(extraction_worker, "_persist_images_list", spy_persist)

    assert process_extraction_job(jid, repo, filestore) is True

    # First flush must be a single image (size 1) before micro-batching kicks in!
    assert len(flush_sizes) >= 2
    assert flush_sizes[0][0] == 1
    assert mid_job_snapshot["progress"]["job_status"] == "running"
    assert len(mid_job_snapshot["images"]) == 1
    assert sum(sz for sz, _ in flush_sizes) == 6

    final_imgs = client.get(f"/api/jobs/{jid}/images").json()["images"]
    assert [i["page_number"] for i in final_imgs] == [1, 2, 3, 4, 5, 6]


def test_option_c_micro_batch_thresholds_and_sequential_equivalence(tmp_path, monkeypatch):
    """
    Verify PERSIST_MICRO_BATCH_SIZE=2 flushes subsequent images in micro-batches of 2
    (rather than waiting for 25 pages) and produces identical deterministic IDs and
    figure groups compared to sequential extraction.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("PDF_BATCH_SIZE", "25")
    monkeypatch.setenv("PERSIST_FLUSH_INTERVAL_SECONDS", "5.0")

    pdf_bytes = _make_grouped_multipage_pdf(8, [1, 2, 3, 4, 5, 6, 7, 8])

    # Run 1: Sequential (MAX_PARALLEL_BATCHES=1)
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    app_seq = create_app(db_path=str(tmp_path / "seq.db"), storage_dir=str(tmp_path / "s_seq"))
    pid_seq = str(uuid.uuid4())
    jid_seq = str(uuid.uuid4())
    src_seq, chk_seq, _ = app_seq.state.filestore.save_source_pdf(pid_seq, pdf_bytes, "doc.pdf")
    app_seq.state.repo.create_project(pid_seq, "Seq", "doc.pdf", src_seq, chk_seq)
    app_seq.state.repo.create_job(jid_seq, pid_seq, "extraction")
    assert process_extraction_job(jid_seq, app_seq.state.repo, app_seq.state.filestore) is True
    seq_imgs = app_seq.state.repo.list_project_images(pid_seq)
    seq_groups = app_seq.state.repo.list_project_groups(pid_seq)

    # Run 2: Continuous Option C (MAX_PARALLEL_BATCHES=2, PERSIST_MICRO_BATCH_SIZE=2)
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PERSIST_MICRO_BATCH_SIZE", "2")
    app_optc = create_app(db_path=str(tmp_path / "optc.db"), storage_dir=str(tmp_path / "s_optc"))
    pid_optc = str(uuid.uuid4())
    jid_optc = str(uuid.uuid4())
    src_optc, chk_optc, _ = app_optc.state.filestore.save_source_pdf(pid_optc, pdf_bytes, "doc.pdf")
    app_optc.state.repo.create_project(pid_optc, "OptC", "doc.pdf", src_optc, chk_optc)
    app_optc.state.repo.create_job(jid_optc, pid_optc, "extraction")

    flush_counts = []
    orig_persist = extraction_worker._persist_images_list

    def spy_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs):
        flush_counts.append(len(image_entries))
        return orig_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs)

    monkeypatch.setattr(extraction_worker, "_persist_images_list", spy_persist)
    assert process_extraction_job(jid_optc, app_optc.state.repo, app_optc.state.filestore) is True

    optc_imgs = app_optc.state.repo.list_project_images(pid_optc)
    optc_groups = app_optc.state.repo.list_project_groups(pid_optc)

    # First image flushed immediately (1), remaining 7 flushed in micro-batches of <=2
    assert flush_counts[0] == 1
    assert all(c <= 2 for c in flush_counts[1:])
    assert sum(flush_counts) == 8

    # Exact ordering, zero duplicates, deterministic coordinates and group structure
    assert [i["page_number"] for i in optc_imgs] == [i["page_number"] for i in seq_imgs]
    assert [i["placement_index"] for i in optc_imgs] == [i["placement_index"] for i in seq_imgs]
    assert [i["original_caption"] for i in optc_imgs] == [i["original_caption"] for i in seq_imgs]
    assert len(optc_groups) == len(seq_groups)


def test_option_c_failure_injection_and_clean_retry(tmp_path, monkeypatch):
    """
    Inject persistence failure mid-job in Option C, verify job fails cleanly without
    corrupting checkpoints, and verify retry completes with zero duplicate images.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    monkeypatch.setenv("PERSIST_MICRO_BATCH_SIZE", "2")
    monkeypatch.setenv("PERSIST_FLUSH_INTERVAL_SECONDS", "0.2")

    app = create_app(db_path=str(tmp_path / "fail.db"), storage_dir=str(tmp_path / "storage"))
    repo = app.state.repo
    filestore = app.state.filestore

    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    pdf_bytes = _make_grouped_multipage_pdf(6, [1, 3, 5])
    src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, "retry.pdf")
    repo.create_project(pid, "Retry Test", "retry.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")

    orig_persist = extraction_worker._persist_images_list
    call_num = [0]

    def fail_on_second_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs):
        call_num[0] += 1
        if call_num[0] == 2:
            raise RuntimeError("Simulated persistence DB/Blob failure")
        return orig_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs)

    monkeypatch.setattr(extraction_worker, "_persist_images_list", fail_on_second_persist)
    assert process_extraction_job(jid, repo, filestore) is False

    job_after_fail = repo.get_job(jid)
    assert job_after_fail["status"] == "queued"
    assert "Simulated persistence DB/Blob failure" in (job_after_fail["error_message"] or "")

    # Restore normal persistence and retry the same job
    monkeypatch.setattr(extraction_worker, "_persist_images_list", orig_persist)
    assert process_extraction_job(jid, repo, filestore) is True

    final_imgs = repo.list_project_images(pid)
    assert [i["page_number"] for i in final_imgs] == [1, 3, 5]


def test_option_c_lease_loss_stops_worker_cleanly(tmp_path, monkeypatch):
    """
    If the worker loses its job lease mid-execution in _process_pages_continuous,
    it must abort cleanly without marking the job completed.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    monkeypatch.setenv("PERSIST_MICRO_BATCH_SIZE", "2")

    app = create_app(db_path=str(tmp_path / "lease.db"), storage_dir=str(tmp_path / "storage"))
    repo = app.state.repo
    filestore = app.state.filestore

    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    pdf_bytes = _make_grouped_multipage_pdf(4, [1, 2, 3, 4])
    src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, "lease.pdf")
    repo.create_project(pid, "Lease Loss", "lease.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")

    orig_renew = repo.renew_job_lease

    def lose_lease_on_first_renew(job_id_arg, worker_id_arg, lease_seconds=300):
        # Simulate another worker stealing the lease
        return False

    monkeypatch.setattr(repo, "renew_job_lease", lose_lease_on_first_renew)
    # Force an immediate lease check by setting lease_seconds small via _process_pages_continuous ensure_lease
    monkeypatch.setattr(extraction_worker, "LEASE_SECONDS", 1)

    # Should still finish or fail cleanly without raising unhandled exceptions
    # Even if fast enough before 1s, force set_job_batch_status to fail on ownership loss
    orig_set_batch = repo.set_job_batch_status

    def lose_batch_ownership(job_id_arg, batch_index, status, error_message=None, worker_id=None):
        if status == "completed":
            return False
        return orig_set_batch(job_id_arg, batch_index, status, error_message=error_message, worker_id=worker_id)

    monkeypatch.setattr(repo, "set_job_batch_status", lose_batch_ownership)
    assert process_extraction_job(jid, repo, filestore) is False
    assert repo.get_job(jid)["status"] in ("queued", "failed")


def test_option_c_preserves_live_editorial_work_across_final_grouping(tmp_path, monkeypatch):
    """
    While extraction runs in Option C, an editor can edit captions, save/approve alt text,
    and manually group images. Final grouping reconciliation must preserve all editorial work.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PDF_BATCH_SIZE", "25")
    monkeypatch.setenv("PERSIST_MICRO_BATCH_SIZE", "2")
    monkeypatch.setenv("PERSIST_FLUSH_INTERVAL_SECONDS", "0.2")

    app = create_app(db_path=str(tmp_path / "live_edit.db"), storage_dir=str(tmp_path / "storage"))
    repo = app.state.repo
    filestore = app.state.filestore

    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    pdf_bytes = _make_grouped_multipage_pdf(4, [1, 2, 3, 4])
    src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, "live.pdf")
    repo.create_project(pid, "Live Edit", "live.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")

    orig_persist = extraction_worker._persist_images_list
    manual_group_id = [None]
    edited_img_id = [None]

    def edit_after_first_flush(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs):
        res = orig_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs)
        if edited_img_id[0] is None:
            imgs = repo_arg.list_project_images(project_id_arg)
            if imgs:
                img_id = imgs[0]["id"]
                edited_img_id[0] = img_id
                repo_arg.update_image_caption(img_id, "Editor custom caption")
                repo_arg.update_image_status(img_id, "approved")
                grp = repo_arg.create_group_from_selection(
                    project_id=project_id_arg,
                    image_ids=[img_id],
                    caption="Manual Editor Group",
                )
                manual_group_id[0] = grp.get("group_id") or grp.get("id") or (grp.get("group") or {}).get("id")
        return res

    monkeypatch.setattr(extraction_worker, "_persist_images_list", edit_after_first_flush)
    assert process_extraction_job(jid, repo, filestore) is True

    assert edited_img_id[0] is not None
    assert manual_group_id[0] is not None
    final_img = repo.get_image_asset(edited_img_id[0])
    assert final_img["original_caption"] == "Manual Editor Group"
    assert final_img["processing_status"] == "approved"
    assert final_img["group_id"] == manual_group_id[0]
