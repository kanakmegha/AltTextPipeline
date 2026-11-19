import io
import os
import shutil
import uuid
from unittest.mock import MagicMock

import psycopg2
import psycopg2.extensions
import pymupdf as fitz
import pytest
from PIL import Image

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.postgres_connection import PostgresConnection
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.worker import extraction_worker
from src.worker.extraction_worker import _extract_pdf_batch, process_extraction_job


def _make_grouped_test_pdf() -> bytes:
    """Create a 6-page PDF with single-image captions and a 2-image panel group."""
    doc = fitz.open()
    for page_num in range(1, 7):
        page = doc.new_page(width=500, height=600)
        page.insert_text((40, 30), f"Book Chapter Page {page_num}")
        if page_num in (1, 3, 5):
            img = Image.new("RGB", (80, 80), color=(page_num * 30, 100, 180))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            page.insert_image(fitz.Rect(60, 80, 220, 240), stream=buf.getvalue())
            page.insert_text((60, 255), f"Figure {page_num}: Diagram on page {page_num}.")
        if page_num == 4:
            for idx, x0 in enumerate((50, 260), start=1):
                img = Image.new("RGB", (70, 70), color=(40 * idx, 160, 90))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                page.insert_image(fitz.Rect(x0, 90, x0 + 150, 240), stream=buf.getvalue())
            page.insert_text((50, 260), "Figure 4: Two-panel comparison chart.")
    data = doc.tobytes()
    doc.close()
    return data


# ------------------------------------------------------------------
# Task 1: PostgreSQL Connection Pooling Tests
# ------------------------------------------------------------------


def test_postgres_pool_reuses_connections_and_closes_cleanly(monkeypatch):
    fake_conn = MagicMock()
    fake_conn.closed = 0
    fake_conn.status = psycopg2.extensions.STATUS_READY

    fake_pool = MagicMock()
    fake_pool.getconn.return_value = fake_conn
    pool_creations = []

    def fake_pool_ctor(minconn, maxconn, dsn, cursor_factory=None):
        pool_creations.append((minconn, maxconn, dsn, cursor_factory))
        return fake_pool

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", fake_pool_ctor)
    pg = PostgresConnection(database_url="postgresql://user:pass@localhost/db", minconn=1, maxconn=4)

    with pg.session() as c1:
        assert c1 is fake_conn
    with pg.session() as c2:
        assert c2 is fake_conn

    assert len(pool_creations) == 1
    assert pool_creations[0][:2] == (1, 4)
    assert fake_conn.commit.call_count == 2
    assert fake_pool.putconn.call_count == 2
    assert all(call.kwargs.get("close") is False for call in fake_pool.putconn.call_args_list)

    pg.close_pool()
    fake_pool.closeall.assert_called_once()


def test_postgres_pool_rolls_back_on_exception_and_reuses_healthy_conn(monkeypatch):
    fake_conn = MagicMock()
    fake_conn.closed = 0
    fake_conn.status = psycopg2.extensions.STATUS_READY

    fake_pool = MagicMock()
    fake_pool.getconn.return_value = fake_conn
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", lambda *a, **kw: fake_pool)

    pg = PostgresConnection(database_url="postgresql://user:pass@localhost/db")
    with pytest.raises(RuntimeError, match="query boom"):
        with pg.session():
            raise RuntimeError("query boom")

    fake_conn.rollback.assert_called_once()
    fake_conn.commit.assert_not_called()
    fake_pool.putconn.assert_called_once_with(fake_conn, close=False)


def test_postgres_pool_disposes_broken_connections(monkeypatch):
    broken_conn = MagicMock()
    broken_conn.closed = 0
    broken_conn.rollback.side_effect = psycopg2.OperationalError("connection lost")

    fake_pool = MagicMock()
    fake_pool.getconn.return_value = broken_conn
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", lambda *a, **kw: fake_pool)

    pg = PostgresConnection(database_url="postgresql://user:pass@localhost/db")
    with pytest.raises(ValueError, match="bad sql"):
        with pg.session():
            raise ValueError("bad sql")

    fake_pool.putconn.assert_called_once_with(broken_conn, close=True)

    # Already-closed connection returned by getconn() is discarded immediately
    dead_conn = MagicMock()
    dead_conn.closed = 1
    healthy_conn = MagicMock()
    healthy_conn.closed = 0
    healthy_conn.status = psycopg2.extensions.STATUS_READY
    fake_pool.reset_mock()
    fake_pool.getconn.side_effect = [dead_conn, healthy_conn]

    with pg.session() as conn:
        assert conn is healthy_conn

    assert fake_pool.putconn.call_args_list[0].args == (dead_conn,)
    assert fake_pool.putconn.call_args_list[0].kwargs == {"close": True}
    assert fake_pool.putconn.call_args_list[1].args == (healthy_conn,)
    assert fake_pool.putconn.call_args_list[1].kwargs == {"close": False}


def test_postgres_pool_recreates_pool_after_fork_without_closing_parent(monkeypatch):
    parent_pool = MagicMock()
    child_pool = MagicMock()
    pools = [parent_pool, child_pool]

    for p in pools:
        c = MagicMock()
        c.closed = 0
        c.status = psycopg2.extensions.STATUS_READY
        p.getconn.return_value = c

    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", lambda *a, **kw: pools.pop(0))

    current_pid = [1001]
    monkeypatch.setattr(os, "getpid", lambda: current_pid[0])

    pg = PostgresConnection(database_url="postgresql://user:pass@localhost/db")
    with pg.session():
        pass
    assert pg._pool is parent_pool

    # Simulate child process after fork()
    current_pid[0] = 2002
    with pg.session():
        pass
    assert pg._pool is child_pool
    parent_pool.closeall.assert_not_called()


