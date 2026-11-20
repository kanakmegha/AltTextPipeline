import io
import os
import uuid

import pymupdf as fitz
import pytest
from PIL import Image

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.extraction.models import BoundingBox, ExtractedImage, ExtractionResult
from src.storage.filestore import FileStore
from src.worker.extraction_worker import (
    _finalize_document_grouping,
    _persist_batch_images,
    process_extraction_job,
)


def _setup_sqlite_project(tmp_path, name="p2", pdf_bytes=None):
    db_path = str(tmp_path / f"{name}.db")
    storage_dir = str(tmp_path / f"storage_{name}")
    os.makedirs(storage_dir, exist_ok=True)
    conn = DatabaseConnection(db_path)
    apply_migrations(conn)
    repo = Repository(conn)
    filestore = FileStore(base_dir=storage_dir)
    pid = str(uuid.uuid4())
    if pdf_bytes is not None:
        src_key, chk, _ = filestore.save_source_pdf(pid, pdf_bytes, "test.pdf")
    else:
        src_key, chk = "source/test.pdf", "chk"
    repo.create_project(
        project_id=pid,
        name=f"Project {name}",
        source_filename="test.pdf",
        source_storage_key=src_key,
        source_checksum=chk,
    )
    return repo, filestore, pid


def _make_extracted_image(page_number: int, xref: int, placement_index: int, caption: str = "") -> ExtractedImage:
    return ExtractedImage(
        image_id=str(uuid.uuid4()),
        page_number=page_number,
        xref=xref,
        placement_index=placement_index,
        bbox=BoundingBox(10.0, 20.0, 100.0, 120.0),
        image_bytes=b"fake-png-bytes",
        image_format="png",
        suggested_filename=f"p{page_number}_x{xref}_{placement_index}.png",
        caption=caption,
        caption_position="below" if caption else None,
        caption_found=bool(caption),
        needs_caption_review=False,
    )


def _make_result(images):
    by_page = {}
    for img in images:
        by_page.setdefault(img.page_number, []).append(img)
    return ExtractionResult(
        pdf_path="test.pdf",
        total_pages=5,
        pages_processed=sorted(by_page),
        images=images,
        errors=[],
        stats={},
        grouping_context={"images_by_page": by_page, "text_blocks_by_page": {}, "page_dimensions": {}},
    )


def test_persist_batch_skips_existing_and_intra_batch_duplicates(tmp_path):
    repo, filestore, pid = _setup_sqlite_project(tmp_path, "dedup")
    img1 = _make_extracted_image(1, 10, 1, "Fig 1")
    img2 = _make_extracted_image(1, 11, 2, "Fig 2")
    img2_dup = _make_extracted_image(1, 11, 2, "Fig 2 dup")

    upload_calls = []
    orig_save = filestore.save_image

    def counting_save(**kwargs):
        upload_calls.append(kwargs["image_id"])
        return orig_save(**kwargs)

    filestore.save_image = counting_save

    metrics = {
        "dur_db_lookup": 0.0, "db_lookup_count": 0,
        "dur_blob_upload": 0.0, "blob_upload_count": 0,
        "dur_db_insert": 0.0, "db_insert_count": 0,
        "dur_blob_verify": 0.0, "blob_verify_count": 0,
    }
    _persist_batch_images(repo, filestore, pid, _make_result([img1, img2, img2_dup]), metrics=metrics)
    assert len(upload_calls) == 2
    assert metrics["blob_upload_count"] == 2
    assert metrics["db_lookup_count"] == 1
    assert metrics["db_insert_count"] == 1
    assert metrics["blob_verify_count"] == 0
    assert len(repo.list_project_images(pid)) == 2

    # Second call with same source keys skips all uploads and inserts
    img1_again = _make_extracted_image(1, 10, 1, "Fig 1")
    img2_again = _make_extracted_image(1, 11, 2, "Fig 2")
    _persist_batch_images(repo, filestore, pid, _make_result([img1_again, img2_again]), metrics=metrics)
    assert len(upload_calls) == 2
    assert len(repo.list_project_images(pid)) == 2


