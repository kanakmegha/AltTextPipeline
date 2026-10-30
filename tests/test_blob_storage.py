"""
Vercel Blob Storage Tests
-------------------------
Tests the BlobStorage implementation using mocked HTTP responses via
httpx.MockTransport. Verifies upload, download, key safety, and cleanup
without requiring a real Vercel Blob account.
"""

import json
import io
import zipfile
import pytest
import httpx

from src.storage.blob_storage import BlobStorage, MAX_OBJECT_BYTES


class MockBlobTransport(httpx.MockTransport):
    """Mock transport that records requests and simulates Vercel Blob API."""

    def __init__(self):
        self.requests = []
        self.objects = {}  # key -> (content, content_type)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers),
            "content": request.content,
        })
        method = request.method
        url_path = request.url.path

        # Parse key from URL: /{storeId}/{key} or /{key}
        parts = [p for p in url_path.split("/") if p]
        if len(parts) >= 2:
            key = "/".join(parts[1:])  # skip storeId
        else:
            key = parts[0] if parts else ""

        if method == "PUT":
            self.objects[key] = (request.content, request.headers.get("content-type", "application/octet-stream"))
            return httpx.Response(200, json={"key": key, "uploaded": True})

        if method == "GET":
            if key in self.objects:
                content, _ = self.objects[key]
                return httpx.Response(200, content=content)
            return httpx.Response(404, json={"error": "not found"})

        if method == "HEAD":
            if key in self.objects:
                return httpx.Response(200)
            return httpx.Response(404)

        if method == "DELETE":
            if key in self.objects:
                del self.objects[key]
                return httpx.Response(200)
            return httpx.Response(404)

        return httpx.Response(200, json={"ok": True})

    def list_requests(self):
        return self.requests


@pytest.fixture
def mock_blob():
    transport = MockBlobTransport()
    storage = BlobStorage(
        blob_token="test_token_12345",
        blob_store_id="test_store_id",
    )
    storage._client = httpx.Client(transport=transport)
    storage._transport = transport
    return storage, transport


def test_blob_save_source_pdf(mock_blob):
    storage, transport = mock_blob
    pdf_bytes = b"%PDF-1.4 fake pdf content"
    key, checksum, size = storage.save_source_pdf(
        project_id="proj_1",
        source_path_or_bytes=pdf_bytes,
        original_filename="document.pdf",
    )
    assert key == "proj_1/source/document.pdf"
    assert size == len(pdf_bytes)
    assert len(checksum) == 64  # SHA-256 hex

    # Verify object was uploaded
    put_requests = [r for r in transport.list_requests() if r["method"] == "PUT"]
    assert len(put_requests) == 1
    assert put_requests[0]["url"].endswith("proj_1/source/document.pdf")
    assert put_requests[0]["content"] == pdf_bytes


def test_blob_save_image(mock_blob):
    storage, transport = mock_blob
    img_bytes = b"\x89PNG fake image bytes"
    image_id = "img_uuid_123"
    key, checksum, size = storage.save_image(
        project_id="proj_1",
        image_id=image_id,
        image_bytes=img_bytes,
        ext="png",
    )
    assert key == "proj_1/images/img_uuid_123.png"
    assert size == len(img_bytes)

    # Verify upload
    put_requests = [r for r in transport.list_requests() if r["method"] == "PUT"]
    assert len(put_requests) == 1
    assert put_requests[0]["content"] == img_bytes


def test_blob_save_export(mock_blob):
    storage, transport = mock_blob
    docx_bytes = b"PK\x03\x04 fake docx zip content"
    export_id = "exp_uuid_123"
    key, checksum, size = storage.save_export(
        project_id="proj_1",
        export_id=export_id,
        export_bytes=docx_bytes,
        filename="Report_alt_text.docx",
    )
    assert key == f"proj_1/exports/{export_id}_Report_alt_text.docx"
    assert size == len(docx_bytes)


def test_blob_read_bytes(mock_blob):
    storage, transport = mock_blob
    img_bytes = b"\x89PNG fake image bytes"
    storage.save_image(
        project_id="proj_1",
        image_id="img_read_test",
        image_bytes=img_bytes,
        ext="png",
    )

    read_back = storage.read_bytes("proj_1", "images/img_read_test.png")
    assert read_back == img_bytes

    # Verify GET request
    get_requests = [r for r in transport.list_requests() if r["method"] == "GET"]
    assert len(get_requests) == 1
    assert get_requests[0]["url"].endswith("proj_1/images/img_read_test.png")


def test_blob_read_bytes_not_found(mock_blob):
    storage, _ = mock_blob
    with pytest.raises(FileNotFoundError):
        storage.read_bytes("proj_1", "images/nonexistent.png")


def test_blob_path_traversal_protection(mock_blob):
    storage, _ = mock_blob
    with pytest.raises(Exception):
        storage.resolve_path("proj_1", "../../etc/passwd")

    with pytest.raises(Exception):
        storage.resolve_path("proj_1", "../sibling/secret.pdf")


def test_blob_project_isolation(mock_blob):
    storage, transport = mock_blob
    # Upload objects for two different projects
    storage.save_image("proj_a", "img_a", b"bytes_a", "png")
    storage.save_image("proj_b", "img_b", b"bytes_b", "png")

    # Each project can read its own
    assert storage.read_bytes("proj_a", "images/img_a.png") == b"bytes_a"
    assert storage.read_bytes("proj_b", "images/img_b.png") == b"bytes_b"

    # Verify keys are project-isolated
    put_requests = [r for r in transport.list_requests() if r["method"] == "PUT"]
    key_paths = []
    for r in put_requests:
        url_path = r["url"]
        # URL format: https://blob.vercel-storage.com/test_store_id/proj_a/images/img_a.png
        parts = url_path.split("/")
        # Find everything after the store_id
        if "test_store_id" in parts:
            idx = parts.index("test_store_id")
            key_paths.append("/".join(parts[idx + 1:]))
    assert "proj_a/images/img_a.png" in key_paths
    assert "proj_b/images/img_b.png" in key_paths


def test_blob_max_object_size(mock_blob):
    storage, _ = mock_blob
    oversized = b"x" * (MAX_OBJECT_BYTES + 1)
    with pytest.raises(Exception):
        storage.save_image("proj_1", "big_img", oversized, "png")


def test_blob_empty_bytes_rejected(mock_blob):
    storage, _ = mock_blob
    with pytest.raises(Exception):
        storage.save_image("proj_1", "empty_img", b"", "png")


def test_blob_exists(mock_blob):
    storage, _ = mock_blob
    storage.save_image("proj_1", "exists_test", b"some bytes", "png")
    assert storage.exists("proj_1", "images/exists_test.png") is True
    assert storage.exists("proj_1", "images/nonexistent.png") is False


def test_blob_get_project_dir(mock_blob):
    storage, _ = mock_blob
    prefix = storage.get_project_dir("proj_1")
    assert prefix == "proj_1/"


def test_blob_content_type_headers(mock_blob):
    storage, transport = mock_blob
    storage.save_image("proj_1", "ct_test", b"\x89PNG bytes", "png")
    put_requests = [r for r in transport.list_requests() if r["method"] == "PUT"]
    headers = put_requests[0]["headers"]
    # httpx Headers are case-insensitive
    assert "image/png" in headers.get("content-type", "")
    assert "x-blob-store-id" in headers
    assert headers["x-blob-store-id"] == "test_store_id"
    assert "authorization" in {k.lower() for k in headers.keys()}
    auth_val = headers.get("authorization") or headers.get("Authorization")
    assert auth_val == "Bearer test_token_12345"
