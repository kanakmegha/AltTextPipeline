"""Security and recovery coverage for browser-to-Blob uploads and workers."""
import asyncio
import io
import json
import re
import shutil
import subprocess
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
    assert "resolveClaimOrigin(req.headers)" in node_token_route
    assert re.search(
        r"claimUploadIntent\s*\(\s*origin\s*,\s*clientPayload\.upload_key\s*,\s*clientPayload\.csrf_token\s*,\s*req\.headers\s*,?\s*\)",
        node_token_route,
    )
    assert 'incomingHeaders.authorization' in node_token_route
    assert 'incomingHeaders["x-operator-token"]' in node_token_route
    assert '...incomingHeaders' not in node_token_route
    assert 'Upload intent claim failed (HTTP ${response.status})' in node_token_route
    assert 'response.text()' in node_token_route
    assert 'safeClaimDetail' in node_token_route
    disabled_legacy = next(route for route in vercel_routes if route.get("src") == "/api/upload-and-extract")
    assert disabled_legacy["status"] == 410
    assert vercel_routes.index(disabled_legacy) < next(i for i, route in enumerate(vercel_routes) if route.get("src") == "/(.*)")
    assert "/chunk" not in source
    # This body is metadata only; the old body-bearing chunk endpoint is gone.
    assert len(client.post("/api/uploads/authorize", json={"filename": "too-big.pdf",
        "content_type": "application/pdf", "size_bytes": 51 * 1024 * 1024}).content) < 1024
    assert all("/chunk" not in getattr(route, "path", "") for route in client.app.routes)


def test_resolve_claim_origin_validates_hosts_and_forwards_credentials():
    """Verify origin resolution security and auth header forwarding for direct Blob uploads."""
    node_token_route = open("api/blob-upload.ts", encoding="utf-8").read()

    # 1. Verify resolveClaimOrigin structure and approved hostname sources in source code
    assert "export function resolveClaimOrigin(" in node_token_route
    assert "env.VERCEL_URL" in node_token_route
    assert "env.VERCEL_BRANCH_URL" in node_token_route
    assert "env.VERCEL_PROJECT_PRODUCTION_URL" in node_token_route
    assert "env.UPLOAD_CLAIM_ALLOWED_HOSTS" in node_token_route
    assert "Request host is not an approved application hostname" in node_token_route
    assert "Invalid request origin" in node_token_route

    # 2. Verify resolved origin is passed to claimUploadIntent
    assert re.search(
        r"const\s+origin\s*=\s*resolveClaimOrigin\(req\.headers\);",
        node_token_route,
    )
    assert re.search(
        r"claimUploadIntent\s*\(\s*origin\s*,\s*clientPayload\.upload_key\s*,\s*clientPayload\.csrf_token\s*,\s*req\.headers\s*,?\s*\)",
        node_token_route,
    )

    # 3. Verify buildClaimHeaders forwards credentials & CSRF without blind header spreading
    assert "export function buildClaimHeaders(" in node_token_route
    assert '"X-CSRF-Token": csrfToken' in node_token_route
    assert 'headers.Authorization = incomingHeaders.authorization' in node_token_route
    assert 'headers["X-Operator-Token"] = incomingHeaders["x-operator-token"]' in node_token_route
    assert "...incomingHeaders" not in node_token_route

    # 4. If Node runtime is available, execute unit tests directly on the TypeScript implementation
    if shutil.which("node"):
        node_script = """
import assert from "node:assert/strict";
import { resolveClaimOrigin, buildClaimHeaders } from "./api/blob-upload.ts";

const baseEnv = {
  VERCEL_URL: "preview-123.vercel.app",
  VERCEL_BRANCH_URL: "branch-feature.vercel.app",
  VERCEL_PROJECT_PRODUCTION_URL: "https://production.example.com",
  UPLOAD_CLAIM_ALLOWED_HOSTS: "custom.domain.com, api.custom.org",
};

// Valid Vercel deployment host
assert.equal(
  resolveClaimOrigin({ host: "preview-123.vercel.app" }, baseEnv),
  "https://preview-123.vercel.app"
);
assert.equal(
  resolveClaimOrigin({ host: "branch-feature.vercel.app" }, baseEnv),
  "https://branch-feature.vercel.app"
);

// Valid production domain
assert.equal(
  resolveClaimOrigin({ host: "production.example.com" }, baseEnv),
  "https://production.example.com"
);

// Explicitly allowed custom hostname
assert.equal(
  resolveClaimOrigin({ host: "custom.domain.com" }, baseEnv),
  "https://custom.domain.com"
);
assert.equal(
  resolveClaimOrigin({ host: "api.custom.org" }, baseEnv),
  "https://api.custom.org"
);

// Rejection of untrusted hostnames
assert.throws(
  () => resolveClaimOrigin({ host: "evil.com" }, baseEnv),
  /Request host is not an approved application hostname/
);
assert.throws(
  () => resolveClaimOrigin({ host: "attacker.vercel.app" }, baseEnv),
  /Request host is not an approved application hostname/
);
assert.throws(
  () => resolveClaimOrigin({ host: "production.example.com:8443" }, baseEnv),
  /Invalid request origin/
);
assert.throws(
  () => resolveClaimOrigin({ host: "production.example.com/path" }, baseEnv),
  /Invalid request origin/
);
assert.throws(
  () => resolveClaimOrigin({ host: "" }, baseEnv),
  /Missing request host/
);
assert.throws(
  () => resolveClaimOrigin({}, baseEnv),
  /Missing request host/
);

// Authentication and CSRF headers are forwarded
const claimHeaders = buildClaimHeaders({
  cookie: "session_id=abc; csrf=xyz",
  authorization: "Bearer secret-token",
  "x-operator-token": "operator-secret-token",
  "x-untrusted-header": "strip-this",
  host: "production.example.com",
}, "csrf-token-123");

assert.equal(claimHeaders["Cookie"], "session_id=abc; csrf=xyz");
assert.equal(claimHeaders["X-CSRF-Token"], "csrf-token-123");
assert.equal(claimHeaders["Authorization"], "Bearer secret-token");
assert.equal(claimHeaders["X-Operator-Token"], "operator-secret-token");
assert.equal("x-untrusted-header" in claimHeaders, false);
assert.equal("host" in claimHeaders, false);
"""
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", node_script],
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"Node tests failed:\n{proc.stderr}\n{proc.stdout}"


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


