"""
PostgreSQL Repository Tests
---------------------------
Tests the PostgreSQL-backed repository implementation against a local
PostgreSQL instance. Verifies that the PostgresRepository mirrors the
SQLite Repository interface and behavior.

Requires a running PostgreSQL instance at /tmp (Postgres.app) with a
database named 'alttext_test'.
"""

import os
import uuid
import json
import pytest
from datetime import datetime, timezone

from src.db.postgres_connection import PostgresConnection
from src.db.postgres_migrations import apply_migrations as pg_apply_migrations
from src.db.postgres_repository import PostgresRepository


@pytest.fixture(scope="module")
def pg_db():
    """Provides a PostgreSQL connection for the test module."""
    database_url = os.environ.get(
        "PG_TEST_DATABASE_URL",
        "host=/tmp user=postgres dbname=alttext_test",
    )
    conn = PostgresConnection(database_url=database_url)
    # Reset schema to ensure clean state for re-runs
    with conn.session() as c:
        with c.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cur.execute("GRANT ALL ON SCHEMA public TO postgres;")
    pg_apply_migrations(conn)
    yield conn


@pytest.fixture
def repo(pg_db):
    """Provides a fresh PostgresRepository (migrations already applied)."""
    return PostgresRepository(pg_db)


def _make_image_kwargs(repo, project_id, **overrides):
    defaults = dict(
        image_id=str(uuid.uuid4()),
        project_id=project_id,
        page_number=1,
        xref=100,
        placement_index=1,
        bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
        original_caption="Test caption",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="Figure_1.png",
        file_storage_key="images/test.png",
        file_checksum="abc123",
        image_format="png",
        processing_status="extracted",
    )
    defaults.update(overrides)
    return defaults


# ------------------------------------------------------------------
# Project tests
# ------------------------------------------------------------------

def test_postgres_create_and_get_project(repo):
    project_id = str(uuid.uuid4())
    project = repo.create_project(
        project_id=project_id,
        name="Test Project",
        source_filename="test.pdf",
        source_storage_key="source/test.pdf",
        source_checksum="sha256abc",
    )
    assert project is not None
    assert project["id"] == project_id
    assert project["name"] == "Test Project"
    assert project["status"] == "created"

    fetched = repo.get_project(project_id)
    assert fetched is not None
    assert fetched["id"] == project_id
    assert fetched["name"] == "Test Project"


def test_postgres_get_nonexistent_project(repo):
    assert repo.get_project(str(uuid.uuid4())) is None


def test_postgres_update_project_status(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Status Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )
    updated = repo.update_project_status(project_id, status="extracting")
    assert updated is not None
    assert updated["status"] == "extracting"


def test_postgres_list_projects(repo):
    for i in range(3):
        repo.create_project(
            project_id=str(uuid.uuid4()),
            name=f"Project {i}",
            source_filename=f"{i}.pdf",
            source_storage_key=f"s/{i}.pdf",
            source_checksum=f"chk{i}",
        )
    projects = repo.list_projects()
    assert len(projects) >= 3


# ------------------------------------------------------------------
# Image asset tests
# ------------------------------------------------------------------

def test_postgres_create_and_get_image_asset(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Image Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )
    kwargs = _make_image_kwargs(repo, project_id)
    asset = repo.create_image_asset(**kwargs)
    assert asset["id"] == kwargs["image_id"]
    assert asset["page_number"] == 1
    assert asset["original_caption"] == "Test caption"
    assert asset["needs_caption_review"] is False
    assert asset["review_reasons"] == []
    assert asset["bbox"]["x0"] == 0.0

    fetched = repo.get_image_asset(kwargs["image_id"])
    assert fetched is not None
    assert fetched["id"] == kwargs["image_id"]


def test_postgres_list_project_images_ordering(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Order Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )
    # Insert images out of order
    repo.create_image_asset(**_make_image_kwargs(repo, project_id, page_number=3, placement_index=0, image_id=str(uuid.uuid4()), suggested_filename="img3.png"))
    repo.create_image_asset(**_make_image_kwargs(repo, project_id, page_number=1, placement_index=1, image_id=str(uuid.uuid4()), suggested_filename="img1b.png"))
    repo.create_image_asset(**_make_image_kwargs(repo, project_id, page_number=1, placement_index=0, image_id=str(uuid.uuid4()), suggested_filename="img1a.png"))

    images = repo.list_project_images(project_id)
    assert len(images) == 3
    # Should be ordered by page_number ASC, placement_index ASC
    assert images[0]["suggested_filename"] == "img1a.png"
    assert images[1]["suggested_filename"] == "img1b.png"
    assert images[2]["suggested_filename"] == "img3.png"


def test_postgres_update_image_status(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Status Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )
    kwargs = _make_image_kwargs(repo, project_id)
    repo.create_image_asset(**kwargs)
    updated = repo.update_image_status(kwargs["image_id"], "approved")
    assert updated is not None
    assert updated["processing_status"] == "approved"


# ------------------------------------------------------------------
# Alt text version tests
# ------------------------------------------------------------------

