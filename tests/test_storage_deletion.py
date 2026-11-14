"""
Storage Layer Deletion Primitives Tests (Stage 1)
------------------------------------------------
Tests individual and batch object deletion in:
- LocalFileStore (filesystem)
- BlobStorage (mocked Vercel Blob)

Verifies:
1. Individual and batch object deletion.
2. Idempotency: missing/already-deleted objects are recorded as missing without error.
3. Path traversal protection: ../, absolute paths, and project-directory targeting raise PathTraversalError.
4. Cross-project protection: attempts to delete assets belonging to other projects raise PathTraversalError.
5. Preservation of unrelated project assets: other images and export DOCX files remain untouched.
6. Partial failure resilience: errors on one object are isolated and captured in result.failed.
7. Structured DeletionResult format.
"""

import pytest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from dataclasses import dataclass

from vercel.blob.errors import BlobNotFoundError

from src.storage.base_storage import (
    DeletionResult,
    PathTraversalError,
    validate_storage_key,
)
from src.storage.filestore import FileStore
from src.storage.blob_storage import BlobStorage


TEST_TOKEN = "vercel_blob_rw_teststore_secret123"


# ============================================================================
# LocalFileStore Tests
# ============================================================================

def test_filestore_delete_single_object_success(tmp_path):
    """Deleting an existing file succeeds, returns structured result, and removes file."""
    store = FileStore(str(tmp_path))
    project_id = "proj_test_1"

    key, checksum, size = store.save_image(project_id, "img_001", b"PNG_DATA_1", "png")
    assert store.exists(project_id, key) is True

    result = store.delete_object(project_id, key)

    assert isinstance(result, DeletionResult)
    assert result.is_success is True
    assert result.deleted == [key]
    assert result.missing == []
    assert result.failed == {}
    assert store.exists(project_id, key) is False


def test_filestore_delete_missing_object_is_idempotent(tmp_path):
    """Deleting a non-existent file returns it in missing without raising an error."""
    store = FileStore(str(tmp_path))
    project_id = "proj_test_2"
    non_existent_key = "images/missing_image.png"

    result = store.delete_object(project_id, non_existent_key)

    assert result.is_success is True
    assert result.deleted == []
    assert result.missing == [non_existent_key]
    assert result.failed == {}

    # Repeated deletion is idempotent
    repeat_result = store.delete_object(project_id, non_existent_key)
    assert repeat_result.is_success is True
    assert repeat_result.missing == [non_existent_key]


def test_filestore_delete_batch_objects(tmp_path):
    """Batch deletion processes multiple files, separates existing vs missing, and preserves unlisted files."""
    store = FileStore(str(tmp_path))
    project_id = "proj_test_3"

    k1, _, _ = store.save_image(project_id, "img_1", b"IMG_1", "png")
    k2, _, _ = store.save_image(project_id, "img_2", b"IMG_2", "png")
    k3, _, _ = store.save_image(project_id, "img_3", b"IMG_3", "png")
    k_missing = "images/ghost.png"

    # Delete img_1, img_2, and ghost. img_3 must be preserved.
    result = store.delete_objects(project_id, [k1, k2, k_missing])

    assert result.is_success is True
    assert set(result.deleted) == {k1, k2}
    assert result.missing == [k_missing]
    assert result.failed == {}

    # Check filesystem state
    assert store.exists(project_id, k1) is False
    assert store.exists(project_id, k2) is False
    assert store.exists(project_id, k3) is True  # Preserved!
    assert store.read_bytes(project_id, k3) == b"IMG_3"


def test_filestore_path_traversal_protection(tmp_path):
    """Path traversal sequences in keys must immediately raise PathTraversalError."""
    store = FileStore(str(tmp_path))
    project_id = "proj_safe"

    traversal_keys = [
        "../../etc/passwd",
        "../sibling_project/images/img.png",
        "/absolute/path/file.png",
        "images/../../../secret.txt",
        "",
        "   ",
    ]

    for bad_key in traversal_keys:
        with pytest.raises(PathTraversalError):
            store.delete_object(project_id, bad_key)

        with pytest.raises(PathTraversalError):
            store.delete_objects(project_id, [bad_key])


