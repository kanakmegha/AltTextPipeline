import io
import os
import threading
import uuid

import pymupdf as fitz
import pytest
from PIL import Image
from starlette.testclient import TestClient

from src.storage.filestore import StorageError
from src.web.app import create_app
from src.worker import extraction_worker
from src.worker.extraction_worker import process_extraction_job


def _make_pdf(num_pages: int = 4) -> bytes:
    doc = fitz.open()
    for p in range(1, num_pages + 1):
        page = doc.new_page(width=450, height=550)
        page.insert_text((40, 30), f"Page {p}")
        img = Image.new("RGB", (60, 60), color=((p * 41) % 255, 110, 180))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        page.insert_image(fitz.Rect(60, 80, 180, 200), stream=buf.getvalue())
        page.insert_text((60, 220), f"Figure {p}: Image on page {p}.")
    data = doc.tobytes()
    doc.close()
    return data


def _setup_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    monkeypatch.setenv("PERSIST_MICRO_BATCH_SIZE", "2")
    app = create_app(db_path=str(tmp_path / "cancel.db"), storage_dir=str(tmp_path / "storage"))
    return app, TestClient(app), app.state.repo, app.state.filestore


def _seed_project_and_job(repo, filestore, name="Proj", pages=4):
    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    src_key, chk, _ = filestore.save_source_pdf(pid, _make_pdf(pages), "doc.pdf")
    repo.create_project(pid, name, "doc.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")
    return pid, jid


def test_1_delete_project_with_queued_job_prevents_worker_claim(tmp_path, monkeypatch):
    app, client, repo, filestore = _setup_app(tmp_path, monkeypatch)
    pid, jid = _seed_project_and_job(repo, filestore, name="Queued Project")

    prev = client.get(f"/api/projects/{pid}/delete/preview").json()["preview"]
    assert prev["can_delete"] is True
    assert len(prev["active_extraction_jobs"]) == 1

    res = client.post(f"/api/projects/{pid}/delete", json={"confirm_project_name": "Queued Project"})
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"

    assert repo.get_project(pid) is None
    assert repo.get_job(jid) is None
    assert process_extraction_job(jid, repo, filestore) is False
    assert repo.list_project_images(pid) == []


def test_2_delete_during_pdf_download(tmp_path, monkeypatch):
    app, client, repo, filestore = _setup_app(tmp_path, monkeypatch)
    pid, jid = _seed_project_and_job(repo, filestore, name="Download Cancel")

    orig_dl = filestore.download_to_file

    def cancel_during_download(project_id_arg, key, dest_path):
        res = orig_dl(project_id_arg, key, dest_path)
        if project_id_arg == pid:
            op = repo.prepare_project_deletion(pid, storage=filestore)
            assert op["status"] == "pending"
        return res

    monkeypatch.setattr(filestore, "download_to_file", cancel_during_download)
    assert process_extraction_job(jid, repo, filestore) is False
    assert repo.get_job(jid)["status"] == "cancelled"
    assert repo.list_project_images(pid) == []


def test_3_and_10_delete_during_continuous_extraction_and_other_project_continues(tmp_path, monkeypatch):
    app, client, repo, filestore = _setup_app(tmp_path, monkeypatch)
    pid1, jid1 = _seed_project_and_job(repo, filestore, name="Cancel Mid Extract", pages=6)
    pid2, jid2 = _seed_project_and_job(repo, filestore, name="Healthy Project", pages=4)

    orig_persist = extraction_worker._persist_images_list
    deleted_triggered = [False]

    def cancel_after_first_flush(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs):
        res = orig_persist(repo_arg, filestore_arg, project_id_arg, image_entries, **kwargs)
        if project_id_arg == pid1 and not deleted_triggered[0]:
            deleted_triggered[0] = True
            del_res = client.post(f"/api/projects/{pid1}/delete", json={"confirm_project_name": "Cancel Mid Extract"})
            assert del_res.status_code == 200
        return res

    monkeypatch.setattr(extraction_worker, "_persist_images_list", cancel_after_first_flush)
    assert process_extraction_job(jid1, repo, filestore) is False
    assert repo.get_project(pid1) is None
    assert repo.list_project_images(pid1) == []
    assert filestore.list_project_keys(pid1) == []

    # Other project continues and completes normally without shared worker/DB pool disruption
    assert process_extraction_job(jid2, repo, filestore) is True
    assert repo.get_project(pid2)["status"] == "ready_for_review"
    assert len(repo.list_project_images(pid2)) == 4