def test_postgres_alt_text_versions(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Alt Text Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )
    img_id = str(uuid.uuid4())
    repo.create_image_asset(**_make_image_kwargs(repo, project_id, image_id=img_id))

    v1 = repo.create_alt_text_version(
        version_id=str(uuid.uuid4()),
        image_id=img_id,
        version_number=1,
        text="First draft alt text.",
    )
    assert v1["version_number"] == 1

    v2 = repo.create_alt_text_version(
        version_id=str(uuid.uuid4()),
        image_id=img_id,
        version_number=2,
        text="Second revised draft.",
        revision_instruction="Make it concise.",
    )
    assert v2["version_number"] == 2

    versions = repo.list_image_versions(img_id)
    assert len(versions) == 2
    assert versions[0]["version_number"] == 1
    assert versions[1]["version_number"] == 2

    latest = repo.get_latest_version(img_id)
    assert latest is not None
    assert latest["version_number"] == 2


# ------------------------------------------------------------------
# Approval tests
# ------------------------------------------------------------------

def test_postgres_approvals(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Approval Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )
    img_id = str(uuid.uuid4())
    repo.create_image_asset(**_make_image_kwargs(repo, project_id, image_id=img_id))
    v_id = str(uuid.uuid4())
    repo.create_alt_text_version(version_id=v_id, image_id=img_id, version_number=1, text="Draft text.")

    approval = repo.create_approval(
        approval_id=str(uuid.uuid4()),
        image_id=img_id,
        alt_text_version_id=v_id,
        reviewer_id="editor_1",
    )
    assert approval is not None
    assert approval["image_id"] == img_id
    assert approval["alt_text_version_id"] == v_id

    fetched = repo.get_approval_for_image(img_id)
    assert fetched is not None
    assert fetched["alt_text_version_id"] == v_id

    # Verify image status changed to approved
    asset = repo.get_image_asset(img_id)
    assert asset["processing_status"] == "approved"


# ------------------------------------------------------------------
# Export tests
# ------------------------------------------------------------------

def test_postgres_exports(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Export Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )

    export = repo.create_export(
        export_id=str(uuid.uuid4()),
        project_id=project_id,
        export_type="docx",
        file_storage_key="exports/test.docx",
        file_checksum="chk123",
        figure_count=3,
    )
    assert export["figure_count"] == 3
    assert export["total_figures_exported"] == 3

    fetched = repo.get_export(export["id"])
    assert fetched is not None
    assert fetched["figure_count"] == 3

    updated = repo.update_export_status(export["id"], status="delivered", mark_delivered=True)
    assert updated["status"] == "delivered"
    assert updated["delivered_at"] is not None

    exports = repo.list_project_exports(project_id)
    assert len(exports) == 1


# ------------------------------------------------------------------
# Audit event tests
# ------------------------------------------------------------------