def test_filestore_cross_project_protection(tmp_path):
    """Attempting to delete a key belonging to another project raises PathTraversalError and preserves the file."""
    store = FileStore(str(tmp_path))
    proj_a = "proj_alpha"
    proj_b = "proj_beta"

    # Save asset in project B
    kb, _, _ = store.save_image(proj_b, "target_img", b"TARGET_B", "png")
    assert store.exists(proj_b, kb) is True

    # Attempt cross-project deletion using prefixed key from project A
    cross_project_key = f"{proj_b}/{kb}"

    with pytest.raises(PathTraversalError) as exc_info:
        store.delete_object(proj_a, cross_project_key)
    assert "Cross-project access forbidden" in str(exc_info.value)

    # Project B's asset must remain completely untouched
    assert store.exists(proj_b, kb) is True
    assert store.read_bytes(proj_b, kb) == b"TARGET_B"


def test_filestore_partial_failure_handling(tmp_path, monkeypatch):
    """When an unlinking failure occurs on one object, other objects are deleted and failure is recorded."""
    store = FileStore(str(tmp_path))
    project_id = "proj_partial_fail"

    k1, _, _ = store.save_image(project_id, "ok_1", b"OK1", "png")
    k2, _, _ = store.save_image(project_id, "fail_2", b"FAIL2", "png")
    k3, _, _ = store.save_image(project_id, "ok_3", b"OK3", "png")

    target_path_k2 = store.resolve_path(project_id, k2)

    original_unlink = Path.unlink

    def mock_unlink(self, *args, **kwargs):
        if self == target_path_k2:
            raise PermissionError("Simulated permission denied on fail_2")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", mock_unlink)

    result = store.delete_objects(project_id, [k1, k2, k3])

    assert result.is_success is False
    assert set(result.deleted) == {k1, k3}
    assert k2 in result.failed
    assert "Simulated permission denied" in result.failed[k2]

    # Verify ok_1 and ok_3 are deleted, fail_2 remains
    assert store.exists(project_id, k1) is False
    assert store.exists(project_id, k2) is True
    assert store.exists(project_id, k3) is False


def test_filestore_post_export_cleanup_preserves_docx(tmp_path):
    """Deleting extracted images and source PDF strictly preserves the exported DOCX."""
    store = FileStore(str(tmp_path))
    project_id = "proj_export_preserve"

    k_pdf, _, _ = store.save_source_pdf(project_id, b"%PDF-1.4 sample content", "report.pdf")
    k_img1, _, _ = store.save_image(project_id, "fig1", b"FIG1_BYTES", "png")
    k_img2, _, _ = store.save_image(project_id, "fig2", b"FIG2_BYTES", "png")
    k_docx, _, _ = store.save_export(project_id, "exp_101", b"DOCX_ZIP_BYTES", "final.docx")

    # Mode A: delete extracted images only
    res_a = store.delete_objects(project_id, [k_img1, k_img2])
    assert res_a.is_success is True
    assert set(res_a.deleted) == {k_img1, k_img2}

    # Verify images gone, source PDF and DOCX intact
    assert store.exists(project_id, k_img1) is False
    assert store.exists(project_id, k_img2) is False
    assert store.exists(project_id, k_pdf) is True
    assert store.exists(project_id, k_docx) is True
    assert store.read_bytes(project_id, k_docx) == b"DOCX_ZIP_BYTES"

    # Mode B: delete source PDF as well
    res_b = store.delete_object(project_id, k_pdf)
    assert res_b.is_success is True

    # Source PDF gone, but DOCX STILL intact
    assert store.exists(project_id, k_pdf) is False
    assert store.exists(project_id, k_docx) is True
    assert store.read_bytes(project_id, k_docx) == b"DOCX_ZIP_BYTES"


# ============================================================================
# BlobStorage Tests (Mocked Vercel Blob)
# ============================================================================

@dataclass
class MockBlobItem:
    url: str
    download_url: str
    pathname: str
    size: int
    uploaded_at: datetime
    etag: str


