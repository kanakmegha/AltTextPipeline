"""Security and recovery coverage for browser-to-Blob uploads and workers."""
import asyncio
import io
import json
import sys
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.web.app import create_app
from src.extraction.engine import ExtractionEngine
from src.worker.extraction_worker import process_extraction_job, run_worker


@pytest.fixture
def local_app(tmp_path):
    db = DatabaseConnection(str(tmp_path / "test.db"))
    apply_migrations(db)
    app = create_app(db_path=str(tmp_path / "test.db"), storage_dir=str(tmp_path / "storage"))
    return TestClient(app), app, tmp_path


def _make_pdf(with_image=False):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Figure 1. Sample diagram")
    if with_image:
        image = Image.new("RGB", (32, 32), "red")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        page.insert_image(fitz.Rect(72, 110, 190, 220), stream=buf.getvalue())
    return doc.tobytes()


def _authorize(client, content=None):
    pdf = content or _make_pdf()
    response = client.post("/api/uploads/authorize", json={
        "filename": "sample.pdf", "content_type": "application/pdf", "size_bytes": len(pdf),
        "project_name": "Async sample", "pages": "1",
    })
    assert response.status_code == 200, response.text
    return response.json(), pdf


def _put_mock_blob(app, intent, pdf):
    store = app.state.filestore
    project_id, relative = intent["pathname"].split("/", 1)
    store.init_project_storage(project_id)
    target = store.get_project_dir(project_id) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(pdf)


def test_browser_uses_blob_client_and_never_posts_pdf_to_fastapi(local_app):
    client, _, _ = local_app
    data, pdf = _authorize(client, b"%PDF-1.7" + b"x" * (5 * 1024 * 1024))
    source = open("src/web/static/upload.js", encoding="utf-8").read()
    bundle = open("src/web/static/upload.bundle.js", encoding="utf-8").read()
    node_token_route = open("api/blob-upload.ts", encoding="utf-8").read()
    vercel_routes = json.load(open("vercel.json", encoding="utf-8"))["routes"]
    assert 'upload(authData.pathname, selectedFile' in source
    assert 'access: "private"' in source
    assert "client/`upload` requires the 'handleUploadUrl' parameter" in bundle
    assert "handleUpload(" in node_token_route
    assert "BLOB_READ_WRITE_TOKEN" not in node_token_route
    disabled_legacy = next(route for route in vercel_routes if route.get("src") == "/api/upload-and-extract")
    assert disabled_legacy["status"] == 410
    assert vercel_routes.index(disabled_legacy) < next(i for i, route in enumerate(vercel_routes) if route.get("src") == "/(.*)")
    assert "/chunk" not in source
    # This body is metadata only; the old body-bearing chunk endpoint is gone.
    assert len(client.post("/api/uploads/authorize", json={"filename": "too-big.pdf",
        "content_type": "application/pdf", "size_bytes": 51 * 1024 * 1024}).content) < 1024
    assert all("/chunk" not in getattr(route, "path", "") for route in client.app.routes)


def test_upload_token_is_single_use_and_completion_verifies_blob(local_app):
    client, app, _ = local_app
    intent, pdf = _authorize(client)
    key = intent["upload_key"]
    claimed = client.post(f"/api/uploads/{key}/claim-token", json={})
    assert claimed.status_code == 200
    assert claimed.json()["pathname"] == intent["pathname"]
    assert client.post(f"/api/uploads/{key}/claim-token", json={}).status_code == 409
    assert client.post(f"/api/uploads/{key}/complete", json={"pages": "1, nope"}).status_code == 400
    assert app.state.repo.get_upload_key(key)["status"] == "uploading"
    _put_mock_blob(app, intent, pdf)
    app.state.filestore.read_bytes = lambda *_args, **_kwargs: pytest.fail("FastAPI must verify Blob metadata without reading PDF bytes")
    completed = client.post(f"/api/uploads/{key}/complete", json={"project_name": "Direct upload", "pages": "1,3"})
    assert completed.status_code == 200, completed.text
    data = completed.json()
    assert data["status"] == "queued"
    assert client.post(f"/api/uploads/{key}/complete", json={}).json()["already_completed"] is True
    repo = app.state.repo
    project = repo.get_project(data["project_id"])
    assert project["source_filename"] == "sample.pdf"
    job = repo.list_project_jobs(data["project_id"])[0]
    assert job["status"] == "queued"
    assert job["page_numbers"] == [1, 3]


def test_upload_authorization_checks_filename_type_size_and_ownership(local_app):
    client, app, _ = local_app
    for payload in (
        {"filename": "../bad.pdf", "content_type": "application/pdf", "size_bytes": 20},
        {"filename": "bad.pdf", "content_type": "text/plain", "size_bytes": 20},
        {"filename": "bad.pdf", "content_type": "application/pdf", "size_bytes": 0},
    ):
        assert client.post("/api/uploads/authorize", json=payload).status_code >= 400
    intent, _ = _authorize(client)
    rec = app.state.repo.get_upload_key(intent["upload_key"])
    assert rec["owner_id"] is not None
    assert rec["expected_size"] > 0


def test_completion_rejects_mismatched_blob_size_and_other_owner(local_app):
    client, app, _ = local_app
    intent, pdf = _authorize(client)
    key = intent["upload_key"]
    assert client.post(f"/api/uploads/{key}/claim-token", json={}).status_code == 200
    _put_mock_blob(app, intent, pdf + b"extra")
    assert client.post(f"/api/uploads/{key}/complete", json={}).status_code == 400
    with app.state.repo.db.session() as conn:
        conn.execute("UPDATE upload_keys SET owner_id='different-user' WHERE upload_key=?", (key,))
    assert client.post(f"/api/uploads/{key}/complete", json={}).status_code == 404