def test_partial_blob_upload_failure_persists_successful_assets_and_recovers_on_retry(tmp_path):
    repo, filestore, pid = _setup_sqlite_project(tmp_path, "partial_blob")
    img1 = _make_extracted_image(1, 10, 1, "Fig 1")
    img2 = _make_extracted_image(1, 11, 2, "Fig 2")
    img3 = _make_extracted_image(1, 12, 3, "Fig 3")

    orig_save = filestore.save_image
    uploaded_ids = []

    def flaky_save(**kwargs):
        if kwargs["image_id"] == img2.image_id:
            raise RuntimeError("transient blob error on img2")
        uploaded_ids.append(kwargs["image_id"])
        return orig_save(**kwargs)

    filestore.save_image = flaky_save
    with pytest.raises(RuntimeError, match="transient blob error"):
        _persist_batch_images(repo, filestore, pid, _make_result([img1, img2, img3]))

    # img1 and img3 were persisted despite img2 failing
    persisted = repo.list_project_images(pid)
    assert len(persisted) == 2
    assert {p["xref"] for p in persisted} == {10, 12}

    # Retry: restore filestore, only img2 should be uploaded
    uploaded_ids.clear()
    filestore.save_image = lambda **kw: (uploaded_ids.append(kw["image_id"]) or orig_save(**kw))
    img1_r = _make_extracted_image(1, 10, 1, "Fig 1")
    img2_r = _make_extracted_image(1, 11, 2, "Fig 2")
    img3_r = _make_extracted_image(1, 12, 3, "Fig 3")
    _persist_batch_images(repo, filestore, pid, _make_result([img1_r, img2_r, img3_r]))

    assert uploaded_ids == [img2_r.image_id]
    assert len(repo.list_project_images(pid)) == 3


def test_db_failure_after_blob_upload_rolls_back_and_retries_cleanly(tmp_path, monkeypatch):
    repo, filestore, pid = _setup_sqlite_project(tmp_path, "db_fail")
    img1 = _make_extracted_image(1, 10, 1, "Fig 1")
    img2 = _make_extracted_image(1, 11, 2, "Fig 2")

    orig_batch_insert = repo.create_image_assets_batch

    def failing_insert(_items):
        raise RuntimeError("db commit failed")

    monkeypatch.setattr(repo, "create_image_assets_batch", failing_insert)
    with pytest.raises(RuntimeError, match="db commit failed"):
        _persist_batch_images(repo, filestore, pid, _make_result([img1, img2]))

    assert len(repo.list_project_images(pid)) == 0

    monkeypatch.setattr(repo, "create_image_assets_batch", orig_batch_insert)
    _persist_batch_images(repo, filestore, pid, _make_result([img1, img2]))
    assert len(repo.list_project_images(pid)) == 2


def test_grouping_batch_idempotency_and_e2e_equivalence(tmp_path, monkeypatch):
    # Build 6-page PDF with single images + 2-panel figure group
    doc = fitz.open()
    for page_num in range(1, 7):
        page = doc.new_page(width=500, height=600)
        page.insert_text((40, 30), f"Page {page_num}")
        if page_num in (1, 5):
            img = Image.new("RGB", (80, 80), color=(page_num * 40, 120, 180))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(60, 80, 220, 240), stream=buf.getvalue())
            page.insert_text((60, 255), f"Figure {page_num}: Single diagram.")
        if page_num == 3:
            for idx, x0 in enumerate((60, 180), start=1):
                img = Image.new("RGB", (70, 70), color=(50 * idx, 160, 90))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                page.insert_image(fitz.Rect(x0, 90, x0 + 100, 240), stream=buf.getvalue())
            page.insert_text((100, 255), "Figure 3: Two-panel comparison.")
    pdf_bytes = doc.tobytes()
    doc.close()

    results = {}
    for mode, parallel in (("seq", "1"), ("par", "2")):
        monkeypatch.setenv("MAX_PARALLEL_BATCHES", parallel)
        monkeypatch.setenv("PDF_BATCH_SIZE", "2")
        repo, filestore, pid = _setup_sqlite_project(tmp_path, mode, pdf_bytes=pdf_bytes)
        jid = str(uuid.uuid4())
        repo.create_job(job_id=jid, project_id=pid, job_type="extraction")
        assert process_extraction_job(jid, repo, filestore) is True
        results[mode] = (repo.list_project_images(pid), repo.list_project_groups(pid))

    assert len(results["seq"][0]) == len(results["par"][0]) == 4
    assert len(results["seq"][1]) == len(results["par"][1]) == 1
    assert results["seq"][1][0]["caption_text"] == results["par"][1][0]["caption_text"]

    # Also verify create_figure_groups_batch idempotency directly
    gid = str(uuid.uuid4())
    repo, _, pid = _setup_sqlite_project(tmp_path, "grp_idem")
    grp_payload = [{
        "group_id": gid,
        "project_id": pid,
        "caption_text": "Figure 1: Panel A & B",
        "caption_page_number": 1,
        "caption_position": "below",
        "figure_label": "Figure 1",
        "is_cross_page": False,
        "review_status": "candidate",
        "review_reasons": [],
    }]
    assert repo.create_figure_groups_batch(grp_payload) == 1
    assert repo.create_figure_groups_batch(grp_payload) == 0
    assert len(repo.list_project_groups(pid)) == 1
