"""
Automated Test Suite for Extraction Inspector Web Interface
-----------------------------------------------------------
Tests the FastAPI application endpoints:
1. Index page and health endpoints.
2. Sample PDF availability endpoint.
3. Upload and extraction workflow using synthetic digital PDF fixtures.
4. Binary image serving via FileStore.
5. Error handling for non-existent projects or invalid input.
"""

import io
import pytest
from fastapi.testclient import TestClient
from src.web.app import create_app
from tests.fixtures.create_synthetic_pdfs import generate_synthetic_fixtures


@pytest.fixture
def web_client(tmp_path):
    db_file = tmp_path / "web_test.db"
    storage_dir = tmp_path / "web_storage"

    app = create_app(db_path=str(db_file), storage_dir=str(storage_dir))
    return TestClient(app), tmp_path


def test_index_and_health_endpoints(web_client):
    client, _ = web_client

    # Index page
    res_index = client.get("/")
    assert res_index.status_code == 200
    assert "AltTextPipeline" in res_index.text
    assert "Extraction Inspector" in res_index.text

    # Health check
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"


def test_sample_pdf_availability(web_client):
    client, _ = web_client
    res = client.get("/api/sample-pdf-available")
    assert res.status_code == 200
    data = res.json()
    assert "available" in data
    assert data["filename"] == "40345.pdf"


def test_upload_and_extract_synthetic_pdf(web_client):
    client, tmp_path = web_client

    fixtures_dir = tmp_path / "fixtures"
    gt = generate_synthetic_fixtures(str(fixtures_dir))
    case1_pdf = gt["case1_caption_below"]["file"]

    with open(case1_pdf, "rb") as f:
        pdf_bytes = f.read()

    # Upload via multipart
    res = client.post(
        "/api/upload-and-extract",
        files={"file": ("test_site_plan.pdf", pdf_bytes, "application/pdf")},
        data={"project_name": "Test Web Upload"}
    )

    assert res.status_code == 200
    data = res.json()

    assert data["project_name"] == "Test Web Upload"
    assert data["stats"]["total_images"] == 1
    assert data["stats"]["matched"] == 1
    assert data["stats"]["needs_review"] == 0

    images = data["images"]
    assert len(images) == 1
    img = images[0]
    assert img["page_number"] == 1
    assert "Figure 1. Archaeological site plan" in img["original_caption"]
    assert img["caption_position"] == "below"
    assert img["needs_caption_review"] is False

    project_id = data["project_id"]
    image_id = img["id"]

    # Verify binary image endpoint serves the extracted file
    img_res = client.get(f"/api/projects/{project_id}/images/{image_id}/file")
    assert img_res.status_code == 200
    assert img_res.headers["content-type"].startswith("image/")
    assert len(img_res.content) > 0

    # Verify extraction target route loads dedicated Review page without upload form
    review_res = client.get(f"/review/{project_id}")
    assert review_res.status_code == 200
    assert 'id="reviewWorkspaceSection"' in review_res.text
    assert 'id="dropzone"' not in review_res.text

    # Verify subsequent export route loads dedicated Export page without review workspace
    export_res = client.get(f"/export/{project_id}")
    assert export_res.status_code == 200
    assert 'id="exportSection"' in export_res.text
    assert 'id="reviewWorkspaceSection"' not in export_res.text


def test_error_handling_invalid_project(web_client):
    client, _ = web_client

    res = client.get("/api/projects/non-existent-uuid")
    assert res.status_code == 404

    res_img = client.get("/api/projects/non-existent-uuid/images/non-existent/file")
    assert res_img.status_code == 404

    res_review = client.get("/review/non-existent-uuid")
    assert res_review.status_code == 404

    res_export = client.get("/export/non-existent-uuid")
    assert res_export.status_code == 404
