
"""
Vercel Blob Storage Tests
-------------------------
Tests the official Vercel Blob SDK adapter using mocked SDK operations.
No real Vercel Blob account or network access is required.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vercel.blob.errors import BlobNotFoundError

from src.storage.blob_storage import BlobStorage, MAX_OBJECT_BYTES
from src.storage.base_storage import (
    CleanupBlockedError,
    PathTraversalError,
    StorageError,
)


TEST_TOKEN = "vercel_blob_rw_teststore_secret123"


@dataclass
class MockListBlob:
    url: str
    download_url: str
    pathname: str
    size: int
    uploaded_at: datetime
    etag: str


@pytest.fixture
def mock_blob(monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("VERCEL_BLOB_READ_WRITE_TOKEN", raising=False)

    objects = {}
    calls = {
        "put": [],
        "get": [],
        "head": [],
        "query": [],
        "delete": [],
    }

    def mock_put(pathname, body, **kwargs):
        calls["put"].append((pathname, body, kwargs))
        objects[pathname] = {
            "body": bytes(body),
            "content_type": kwargs.get("content_type"),
            "etag": f"etag-{len(objects) + 1}",
        }
        return SimpleNamespace(
            url=f"https://example.blob.vercel-storage.com/{pathname}",
            pathname=pathname,
        )

    def mock_get(pathname, *, access):
        calls["get"].append((pathname, access))

        if pathname not in objects:
            raise BlobNotFoundError()

        return SimpleNamespace(body=objects[pathname]["body"])

    def mock_head(pathname):
        calls["head"].append(pathname)

        if pathname not in objects:
            raise BlobNotFoundError()

        return SimpleNamespace(pathname=pathname)

    def mock_query(*, prefix=None, page_size=None, cursor=None):
        calls["query"].append({
            "prefix": prefix,
            "page_size": page_size,
            "cursor": cursor,
        })

        # Simulate the SDK iterator, including prefix filtering.
        for pathname, item in list(objects.items()):
            if prefix is None or pathname.startswith(prefix):
                yield MockListBlob(
                    url=f"https://example.blob.vercel-storage.com/{pathname}",
                    download_url=f"https://example.blob.vercel-storage.com/{pathname}",
                    pathname=pathname,
                    size=len(item["body"]),
                    uploaded_at=datetime.now(timezone.utc),
                    etag=item["etag"],
                )

    def mock_delete(pathname, *, if_match=None):
        calls["delete"].append((pathname, if_match))

        if pathname not in objects:
            raise BlobNotFoundError()

        if if_match and objects[pathname]["etag"] != if_match:
            raise RuntimeError("ETag mismatch")

        del objects[pathname]

    monkeypatch.setattr(
        "src.storage.blob_storage.blob.put_blob", mock_put
    )
    monkeypatch.setattr(
        "src.storage.blob_storage.blob.get_blob", mock_get
    )
    monkeypatch.setattr(
        "src.storage.blob_storage.blob.head_blob", mock_head
    )
    monkeypatch.setattr(
        "src.storage.blob_storage.blob.query_blobs", mock_query
    )
    monkeypatch.setattr(
        "src.storage.blob_storage.blob.delete_blob", mock_delete
    )

    storage = BlobStorage(
        blob_token=TEST_TOKEN,
        blob_store_id="teststore",
    )

    return storage, objects, calls


def test_blob_save_source_pdf(mock_blob):
    storage, objects, calls = mock_blob

    pdf_bytes = b"%PDF-1.4 fake pdf content"

    key, checksum, size = storage.save_source_pdf(
        project_id="proj_1",
        source_path_or_bytes=pdf_bytes,
        original_filename="document.pdf",
    )

    assert key == "proj_1/source/document.pdf"
    assert size == len(pdf_bytes)
    assert len(checksum) == 64
    assert objects[key]["body"] == pdf_bytes

    pathname, body, kwargs = calls["put"][0]

    assert pathname == key
    assert body == pdf_bytes
    assert kwargs["access"] == "private"
    assert kwargs["content_type"] == "application/pdf"


def test_blob_save_image(mock_blob):
    storage, objects, calls = mock_blob

    img_bytes = b"\x89PNG fake image bytes"

    key, checksum, size = storage.save_image(
        project_id="proj_1",
        image_id="img_uuid_123",
        image_bytes=img_bytes,
        ext="png",
    )

    assert key == "proj_1/images/img_uuid_123.png"
    assert size == len(img_bytes)
    assert len(checksum) == 64
    assert objects[key]["body"] == img_bytes
    assert calls["put"][0][2]["access"] == "private"
    assert calls["put"][0][2]["content_type"] == "image/png"


def test_blob_save_export(mock_blob):
    storage, objects, calls = mock_blob

    docx_bytes = b"PK\x03\x04 fake docx zip content"

    key, checksum, size = storage.save_export(
        project_id="proj_1",
        export_id="exp_uuid_123",
        export_bytes=docx_bytes,
        filename="Report_alt_text.docx",
    )

    assert key == "proj_1/exports/exp_uuid_123_Report_alt_text.docx"
    assert size == len(docx_bytes)
    assert len(checksum) == 64
    assert objects[key]["body"] == docx_bytes
    assert calls["put"][0][2]["access"] == "private"


def test_blob_read_bytes(mock_blob):
    storage, objects, calls = mock_blob

    img_bytes = b"\x89PNG fake image bytes"

    storage.save_image(
        project_id="proj_1",
        image_id="img_read_test",
        image_bytes=img_bytes,
        ext="png",
    )

    read_back = storage.read_bytes(
        "proj_1",
        "images/img_read_test.png",
    )

    assert read_back == img_bytes
    assert calls["get"] == [
        ("proj_1/images/img_read_test.png", "private")
    ]


def test_blob_read_bytes_not_found(mock_blob):
    storage, _, _ = mock_blob

    with pytest.raises(FileNotFoundError):
        storage.read_bytes(
            "proj_1",
            "images/nonexistent.png",
        )


def test_blob_path_traversal_protection(mock_blob):
    storage, _, _ = mock_blob

    with pytest.raises(PathTraversalError):
        storage.resolve_path("proj_1", "../../etc/passwd")

    with pytest.raises(PathTraversalError):
        storage.resolve_path("proj_1", "../sibling/secret.pdf")

    with pytest.raises(PathTraversalError):
        storage.resolve_path("proj_1", "/absolute/path.pdf")

    with pytest.raises(PathTraversalError):
        storage.get_project_dir("../other_project")


def test_blob_project_isolation(mock_blob):
    storage, objects, _ = mock_blob

    storage.save_image("proj_a", "img_a", b"bytes_a", "png")
    storage.save_image("proj_b", "img_b", b"bytes_b", "png")

    assert storage.read_bytes(
        "proj_a", "images/img_a.png"
    ) == b"bytes_a"

    assert storage.read_bytes(
        "proj_b", "images/img_b.png"
    ) == b"bytes_b"

    assert "proj_a/images/img_a.png" in objects
    assert "proj_b/images/img_b.png" in objects

    with pytest.raises(FileNotFoundError):
        storage.read_bytes("proj_a", "images/img_b.png")


def test_blob_max_object_size(mock_blob):
    storage, _, calls = mock_blob

    oversized = b"x" * (MAX_OBJECT_BYTES + 1)

    with pytest.raises(StorageError):
        storage.save_image(
            "proj_1",
            "big_img",
            oversized,
            "png",
        )

    assert calls["put"] == []


def test_blob_empty_bytes_rejected(mock_blob):
    storage, _, calls = mock_blob

    with pytest.raises(StorageError):
        storage.save_image(
            "proj_1",
            "empty_img",
            b"",
            "png",
        )

    assert calls["put"] == []


def test_blob_exists(mock_blob):
    storage, _, calls = mock_blob

    storage.save_image(
        "proj_1",
        "exists_test",
        b"some bytes",
        "png",
    )

    assert storage.exists(
        "proj_1",
        "images/exists_test.png",
    ) is True

    assert storage.exists(
        "proj_1",
        "images/nonexistent.png",
    ) is False

    assert len(calls["head"]) == 2


def test_blob_get_project_dir(mock_blob):
    storage, _, _ = mock_blob

    assert storage.get_project_dir("proj_1") == "proj_1/"
    assert storage.init_project_storage("proj_1") == "proj_1/"


def test_blob_content_type_headers(mock_blob):
    storage, _, calls = mock_blob

    storage.save_image(
        "proj_1",
        "ct_test",
        b"\x89PNG bytes",
        "png",
    )

    kwargs = calls["put"][0][2]

    assert kwargs["content_type"] == "image/png"
    assert kwargs["access"] == "private"
    assert kwargs["content_length"] == len(b"\x89PNG bytes")


def test_cleanup_requires_verified_export_or_cancellation(mock_blob):
    storage, _, calls = mock_blob

    with pytest.raises(CleanupBlockedError):
        storage.cleanup_project(
            "proj_1",
            export_verified_and_delivered=False,
            project_cancelled=False,
        )

    assert calls["query"] == []
    assert calls["delete"] == []


def test_cleanup_blocked_when_jobs_are_active(mock_blob):
    storage, _, calls = mock_blob

    with pytest.raises(CleanupBlockedError):
        storage.cleanup_project(
            "proj_1",
            project_cancelled=True,
            active_jobs_running=True,
        )

    assert calls["query"] == []
    assert calls["delete"] == []


def test_cleanup_deletes_only_project_objects(mock_blob):
    storage, objects, calls = mock_blob

    storage.save_image("proj_a", "img_a", b"bytes_a", "png")
    storage.save_image("proj_a", "img_b", b"bytes_b", "png")
    storage.save_image("proj_b", "img_c", b"bytes_c", "png")

    result = storage.cleanup_project(
        "proj_a",
        project_cancelled=True,
    )

    assert result is True
    assert "proj_a/images/img_a.png" not in objects
    assert "proj_a/images/img_b.png" not in objects
    assert "proj_b/images/img_c.png" in objects

    deleted_keys = [item[0] for item in calls["delete"]]

    assert set(deleted_keys) == {
        "proj_a/images/img_a.png",
        "proj_a/images/img_b.png",
    }


def test_cleanup_uses_etag_protection(mock_blob):
    storage, _, calls = mock_blob

    storage.save_image("proj_a", "img_a", b"bytes_a", "png")

    storage.cleanup_project(
        "proj_a",
        project_cancelled=True,
    )

    assert len(calls["delete"]) == 1
    assert calls["delete"][0][1] is not None


def test_blob_close_is_safe(mock_blob):
    storage, _, _ = mock_blob

    assert storage.close() is None



def test_blob_read_bytes_accepts_prefixed_storage_key(mock_blob):
    """A database key already containing project_id must not be prefixed twice."""
    storage, objects, calls = mock_blob

    project_id = "proj_1"
    image_bytes = b"\x89PNG fake image bytes"

    key, _, _ = storage.save_image(
        project_id=project_id,
        image_id="prefixed_read_test",
        image_bytes=image_bytes,
        ext="png",
    )

    # Simulate the full key stored in the database.
    assert key == "proj_1/images/prefixed_read_test.png"

    read_back = storage.read_bytes(
        project_id,
        key,
    )

    assert read_back == image_bytes

    # Verify that the project ID appears only once.
    assert calls["get"][-1] == (
        "proj_1/images/prefixed_read_test.png",
        "private",
    )


def test_blob_exists_accepts_prefixed_storage_key(mock_blob):
    """Existence checks must also support database-stored full keys."""
    storage, _, calls = mock_blob

    project_id = "proj_1"

    key, _, _ = storage.save_image(
        project_id=project_id,
        image_id="prefixed_exists_test",
        image_bytes=b"image bytes",
        ext="png",
    )

    assert storage.exists(project_id, key) is True

    assert calls["head"][-1] == (
        "proj_1/images/prefixed_exists_test.png"
    )


def test_blob_resolve_path_accepts_prefixed_storage_key(mock_blob):
    """Resolving an already prefixed key must not duplicate project_id."""
    storage, _, _ = mock_blob

    project_id = "proj_1"

    key = "proj_1/images/test_image.png"

    resolved = storage.resolve_path(project_id, key)

    assert resolved == "proj_1/images/test_image.png"