def test_postgres_audit_events(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Audit Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )

    event = repo.record_audit_event(
        event_id=str(uuid.uuid4()),
        project_id=project_id,
        event_type="test_event",
        target_type="image",
        target_id="img_1",
        details={"key": "value"},
        actor_id="test_actor",
    )
    assert event is not None
    assert event["details"] == {"key": "value"}

    events = repo.list_project_audit_events(project_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "test_event"


# ------------------------------------------------------------------
# Job tests
# ------------------------------------------------------------------

def test_postgres_job_lifecycle(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Job Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )

    job = repo.create_job(
        job_id=str(uuid.uuid4()),
        project_id=project_id,
        job_type="extraction_chunk",
        page_start=1,
        page_end=20,
        max_attempts=3,
    )
    assert job["status"] == "queued"
    assert job["attempt_count"] == 0

    job = repo.update_job_status(job["id"], status="running", increment_attempt=True)
    assert job["status"] == "running"
    assert job["attempt_count"] == 1
    assert job["started_at"] is not None

    job = repo.update_job_status(job["id"], status="completed")
    assert job["status"] == "completed"
    assert job["completed_at"] is not None


# ------------------------------------------------------------------
# AI usage ledger tests
# ------------------------------------------------------------------

def test_postgres_ai_usage(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="AI Usage Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )

    record = repo.record_ai_usage(
        ledger_id=str(uuid.uuid4()),
        project_id=project_id,
        image_id=None,
        provider="novita",
        model="zai-org/GLM-5.3-Flash",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.01,
    )
    assert record["input_tokens"] == 100
    assert record["output_tokens"] == 50

    usage = repo.get_project_ai_usage(project_id)
    assert usage["request_count"] == 1
    assert usage["total_input_tokens"] == 100
    assert usage["total_output_tokens"] == 50

    total = repo.get_total_ai_usage()
    assert total["request_count"] >= 1


# ------------------------------------------------------------------
# Figure group tests
# ------------------------------------------------------------------

def test_postgres_figure_groups(repo):
    project_id = str(uuid.uuid4())
    repo.create_project(
        project_id=project_id,
        name="Group Test",
        source_filename="t.pdf",
        source_storage_key="s/t.pdf",
        source_checksum="chk",
    )

    group = repo.create_figure_group(
        group_id=str(uuid.uuid4()),
        project_id=project_id,
        caption_text="Shared caption",
        caption_page_number=1,
        caption_position="below",
        figure_label="Figure 1",
        is_cross_page=False,
        review_status="candidate",
        review_reasons=[],
    )
    assert group is not None
    assert group["caption_text"] == "Shared caption"
    assert group["is_cross_page"] is False

    fetched = repo.get_figure_group(group["id"])
    assert fetched is not None
    assert fetched["image_count"] == 0

    groups = repo.list_project_groups(project_id)
    assert len(groups) == 1


def test_postgres_auth_schema_and_project_owner_isolation(pg_db, repo):
    from src.web import auth

    auth.ensure_auth_schema(pg_db)

    u1_id = str(uuid.uuid4())
    u2_id = str(uuid.uuid4())
    repo.create_user(u1_id, f"pg_u1_{u1_id[:8]}@example.com", "PgPass123!")
    repo.create_user(u2_id, f"pg_u2_{u2_id[:8]}@example.com", "PgPass456!")

    p1_id = str(uuid.uuid4())
    p2_id = str(uuid.uuid4())
    repo.create_project(p1_id, "U1 Proj", "u1.pdf", "s/u1.pdf", "c1", owner_id=u1_id)
    repo.create_project(p2_id, "U2 Proj", "u2.pdf", "s/u2.pdf", "c2", owner_id=u2_id)

    u1_projs = repo.list_projects(owner_id=u1_id)
    assert any(p["id"] == p1_id for p in u1_projs)
    assert not any(p["id"] == p2_id for p in u1_projs)

    # Deactivating u1 prevents password verification
    repo.set_user_active(u1_id, False)
    assert repo.verify_user_password(f"pg_u1_{u1_id[:8]}@example.com", "PgPass123!") is None


def test_postgres_upload_intent_claim_and_atomic_finalize(repo):
    from src.db.repository import utc_now_iso

    now = utc_now_iso()
    upload_key, project_id, job_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    record = repo.create_upload_key(
        upload_key=upload_key, content_type="application/pdf", expires_at="2999-01-01T00:00:00+00:00",
        owner_id="owner-1", filename="validated.pdf", expected_size=64,
        blob_key=f"{project_id}/source/{upload_key}.pdf",
    )
    assert record["owner_id"] == "owner-1"
    assert repo.claim_upload_token(upload_key, "owner-1", now) is True
    assert repo.claim_upload_token(upload_key, "owner-1", now) is False
    assert repo.claim_upload_finalization(upload_key, "owner-1", now) is True
    first = repo.finalize_upload_project(
        upload_key=upload_key, project_id=project_id, job_id=job_id, owner_id="owner-1",
        name="Upload", source_filename="validated.pdf", source_storage_key="source/upload.pdf",
        source_checksum="etag:blob", source_size=64, page_start=1, page_end=2, page_numbers=[1, 2],
    )
    second = repo.finalize_upload_project(
        upload_key=upload_key, project_id=project_id, job_id=job_id, owner_id="owner-1",
        name="Upload", source_filename="validated.pdf", source_storage_key="source/upload.pdf",
        source_checksum="etag:blob", source_size=64, page_start=1, page_end=2, page_numbers=[1, 2],
    )
    assert first["project"]["id"] == second["project"]["id"] == project_id
    assert first["job"]["id"] == second["job"]["id"] == job_id
    assert first["job"]["page_numbers"] == [1, 2]
    assert len(repo.list_project_jobs(project_id)) == 1
    assert repo.get_upload_key(upload_key)["status"] == "completed"


def test_postgres_job_claim_is_atomic_and_reclaimable(repo):
    from src.db.repository import utc_now_iso

    project_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    repo.create_project(project_id, "Claim", "x.pdf", "source/x.pdf", "checksum")
    repo.create_job(job_id, project_id, "extraction")
    now = utc_now_iso()
    assert repo.claim_job(job_id, "2000-01-01T00:00:00+00:00", now) is not None
    assert repo.claim_job(job_id, "2000-01-01T00:00:00+00:00", now) is None
    with repo.db.session() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_updated_at='2000-01-01T00:00:00+00:00' WHERE id=%s;", (job_id,))
    assert repo.claim_job(job_id, "2020-01-01T00:00:00+00:00", utc_now_iso()) is not None
    assert repo.get_job(job_id)["attempt_count"] == 2


def test_postgres_running_status_renews_lease_without_resurrecting_terminal_job(repo):
    from src.db.repository import utc_now_iso

    project_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    repo.create_project(project_id, "Lease renewal", "x.pdf", "source/x.pdf", "checksum")
    repo.create_job(job_id, project_id, "extraction")
    repo.update_job_status(job_id, "running")
    with repo.db.session() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET lease_updated_at='2000-01-01T00:00:00+00:00' WHERE id=%s;", (job_id,))

    repo.update_job_status(job_id, "running")
    renewed = repo.get_job(job_id)
    assert renewed["lease_updated_at"] > "2000-01-01T00:00:00+00:00"

    repo.update_job_status(job_id, "completed")
    completed = repo.get_job(job_id)
    repo.update_job_status(job_id, "running")
    after_late_heartbeat = repo.get_job(job_id)
    assert after_late_heartbeat["status"] == "completed"
    assert after_late_heartbeat["completed_at"] == completed["completed_at"]
    assert after_late_heartbeat["lease_updated_at"] == completed["lease_updated_at"]


def test_postgres_worker_lease_fences_batch_and_terminal_writes(repo):
    from src.db.repository import utc_now_iso

    project_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
    repo.create_project(project_id, "Fenced lease", "x.pdf", "source/x.pdf", "checksum")
    repo.create_job(job_id, project_id, "extraction")
    now = utc_now_iso()
    assert repo.claim_job(job_id, "2000-01-01T00:00:00+00:00", now, worker_id="worker-a")
    assert repo.renew_job_lease(job_id, "worker-a", utc_now_iso()) is True
    repo.initialize_job_batches(job_id, [[1], [2]], total_document_pages=2)
    assert repo.start_job_batch(job_id, 0, utc_now_iso(), worker_id="worker-a")
    assert repo.set_job_batch_status(job_id, 0, "completed", worker_id="worker-a") is True
    assert repo.get_job(job_id)["completed_pages"] == 1

    assert repo.update_claimed_job_status(job_id, "worker-a", "completed") is True
    completed = repo.get_job(job_id)
    assert repo.renew_job_lease(job_id, "worker-a", utc_now_iso()) is False
    assert repo.set_job_batch_status(job_id, 1, "completed", worker_id="worker-a") is False
    assert repo.get_job(job_id)["status"] == "completed"
    assert repo.get_job(job_id)["lease_updated_at"] == completed["lease_updated_at"]


def test_postgres_cleanup_preview_and_prepare_both_modes(repo):
    from src.storage.base_storage import CleanupBlockedError

    # Mode A: extracted
    pid_a = f"pg_clean_a_{uuid.uuid4().hex[:8]}"
    repo.create_project(pid_a, "PG Cleanup Mode A", "doc_a.pdf", "source/doc_a.pdf", "chk_a")
    repo.create_image_asset(
        image_id="img_a_1",
        project_id=pid_a,
        page_number=1,
        xref=1,
        placement_index=1,
        bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
        original_caption="Fig 1",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="p1_1.png",
        file_storage_key="images/p1_1.png",
        file_checksum="chk_img_a",
        image_format="png",
    )
    exp_a = f"exp_a_{uuid.uuid4().hex[:8]}"
    repo.create_export(exp_a, pid_a, "docx", "exports/doc_a.docx", "chk_exp_a")
    repo.update_export_status(exp_a, "verified")

    preview_a = repo.preview_project_cleanup(pid_a, "extracted")
    assert preview_a["project_id"] == pid_a
    assert preview_a["cleanup_mode"] == "extracted"
    assert preview_a["can_cleanup"] is True
    assert preview_a["target_blob_keys"] == ["images/p1_1.png"]
    assert "exports/doc_a.docx" in preview_a["preserved_assets"]["export_storage_keys"]
    assert preview_a["preserved_assets"]["source_storage_key"] == "source/doc_a.pdf"

    op_a = repo.prepare_cleanup_operation(pid_a, "extracted")
    assert op_a["status"] == "pending"
    assert op_a["cleanup_mode"] == "extracted"
    assert op_a["target_blob_keys"] == ["images/p1_1.png"]
    assert repo.get_project(pid_a)["cleanup_status"] == "cleaning"

    # Concurrent prepare & job/export creation must be blocked while cleaning
    with pytest.raises(CleanupBlockedError):
        repo.prepare_cleanup_operation(pid_a, "extracted")
    with pytest.raises(CleanupBlockedError):
        repo.create_job(str(uuid.uuid4()), pid_a, "extraction")
    with pytest.raises(CleanupBlockedError):
        repo.create_export(str(uuid.uuid4()), pid_a, "docx", "exports/blocked.docx", "chk_blocked")

    # Mode B: all_source
    pid_b = f"pg_clean_b_{uuid.uuid4().hex[:8]}"
    repo.create_project(pid_b, "PG Cleanup Mode B", "doc_b.pdf", "source/doc_b.pdf", "chk_b")
    repo.create_image_asset(
        image_id="img_b_1",
        project_id=pid_b,
        page_number=1,
        xref=1,
        placement_index=1,
        bbox={"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0},
        original_caption="Fig 1",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="p1_1.png",
        file_storage_key="images/p1_1.png",
        file_checksum="chk_img_a",
        image_format="png",
    )
    exp_b = f"exp_b_{uuid.uuid4().hex[:8]}"
    repo.create_export(exp_b, pid_b, "docx", "exports/doc_b.docx", "chk_exp_b")
    repo.update_export_status(exp_b, "verified")

    preview_b = repo.preview_project_cleanup(pid_b, "all_source")
    assert preview_b["project_id"] == pid_b
    assert preview_b["cleanup_mode"] == "all_source"
    assert preview_b["can_cleanup"] is True
    assert preview_b["target_blob_keys"] == ["images/p1_1.png", "source/doc_b.pdf"]
    assert "exports/doc_b.docx" in preview_b["preserved_assets"]["export_storage_keys"]

    op_b = repo.prepare_cleanup_operation(pid_b, "all_source")
    assert op_b["status"] == "pending"
    assert op_b["cleanup_mode"] == "all_source"
    assert op_b["target_blob_keys"] == ["images/p1_1.png", "source/doc_b.pdf"]
    assert repo.get_project(pid_b)["cleanup_status"] == "cleaning"


def test_postgres_orphan_upload_recovery_and_stale_claims(repo):
    from datetime import datetime, timedelta, timezone
    from concurrent.futures import ThreadPoolExecutor
    from src.storage.base_storage import DeletionResult

    past_exp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    future_exp = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    k_expired = f"uk_exp_{uuid.uuid4().hex[:8]}"
    k_fresh_claim = f"uk_fresh_{uuid.uuid4().hex[:8]}"
    k_stale_claim = f"uk_stale_{uuid.uuid4().hex[:8]}"
    k_active = f"uk_act_{uuid.uuid4().hex[:8]}"
    k_finalizing = f"uk_fin_{uuid.uuid4().hex[:8]}"

    repo.create_upload_key(
        upload_key=k_expired,
        content_type="application/pdf",
        expires_at=past_exp,
        filename="expired.pdf",
        expected_size=1024,
        blob_key="proj_exp/source/expired.pdf",
    )
    repo.create_upload_key(
        upload_key=k_fresh_claim,
        content_type="application/pdf",
        expires_at=past_exp,
        filename="fresh.pdf",
        expected_size=1024,
        blob_key="proj_fresh/source/fresh.pdf",
    )
    repo.create_upload_key(
        upload_key=k_stale_claim,
        content_type="application/pdf",
        expires_at=past_exp,
        filename="stale.pdf",
        expected_size=1024,
        blob_key="proj_stale/source/stale.pdf",
    )
    repo.create_upload_key(
        upload_key=k_active,
        content_type="application/pdf",
        expires_at=future_exp,
        filename="active.pdf",
        expected_size=1024,
        blob_key="proj_act/source/active.pdf",
    )
    repo.create_upload_key(
        upload_key=k_finalizing,
        content_type="application/pdf",
        expires_at=past_exp,
        filename="fin.pdf",
        expected_size=1024,
        blob_key="proj_fin/source/fin.pdf",
    )
    repo.update_upload_key(k_finalizing, status="finalizing")

    # Claim k_fresh_claim now (fresh claim must not be stolen)
    claimed_fresh = repo.claim_orphan_upload_cleanup(k_fresh_claim, claim_ttl_seconds=300)
    assert claimed_fresh is not None
    assert claimed_fresh["status"] == "cleaning_orphan"
    assert isinstance(claimed_fresh["expires_at"], str)
    assert isinstance(claimed_fresh["created_at"], str)
    assert isinstance(claimed_fresh["updated_at"], str)
    # Second claim on fresh row must fail
    assert repo.claim_orphan_upload_cleanup(k_fresh_claim, claim_ttl_seconds=300) is None

    # Simulate crashed worker holding k_stale_claim for 10 minutes
    assert repo.claim_orphan_upload_cleanup(k_stale_claim, claim_ttl_seconds=300) is not None
    stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with repo.db.session() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE upload_keys SET updated_at = %s WHERE upload_key = %s;", (stale_ts, k_stale_claim))

    eligible = {r["upload_key"]: r for r in repo.list_expired_incomplete_uploads(limit=50, claim_ttl_seconds=300)}
    assert k_expired in eligible
    assert k_stale_claim in eligible
    assert k_fresh_claim not in eligible
    assert k_active not in eligible
    assert k_finalizing not in eligible
    assert isinstance(eligible[k_expired]["expires_at"], str)

    # Ineligible rows cannot be claimed
    assert repo.claim_orphan_upload_cleanup(k_active, claim_ttl_seconds=300) is None
    assert repo.claim_orphan_upload_cleanup(k_finalizing, claim_ttl_seconds=300) is None

    # Concurrent workers racing to reclaim k_stale_claim: exactly one wins
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: repo.claim_orphan_upload_cleanup(k_stale_claim, claim_ttl_seconds=300), range(4)))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0]["status"] == "cleaning_orphan"

    # Reset k_stale_claim to stale again and verify full recover_orphan_uploads idempotency
    with repo.db.session() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE upload_keys SET updated_at = %s WHERE upload_key = %s;", (stale_ts, k_stale_claim))

    deleted_calls = []

    class DummyStorage:
        def delete_object(self, project_id, relative_key):
            deleted_calls.append((project_id, relative_key))
            return DeletionResult(deleted=[f"{project_id}/{relative_key}"])

    rec = repo.recover_orphan_uploads(DummyStorage(), limit=50, claim_ttl_seconds=300)
    assert k_expired in rec["cleaned"]
    assert k_stale_claim in rec["cleaned"]
    assert k_fresh_claim not in rec["cleaned"]
    assert ("proj_exp", "source/expired.pdf") in deleted_calls
    assert ("proj_stale", "source/stale.pdf") in deleted_calls
    assert repo.get_upload_key(k_expired)["status"] == "cleaned_orphan"
    assert repo.get_upload_key(k_stale_claim)["status"] == "cleaned_orphan"
    assert repo.get_upload_key(k_fresh_claim)["status"] == "cleaning_orphan"


