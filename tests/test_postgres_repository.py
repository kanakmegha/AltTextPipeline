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
