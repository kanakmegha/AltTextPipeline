import pytest
from fastapi.testclient import TestClient
from src.web.app import create_app


@pytest.fixture
def api_client(tmp_path):
    db_file = tmp_path / "test_progress.db"
    storage_dir = tmp_path / "test_storage"
    app = create_app(db_path=str(db_file), storage_dir=str(storage_dir))
    return TestClient(app), app


def test_project_status_returns_authoritative_progress_fields(api_client):
    client, app = api_client
    repo = app.state.repo

    # 1. Create a project
    project = repo.create_project(
        project_id="test-proj-progress",
        name="Progress Test Project",
        source_filename="test.pdf",
        source_storage_key="source/test.pdf",
        source_checksum="abc123hash",
        owner_id="single_user",
    )
    repo.update_project_status(project["id"], status="extracting")

    # 2. Create job and job batches
    job = repo.create_job(
        job_id="job-1",
        project_id=project["id"],
        job_type="extraction",
        max_attempts=3,
    )
    # Simulate batch creation
    batches = [
        [1, 2, 3, 4, 5],
        [6, 7, 8, 9, 10],
        [11, 12, 13, 14, 15],
    ]
    repo.initialize_job_batches(job["id"], batches, total_document_pages=15, max_attempts=3)

    # Complete batch 0
    repo.start_job_batch(job["id"], 0, "2026-11-02T12:00:00Z")
    repo.set_job_batch_status(job["id"], 0, "completed")

    # Fail batch 1
    repo.start_job_batch(job["id"], 1, "2026-11-02T12:01:00Z")
    repo.set_job_batch_status(job["id"], 1, "failed", error_message="Simulated corrupt stream")

    # Add 1 persisted image
    repo.create_image_asset(
        image_id="img-1",
        project_id=project["id"],
        page_number=1,
        xref=10,
        placement_index=1,
        bbox={"x0": 50, "y0": 100, "x1": 200, "y1": 300},
        original_caption="Figure 1",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="Figure_1.png",
        file_storage_key="images/img-1.png",
        file_checksum="hash1",
        image_format="png",
    )

    # 3. Query GET /api/projects/{project_id}/status
    res = client.get(f"/api/projects/{project['id']}/status")
    assert res.status_code == 200
    data = res.json()

    assert data["project_id"] == project["id"]
    assert data["project_status"] == "extracting"
    assert data["job_id"] == "job-1"
    assert data["total_pages"] == 15
    assert data["completed_pages"] == 5
    assert data["total_batches"] == 3
    assert data["completed_batches"] == 1
    assert data["failed_batches"] == 1
    assert data["total_images"] == 1
    assert len(data["images"]) == 1
    assert data["images"][0]["id"] == "img-1"
    assert len(data["batches"]) == 3


def test_project_status_selects_latest_job_when_retried(api_client):
    client, app = api_client
    repo = app.state.repo

    project = repo.create_project(
        project_id="test-proj-retry",
        name="Retry Test Project",
        source_filename="test2.pdf",
        source_storage_key="source/test2.pdf",
        source_checksum="hash2",
        owner_id="single_user",
    )

    # Old failed job
    repo.create_job(
        job_id="job-old",
        project_id=project["id"],
        job_type="extraction",
        max_attempts=1,
    )
    repo.update_job_status("job-old", "failed", error_message="Worker killed")

    # New running job
    repo.create_job(
        job_id="job-new",
        project_id=project["id"],
        job_type="extraction",
        max_attempts=3,
    )
    repo.update_job_status("job-new", "running")

    res = client.get(f"/api/projects/{project['id']}/status")
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == "job-new"
    assert data["job_status"] == "running"