def test_postgres_permanent_project_deletion_and_retry(repo):
    from datetime import datetime, timedelta, timezone
    from src.storage.base_storage import CleanupBlockedError, DeletionResult, StorageError

    pid_del = f"pg_del_{uuid.uuid4().hex[:8]}"
    pid_keep = f"pg_keep_{uuid.uuid4().hex[:8]}"
    for pid, name in [(pid_del, "PG Delete Me"), (pid_keep, "PG Keep Me")]:
        repo.create_project(pid, name, f"{pid}.pdf", f"source/{pid}.pdf", "chk")
        repo.create_image_asset(
            image_id=f"img_{pid}", project_id=pid, page_number=1, xref=1, placement_index=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}, original_caption="Cap",
            caption_position="below", needs_caption_review=False, review_reasons=[],
            suggested_filename="f1.png", file_storage_key="images/f1.png",
            file_checksum="chk_img", image_format="png",
        )
        repo.create_alt_text_version(
            version_id=f"ver_{pid}",
            image_id=f"img_{pid}",
            version_number=1,
            text="Alt",
            provider="openai",
            model="gpt-4o",
        )
        repo.create_approval(f"app_{pid}", f"img_{pid}", f"ver_{pid}", "approved")
        repo.create_export(f"exp_{pid}", pid, "docx", "exports/out.docx", "chk_exp")
        repo.update_export_status(f"exp_{pid}", "verified")
        repo.record_audit_event(f"aud_{pid}", pid, "approved", "image", f"img_{pid}", "user", "{}")
        repo.record_ai_usage(f"ai_{pid}", pid, f"img_{pid}", "openai", "gpt-4o", 5, 5, 0.01)
        exp_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        repo.create_upload_key(f"uk_{pid}", "application/pdf", exp_at, blob_key=f"{pid}/source/{pid}.pdf")
        repo.update_upload_key(f"uk_{pid}", status="completed", project_id=pid)

    class FlakyStorage:
        def __init__(self):
            self.calls = 0

        def list_project_keys(self, project_id):
            return ["artifacts/extra.pdf"] if project_id == pid_del else []

        def delete_objects(self, project_id, keys):
            self.calls += 1
            if self.calls == 1:
                return DeletionResult(deleted=list(keys[:-1]), failed={keys[-1]: "Simulated Blob 503"})
            return DeletionResult(deleted=list(keys))

    storage = FlakyStorage()
    preview = repo.preview_project_deletion(pid_del, storage=storage)
    assert preview["can_delete"] is True
    assert "exports/out.docx" in preview["target_blob_keys"]
    assert "artifacts/extra.pdf" in preview["target_blob_keys"]

    with pytest.raises(StorageError):
        repo.execute_project_deletion(pid_del, storage=storage)

    # Project & records remain while failed; job/cleanup fenced
    assert repo.get_project(pid_del) is not None
    with pytest.raises(CleanupBlockedError):
        repo.create_job(str(uuid.uuid4()), pid_del, "extraction")
    op = repo.get_latest_cleanup_operation(pid_del)
    assert op is not None and op["status"] == "failed"

    # Retry succeeds and purges all records for pid_del while keeping pid_keep intact
    done = repo.retry_project_deletion(op["id"], storage=storage)
    assert done["status"] == "completed"
    assert repo.get_project(pid_del) is None
    assert repo.get_upload_key(f"uk_{pid_del}") is None
    assert repo.get_project(pid_keep) is not None
    assert repo.get_export(f"exp_{pid_keep}") is not None