def test_claim_token_accepts_single_user_operator_cookie_and_csrf(local_app, monkeypatch):
    from src.web import auth

    client, _, _ = local_app
    monkeypatch.setenv("SINGLE_USER_ACCESS_TOKEN", "test-operator-cookie-token")
    monkeypatch.setenv("CSRF_ENABLED", "true")
    csrf = client.get("/api/auth/csrf-token").json()["csrf_token"]
    authorized = client.post("/api/uploads/authorize", headers={
        "X-Operator-Token": "test-operator-cookie-token", "X-CSRF-Token": csrf,
    }, json={"filename": "sample.pdf", "content_type": "application/pdf", "size_bytes": 20})
    assert authorized.status_code == 200, authorized.text
    # The cookie is signed exactly as the production operator-cookie auth path expects.
    client.cookies.set(auth.OPERATOR_COOKIE_NAME, auth._sign("test-operator-cookie-token"))
    response = client.post(f"/api/uploads/{authorized.json()['upload_key']}/claim-token",
                           headers={"X-CSRF-Token": csrf}, json={})
    assert response.status_code == 200, response.text


def test_claim_token_accepts_single_user_operator_header_and_csrf(local_app, monkeypatch):
    client, _, _ = local_app
    monkeypatch.setenv("SINGLE_USER_ACCESS_TOKEN", "test-operator-header-token")
    monkeypatch.setenv("CSRF_ENABLED", "true")
    csrf = client.get("/api/auth/csrf-token").json()["csrf_token"]
    headers = {"X-Operator-Token": "test-operator-header-token", "X-CSRF-Token": csrf}
    authorized = client.post("/api/uploads/authorize", headers=headers, json={
        "filename": "sample.pdf", "content_type": "application/pdf", "size_bytes": 20,
    })
    assert authorized.status_code == 200, authorized.text
    claimed = client.post(f"/api/uploads/{authorized.json()['upload_key']}/claim-token",
                          headers=headers, json={})
    assert claimed.status_code == 200, claimed.text