def test_4_delete_during_blob_uploads_sweeps_in_flight_blobs(tmp_path, monkeypatch):
    app, client, repo, filestore = _setup_app(tmp_path, monkeypatch)
    pid, jid = _seed_project_and_job(repo, filestore, name="Blob Race", pages=4)

    orig_save = filestore.save_image
    race_done = [False]

    def delete_right_after_blob_save(**kwargs):
        res = orig_save(**kwargs)
        if kwargs.get("project_id") == pid and not race_done[0]:
            race_done[0] = True
            # Mark deleting and cancel job while Blob upload just finished before DB insert
            repo.prepare_project_deletion(pid, storage=filestore)
        return res

    monkeypatch.setattr(filestore, "save_image", delete_right_after_blob_save)
    assert process_extraction_job(jid, repo, filestore) is False
    # DB insert must be blocked and job must stay cancelled
    assert repo.list_project_images(pid) == []
    assert repo.get_job(jid)["status"] == "cancelled"

    # Executing/resuming deletion operation sweeps any in-flight Blobs cleanly
    op = repo.get_latest_cleanup_operation(pid)
    assert op is not None
    done_op = repo.retry_project_deletion(op["id"], storage=filestore)
    assert done_op["status"] == "completed"
    assert repo.get_project(pid) is None
    assert filestore.list_project_keys(pid) == []


def test_5_6_9_db_persistence_and_final_grouping_blocked_when_deleting(tmp_path, monkeypatch):
    app, client, repo, filestore = _setup_app(tmp_path, monkeypatch)
    pid, jid = _seed_project_and_job(repo, filestore, name="Guard DB", pages=2)

    repo.prepare_project_deletion(pid, storage=filestore)

    # Stale worker attempts to insert images, groups, or update project status must be no-ops
    inserted_imgs = repo.create_image_assets_batch([{
        "image_id": str(uuid.uuid4()),
        "project_id": pid,
        "page_number": 1,
        "xref": 1,
        "placement_index": 1,
        "bbox": {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        "suggested_filename": "img.png",
        "file_storage_key": f"{pid}/images/x.png",
        "file_checksum": "abc",
        "image_format": "png",
    }])
    assert inserted_imgs == 0
    assert repo.list_project_images(pid) == []

    inserted_groups = repo.create_figure_groups_batch([{
        "group_id": str(uuid.uuid4()),
        "project_id": pid,
        "caption_text": "Fig 1",
        "caption_page_number": 1,
    }])
    assert inserted_groups == 0
    assert repo.list_project_groups(pid) == []

    repo.update_project_status(pid, status="ready_for_review")
    assert repo.get_project(pid)["cleanup_status"] == "deleting"
    assert repo.get_project(pid)["status"] != "ready_for_review"


def test_7_and_8_worker_crash_and_retry_incomplete_cleanup(tmp_path, monkeypatch):
    app, client, repo, filestore = _setup_app(tmp_path, monkeypatch)
    pid, jid = _seed_project_and_job(repo, filestore, name="Retry Cleanup", pages=2)

    orig_del = filestore.delete_objects
    fail_once = [True]

    def flaky_delete(project_id_arg, keys):
        if fail_once[0]:
            fail_once[0] = False
            res = orig_del(project_id_arg, [])
            res.failed = {k: "Transient Blob delete failure" for k in keys}
            return res
        return orig_del(project_id_arg, keys)

    monkeypatch.setattr(filestore, "delete_objects", flaky_delete)
    res1 = client.post(f"/api/projects/{pid}/delete", json={"confirm_project_name": "Retry Cleanup"})
    assert res1.status_code in (200, 409)
    latest_op = repo.get_latest_cleanup_operation(pid)
    assert latest_op is not None and latest_op["status"] == "failed"
    op_id = latest_op["id"]

    # Retry succeeds and completes cleanup
    res2 = client.post(f"/api/deletions/{op_id}/retry")
    assert res2.status_code == 200
    assert res2.json()["status"] == "deleted"
    assert repo.get_project(pid) is None
    assert filestore.list_project_keys(pid) == []