@pytest.fixture
def mock_blob_fixture(monkeypatch):
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
        calls["query"].append({"prefix": prefix, "page_size": page_size, "cursor": cursor})
        for pathname, item in list(objects.items()):
            if prefix is None or pathname.startswith(prefix):
                yield MockBlobItem(
                    url=f"https://example.blob.vercel-storage.com/{pathname}",
                    download_url=f"https://example.blob.vercel-storage.com/{pathname}",
                    pathname=pathname,
                    size=len(item["body"]),
                    uploaded_at=datetime.now(timezone.utc),
                    etag=item["etag"],
                )

    def mock_delete(url_or_pathname, *, if_match=None):
        calls["delete"].append((url_or_pathname, if_match))
        if isinstance(url_or_pathname, str):
            targets = [url_or_pathname]
        else:
            targets = list(url_or_pathname)

        for target in targets:
            if target not in objects:
                if len(targets) == 1:
                    raise BlobNotFoundError()
                continue
            if if_match and objects[target]["etag"] != if_match:
                raise RuntimeError("ETag mismatch")
            del objects[target]

    monkeypatch.setattr("src.storage.blob_storage.blob.put_blob", mock_put)
    monkeypatch.setattr("src.storage.blob_storage.blob.get_blob", mock_get)
    monkeypatch.setattr("src.storage.blob_storage.blob.head_blob", mock_head)
    monkeypatch.setattr("src.storage.blob_storage.blob.query_blobs", mock_query)
    monkeypatch.setattr("src.storage.blob_storage.blob.delete_blob", mock_delete)

    storage = BlobStorage(blob_token=TEST_TOKEN, blob_store_id="teststore")
    return storage, objects, calls


def test_blob_delete_single_object_success(mock_blob_fixture):
    """Deleting a single Blob object succeeds, removes it from storage, and records it in deleted."""
    storage, objects, calls = mock_blob_fixture
    project_id = "proj_blob_1"

    key, checksum, size = storage.save_image(project_id, "fig_10", b"BLOB_PNG_BYTES", "png")
    safe_key = storage._safe_key(project_id, key)
    assert storage.exists(project_id, key) is True
    assert safe_key in objects

    result = storage.delete_object(project_id, key)

    assert result.is_success is True
    assert result.deleted == [key]
    assert result.missing == []
    assert result.failed == {}
    assert storage.exists(project_id, key) is False
    assert safe_key not in objects


def test_blob_delete_missing_object_is_idempotent(mock_blob_fixture):
    """Deleting an already-missing Blob object records it in missing without raising."""
    storage, objects, calls = mock_blob_fixture
    project_id = "proj_blob_2"
    non_existent = "images/ghost_blob.png"

    result = storage.delete_object(project_id, non_existent)

    assert result.is_success is True
    assert result.deleted == []
    assert result.missing == [non_existent]
    assert result.failed == {}


def test_blob_delete_batch_objects(mock_blob_fixture):
    """Batch deletion in Blob correctly deletes target objects, identifies missing ones, and preserves others."""
    storage, objects, calls = mock_blob_fixture
    project_id = "proj_blob_batch"

    k1, _, _ = storage.save_image(project_id, "img_a", b"BYTES_A", "png")
    k2, _, _ = storage.save_image(project_id, "img_b", b"BYTES_B", "png")
    k3, _, _ = storage.save_image(project_id, "img_c", b"BYTES_C", "png")
    k_missing = "images/not_uploaded.png"

    result = storage.delete_objects(project_id, [k1, k2, k_missing])

    assert result.is_success is True
    assert set(result.deleted) == {k1, k2}
    assert result.missing == [k_missing]
    assert result.failed == {}

    # Verify k1 and k2 deleted, k3 preserved
    assert storage.exists(project_id, k1) is False
    assert storage.exists(project_id, k2) is False
    assert storage.exists(project_id, k3) is True
    assert storage.read_bytes(project_id, k3) == b"BYTES_C"