def test_claim_token_accepts_single_user_bearer_header(local_app, monkeypatch):
    client, _, _ = local_app
    monkeypatch.setenv("SINGLE_USER_ACCESS_TOKEN", "test-operator-bearer-token")
    csrf = client.get("/api/auth/csrf-token").json()["csrf_token"]
    headers = {"Authorization": "Bearer test-operator-bearer-token", "X-CSRF-Token": csrf}
    authorized = client.post("/api/uploads/authorize", headers=headers, json={
        "filename": "sample.pdf", "content_type": "application/pdf", "size_bytes": 20,
    })
    assert authorized.status_code == 200, authorized.text
    claimed = client.post(f"/api/uploads/{authorized.json()['upload_key']}/claim-token",
                          headers=headers, json={})
    assert claimed.status_code == 200, claimed.text


def test_upload_claim_rejects_missing_invalid_auth_and_invalid_csrf(local_app, monkeypatch):
    client, _, _ = local_app
    monkeypatch.setenv("SINGLE_USER_ACCESS_TOKEN", "test-operator-required-token")
    monkeypatch.setenv("CSRF_ENABLED", "true")
    # Public CSRF bootstrap is available, but upload authorization requires operator auth.
    csrf = client.get("/api/auth/csrf-token").json()["csrf_token"]
    payload = {"filename": "sample.pdf", "content_type": "application/pdf", "size_bytes": 20}
    assert client.post("/api/uploads/authorize", headers={"X-CSRF-Token": csrf}, json=payload).status_code == 401
    bad = {"X-Operator-Token": "wrong-token", "X-CSRF-Token": csrf}
    assert client.post("/api/uploads/authorize", headers=bad, json=payload).status_code == 401
    good = {"X-Operator-Token": "test-operator-required-token", "X-CSRF-Token": csrf}
    authorized = client.post("/api/uploads/authorize", headers=good, json=payload)
    assert authorized.status_code == 200
    key = authorized.json()["upload_key"]
    assert client.post(f"/api/uploads/{key}/claim-token", headers={
        "X-Operator-Token": "test-operator-required-token", "X-CSRF-Token": "wrong-csrf",
    }, json={}).status_code == 403
    assert client.post(f"/api/uploads/{key}/claim-token", headers=good, json={}).status_code == 200


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

    # The worker's fenced heartbeat path is also a no-op after a terminal transition.
    fenced_job = "lease-fenced-job"
    repo.create_job(fenced_job, "project-1", "extraction")
    now = "2026-10-31T00:00:00+00:00"
    assert repo.claim_job(fenced_job, "2000-01-01T00:00:00+00:00", now, worker_id="lease-owner")
    assert repo.renew_job_lease(fenced_job, "lease-owner", "2030-01-01T00:00:00+00:00") is True
    assert repo.update_claimed_job_status(fenced_job, "lease-owner", "failed", "test failure") is True
    failed = repo.get_job(fenced_job)
    assert repo.renew_job_lease(fenced_job, "lease-owner", "2040-01-01T00:00:00+00:00") is False
    assert repo.get_job(fenced_job)["status"] == "failed"
    assert repo.get_job(fenced_job)["lease_updated_at"] == failed["lease_updated_at"]


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


def test_worker_processes_database_jobs_without_redis(tmp_path, monkeypatch):
    repo, store, job_id = _make_job(tmp_path)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setitem(sys.modules, "redis", None)
    from src.worker.queueing import publish_extraction_job

    # FastAPI's optional publish call is a no-op; the committed PostgreSQL row remains discoverable.
    assert asyncio.run(publish_extraction_job(job_id)) is False
    assert [job["id"] for job in repo.list_recoverable_jobs("9999-01-01T00:00:00+00:00")] == [job_id]

    class StopAfterOneIteration:
        waiting = False
        def is_set(self):
            return self.waiting
        def wait(self, _seconds):
            self.waiting = True

    run_worker(repo, store, redis_url=None, poll_seconds=0.01, stop_event=StopAfterOneIteration())

    assert repo.get_job(job_id)["status"] == "completed"
    assert repo.get_project("project-1")["status"] == "ready_for_review"


