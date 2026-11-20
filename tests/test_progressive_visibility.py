import io
import os
import uuid

import pymupdf as fitz
from PIL import Image
from starlette.testclient import TestClient

from src.web.app import create_app
from src.worker import extraction_worker
from src.worker.extraction_worker import process_extraction_job


def _make_multipage_pdf(num_pages: int, image_pages: list[int]) -> bytes:
    doc = fitz.open()
    for p in range(1, num_pages + 1):
        page = doc.new_page(width=450, height=550)
        page.insert_text((40, 30), f"Page {p}")
        if p in image_pages:
            img = Image.new("RGB", (60, 60), color=(p * 40 % 255, 120, 180))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(60, 80, 180, 200), stream=buf.getvalue())
            page.insert_text((60, 220), f"Figure {p}: Cover or figure on page {p}.")
    data = doc.tobytes()
    doc.close()
    return data


def test_images_become_visible_via_api_while_job_is_still_running(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret-key-at-least-32-bytes-long")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")

    db_path = str(tmp_path / "prog.db")
    storage_dir = str(tmp_path / "storage")
    os.makedirs(storage_dir, exist_ok=True)
    app = create_app(db_path=db_path, storage_dir=storage_dir)
    client = TestClient(app)
    repo = app.state.repo
    filestore = app.state.filestore

    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    pdf_bytes = _make_multipage_pdf(6, [1, 5])
    src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, "book.pdf")
    repo.create_project(pid, "Progressive Vis", "book.pdf", src_key, chk)
    repo.create_job(jid, pid, "extraction")

    observed_mid_job = {}
    orig_process_batch = extraction_worker._process_batch

    def inspect_after_batch1(pdf_path, pages, engine):
        # When batch 2 (pages 3-4) starts, batch 1 (pages 1-2, containing cover image on page 1)
        # must already be persisted and visible via API while the job is still running!
        if 3 in pages and not observed_mid_job:
            prog_res = client.get(f"/api/jobs/{jid}/progress")
            imgs_res = client.get(f"/api/jobs/{jid}/images")
            proj_imgs_res = client.get(f"/api/projects/{pid}/images")
            observed_mid_job["progress"] = prog_res.json()
            observed_mid_job["job_images"] = imgs_res.json()["images"]
            observed_mid_job["project_images"] = proj_imgs_res.json()["images"]
        return orig_process_batch(pdf_path, pages, engine)

    monkeypatch.setattr(extraction_worker, "_process_batch", inspect_after_batch1)
    assert process_extraction_job(jid, repo, filestore) is True

    # Mid-job assertions: job was still running, yet page 1 image was already visible
    assert observed_mid_job["progress"]["job_status"] == "running"
    assert observed_mid_job["progress"]["project_status"] == "extracting"
    assert observed_mid_job["progress"]["completed_batches"] == 1
    assert observed_mid_job["progress"]["total_images"] == 1
    assert len(observed_mid_job["job_images"]) == 1
    assert observed_mid_job["job_images"][0]["page_number"] == 1
    assert len(observed_mid_job["project_images"]) == 1

    # Post-job assertions: all 2 images visible and job completed
    final_imgs = client.get(f"/api/jobs/{jid}/images").json()["images"]
    assert len(final_imgs) == 2
    assert [i["page_number"] for i in final_imgs] == [1, 5]