def test_blob_path_traversal_and_cross_project_protection(mock_blob_fixture):
    """BlobStorage validates project isolation and blocks path traversal & cross-project keys."""
    storage, objects, calls = mock_blob_fixture
    proj_a = "proj_a"
    proj_b = "proj_b"

    # Save object in project B
    kb, _, _ = storage.save_image(proj_b, "isolated_img", b"SECRET_B", "png")
    assert storage.exists(proj_b, kb) is True

    # Cross-project deletion attempt
    with pytest.raises(PathTraversalError):
        storage.delete_object(proj_a, kb)

    with pytest.raises(PathTraversalError):
        storage.delete_objects(proj_a, [kb])

    # Path traversal attempts
    with pytest.raises(PathTraversalError):
        storage.delete_object(proj_a, "../../outside.pdf")

    with pytest.raises(PathTraversalError):
        storage.delete_object(proj_a, "/absolute/path.pdf")

    # Project B's asset must remain untouched
    assert storage.exists(proj_b, kb) is True
    assert storage.read_bytes(proj_b, kb) == b"SECRET_B"


def test_blob_partial_failure_handling(mock_blob_fixture, monkeypatch):
    """When a single object deletion fails in Blob SDK, individual failure is recorded in result.failed."""
    storage, objects, calls = mock_blob_fixture
    project_id = "proj_blob_partial"

    k1, _, _ = storage.save_image(project_id, "good_1", b"G1", "png")
    k2, _, _ = storage.save_image(project_id, "bad_2", b"B2", "png")
    k3, _, _ = storage.save_image(project_id, "good_3", b"G3", "png")

    bad_pathname = storage._safe_key(project_id, k2)

    original_delete = src_delete = src_blob_delete = None
    import src.storage.blob_storage as bs_mod
    original_delete = bs_mod.blob.delete_blob

    def mock_flaky_delete(url_or_pathname, *, if_match=None):
        if isinstance(url_or_pathname, (list, tuple, set)):
            # If batch contains bad_2, raise to trigger individual fallback
            if any(bad_pathname in str(t) for t in url_or_pathname):
                raise RuntimeError("Batch deletion network failure")
            return original_delete(url_or_pathname, if_match=if_match)
        if str(url_or_pathname) == bad_pathname:
            raise RuntimeError("Individual delete network timeout on bad_2")
        return original_delete(url_or_pathname, if_match=if_match)

    monkeypatch.setattr("src.storage.blob_storage.blob.delete_blob", mock_flaky_delete)

    result = storage.delete_objects(project_id, [k1, k2, k3])

    assert result.is_success is False
    assert set(result.deleted) == {k1, k3}
    assert k2 in result.failed
    assert "timeout on bad_2" in result.failed[k2]

    # Verify good_1 and good_3 deleted, bad_2 remains
    assert storage.exists(project_id, k1) is False
    assert storage.exists(project_id, k2) is True
    assert storage.exists(project_id, k3) is False


def test_blob_post_export_preserves_docx(mock_blob_fixture):
    """In Blob storage, deleting extracted images and source PDF strictly preserves the exported DOCX."""
    storage, objects, calls = mock_blob_fixture
    project_id = "proj_blob_export_preserve"

    k_pdf, _, _ = storage.save_source_pdf(project_id, b"%PDF-1.4 sample content", "source.pdf")
    k_img1, _, _ = storage.save_image(project_id, "fig1", b"FIG1", "png")
    k_img2, _, _ = storage.save_image(project_id, "fig2", b"FIG2", "png")
    k_docx, _, _ = storage.save_export(project_id, "exp_01", b"DOCX_ARCHIVE", "exported.docx")

    # Mode A: delete images only
    res_a = storage.delete_objects(project_id, [k_img1, k_img2])
    assert res_a.is_success is True

    assert storage.exists(project_id, k_img1) is False
    assert storage.exists(project_id, k_img2) is False
    assert storage.exists(project_id, k_pdf) is True
    assert storage.exists(project_id, k_docx) is True
    assert storage.read_bytes(project_id, k_docx) == b"DOCX_ARCHIVE"

    # Mode B: delete source PDF
    res_b = storage.delete_object(project_id, k_pdf)
    assert res_b.is_success is True

    assert storage.exists(project_id, k_pdf) is False
    assert storage.exists(project_id, k_docx) is True
    assert storage.read_bytes(project_id, k_docx) == b"DOCX_ARCHIVE"