def test_postgres_project_doc_design_save_restore_reset(repo):
    pid = f"pg_des_{uuid.uuid4().hex[:8]}"
    repo.create_project(pid, "PG Designer Book", f"{pid}.pdf", f"source/{pid}.pdf", "chk")
    assert repo.get_project_doc_design(pid) is None

    cfg = {
        "style_preset": "professional",
        "document_title": "PG Accessibility Report",
        "font_family": "Arial",
        "fields": [{"key": "image", "enabled": True}, {"key": "approved_alt_text", "enabled": True}],
    }
    saved = repo.save_project_doc_design(pid, cfg)
    assert saved["config"]["document_title"] == "PG Accessibility Report"

    restored = repo.get_project_doc_design(pid)
    assert restored is not None
    assert restored["style_preset"] == "professional"
    assert restored["document_title"] == "PG Accessibility Report"

    repo.reset_project_doc_design(pid)
    assert repo.get_project_doc_design(pid) is None


def test_postgres_deletion_crash_recovery_lease_fencing_and_partial_db(repo, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import threading
    from src.storage.base_storage import CleanupBlockedError, DeletionResult, StorageError

    monkeypatch.setenv("DELETION_LEASE_SECONDS", "300")
    pid_del = f"pg_crash_{uuid.uuid4().hex[:8]}"
    pid_keep = f"pg_safe_{uuid.uuid4().hex[:8]}"
    for pid, name in [(pid_del, "PG Crash"), (pid_keep, "PG Safe")]:
        repo.create_project(pid, name, f"{pid}.pdf", f"source/{pid}.pdf", "chk")
        repo.create_image_asset(
            image_id=f"img_{pid}", project_id=pid, page_number=1, xref=1, placement_index=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}, original_caption="Cap",
            caption_position="below", needs_caption_review=False, review_reasons=[],
            suggested_filename="f1.png", file_storage_key="images/f1.png",
            file_checksum="chk_img", image_format="png",
        )
        repo.create_alt_text_version(
            version_id=f"ver_{pid}", image_id=f"img_{pid}", version_number=1, text="Alt",
        )
        repo.create_approval(f"app_{pid}", f"img_{pid}", f"ver_{pid}", "approved")
        repo.create_export(f"exp_{pid}", pid, "docx", "exports/out.docx", "chk_exp")
        repo.update_export_status(f"exp_{pid}", "verified")

    class CrashThenRecoverStorage:
        def __init__(self):
            self.mode = "boom"

        def list_project_keys(self, project_id):
            return []

        def delete_objects(self, project_id, keys):
            if self.mode == "boom":
                raise RuntimeError("Unexpected Postgres Blob SDK error")
            return DeletionResult(deleted=list(keys))

    storage = CrashThenRecoverStorage()
    op = repo.prepare_project_deletion(pid_del, storage=storage)
    op_id = op["id"]

    # 1. Unexpected Blob exception leaves operation recoverable in failed state
    with pytest.raises(StorageError):
        repo._run_project_deletion_operation(op, storage)
    failed_op = repo.get_cleanup_operation(op_id)
    assert failed_op["status"] == "failed"
    assert "Unexpected Postgres Blob SDK error" in (failed_op["error_message"] or "")

    # 2. Fresh lease blocks reclaim; stale lease allows atomic reclaim
    fresh_exp = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
    with repo.db.session() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE cleanup_operations SET status = 'deleting_blobs', lease_token = 'live-pg', lease_expires_at = %s WHERE id = %s;",
                (fresh_exp, op_id),
            )
    with pytest.raises(CleanupBlockedError):
        repo.retry_project_deletion(op_id, storage=storage)

    stale_exp = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    with repo.db.session() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE cleanup_operations SET lease_expires_at = %s WHERE id = %s;", (stale_exp, op_id))
    prev = repo.preview_project_deletion(pid_del, storage=storage)
    assert prev["existing_operation"]["id"] == op_id
    assert prev["can_resume"] is True

    # 3. Concurrent reclaimers: only one wins
    barrier = threading.Barrier(2)
    results = []

    def _compete():
        barrier.wait()
        try:
            _, tok = repo._claim_project_deletion_operation(op_id)
            results.append(("ok", tok))
        except CleanupBlockedError:
            results.append(("blocked", None))

    t1 = threading.Thread(target=_compete)
    t2 = threading.Thread(target=_compete)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert sorted(r[0] for r in results) == ["blocked", "ok"]
    winning_tok = next(tok for status, tok in results if status == "ok")

    # Old worker fenced out
    with pytest.raises(CleanupBlockedError):
        repo._renew_deletion_lease(op_id, "live-pg")
    assert repo._record_deletion_failure(op_id, "live-pg", [], [], {}, "stale") is False
    assert winning_tok != "live-pg"

    # 4. Crash during deleting_records with partial DB deletion -> retry completes cleanly
    storage.mode = "ok"
    with repo.db.session() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM approvals WHERE image_id IN (SELECT id FROM image_assets WHERE project_id = %s);", (pid_del,))
            cur.execute(
                "UPDATE cleanup_operations SET status = 'deleting_records', deleted_blob_keys = target_blob_keys, lease_expires_at = %s WHERE id = %s;",
                (stale_exp, op_id),
            )
    done = repo.retry_project_deletion(op_id, storage=storage)
    assert done["status"] == "completed"
    assert repo.get_project(pid_del) is None
    assert repo.get_project(pid_keep) is not None
    assert repo.get_export(f"exp_{pid_keep}") is not None