def test_upload_mutations_keep_csrf_protection(local_app, monkeypatch):
    client, _, _ = local_app
    monkeypatch.setenv("CSRF_ENABLED", "true")
    assert client.post("/api/uploads/authorize", json={
        "filename": "sample.pdf", "content_type": "application/pdf", "size_bytes": 12,
    }).status_code == 403
    token = client.get("/api/auth/csrf-token").json()["csrf_token"]
    response = client.post("/api/uploads/authorize", headers={"X-CSRF-Token": token}, json={
        "filename": "sample.pdf", "content_type": "application/pdf", "size_bytes": 12,
    })
    assert response.status_code == 200


def _make_job(tmp_path, with_image=False):
    db = DatabaseConnection(str(tmp_path / "worker.db"))
    apply_migrations(db)
    repo = Repository(db)
    store = FileStore(str(tmp_path / "worker-storage"))
    project_id, job_id = "project-1", "job-1"
    key, checksum, _ = store.save_source_pdf(project_id, _make_pdf(with_image), "src.pdf")
    repo.create_project(project_id, "Worker test", "src.pdf", key, checksum, status="queued")
    repo.create_job(job_id, project_id, "extraction")
    return repo, store, job_id


def test_duplicate_queue_delivery_does_not_duplicate_extraction_records(tmp_path):
    repo, store, job_id = _make_job(tmp_path, with_image=True)
    assert process_extraction_job(job_id, repo, store)
    first = repo.list_project_images("project-1")
    assert first
    assert process_extraction_job(job_id, repo, store) is False
    assert len(repo.list_project_images("project-1")) == len(first)
    assert repo.get_job(job_id)["status"] == "completed"


def test_worker_failure_then_restart_recovers_job(tmp_path):
    repo, store, job_id = _make_job(tmp_path)

    class TemporaryFailure:
        def process_pdf(self, *_args, **_kwargs):
            raise OSError("temporary extraction storage failure")

    assert process_extraction_job(job_id, repo, store, engine=TemporaryFailure()) is False
    assert repo.get_job(job_id)["status"] == "queued"
    assert process_extraction_job(job_id, repo, store, engine=ExtractionEngine()) is True
    assert repo.get_job(job_id)["status"] == "completed"
    assert repo.get_project("project-1")["status"] == "ready_for_review"


def test_worker_reclaims_abandoned_running_job_after_restart(tmp_path):
    repo, store, job_id = _make_job(tmp_path)
    repo.update_job_status(job_id, "running")
    with repo.db.session() as conn:
        conn.execute("UPDATE jobs SET lease_updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job_id,))
    assert process_extraction_job(job_id, repo, store, engine=ExtractionEngine())
    assert repo.get_job(job_id)["status"] == "completed"


def test_running_status_renews_lease_without_resurrecting_terminal_job(tmp_path):
    repo, _, job_id = _make_job(tmp_path)
    repo.update_job_status(job_id, "running")
    with repo.db.session() as conn:
        conn.execute("UPDATE jobs SET lease_updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (job_id,))

    repo.update_job_status(job_id, "running")
    renewed = repo.get_job(job_id)
    assert renewed["lease_updated_at"] > "2000-01-01T00:00:00+00:00"
    assert renewed["status"] == "running"

    repo.update_job_status(job_id, "completed")
    completed = repo.get_job(job_id)
    repo.update_job_status(job_id, "running")
    after_late_heartbeat = repo.get_job(job_id)
    assert after_late_heartbeat["status"] == "completed"
    assert after_late_heartbeat["completed_at"] == completed["completed_at"]
    assert after_late_heartbeat["lease_updated_at"] == completed["lease_updated_at"]


def test_queue_publish_failure_leaves_job_recoverable_from_database(tmp_path, monkeypatch):
    repo, _, job_id = _make_job(tmp_path)
    monkeypatch.setenv("REDIS_URL", "redis://queue.invalid/0")
    class FailedRedis:
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            raise ConnectionError("Redis unavailable")
    fake_async = SimpleNamespace(Redis=FailedRedis)
    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(asyncio=fake_async))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_async)
    from src.worker.queueing import publish_extraction_job
    with pytest.raises(ConnectionError):
        asyncio.run(publish_extraction_job(job_id))
    recoverable = repo.list_recoverable_jobs("9999-01-01T00:00:00+00:00")
    assert [job["id"] for job in recoverable] == [job_id]
    assert process_extraction_job(job_id, repo, _make_job_storage(repo, tmp_path))


def test_worker_recovers_database_job_while_redis_is_unavailable(tmp_path, monkeypatch):
    repo, store, job_id = _make_job(tmp_path)

    class OfflineRedis:
        @classmethod
        def from_url(cls, *_args, **_kwargs):
            return cls()
        def xgroup_create(self, *_args, **_kwargs):
            raise ConnectionError("Redis unavailable")
        def close(self):
            pass

    class ResponseError(Exception):
        pass

    class StopAfterOneIteration:
        waiting = False
        def is_set(self):
            return self.waiting
        def wait(self, _seconds):
            self.waiting = True

    redis_module = SimpleNamespace(Redis=OfflineRedis, exceptions=SimpleNamespace(ResponseError=ResponseError))
    monkeypatch.setitem(sys.modules, "redis", redis_module)
    monkeypatch.setitem(sys.modules, "redis.exceptions", redis_module.exceptions)
    run_worker(repo, store, "redis://offline/0", poll_seconds=0.01, stop_event=StopAfterOneIteration())
    assert repo.get_job(job_id)["status"] == "completed"


def _make_job_storage(_repo, tmp_path):
    return FileStore(str(tmp_path / "worker-storage"))