def test_worker_persists_parallel_batches_and_recovers_incomplete_batch(tmp_path, monkeypatch):
    db = DatabaseConnection(str(tmp_path / "batched.db"))
    apply_migrations(db)
    repo = Repository(db)
    store = FileStore(str(tmp_path / "batched-storage"))
    doc = fitz.open()
    for page_number in range(2):
        page = doc.new_page()
        page.insert_text((72, 72), f"Figure {page_number + 1}. Batched diagram")
    pdf = doc.tobytes()
    doc.close()
    source_key, checksum, _ = store.save_source_pdf("batch-project", pdf, "batch.pdf")
    repo.create_project("batch-project", "Batch project", "batch.pdf", source_key, checksum, status="queued")
    repo.create_job("batch-job", "batch-project", "extraction")
    monkeypatch.setenv("PDF_BATCH_SIZE", "1")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")

    class FailSecondBatchOnce(ExtractionEngine):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.failed = False
        def process_pdf(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2 and not self.failed:
                self.failed = True
                raise OSError("temporary second batch error")
            return super().process_pdf(*args, **kwargs)

    # Injected engines use sequential batch execution; the first batch is durable before the second fails.
    engine = FailSecondBatchOnce()
    assert process_extraction_job("batch-job", repo, store, engine=engine) is False
    batches_after_failure = repo.list_job_batches("batch-job")
    assert batches_after_failure[0]["status"] == "completed"
    assert batches_after_failure[1]["status"] == "queued"
    assert process_extraction_job("batch-job", repo, store, engine=engine) is True
    job = repo.get_job("batch-job")
    assert job["status"] == "completed"
    assert job["total_pages"] == 2
    assert job["pages_to_process"] == job["completed_pages"] == 2
    assert job["total_batches"] == job["completed_batches"] == 2
    assert [batch["status"] for batch in repo.list_job_batches("batch-job")] == ["completed", "completed"]
    assert len(repo.list_project_images("batch-project")) == 0


def test_worker_executes_default_engine_batches_in_bounded_process_pool(tmp_path, monkeypatch):
    repo, store, job_id = _make_job(tmp_path)
    doc = fitz.open()
    for page_number in range(3):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page batch {page_number + 1}")
    pdf = doc.tobytes()
    doc.close()
    source_key, checksum, _ = store.save_source_pdf("project-1", pdf, "three-pages.pdf")
    repo.update_project_source_checksum("project-1", checksum)
    with repo.db.session() as conn:
        conn.execute("UPDATE projects SET source_storage_key=? WHERE id=?", (source_key, "project-1"))
    monkeypatch.setenv("PDF_BATCH_SIZE", "1")
    monkeypatch.setenv("MAX_PARALLEL_BATCHES", "2")

    assert process_extraction_job(job_id, repo, store) is True
    job = repo.get_job(job_id)
    assert job["status"] == "completed"
    assert job["total_batches"] == job["completed_batches"] == 3
    assert job["completed_pages"] == 3
    assert [batch["status"] for batch in repo.list_job_batches(job_id)] == ["completed"] * 3


def test_job_progress_and_images_api_returns_partial_results_in_page_order(local_app):
    client, app, _ = local_app
    repo = app.state.repo
    project_id, job_id = "progress-project", "progress-job"
    repo.create_project(project_id, "Progress", "source.pdf", "source/source.pdf", "checksum", status="extracting")
    repo.create_job(job_id, project_id, "extraction")
    repo.initialize_job_batches(job_id, [[1], [2]], total_document_pages=2)
    now = "2026-10-31T00:00:00+00:00"
    repo.claim_job(job_id, "2000-01-01T00:00:00+00:00", now, worker_id="progress-worker")
    assert repo.start_job_batch(job_id, 0, now, worker_id="progress-worker")
    repo.set_job_batch_status(job_id, 0, "completed", worker_id="progress-worker")
    for page in (2, 1):
        repo.create_image_asset(
            image_id=f"progress-image-{page}", project_id=project_id, page_number=page,
            xref=page, placement_index=1, bbox={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
            original_caption=f"Figure {page}", caption_position="below", needs_caption_review=False,
            review_reasons=[], suggested_filename=f"page-{page}.png", file_storage_key=f"images/{page}.png",
            file_checksum="checksum", image_format="png", processing_status="ready_for_review",
        )

    progress = client.get(f"/api/jobs/{job_id}/progress")
    images = client.get(f"/api/jobs/{job_id}/images")
    assert progress.status_code == 200, progress.text
    assert progress.json()["completed_batches"] == 1
    assert progress.json()["completed_pages"] == 1
    assert progress.json()["total_batches"] == 2
    assert progress.json()["total_images"] == 2
    assert images.status_code == 200, images.text
    assert [image["page_number"] for image in images.json()["images"]] == [1, 2]


def _make_job_storage(_repo, tmp_path):
    return FileStore(str(tmp_path / "worker-storage"))