def test_postgres_project_doc_design_save_restore_reset(repo):
    pid = f"pg_des_{uuid.uuid4().hex[:8]}"
    repo.create_project(pid, "PG Designer Book", f"{pid}.pdf", f"source/{pid}.pdf", "chk")
    assert repo.get_project_doc_design(pid) is None

    cfg = {
        "style_preset": "professional",
        "document_title": "PG Accessibility Report",
        "font_family": "Arial",
        "fields": [{"key": "image", "enabled": True}, {"key": "approved_alt_text", "enabled": True}],
    }
    saved = repo.save_project_doc_design(pid, cfg)
    assert saved["config"]["document_title"] == "PG Accessibility Report"

    restored = repo.get_project_doc_design(pid)
    assert restored is not None
    assert restored["style_preset"] == "professional"
    assert restored["document_title"] == "PG Accessibility Report"

    repo.reset_project_doc_design(pid)
    assert repo.get_project_doc_design(pid) is None


def test_postgres_user_doc_design_save_restore_reset_and_isolation(repo):
    u1 = f"pg_user1_{uuid.uuid4().hex[:8]}"
    u2 = f"pg_user2_{uuid.uuid4().hex[:8]}"
    assert repo.get_user_doc_design(u1) is None

    cfg1 = {"style_preset": "editorial", "document_title": "User 1 Prefs"}
    cfg2 = {"style_preset": "table", "document_title": "User 2 Prefs"}
    repo.save_user_doc_design(u1, cfg1)
    repo.save_user_doc_design(u2, cfg2)

    assert repo.get_user_doc_design(u1)["document_title"] == "User 1 Prefs"
    assert repo.get_user_doc_design(u2)["document_title"] == "User 2 Prefs"

    repo.reset_user_doc_design(u1)
    assert repo.get_user_doc_design(u1) is None
    assert repo.get_user_doc_design(u2)["document_title"] == "User 2 Prefs"
    repo.reset_user_doc_design(u2)