# ------------------------------------------------------------------
# Task 2 & 3: Direct Read-Only Batch Extraction & Regression Tests
# ------------------------------------------------------------------


def test_extract_pdf_batch_reads_directly_without_splitting_temp_pdf(tmp_path, monkeypatch):
    pdf_path = tmp_path / "book.pdf"
    pdf_path.write_bytes(_make_grouped_test_pdf())

    def forbid_split(*_args, **_kwargs):
        raise AssertionError("_split_pdf_batch_to_temp_file should not be called")

    monkeypatch.setattr(extraction_worker, "_split_pdf_batch_to_temp_file", forbid_split)

    result, temp_dir = _extract_pdf_batch(str(pdf_path), [3, 4])
    try:
        assert not os.path.exists(os.path.join(temp_dir, "batch.pdf"))
        pages = sorted(img.page_number for img in result.images)
        assert pages == [3, 4, 4]
        assert len(result.images) == 3
        for img in result.images:
            assert img.image_bytes == b""
            assert os.path.isfile(os.path.join(temp_dir, img.suggested_filename))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _setup_job(tmp_path, subdir):
    db_path = str(tmp_path / f"{subdir}.db")
    storage_dir = str(tmp_path / f"storage_{subdir}")
    os.makedirs(storage_dir, exist_ok=True)
    conn = DatabaseConnection(db_path)
    apply_migrations(conn)
    repo = Repository(conn)
    filestore = FileStore(base_dir=storage_dir)

    pid = str(uuid.uuid4())
    jid = str(uuid.uuid4())
    pdf_bytes = _make_grouped_test_pdf()
    src_key, checksum, _ = filestore.save_source_pdf(pid, pdf_bytes, "grouped.pdf")
    repo.create_project(
        project_id=pid,
        name=f"Proj {subdir}",
        source_filename="grouped.pdf",
        source_storage_key=src_key,
        source_checksum=checksum,
    )
    repo.create_job(job_id=jid, project_id=pid, job_type="extraction")
    return repo, filestore, pid, jid


def test_sequential_and_parallel_extraction_produce_identical_assets_and_groups(tmp_path, monkeypatch):
    # Sequential run
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "1")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    seq_repo, seq_fs, seq_pid, seq_jid = _setup_job(tmp_path, "seq")
    assert process_extraction_job(seq_jid, seq_repo, seq_fs) is True

    # Parallel run
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    par_repo, par_fs, par_pid, par_jid = _setup_job(tmp_path, "par")
    assert process_extraction_job(par_jid, par_repo, par_fs) is True

    seq_imgs = seq_repo.list_project_images(seq_pid)
    par_imgs = par_repo.list_project_images(par_pid)
    assert len(seq_imgs) == len(par_imgs) == 5

    def _signature(imgs):
        return [
            (
                img["page_number"],
                img["xref"],
                img["placement_index"],
                round(img["bbox"]["x0"], 2),
                round(img["bbox"]["y0"], 2),
                round(img["bbox"]["x1"], 2),
                round(img["bbox"]["y1"], 2),
                img["original_caption"],
                img["association_type"],
            )
            for img in sorted(imgs, key=lambda r: (r["page_number"], r["placement_index"]))
        ]

    assert _signature(seq_imgs) == _signature(par_imgs)
    # Verify no duplicate (page, xref, placement_index)
    keys = [(i["page_number"], i["xref"], i["placement_index"]) for i in par_imgs]
    assert len(keys) == len(set(keys))

    seq_groups = seq_repo.list_project_groups(seq_pid)
    par_groups = par_repo.list_project_groups(par_pid)
    assert len(seq_groups) == len(par_groups) == 1
    assert seq_groups[0]["caption_text"] == par_groups[0]["caption_text"]
    assert seq_groups[0]["caption_page_number"] == par_groups[0]["caption_page_number"] == 4


def test_parallel_worker_failure_and_retry_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")
    monkeypatch.setenv("PDF_BATCH_SIZE", "2")
    monkeypatch.setenv("MAX_RETRIES", "2")
    repo, filestore, pid, jid = _setup_job(tmp_path, "retry")

    orig_extract = _extract_pdf_batch
    failed_once = {"done": False}

    def flaky_extract(pdf_path, page_numbers):
        if 3 in page_numbers and not failed_once["done"]:
            failed_once["done"] = True
            raise RuntimeError("transient batch failure")
        return orig_extract(pdf_path, page_numbers)

    monkeypatch.setattr(extraction_worker, "_extract_pdf_batch", flaky_extract)
    assert process_extraction_job(jid, repo, filestore) is False
    job_after_fail = repo.get_job(jid)
    assert job_after_fail["status"] == "queued"

    # Retry succeeds and avoids duplicate assets
    monkeypatch.setattr(extraction_worker, "_extract_pdf_batch", orig_extract)
    assert process_extraction_job(jid, repo, filestore) is True
    assert repo.get_job(jid)["status"] == "completed"
    assert len(repo.list_project_images(pid)) == 5
