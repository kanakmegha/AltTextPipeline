import os
import sqlite3
import threading
import uuid

import pytest

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.extraction.models import BoundingBox, ExtractedImage, ExtractionResult
from src.storage.filestore import FileStore
from src.worker.extraction_worker import _persist_batch_images


def _make_item(project_id: str, page_number: int = 1, xref: int = 10, placement_index: int = 1):
    return {
        "image_id": str(uuid.uuid4()),
        "project_id": project_id,
        "page_number": page_number,
        "xref": xref,
        "placement_index": placement_index,
        "bbox": {"x0": 0.0, "y0": 0.0, "x1": 50.0, "y1": 50.0},
        "original_caption": "Fig 1",
        "caption_position": "below",
        "needs_caption_review": False,
        "review_reasons": [],
        "suggested_filename": "fig1.png",
        "file_storage_key": f"images/{uuid.uuid4()}.png",
        "file_checksum": "chk",
        "image_format": "png",
    }


def test_sqlite_migration_15_enforces_unique_source_index(tmp_path):
    db_path = str(tmp_path / "mig15.db")
    conn = DatabaseConnection(db_path)
    version = apply_migrations(conn)
    assert version >= 15

    repo = Repository(conn)
    pid = str(uuid.uuid4())
    repo.create_project(pid, "Mig15", "m.pdf", "source/m.pdf", "chk")

    item1 = _make_item(pid, 1, 10, 1)
    item2 = _make_item(pid, 1, 10, 1)  # same source key, different UUID
    assert repo.create_image_assets_batch([item1]) == 1
    assert repo.create_image_assets_batch([item2]) == 0

    # Raw INSERT bypassing ON CONFLICT must fail with IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_image_asset(
            image_id=item2["image_id"],
            project_id=pid,
            page_number=1,
            xref=10,
            placement_index=1,
            bbox=item2["bbox"],
            original_caption="dup",
            caption_position="below",
            needs_caption_review=False,
            review_reasons=[],
            suggested_filename="dup.png",
            file_storage_key="images/dup.png",
            file_checksum="chk2",
            image_format="png",
        )


def test_sqlite_migration_15_refuses_silently_deleting_existing_duplicates(tmp_path):
    db_path = str(tmp_path / "dups.db")
    conn = DatabaseConnection(db_path)
    apply_migrations(conn)

    # Simulate a pre-Migration-15 database containing a duplicate source key
    with conn.session() as c:
        c.execute("DROP INDEX IF EXISTS uq_image_assets_source;")
        c.execute("DELETE FROM schema_migrations WHERE version >= 15;")
        pid = str(uuid.uuid4())
        c.execute(
            "INSERT INTO projects (id, name, source_filename, source_storage_key, source_checksum, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');",
            (pid, "DupProj", "d.pdf", "source/d.pdf", "chk"),
        )
        for img_id in (str(uuid.uuid4()), str(uuid.uuid4())):
            c.execute(
                """
                INSERT INTO image_assets (
                    id, project_id, page_number, xref, placement_index,
                    bbox_x0, bbox_y0, bbox_x1, bbox_y1, suggested_filename,
                    file_storage_key, file_checksum, image_format, created_at, updated_at
                ) VALUES (?, ?, 1, 77, 1, 0, 0, 10, 10, 'f.png', 'images/f.png', 'c', 'png',
                          '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
                """,
                (img_id, pid),
            )

    with pytest.raises(RuntimeError, match="duplicate source key"):
        apply_migrations(conn)

    # Verify both historical records were preserved untouched
    with conn.session() as c:
        count = c.execute("SELECT COUNT(*) FROM image_assets WHERE project_id = ?;", (pid,)).fetchone()[0]
    assert count == 2


def test_concurrent_workers_cannot_create_duplicate_image_assets_sqlite(tmp_path):
    db_path = str(tmp_path / "conc.db")
    conn = DatabaseConnection(db_path)
    apply_migrations(conn)
    repo = Repository(conn)
    pid = str(uuid.uuid4())
    repo.create_project(pid, "ConcProj", "c.pdf", "source/c.pdf", "chk")

    barrier = threading.Barrier(2)
    counts = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        inserted = repo.create_image_assets_batch([_make_item(pid, 2, 42, 1)])
        with lock:
            counts.append(inserted)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sum(counts) == 1
    assert len(repo.list_project_images(pid)) == 1


def test_deterministic_blob_key_prevents_orphans_on_db_failure_and_retry(tmp_path, monkeypatch):
    db_path = str(tmp_path / "det.db")
    storage_dir = str(tmp_path / "storage")
    os.makedirs(storage_dir, exist_ok=True)
    conn = DatabaseConnection(db_path)
    apply_migrations(conn)
    repo = Repository(conn)
    filestore = FileStore(base_dir=storage_dir)
    pid = str(uuid.uuid4())
    repo.create_project(pid, "DetProj", "d.pdf", "source/d.pdf", "chk")

    def _build_res():
        img = ExtractedImage(
            image_id=str(uuid.uuid4()),  # fresh random UUID each attempt
            page_number=3,
            xref=99,
            placement_index=1,
            bbox=BoundingBox(0, 0, 10, 10),
            image_bytes=b"png-payload",
            image_format="png",
            suggested_filename="fig3.png",
            caption="Fig 3",
            caption_position="below",
            caption_found=True,
            needs_caption_review=False,
        )
        return ExtractionResult(
            pdf_path="d.pdf",
            total_pages=5,
            pages_processed=[3],
            images=[img],
            errors=[],
            stats={},
            grouping_context={"images_by_page": {3: [img]}, "text_blocks_by_page": {}, "page_dimensions": {}},
        )

    orig_insert = repo.create_image_assets_batch
    monkeypatch.setattr(repo, "create_image_assets_batch", lambda _items: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError, match="db down"):
        _persist_batch_images(repo, filestore, pid, _build_res())

    images_dir = os.path.join(storage_dir, pid, "images")
    files_after_fail = os.listdir(images_dir)
    assert len(files_after_fail) == 1

    # Retry with a new ExtractionResult -> overwrites exact same deterministic key, no orphan!
    monkeypatch.setattr(repo, "create_image_assets_batch", orig_insert)
    _persist_batch_images(repo, filestore, pid, _build_res())
    files_after_retry = os.listdir(images_dir)
    assert files_after_retry == files_after_fail
    assert len(repo.list_project_images(pid)) == 1