def test_postgres_batch_image_and_group_persistence_idempotency(repo):
    pid = str(uuid.uuid4())
    repo.create_project(
        project_id=pid,
        name="PG Batch Project",
        source_filename="batch.pdf",
        source_storage_key="source/batch.pdf",
        source_checksum="chk123",
    )
    img1_id = str(uuid.uuid4())
    img2_id = str(uuid.uuid4())
    items = [
        {
            "image_id": img1_id,
            "project_id": pid,
            "page_number": 1,
            "xref": 10,
            "placement_index": 1,
            "bbox": {"x0": 0.0, "y0": 0.0, "x1": 50.0, "y1": 50.0},
            "original_caption": "Fig 1",
            "caption_position": "below",
            "needs_caption_review": False,
            "review_reasons": [],
            "suggested_filename": "fig1.png",
            "file_storage_key": "images/fig1.png",
            "file_checksum": "c1",
            "image_format": "png",
        },
        {
            "image_id": img2_id,
            "project_id": pid,
            "page_number": 1,
            "xref": 11,
            "placement_index": 2,
            "bbox": {"x0": 60.0, "y0": 0.0, "x1": 110.0, "y1": 50.0},
            "original_caption": "Fig 1",
            "caption_position": "below",
            "needs_caption_review": False,
            "review_reasons": [],
            "suggested_filename": "fig2.png",
            "file_storage_key": "images/fig2.png",
            "file_checksum": "c2",
            "image_format": "png",
        },
    ]
    assert repo.create_image_assets_batch(items) == 2
    # Idempotent repeat with different UUIDs for same source keys inserts 0 rows
    repeat_items = [{**item, "image_id": str(uuid.uuid4())} for item in items]
    assert repo.create_image_assets_batch(repeat_items) == 0

    src_map = repo.list_image_asset_source_keys(pid)
    assert src_map == {(1, 10, 1): img1_id, (1, 11, 2): img2_id}

    gid = str(uuid.uuid4())
    groups = [{
        "group_id": gid,
        "project_id": pid,
        "caption_text": "Figure 1: Dual panel.",
        "caption_page_number": 1,
        "caption_position": "below",
        "figure_label": "Figure 1",
        "is_cross_page": False,
        "review_status": "candidate",
        "review_reasons": [],
    }]
    assert repo.create_figure_groups_batch(groups) == 1
    assert repo.create_figure_groups_batch(groups) == 0

    updated = repo.update_image_group_associations_batch([
        {
            "image_id": img1_id,
            "group_id": gid,
            "association_type": "group",
            "association_confidence": 0.95,
            "panel_label": "A",
            "caption": "Figure 1: Dual panel.",
            "caption_position": "below",
            "needs_caption_review": False,
            "review_reasons": [],
        },
        {
            "image_id": img2_id,
            "group_id": gid,
            "association_type": "group",
            "association_confidence": 0.95,
            "panel_label": "B",
            "caption": "Figure 1: Dual panel.",
            "caption_position": "below",
            "needs_caption_review": False,
            "review_reasons": [],
        },
    ])
    assert updated == 2
    imgs = repo.list_project_images(pid)
    assert all(img["group_id"] == gid for img in imgs)
    assert {img["panel_label"] for img in imgs} == {"A", "B"}


def test_postgres_concurrent_duplicate_source_key_and_grouping_safety(pg_db, repo):
    import threading
    import psycopg2

    pid = str(uuid.uuid4())
    repo.create_project(
        project_id=pid,
        name="PG Conc Project",
        source_filename="conc.pdf",
        source_storage_key="source/conc.pdf",
        source_checksum="chk_conc",
    )

    # Confirm Migration 15 unique index rejects raw conflicting INSERT
    img_a = _make_image_kwargs(repo, pid, page_number=1, xref=55, placement_index=1)
    repo.create_image_asset(**img_a)
    img_b = _make_image_kwargs(repo, pid, page_number=1, xref=55, placement_index=1)
    with pytest.raises(psycopg2.IntegrityError):
        repo.create_image_asset(**img_b)

    # Two concurrent threads calling create_image_assets_batch for the same new source key
    barrier = threading.Barrier(2)
    counts = []
    lock = threading.Lock()

    def worker_insert():
        barrier.wait()
        inserted = repo.create_image_assets_batch([{
            "image_id": str(uuid.uuid4()),
            "project_id": pid,
            "page_number": 2,
            "xref": 88,
            "placement_index": 1,
            "bbox": {"x0": 0.0, "y0": 0.0, "x1": 40.0, "y1": 40.0},
            "original_caption": "Fig 2",
            "caption_position": "below",
            "needs_caption_review": False,
            "review_reasons": [],
            "suggested_filename": "fig2.png",
            "file_storage_key": f"images/{uuid.uuid4()}.png",
            "file_checksum": "c88",
            "image_format": "png",
        }])
        with lock:
            counts.append(inserted)

    t1 = threading.Thread(target=worker_insert)
    t2 = threading.Thread(target=worker_insert)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sum(counts) == 1
    assert len(repo.list_project_images(pid)) == 2

    # Concurrent grouping updates on existing images remain consistent
    imgs = repo.list_project_images(pid)
    gid = str(uuid.uuid4())
    repo.create_figure_groups_batch([{
        "group_id": gid,
        "project_id": pid,
        "caption_text": "Concurrent Group",
        "caption_page_number": 1,
        "caption_position": "below",
        "figure_label": "Fig 1",
        "is_cross_page": False,
        "review_status": "candidate",
        "review_reasons": [],
    }])

    g_barrier = threading.Barrier(2)

    def worker_group():
        g_barrier.wait()
        repo.update_image_group_associations_batch([
            {
                "image_id": img["id"],
                "group_id": gid,
                "association_type": "group",
                "association_confidence": 0.9,
                "panel_label": "A",
                "caption": "Concurrent Group",
                "caption_position": "below",
                "needs_caption_review": False,
                "review_reasons": [],
            }
            for img in imgs
        ])

    gt1 = threading.Thread(target=worker_group)
    gt2 = threading.Thread(target=worker_group)
    gt1.start()
    gt2.start()
    gt1.join()
    gt2.join()

    assert len(repo.list_project_groups(pid)) == 1
    assert all(img["group_id"] == gid for img in repo.list_project_images(pid))
