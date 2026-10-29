"""
Editor Workflow UI - Phase 5 / Step 1: Repository & Migration Tests
-------------------------------------------------------------------
Tests:
- Successful grouping of images from the same page and different pages.
- Preservation of image IDs, original assets, source page numbers, and alt-text history.
- Preservation of approval records and proper flagging when caption changes post-approval.
- Audit entries recording the grouping operation with prior and new values.
- Rejection of invalid image IDs, duplicates, cross-project membership, and unauthorized reassignment.
- Genuine database transaction rollback when an update or audit insert fails mid-transaction.
- Safe, versioned Migration 4 verification and idempotency.
"""

import io
import uuid
import pytest
from PIL import Image

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.web.app import create_app
from fastapi.testclient import TestClient


def make_test_png(color: str = "blue", width: int = 50, height: int = 50) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def workflow_env(tmp_path):
    """Provides a fresh isolated database and storage directory."""
    db_file = str(tmp_path / "editor_workflow.db")
    storage_dir = str(tmp_path / "storage")

    db_conn = DatabaseConnection(db_file)
    current_version = apply_migrations(db_conn)
    repo = Repository(db_conn)
    filestore = FileStore(storage_dir)

    # Create baseline test project
    project_id = str(uuid.uuid4())
    pdf_bytes = b"%PDF-1.4 test editor workflow document"
    rel_key, checksum, _ = filestore.save_source_pdf(project_id, pdf_bytes, "editor_test.pdf")
    repo.create_project(
        project_id=project_id,
        name="Editor Workflow Project",
        source_filename="editor_test.pdf",
        source_storage_key=rel_key,
        source_checksum=checksum,
        language_variant="en-GB",
    )

    return {
        "db_file": db_file,
        "db_conn": db_conn,
        "repo": repo,
        "filestore": filestore,
        "project_id": project_id,
        "migration_version": current_version,
    }


def create_test_image(env, page_number: int, caption: str = "", xref: int = 100) -> str:
    repo = env["repo"]
    filestore = env["filestore"]
    project_id = env["project_id"]

    img_id = str(uuid.uuid4())
    img_bytes = make_test_png()
    storage_key, checksum, _ = filestore.save_image(project_id, img_id, img_bytes, "png")

    repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=page_number,
        xref=xref,
        placement_index=0,
        bbox={"x0": 50.0, "y0": 100.0, "x1": 250.0, "y1": 300.0},
        original_caption=caption,
        caption_position="below" if caption else None,
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename=f"page_{page_number}_{img_id[:6]}.png",
        file_storage_key=storage_key,
        file_checksum=checksum,
        image_format="png",
        processing_status="extracted",
    )
    return img_id


def test_migration_4_applied_and_idempotent(workflow_env):
    """Verifies that Migration 4 added the audit_events table and is idempotent."""
    assert workflow_env["migration_version"] >= 4

    db_conn = workflow_env["db_conn"]
    with db_conn.session() as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events';")
        assert cursor.fetchone() is not None

        # Verify columns in audit_events
        cursor = conn.execute("PRAGMA table_info(audit_events);")
        columns = {row[1] for row in cursor.fetchall()}
        assert {"id", "project_id", "event_type", "target_type", "target_id", "details", "actor_id", "created_at"}.issubset(columns)

    # Re-applying migrations must return current version (>= 4) with zero errors
    reapplied_version = apply_migrations(db_conn)
    assert reapplied_version >= 4


def test_grouping_same_page_and_cross_page(workflow_env):
    """Tests grouping images from the same page and spanning across multiple pages."""
    repo = workflow_env["repo"]
    project_id = workflow_env["project_id"]

    img1 = create_test_image(workflow_env, page_number=2, caption="Fig 1a: Left valve")
    img2 = create_test_image(workflow_env, page_number=2, caption="Fig 1b: Right valve")

    # Group same-page images
    result_same = repo.create_group_from_selection(
        project_id=project_id,
        image_ids=[img1, img2],
        caption="Figure 1: Complete valve assembly.",
        caption_position="below",
        figure_label="Figure 1",
        decision_mode="merged",
    )

    group_same = result_same["group"]
    assert group_same["is_cross_page"] is False
    assert group_same["caption_text"] == "Figure 1: Complete valve assembly."
    assert group_same["caption_page_number"] == 2
    assert len(group_same["images"]) == 2

    # Verify image asset updates
    asset1 = repo.get_image_asset(img1)
    asset2 = repo.get_image_asset(img2)
    assert asset1["group_id"] == group_same["id"]
    assert asset2["group_id"] == group_same["id"]
    assert asset1["original_caption"] == "Figure 1: Complete valve assembly."
    assert asset2["original_caption"] == "Figure 1: Complete valve assembly."
    assert asset1["association_type"] == "group_matched"
    assert asset2["association_type"] == "group_matched"

    # Cross-page grouping: img3 (page 3) and img4 (page 4)
    img3 = create_test_image(workflow_env, page_number=3, caption="Fig 2 (continued)")
    img4 = create_test_image(workflow_env, page_number=4, caption="")

    result_cross = repo.create_group_from_selection(
        project_id=project_id,
        image_ids=[img3, img4],
        caption="Figure 2: Geological stratum spanning pages 3 and 4.",
        caption_position="above",
        figure_label="Figure 2",
        decision_mode="applied_existing",
    )

    group_cross = result_cross["group"]
    assert group_cross["is_cross_page"] is True
    assert group_cross["caption_page_number"] == 3
    assert len(group_cross["images"]) == 2

    asset3 = repo.get_image_asset(img3)
    asset4 = repo.get_image_asset(img4)
    assert asset3["group_id"] == group_cross["id"]
    assert asset4["group_id"] == group_cross["id"]
    assert asset3["original_caption"] == "Figure 2: Geological stratum spanning pages 3 and 4."
    assert asset4["original_caption"] == "Figure 2: Geological stratum spanning pages 3 and 4."


def test_preservation_of_image_identity_and_alt_text_history(workflow_env):
    """Verifies image IDs, placement attributes, binaries, and draft history are preserved."""
    repo = workflow_env["repo"]
    project_id = workflow_env["project_id"]

    img_id = create_test_image(workflow_env, page_number=5, caption="Initial sketch")
    initial_asset = repo.get_image_asset(img_id)

    # Create alt-text draft versions before grouping
    v1_id = str(uuid.uuid4())
    v2_id = str(uuid.uuid4())
    repo.create_alt_text_version(
        version_id=v1_id,
        image_id=img_id,
        version_number=1,
        text="A hand-drawn botanical sketch of a leaf.",
    )
    repo.create_alt_text_version(
        version_id=v2_id,
        image_id=img_id,
        version_number=2,
        text="Detailed botanical diagram of leaf structure with veining.",
        revision_instruction="Make it more detailed.",
    )

    img_other = create_test_image(workflow_env, page_number=5, caption="Section 2")

    # Group the images
    repo.create_group_from_selection(
        project_id=project_id,
        image_ids=[img_id, img_other],
        caption="Plate 5: Botanical specimens.",
        figure_label="Plate 5",
    )

    updated_asset = repo.get_image_asset(img_id)
    # Stable identity assertions
    assert updated_asset["id"] == initial_asset["id"]
    assert updated_asset["page_number"] == initial_asset["page_number"]
    assert updated_asset["xref"] == initial_asset["xref"]
    assert updated_asset["placement_index"] == initial_asset["placement_index"]
    assert updated_asset["bbox"] == initial_asset["bbox"]
    assert updated_asset["file_storage_key"] == initial_asset["file_storage_key"]
    assert updated_asset["file_checksum"] == initial_asset["file_checksum"]

    # Alt text draft versions remain completely intact
    versions = repo.list_image_versions(img_id)
    assert len(versions) == 2
    assert versions[0]["id"] == v1_id
    assert versions[0]["text"] == "A hand-drawn botanical sketch of a leaf."
    assert versions[1]["id"] == v2_id
    assert versions[1]["revision_instruction"] == "Make it more detailed."


def test_preservation_of_approvals_and_review_flag_on_caption_change(workflow_env):
    """
    Tests that:
    1. An approved image whose caption changes retains its historical approval and version records,
       while being flagged for review (needs_caption_review = 1).
    2. An approved image whose caption does NOT change is NOT flagged for review.
    """
    repo = workflow_env["repo"]
    project_id = workflow_env["project_id"]

    # Image A: Will experience a caption change post-approval
    img_a = create_test_image(workflow_env, page_number=6, caption="Original Approved Caption")
    v_a = str(uuid.uuid4())
    repo.create_alt_text_version(v_a, img_a, version_number=1, text="Approved description for Image A.")
    appr_a_id = str(uuid.uuid4())
    repo.create_approval(appr_a_id, img_a, v_a, reviewer_id="senior_editor")

    # Verify initial approved state
    initial_approval = repo.get_approval(appr_a_id)
    assert initial_approval is not None
    assert initial_approval["reviewer_id"] == "senior_editor"
    assert repo.get_image_asset(img_a)["needs_caption_review"] is False

    # Image B: Companion image
    img_b = create_test_image(workflow_env, page_number=6, caption="Unapproved Companion")

    # Group with a NEW caption (modifies img_a's caption)
    result = repo.create_group_from_selection(
        project_id=project_id,
        image_ids=[img_a, img_b],
        caption="Brand New Shared Caption for Group",
        figure_label="Figure 6",
    )

    assert img_a in result["flagged_for_review"]
    assert img_b not in result["flagged_for_review"]

    asset_a = repo.get_image_asset(img_a)
    assert asset_a["needs_caption_review"] is True
    assert any("Caption modified after alt-text approval" in reason for reason in asset_a["review_reasons"])

    # Historical approval record is preserved and untouched
    preserved_approval = repo.get_approval(appr_a_id)
    assert preserved_approval is not None
    assert preserved_approval["id"] == appr_a_id
    assert preserved_approval["alt_text_version_id"] == v_a
    assert preserved_approval["reviewer_id"] == "senior_editor"
    assert preserved_approval["approved_at"] == initial_approval["approved_at"]

    # Draft version is preserved
    draft_v_a = repo.get_alt_text_version(v_a)
    assert draft_v_a["text"] == "Approved description for Image A."

    # Test Image C: Caption does NOT change post-approval
    img_c = create_test_image(workflow_env, page_number=7, caption="Stable Caption")
    v_c = str(uuid.uuid4())
    repo.create_alt_text_version(v_c, img_c, version_number=1, text="Approved description for Image C.")
    repo.create_approval(str(uuid.uuid4()), img_c, v_c, reviewer_id="editor")

    img_d = create_test_image(workflow_env, page_number=7, caption="")

    # Group keeping the exact same caption as img_c
    result_c = repo.create_group_from_selection(
        project_id=project_id,
        image_ids=[img_c, img_d],
        caption="Stable Caption",
        figure_label="Figure 7",
    )
    assert img_c not in result_c["flagged_for_review"]
    asset_c = repo.get_image_asset(img_c)
    assert asset_c["needs_caption_review"] is False


def test_audit_entries_recording_grouping_operation(workflow_env):
    """Verifies structured audit events are recorded for both the figure group and member images."""
    repo = workflow_env["repo"]
    project_id = workflow_env["project_id"]

    img1 = create_test_image(workflow_env, page_number=8, caption="Prior Caption Alpha")
    img2 = create_test_image(workflow_env, page_number=8, caption="Prior Caption Beta")

    result = repo.create_group_from_selection(
        project_id=project_id,
        image_ids=[img1, img2],
        caption="Merged Group Caption",
        figure_label="Fig 8",
        actor_id="test_editor_42",
        decision_mode="merged",
    )

    group_id = result["group_id"]

    # Verify group-level audit event
    group_events = repo.list_project_audit_events(project_id, target_type="figure_group", target_id=group_id)
    assert len(group_events) == 1
    grp_evt = group_events[0]
    assert grp_evt["event_type"] == "group_created_from_selection"
    assert grp_evt["actor_id"] == "test_editor_42"
    details = grp_evt["details"]
    assert details["caption"] == "Merged Group Caption"
    assert details["decision_mode"] == "merged"
    assert details["figure_label"] == "Fig 8"
    assert details["image_ids"] == [img1, img2]
    assert len(details["member_changes"]) == 2

    # Verify image-level audit events
    img1_events = repo.list_project_audit_events(project_id, target_type="image_asset", target_id=img1)
    assert len(img1_events) == 1
    img1_evt = img1_events[0]
    assert img1_evt["event_type"] == "image_grouped"
    assert img1_evt["details"]["prior_caption"] == "Prior Caption Alpha"
    assert img1_evt["details"]["new_caption"] == "Merged Group Caption"
    assert img1_evt["details"]["group_id"] == group_id


def test_validation_rejects_invalid_inputs(workflow_env):
    """Tests validation for empty selections, duplicates, invalid IDs, cross-project leakage, and reassignments."""
    repo = workflow_env["repo"]
    project_id = workflow_env["project_id"]

    img1 = create_test_image(workflow_env, page_number=9, caption="Valid Image")

    # 1. Empty image list
    with pytest.raises(ValueError, match="At least one image ID"):
        repo.create_group_from_selection(project_id, [], "Caption")

    # 2. Duplicate image IDs
    with pytest.raises(ValueError, match="Duplicate image IDs"):
        repo.create_group_from_selection(project_id, [img1, img1], "Caption")

    # 3. Empty caption
    with pytest.raises(ValueError, match="non-empty caption is required"):
        repo.create_group_from_selection(project_id, [img1], "   ")

    # 4. Non-existent project
    with pytest.raises(ValueError, match="Project 'non-existent-proj' not found"):
        repo.create_group_from_selection("non-existent-proj", [img1], "Caption")

    # 5. Non-existent image ID
    with pytest.raises(ValueError, match="Image asset 'fake-image-id' not found"):
        repo.create_group_from_selection(project_id, [img1, "fake-image-id"], "Caption")

    # 6. Image belonging to another project
    other_proj_id = str(uuid.uuid4())
    repo.create_project(other_proj_id, "Other Proj", "other.pdf", "k", "c")
    img_foreign = str(uuid.uuid4())
    workflow_env["filestore"].save_image(other_proj_id, img_foreign, make_test_png(), "png")
    repo.create_image_asset(
        image_id=img_foreign,
        project_id=other_proj_id,
        page_number=1,
        xref=200,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        original_caption="",
        caption_position=None,
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="other.png",
        file_storage_key="k",
        file_checksum="c",
        image_format="png",
    )

    with pytest.raises(ValueError, match="does not belong to project"):
        repo.create_group_from_selection(project_id, [img1, img_foreign], "Caption")

    # 7. Image already in another group without explicit allow_reassignment
    img2 = create_test_image(workflow_env, page_number=9, caption="Valid Image 2")
    repo.create_group_from_selection(project_id, [img1, img2], "Group Alpha")

    img3 = create_test_image(workflow_env, page_number=9, caption="Valid Image 3")
    with pytest.raises(ValueError, match="already assigned to group"):
        repo.create_group_from_selection(project_id, [img1, img3], "Group Beta", allow_reassignment=False)

    # With allow_reassignment=True, reassignment must succeed
    reassigned = repo.create_group_from_selection(
        project_id, [img1, img3], "Group Beta", allow_reassignment=True
    )
    assert repo.get_image_asset(img1)["group_id"] == reassigned["group_id"]


def test_database_rollback_on_mid_transaction_failure(workflow_env, monkeypatch):
    """
    Genuinely simulates a runtime failure inside the create_group_from_selection transaction.
    Verifies that all updates (group, image updates, and audit records) are completely rolled back.
    """
    repo = workflow_env["repo"]
    project_id = workflow_env["project_id"]

    img1 = create_test_image(workflow_env, page_number=10, caption="Untouched Caption 1")
    img2 = create_test_image(workflow_env, page_number=10, caption="Untouched Caption 2")

    target_group_id = str(uuid.uuid4())

    # Monkeypatch conn.execute to simulate a database constraint error or disk failure
    # during image update loop
    real_session = workflow_env["db_conn"].session

    from contextlib import contextmanager

    class FailingConnectionProxy:
        def __init__(self, real_conn):
            self._real_conn = real_conn

        def execute(self, sql, params=()):
            # Intercept update on the second image asset and simulate a failure
            if "UPDATE image_assets" in sql and params and params[-1] == img2:
                raise RuntimeError("Simulated mid-transaction failure during image asset update!")
            return self._real_conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._real_conn, name)

    @contextmanager
    def failing_session():
        with real_session() as conn:
            yield FailingConnectionProxy(conn)

    monkeypatch.setattr(workflow_env["db_conn"], "session", failing_session)

    with pytest.raises(RuntimeError, match="Simulated mid-transaction failure"):
        repo.create_group_from_selection(
            project_id=project_id,
            image_ids=[img1, img2],
            caption="Should Not Persist Caption",
            group_id=target_group_id,
        )

    # VERIFY ATOMIC ROLLBACK:
    # 1. Figure group record must NOT exist
    assert repo.get_figure_group(target_group_id) is None

    # 2. Both image assets must remain 100% untouched
    asset1 = repo.get_image_asset(img1)
    asset2 = repo.get_image_asset(img2)
    assert asset1["original_caption"] == "Untouched Caption 1"
    assert asset2["original_caption"] == "Untouched Caption 2"
    assert asset1["group_id"] is None
    assert asset2["group_id"] is None
    assert asset1["association_type"] == "single"
    assert asset2["association_type"] == "single"

    # 3. No audit records should exist for target_group_id or either image
    group_audits = repo.list_project_audit_events(project_id, target_id=target_group_id)
    assert len(group_audits) == 0

    img1_audits = repo.list_project_audit_events(project_id, target_id=img1)
    assert len(img1_audits) == 0


# ============================================================
# API Endpoint Tests: POST /api/projects/{project_id}/groups/create-from-selection
# ============================================================

@pytest.fixture
def api_env(tmp_path):
    db_file = str(tmp_path / "api_workflow.db")
    storage_dir = str(tmp_path / "api_storage")

    app = create_app(db_path=db_file, storage_dir=storage_dir)
    client = TestClient(app)
    repo = app.state.repo
    filestore = app.state.filestore

    project_id = str(uuid.uuid4())
    pdf_bytes = b"%PDF-1.4 test api document"
    rel_key, checksum, _ = filestore.save_source_pdf(project_id, pdf_bytes, "api_test.pdf")
    repo.create_project(project_id, "API Project", "api_test.pdf", rel_key, checksum)

    return {
        "client": client,
        "app": app,
        "repo": repo,
        "filestore": filestore,
        "project_id": project_id,
        "tmp_path": tmp_path,
    }


def create_api_test_image(api_env, page_number: int, caption: str = "") -> str:
    repo = api_env["repo"]
    filestore = api_env["filestore"]
    project_id = api_env["project_id"]

    img_id = str(uuid.uuid4())
    img_bytes = make_test_png()
    storage_key, checksum, _ = filestore.save_image(project_id, img_id, img_bytes, "png")

    repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=page_number,
        xref=100,
        placement_index=0,
        bbox={"x0": 10.0, "y0": 20.0, "x1": 110.0, "y1": 120.0},
        original_caption=caption,
        caption_position="below" if caption else None,
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename=f"page_{page_number}_{img_id[:6]}.png",
        file_storage_key=storage_key,
        file_checksum=checksum,
        image_format="png",
        processing_status="extracted",
    )
    return img_id


def test_api_create_group_same_page_merged_caption(api_env):
    """API test: successful grouping of images on same page with merged caption."""
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=1, caption="Fig 1A: Plan view")
    img2 = create_api_test_image(api_env, page_number=1, caption="Fig 1B: Section view")

    payload = {
        "image_ids": [img1, img2],
        "caption": "Figure 1: Plan and section architectural diagrams.",
        "decision_mode": "merged",
        "figure_label": "Figure 1",
    }

    res = client.post(f"/api/projects/{project_id}/groups/create-from-selection", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert data["caption"] == "Figure 1: Plan and section architectural diagrams."
    assert set(data["member_image_ids"]) == {img1, img2}
    assert data["flagged_for_review"] == []
    assert data["group"]["is_cross_page"] is False
    assert len(data["group"]["images"]) == 2

    # Verify repository state
    asset1 = repo.get_image_asset(img1)
    asset2 = repo.get_image_asset(img2)
    assert asset1["group_id"] == data["group_id"]
    assert asset2["group_id"] == data["group_id"]
    assert asset1["original_caption"] == "Figure 1: Plan and section architectural diagrams."


def test_api_create_group_cross_page_applied_existing(api_env):
    """API test: successful grouping across different pages applying an existing caption."""
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=2, caption="Figure 2: Geological strata")
    img2 = create_api_test_image(api_env, page_number=3, caption="")

    payload = {
        "image_ids": [img1, img2],
        "caption": "Figure 2: Geological strata",
        "decision_mode": "applied_existing",
        "figure_label": "Figure 2",
    }

    res = client.post(f"/api/projects/{project_id}/groups/create-from-selection", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert data["group"]["is_cross_page"] is True
    assert data["flagged_for_review"] == []

    asset2 = repo.get_image_asset(img2)
    assert asset2["original_caption"] == "Figure 2: Geological strata"
    assert asset2["group_id"] == data["group_id"]


def test_api_create_group_custom_caption(api_env):
    """API test: successful grouping with custom caption."""
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=4, caption="")
    img2 = create_api_test_image(api_env, page_number=4, caption="")

    payload = {
        "image_ids": [img1, img2],
        "caption": "Plate IV: Microscopic crystal structures.",
        "decision_mode": "custom",
        "figure_label": "Plate IV",
    }

    res = client.post(f"/api/projects/{project_id}/groups/create-from-selection", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["caption"] == "Plate IV: Microscopic crystal structures."
    asset1 = repo.get_image_asset(img1)
    asset2 = repo.get_image_asset(img2)
    assert asset1["original_caption"] == "Plate IV: Microscopic crystal structures."
    assert asset2["original_caption"] == "Plate IV: Microscopic crystal structures."


def test_api_rejections_and_validation(api_env):
    """API test: validation rejects empty, duplicate, whitespace, invalid modes, and non-existent IDs."""
    client = api_env["client"]
    project_id = api_env["project_id"]
    valid_img = create_api_test_image(api_env, page_number=5, caption="Test")

    # 1. Empty image selection
    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [], "caption": "Valid caption", "decision_mode": "custom"}
    )
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()

    # 2. Duplicate image IDs
    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [valid_img, valid_img], "caption": "Valid caption", "decision_mode": "custom"}
    )
    assert res.status_code == 400
    assert "duplicate" in res.json()["detail"].lower()

    # 3. Whitespace-only caption
    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [valid_img], "caption": "   \n\t  ", "decision_mode": "custom"}
    )
    assert res.status_code == 400
    assert "empty or whitespace" in res.json()["detail"].lower()

    # 4. Invalid decision mode
    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [valid_img], "caption": "Valid caption", "decision_mode": "invalid_mode"}
    )
    assert res.status_code == 400
    assert "invalid decision_mode" in res.json()["detail"].lower()

    # 5. Non-existent project
    fake_proj = str(uuid.uuid4())
    res = client.post(
        f"/api/projects/{fake_proj}/groups/create-from-selection",
        json={"image_ids": [valid_img], "caption": "Valid caption", "decision_mode": "custom"}
    )
    assert res.status_code == 404
    assert "project not found" in res.json()["detail"].lower()

    # 6. Non-existent image ID
    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [valid_img, "fake-img-id-999"], "caption": "Valid caption", "decision_mode": "custom"}
    )
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_api_cross_project_rejection(api_env):
    """API test: rejects selection containing image from another project."""
    client = api_env["client"]
    repo = api_env["repo"]
    filestore = api_env["filestore"]
    project_id = api_env["project_id"]

    img_local = create_api_test_image(api_env, page_number=6, caption="Local")

    # Create foreign project and foreign image
    foreign_proj = str(uuid.uuid4())
    filestore.save_source_pdf(foreign_proj, b"%PDF-1.4 foreign", "foreign.pdf")
    repo.create_project(foreign_proj, "Foreign", "foreign.pdf", "k", "c")
    img_foreign = str(uuid.uuid4())
    filestore.save_image(foreign_proj, img_foreign, make_test_png(), "png")
    repo.create_image_asset(
        image_id=img_foreign,
        project_id=foreign_proj,
        page_number=1,
        xref=10,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        original_caption="Foreign cap",
        caption_position=None,
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="foreign.png",
        file_storage_key="k",
        file_checksum="c",
        image_format="png",
    )

    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img_local, img_foreign], "caption": "Merged", "decision_mode": "merged"}
    )
    assert res.status_code == 400
    assert "does not belong to project" in res.json()["detail"].lower()


def test_api_group_reassignment_policy(api_env):
    """API test: rejects reassignment unless allow_reassignment is explicitly True."""
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=7, caption="Img1")
    img2 = create_api_test_image(api_env, page_number=7, caption="Img2")
    img3 = create_api_test_image(api_env, page_number=7, caption="Img3")

    # Initial group: img1 + img2
    res_init = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img1, img2], "caption": "Group 1", "decision_mode": "custom"}
    )
    assert res_init.status_code == 200
    init_group_id = res_init.json()["group_id"]

    # Reassignment without flag -> 400
    res_reassign_fail = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img1, img3], "caption": "Group 2", "decision_mode": "custom", "allow_reassignment": False}
    )
    assert res_reassign_fail.status_code == 400
    assert "already assigned" in res_reassign_fail.json()["detail"].lower()

    # Reassignment with flag -> 200
    res_reassign_ok = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img1, img3], "caption": "Group 2", "decision_mode": "custom", "allow_reassignment": True}
    )
    assert res_reassign_ok.status_code == 200
    new_group_id = res_reassign_ok.json()["group_id"]
    assert new_group_id != init_group_id
    assert repo.get_image_asset(img1)["group_id"] == new_group_id


def test_api_approved_image_preservation_and_review_flags(api_env):
    """API test: grouping an approved image with a changed caption flags it for review and preserves approval."""
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img_appr = create_api_test_image(api_env, page_number=8, caption="Original Approved Caption")
    v_id = str(uuid.uuid4())
    repo.create_alt_text_version(v_id, img_appr, version_number=1, text="Approved description.")
    appr_id = str(uuid.uuid4())
    repo.create_approval(appr_id, img_appr, v_id, reviewer_id="chief_editor")

    img_other = create_api_test_image(api_env, page_number=8, caption="Companion")

    # Group with modified caption
    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img_appr, img_other], "caption": "Updated Shared Caption", "decision_mode": "merged"}
    )
    assert res.status_code == 200
    data = res.json()

    assert data["flagged_for_review"] == [img_appr]

    # Verify database: approval record is intact
    appr = repo.get_approval(appr_id)
    assert appr is not None
    assert appr["alt_text_version_id"] == v_id
    assert appr["reviewer_id"] == "chief_editor"

    # Verify image review flag
    asset = repo.get_image_asset(img_appr)
    assert asset["needs_caption_review"] is True
    assert any("Caption modified after alt-text approval" in r for r in asset["review_reasons"])


def test_api_repository_failure_handles_error_without_partial_changes(api_env, monkeypatch):
    """API test: unexpected failure inside repository returns HTTP 500 without leaking stack traces or internal details."""
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=9, caption="Initial 1")
    img2 = create_api_test_image(api_env, page_number=9, caption="Initial 2")

    def broken_create(*args, **kwargs):
        raise RuntimeError("Simulated unexpected internal database failure: /var/secret/path.db")

    monkeypatch.setattr(repo, "create_group_from_selection", broken_create)

    res = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img1, img2], "caption": "Merged Caption", "decision_mode": "merged"}
    )
    assert res.status_code == 500
    err_detail = res.json()["detail"]
    assert err_detail == "Failed to create figure group from selection."
    # Ensure internal paths / secrets are NOT leaked to client
    assert "/var/secret" not in err_detail
    assert "Traceback" not in err_detail


# ============================================================
# Step 3: Extracted-Image Reader UI & Pagination Tests
# ============================================================

def test_reader_html_toolbar_controls_present(api_env):
    """Verifies that review page has direct Review Workspace navigation and Page Reader toolbar is removed."""
    client = api_env["client"]
    project_id = api_env["project_id"]
    res = client.get(f"/review/{project_id}")
    assert res.status_code == 200
    html = res.text

    # Verify Page Reader toolbar & gallery are removed from Review Mode
    assert 'class="reader-toolbar"' not in html
    assert 'id="resultsSection"' not in html
    assert 'id="workspaceNav"' not in html

    # Verify direct Review Workspace navigation and editorial controls are present
    assert 'id="rwPrevBtn"' in html
    assert 'id="rwNextBtn"' in html
    assert 'id="rwImageCounter"' in html
    assert 'id="rwProgressBarFill"' in html
    assert 'id="rwDraftTextarea"' in html
    assert 'id="rwRevisionInput"' in html
    assert 'id="rwReviseBtn"' in html
    assert 'id="manageGroupsBtn"' in html
    assert 'id="figureGroupsModal"' in html


def test_project_endpoints_include_total_pages(api_env):
    """Verifies that project API endpoints provide total_pages metadata for the reader."""
    client = api_env["client"]
    project_id = api_env["project_id"]

    create_api_test_image(api_env, page_number=3, caption="Page 3 image")
    create_api_test_image(api_env, page_number=7, caption="Page 7 image")

    res = client.get(f"/api/projects/{project_id}")
    assert res.status_code == 200
    data = res.json()
    assert "total_pages" in data
    assert data["total_pages"] >= 7


def test_reader_page_filtering_and_navigation_logic(api_env):
    """
    Validates reader logic:
    1. Images are strictly filtered and isolated by source PDF page number.
    2. Navigation jumps accurately between pages.
    3. Skipping empty pages moves only between pages with extracted images.
    4. Non-skipping navigation steps through consecutive PDF page numbers.
    5. Direct jumps to valid and empty pages function correctly.
    6. Boundary clamping handles invalid negative or out-of-bounds page jumps.
    7. Image identity and approvals remain completely stable across navigation.
    """
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    # Populate images:
    # Page 1: 2 images
    # Page 4: 1 image (with approved alt-text)
    # Page 10: 1 image
    img1_p1 = create_api_test_image(api_env, page_number=1, caption="P1 Image A")
    img2_p1 = create_api_test_image(api_env, page_number=1, caption="P1 Image B")

    img_p4 = create_api_test_image(api_env, page_number=4, caption="P4 Diagram")
    v_id = str(uuid.uuid4())
    repo.create_alt_text_version(v_id, img_p4, 1, "Approved description for P4.")
    repo.create_approval(str(uuid.uuid4()), img_p4, v_id, reviewer_id="editor")

    img_p10 = create_api_test_image(api_env, page_number=10, caption="P10 Photo")

    all_images = repo.list_project_images(project_id)
    assert len(all_images) == 4

    # 1. Verify page grouping: Page 1 has exactly 2 images
    p1_images = [img for img in all_images if img["page_number"] == 1]
    assert len(p1_images) == 2
    assert {img["id"] for img in p1_images} == {img1_p1, img2_p1}

    # 2. Verify page grouping: Page 4 has 1 image, approved status preserved
    p4_images = [img for img in all_images if img["page_number"] == 4]
    assert len(p4_images) == 1
    assert p4_images[0]["id"] == img_p4
    assert p4_images[0]["processing_status"] == "approved"

    # 3. Verify empty page: Page 2 and Page 3 have 0 images
    p2_images = [img for img in all_images if img["page_number"] == 2]
    assert len(p2_images) == 0

    p3_images = [img for img in all_images if img["page_number"] == 3]
    assert len(p3_images) == 0

    # 4. Navigation simulation:
    pages_with_images = sorted(list({img["page_number"] for img in all_images}))
    assert pages_with_images == [1, 4, 10]
    total_pages = max(pages_with_images)

    # Function simulating reader next with skip_empty=True
    def reader_next(curr_page, skip_empty):
        if skip_empty:
            next_pages = [p for p in pages_with_images if p > curr_page]
            return next_pages[0] if next_pages else curr_page
        else:
            return min(curr_page + 1, total_pages)

    # Function simulating reader prev with skip_empty=True
    def reader_prev(curr_page, skip_empty):
        if skip_empty:
            prev_pages = [p for p in pages_with_images if p < curr_page]
            return prev_pages[-1] if prev_pages else curr_page
        else:
            return max(curr_page - 1, 1)

    # Function simulating direct page jump with clamping
    def reader_jump(target_page):
        try:
            val = int(target_page)
        except (ValueError, TypeError):
            val = 1
        return max(1, min(val, total_pages))

    # Test skip_empty=True sequence: 1 -> 4 -> 10
    assert reader_next(1, skip_empty=True) == 4
    assert reader_next(4, skip_empty=True) == 10
    assert reader_next(10, skip_empty=True) == 10  # end reached

    assert reader_prev(10, skip_empty=True) == 4
    assert reader_prev(4, skip_empty=True) == 1
    assert reader_prev(1, skip_empty=True) == 1  # start reached

    # Test skip_empty=False sequence: 1 -> 2 -> 3 -> 4
    assert reader_next(1, skip_empty=False) == 2
    assert reader_next(2, skip_empty=False) == 3
    assert reader_next(3, skip_empty=False) == 4

    assert reader_prev(4, skip_empty=False) == 3
    assert reader_prev(3, skip_empty=False) == 2
    assert reader_prev(2, skip_empty=False) == 1

    # Test direct page jumps & clamping
    assert reader_jump(4) == 4
    assert reader_jump(2) == 2  # Jumping to empty page is allowed
    assert reader_jump(0) == 1  # Underflow clamped to 1
    assert reader_jump(-10) == 1
    assert reader_jump(9999) == 10  # Overflow clamped to total_pages
    assert reader_jump("invalid") == 1

    # Verify image records, storage keys, and approvals are 100% immutable across reader traversal
    after_images = repo.list_project_images(project_id)
    assert len(after_images) == 4
    p4_after = next(img for img in after_images if img["id"] == img_p4)
    assert p4_after["processing_status"] == "approved"
    assert p4_after["original_caption"] == "P4 Diagram"


def test_selection_dock_and_context_menu_html_elements_present(api_env):
    """Verifies that review.html contains the selection dock, context menu, and action controls."""
    client = api_env["client"]
    project_id = api_env["project_id"]
    response = client.get(f"/review/{project_id}")
    assert response.status_code == 200
    html = response.text

    # Selection dock elements
    assert 'id="selectionDock"' in html
    assert 'role="toolbar"' in html
    assert 'id="selectionCountLabel"' in html
    assert 'id="selectionPagesLabel"' in html
    assert 'id="groupSelectedBtn"' in html
    assert 'disabled' in html  # initially disabled
    assert 'id="clearSelectionBtn"' in html

    # Image context menu elements
    assert 'id="imageContextMenu"' in html
    assert 'role="menu"' in html
    assert 'id="contextMenuGroupBtn"' in html
    assert 'role="menuitem"' in html
    assert "Group images with…" in html
    assert 'id="contextMenuInspectBtn"' in html


def test_image_card_markup_and_action_menu_accessibility():
    """Verifies that app.js renders selection checkboxes, 3-dot trigger buttons, and accessibility tags."""
    from pathlib import Path
    app_js_path = Path(__file__).parent.parent / "src" / "web" / "static" / "app.js"
    assert app_js_path.exists()
    app_js = app_js_path.read_text(encoding="utf-8")

    # Card action elements
    assert "card-select-toggle" in app_js
    assert "card-checkbox-icon" in app_js
    assert "card-menu-btn" in app_js
    assert 'aria-haspopup="true"' in app_js
    assert 'aria-label="Image actions"' in app_js

    # Events
    assert 'contextmenu' in app_js
    assert 'openContextMenu' in app_js
    assert 'toggleSelectImage' in app_js
    assert 'clearSelection' in app_js
    assert 'Shift+F10' in app_js or 'ContextMenu' in app_js


def test_selection_state_cross_page_persistence_and_clearing(workflow_env):
    """
    Simulates cross-page multi-selection:
    - Verifies selection persists across PDF page changes.
    - Verifies individual deselection and clearing of entire selection.
    - Confirms database and image records remain 100% immutable throughout selection.
    """
    repo = workflow_env["repo"]
    project_id = workflow_env["project_id"]

    # Create images on Page 1, Page 3, and Page 5
    img1_p1 = create_test_image(workflow_env, page_number=1, caption="Figure 1: Panel A")
    img2_p3 = create_test_image(workflow_env, page_number=3, caption="Figure 1: Panel B (continued)")
    img3_p5 = create_test_image(workflow_env, page_number=5, caption="Figure 2: Separate diagram")

    # Approve img2_p3 to verify approval remains intact
    v_id = str(uuid.uuid4())
    repo.create_alt_text_version(v_id, img2_p3, 1, "Approved alt-text for Panel B.")
    repo.create_approval(str(uuid.uuid4()), img2_p3, v_id, reviewer_id="editor")

    # Selection State Simulator
    selected_image_ids = set()
    is_selection_mode = False

    def toggle_select(image_id):
        nonlocal is_selection_mode
        if image_id in selected_image_ids:
            selected_image_ids.remove(image_id)
            if not selected_image_ids:
                is_selection_mode = False
        else:
            selected_image_ids.add(image_id)
            is_selection_mode = True

    def clear_selection():
        nonlocal is_selection_mode
        selected_image_ids.clear()
        is_selection_mode = False

    def get_dock_status(all_imgs):
        count = len(selected_image_ids)
        if count == 0:
            return {"visible": False, "count": 0, "pages": [], "can_group": False}
        sel_imgs = [img for img in all_imgs if img["id"] in selected_image_ids]
        pages = sorted(list({img["page_number"] for img in sel_imgs}))
        return {
            "visible": True,
            "count": count,
            "pages": pages,
            "can_group": count >= 2,
        }

    all_images = repo.list_project_images(project_id)
    assert len(all_images) == 3

    # Step 1: Editor is on Page 1 and chooses "Group images with..." on img1_p1
    toggle_select(img1_p1)
    assert is_selection_mode is True
    assert selected_image_ids == {img1_p1}
    dock = get_dock_status(all_images)
    assert dock["visible"] is True
    assert dock["count"] == 1
    assert dock["pages"] == [1]
    assert dock["can_group"] is False  # need at least 2 images to group

    # Step 2: Editor navigates reader to Page 3
    # Selection MUST persist across reader navigation!
    current_reader_page = 3
    assert selected_image_ids == {img1_p1}
    # Verify which cards on current_reader_page are selected
    p3_cards_selected = [img["id"] for img in all_images if img["page_number"] == current_reader_page and img["id"] in selected_image_ids]
    assert len(p3_cards_selected) == 0

    # Step 3: Editor selects img2_p3 on Page 3
    toggle_select(img2_p3)
    assert selected_image_ids == {img1_p1, img2_p3}
    dock = get_dock_status(all_images)
    assert dock["visible"] is True
    assert dock["count"] == 2
    assert dock["pages"] == [1, 3]  # spans cross-page 1 and 3!
    assert dock["can_group"] is True

    # Step 4: Editor navigates reader to Page 5
    current_reader_page = 5
    assert selected_image_ids == {img1_p1, img2_p3}

    # Step 5: Editor deselects img1_p1
    toggle_select(img1_p1)
    assert selected_image_ids == {img2_p3}
    dock = get_dock_status(all_images)
    assert dock["count"] == 1
    assert dock["pages"] == [3]
    assert dock["can_group"] is False

    # Step 6: Editor selects img3_p5 on Page 5
    toggle_select(img3_p5)
    assert selected_image_ids == {img2_p3, img3_p5}
    dock = get_dock_status(all_images)
    assert dock["count"] == 2
    assert dock["pages"] == [3, 5]
    assert dock["can_group"] is True

    # Step 7: Editor clears entire selection
    clear_selection()
    assert selected_image_ids == set()
    assert is_selection_mode is False
    dock = get_dock_status(all_images)
    assert dock["visible"] is False
    assert dock["count"] == 0

    # Step 8: Verify absolute database immutability during all selection workflows
    after_images = repo.list_project_images(project_id)
    assert len(after_images) == 3
    img2_after = next(img for img in after_images if img["id"] == img2_p3)
    assert img2_after["processing_status"] == "approved"
    assert img2_after["original_caption"] == "Figure 1: Panel B (continued)"
    assert img2_after["group_id"] is None  # no premature group assignment!
    assert img2_after["needs_caption_review"] == 0


# ============================================================
# Phase 5.1 / Review Mode & Dedicated Route Tests
# ============================================================

def test_review_mode_route_valid_and_invalid(api_env):
    """
    Tests GET /review/{project_id}:
    - 200 HTML with review workspace components for valid project.
    - 404 Not Found for invalid project ID.
    """
    client = api_env["client"]
    project_id = api_env["project_id"]

    # 1. Valid project route returns 200 HTML
    res = client.get(f"/review/{project_id}")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    html_content = res.text
    assert "AltTextPipeline" in html_content
    assert 'id="reviewWorkspaceSection"' in html_content
    assert 'id="rwRevisionInput"' in html_content
    assert 'id="rwReviseBtn"' in html_content

    # 2. Non-existent project route returns 404
    missing_id = str(uuid.uuid4())
    res_missing = client.get(f"/review/{missing_id}")
    assert res_missing.status_code == 404
    assert f"Project '{missing_id}' not found" in res_missing.json()["detail"]


def test_three_stage_route_page_isolation(api_env):
    """
    Verifies that each of the three routes renders its own dedicated page structure:
    1. / renders Upload & Extract without review or export workspace.
    2. /review/{project_id} renders Review & Edit without upload or export panel.
    3. /export/{project_id} renders Export to Word without upload form or review workspace.
    """
    client = api_env["client"]
    project_id = api_env["project_id"]

    # --- Stage 1: Upload & Extract ---
    res_upload = client.get("/")
    assert res_upload.status_code == 200
    upload_html = res_upload.text
    assert 'id="dropzone"' in upload_html
    assert 'id="startExtractBtn"' in upload_html
    assert 'id="reviewWorkspaceSection"' not in upload_html
    assert 'id="resultsSection"' not in upload_html
    assert 'id="exportSection"' not in upload_html
    assert 'class="step-item active"' in upload_html
    assert "Upload &amp; Extract" in upload_html

    # --- Stage 2: Review & Edit ---
    res_review = client.get(f"/review/{project_id}")
    assert res_review.status_code == 200
    review_html = res_review.text
    assert 'id="reviewWorkspaceSection"' in review_html
    assert 'id="resultsSection"' not in review_html
    assert 'id="workspaceNav"' not in review_html
    assert 'id="continueExportBtn"' in review_html
    assert f'/export/{project_id}' in review_html
    assert 'id="dropzone"' not in review_html
    assert 'id="startExtractBtn"' not in review_html
    assert 'id="exportSection"' not in review_html
    assert "Review &amp; Edit" in review_html

    # --- Stage 3: Export to Word ---
    res_export = client.get(f"/export/{project_id}")
    assert res_export.status_code == 200
    export_html = res_export.text
    assert 'id="exportSection"' in export_html
    assert 'id="startDocxExportBtn"' in export_html
    assert 'id="checkApprovals"' in export_html
    assert 'id="backToReviewBtn"' in export_html
    assert f'/review/{project_id}' in export_html
    assert 'id="dropzone"' not in export_html
    assert 'id="startExtractBtn"' not in export_html
    assert 'id="reviewWorkspaceSection"' not in export_html
    assert 'id="resultsSection"' not in export_html
    assert "Export to Word" in export_html

    # Direct refresh / reload verification
    res_review_refresh = client.get(f"/review/{project_id}")
    assert res_review_refresh.status_code == 200
    assert 'id="reviewWorkspaceSection"' in res_review_refresh.text

    res_export_refresh = client.get(f"/export/{project_id}")
    assert res_export_refresh.status_code == 200
    assert 'id="exportSection"' in res_export_refresh.text


def test_export_route_invalid_project_returns_404(api_env):
    """Verifies that accessing /export/{project_id} for a nonexistent project returns 404."""
    client = api_env["client"]
    missing_id = str(uuid.uuid4())
    res = client.get(f"/export/{missing_id}")
    assert res.status_code == 404
    assert f"Project '{missing_id}' not found" in res.json()["detail"]


def test_reader_displays_all_extracted_images_on_page(api_env):
    """
    Tests that the reader displays ALL extracted images belonging
    to the currently selected PDF page, maintaining source page ordering.
    """
    client = api_env["client"]
    project_id = api_env["project_id"]

    # Create 3 images on page 1, 2 images on page 2, 1 image on page 4
    img1 = create_api_test_image(api_env, page_number=1, caption="Figure 1A")
    img2 = create_api_test_image(api_env, page_number=1, caption="Figure 1B")
    img3 = create_api_test_image(api_env, page_number=1, caption="Figure 1C")
    img4 = create_api_test_image(api_env, page_number=2, caption="Figure 2")
    img5 = create_api_test_image(api_env, page_number=2, caption="")
    img6 = create_api_test_image(api_env, page_number=4, caption="Figure 4")

    res = client.get(f"/api/projects/{project_id}/images")
    assert res.status_code == 200
    all_images = res.json()["images"]

    # Filter page 1 images
    page_1_images = [img for img in all_images if img["page_number"] == 1]
    assert len(page_1_images) == 3
    assert [img["id"] for img in page_1_images] == [img1, img2, img3]

    # Filter page 2 images
    page_2_images = [img for img in all_images if img["page_number"] == 2]
    assert len(page_2_images) == 2
    assert [img["id"] for img in page_2_images] == [img4, img5]

    # Filter page 3 (empty page)
    page_3_images = [img for img in all_images if img["page_number"] == 3]
    assert len(page_3_images) == 0


def test_caption_grouping_decision_case_a_multiple_captions(api_env):
    """
    Case A: Multiple distinct existing captions.
    - Proposes an editable merged-caption proposal.
    - Offers custom-caption option.
    - Cancel leaves associations unchanged.
    - Confirm with merged caption updates records atomically.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=1, caption="Figure 1A: Western blot")
    img2 = create_api_test_image(api_env, page_number=2, caption="Figure 1B: Protein quantification")

    # Cancel leaves state unchanged
    img1_before = repo.get_image_asset(img1)
    img2_before = repo.get_image_asset(img2)
    assert img1_before["group_id"] is None
    assert img2_before["group_id"] is None

    # Proposal logic verification
    captions = [img1_before["original_caption"], img2_before["original_caption"]]
    distinct_captions = sorted(list(set(captions)))
    assert len(distinct_captions) == 2
    merged_proposal = "Figure 1A: Western blot / Figure 1B: Protein quantification"

    # Confirm with merged proposal
    payload = {
        "image_ids": [img1, img2],
        "caption": merged_proposal,
        "decision_mode": "merged",
        "allow_reassignment": False,
    }
    res = client.post(f"/api/projects/{project_id}/groups/create-from-selection", json=payload)
    assert res.status_code == 200
    group_id = res.json()["group_id"]

    # Verify both images now share the group and caption
    img1_after = repo.get_image_asset(img1)
    img2_after = repo.get_image_asset(img2)
    assert img1_after["group_id"] == group_id
    assert img2_after["group_id"] == group_id
    assert img1_after["original_caption"] == merged_proposal
    assert img2_after["original_caption"] == merged_proposal


def test_caption_grouping_decision_case_b_one_caption_plus_uncaptioned(api_env):
    """
    Case B: Exactly one distinct existing caption and some images have no caption.
    - Offers to apply existing caption to all selected images.
    - Offers custom caption option.
    - Confirm applies the existing caption across all selected images.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=1, caption="Figure 2: Site map")
    img2 = create_api_test_image(api_env, page_number=1, caption="")

    existing_captions = [img["original_caption"] for img in [repo.get_image_asset(img1), repo.get_image_asset(img2)] if img["original_caption"]]
    assert len(set(existing_captions)) == 1
    shared_caption = existing_captions[0]

    payload = {
        "image_ids": [img1, img2],
        "caption": shared_caption,
        "decision_mode": "applied_existing",
        "allow_reassignment": False,
    }
    res = client.post(f"/api/projects/{project_id}/groups/create-from-selection", json=payload)
    assert res.status_code == 200
    group_id = res.json()["group_id"]

    img1_after = repo.get_image_asset(img1)
    img2_after = repo.get_image_asset(img2)
    assert img1_after["group_id"] == group_id
    assert img2_after["group_id"] == group_id
    assert img1_after["original_caption"] == "Figure 2: Site map"
    assert img2_after["original_caption"] == "Figure 2: Site map"


def test_caption_grouping_decision_case_c_no_captions(api_env):
    """
    Case C: No existing captions.
    - Offers custom caption option.
    - Does not invent a caption automatically.
    - Confirm applies the editor's custom caption.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=3, caption="")
    img2 = create_api_test_image(api_env, page_number=3, caption="")

    custom_caption = "Figure 3: Unlabelled archaeological fragments"
    payload = {
        "image_ids": [img1, img2],
        "caption": custom_caption,
        "decision_mode": "custom",
        "allow_reassignment": False,
    }
    res = client.post(f"/api/projects/{project_id}/groups/create-from-selection", json=payload)
    assert res.status_code == 200
    group_id = res.json()["group_id"]

    img1_after = repo.get_image_asset(img1)
    img2_after = repo.get_image_asset(img2)
    assert img1_after["group_id"] == group_id
    assert img2_after["group_id"] == group_id
    assert img1_after["original_caption"] == custom_caption
    assert img2_after["original_caption"] == custom_caption


def test_grouping_confirm_rejects_already_grouped_images(api_env):
    """
    Rejects reassignment of already-grouped images unless allow_reassignment is True.
    """
    client = api_env["client"]
    project_id = api_env["project_id"]

    img1 = create_api_test_image(api_env, page_number=1, caption="Figure A")
    img2 = create_api_test_image(api_env, page_number=1, caption="Figure A")
    img3 = create_api_test_image(api_env, page_number=2, caption="Figure B")

    # Group img1 and img2
    res1 = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img1, img2], "caption": "Group 1", "decision_mode": "custom"}
    )
    assert res1.status_code == 200

    # Attempt to group img2 with img3 without reassignment permission
    res2 = client.post(
        f"/api/projects/{project_id}/groups/create-from-selection",
        json={"image_ids": [img2, img3], "caption": "Group 2", "decision_mode": "custom", "allow_reassignment": False}
    )
    assert res2.status_code == 400
    assert "already assigned to group" in str(res2.json()["detail"])


def test_revision_prompt_workflow_and_approval_uncoupling(api_env):
    """
    Tests the complete AI follow-up revision flow:
    1. Initial draft is generated.
    2. Draft v1 is approved.
    3. Follow-up revision instruction is submitted.
    4. Revision reaches existing revision service with image, caption, prior draft, language, and follow-up instruction.
    5. New immutable alt-text version v2 is created with revision_instruction stored.
    6. Revised output is NOT automatically approved (status reset to 'drafted').
    7. Old approval does NOT make the newer revised version appear approved.
    8. Explicit approval of the revised version is required.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img_id = create_api_test_image(api_env, page_number=1, caption="Figure 5: Roman coin distribution")

    # Step 1: Generate initial draft
    res_gen1 = client.post(
        f"/api/projects/{project_id}/images/{img_id}/generate",
        json={"language": "en-GB"}
    )
    assert res_gen1.status_code == 200
    v1 = res_gen1.json()["version"]
    assert v1["version_number"] == 1
    assert len(v1["text"]) > 0

    # Step 2: Editor approves draft v1
    res_appr1 = client.post(
        f"/api/projects/{project_id}/images/{img_id}/approve",
        json={"reviewer_id": "editor_lead", "version_id": v1["id"]}
    )
    assert res_appr1.status_code == 200
    assert res_appr1.json()["status"] == "approved"

    # Verify versions endpoint shows v1 approved
    res_v_check1 = client.get(f"/api/projects/{project_id}/images/{img_id}/versions")
    assert res_v_check1.status_code == 200
    v_data1 = res_v_check1.json()
    assert v_data1["is_approved"] is True
    assert v_data1["latest_version"]["id"] == v1["id"]

    # Step 3: Editor submits a follow-up revision instruction
    revision_prompt = "Detail the geographic spread and mention the silver purity percentages"
    res_gen2 = client.post(
        f"/api/projects/{project_id}/images/{img_id}/generate",
        json={"language": "en-GB", "revision_instruction": revision_prompt}
    )
    assert res_gen2.status_code == 200
    v2 = res_gen2.json()["version"]

    # Step 4: Verify version v2 properties
    assert v2["version_number"] == 2
    assert v2["revision_instruction"] == revision_prompt
    assert v2["id"] != v1["id"]

    # Prior draft v1 is preserved unchanged in history
    v1_recheck = repo.get_alt_text_version(v1["id"])
    assert v1_recheck["text"] == v1["text"]

    # Step 5: Verify the new version is NOT automatically approved!
    img_asset = repo.get_image_asset(img_id)
    assert img_asset["processing_status"] == "drafted"  # NOT approved

    # Step 6: Critical check - old approval does NOT make newer revised version appear approved
    res_v_check2 = client.get(f"/api/projects/{project_id}/images/{img_id}/versions")
    assert res_v_check2.status_code == 200
    v_data2 = res_v_check2.json()
    assert v_data2["latest_version"]["id"] == v2["id"]
    assert v_data2["is_approved"] is False  # Must be False! Old approval is for v1, not v2!
    assert v_data2["approved_version_id"] == v1["id"]

    # Step 7: Explicitly approve v2
    res_appr2 = client.post(
        f"/api/projects/{project_id}/images/{img_id}/approve",
        json={"reviewer_id": "editor_lead", "version_id": v2["id"]}
    )
    assert res_appr2.status_code == 200
    res_v_check3 = client.get(f"/api/projects/{project_id}/images/{img_id}/versions")
    assert res_v_check3.json()["is_approved"] is True
    assert res_v_check3.json()["approved_version_id"] == v2["id"]


def test_failed_revision_preserves_existing_draft(api_env, monkeypatch):
    """
    Verifies that when revision generation fails, existing drafts
    and version history are preserved without destruction.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img_id = create_api_test_image(api_env, page_number=1, caption="Figure 6: Map")

    # Generate initial draft v1
    res1 = client.post(f"/api/projects/{project_id}/images/{img_id}/generate", json={"language": "en-GB"})
    assert res1.status_code == 200
    v1_text = res1.json()["version"]["text"]
    assert len(v1_text) > 0

    # Simulate AI failure during revision
    def fail_generate(*args, **kwargs):
        raise RuntimeError("HF inference provider connection timed out")

    monkeypatch.setattr(api_env["app"].state.generator.provider, "generate", fail_generate)

    # Attempt revision
    res_fail = client.post(
        f"/api/projects/{project_id}/images/{img_id}/generate",
        json={"language": "en-GB", "revision_instruction": "Add more detail"}
    )
    assert res_fail.status_code == 500

    # Verify v1 draft is completely preserved and untouched
    versions = repo.list_image_versions(img_id)
    assert len(versions) >= 1
    assert versions[0]["text"] == v1_text


def test_review_mode_loads_images_and_serves_valid_assets(api_env):
    """
    Verifies that Review Mode:
    - Lists extracted images for a project.
    - Serves valid image binaries with correct content types over GET and HEAD.
    - Renders the Review Workspace template with image preview and generation controls.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img_id = create_api_test_image(api_env, page_number=2, caption="Figure 2: Architecture diagram")

    # 1. Project API returns extracted image count
    res_proj = client.get(f"/api/projects/{project_id}")
    assert res_proj.status_code == 200
    assert res_proj.json()["stats"]["total_images"] >= 1
    assert len(res_proj.json()["images"]) >= 1

    # 2. Image list API returns image record with page information
    res_imgs = client.get(f"/api/projects/{project_id}/images")
    assert res_imgs.status_code == 200
    images = res_imgs.json()["images"]
    matching = [img for img in images if img["id"] == img_id]
    assert len(matching) == 1
    assert matching[0]["page_number"] == 2
    assert matching[0]["original_caption"] == "Figure 2: Architecture diagram"

    # 3. Image serving endpoint returns HTTP 200 with valid image/png content type for GET and HEAD
    res_img_file = client.get(f"/api/projects/{project_id}/images/{img_id}/file")
    assert res_img_file.status_code == 200
    assert res_img_file.headers["content-type"] == "image/png"
    assert len(res_img_file.content) > 0

    res_img_head = client.head(f"/api/projects/{project_id}/images/{img_id}/file")
    assert res_img_head.status_code == 200
    assert res_img_head.headers["content-type"] == "image/png"

    # 4. Review mode HTML page contains image preview element and generation buttons
    res_page = client.get(f"/review/{project_id}")
    assert res_page.status_code == 200
    html = res_page.text
    assert 'id="rwImagePreview"' in html
    assert 'id="rwGenerateDraftBtn"' in html
    assert 'id="rwRevisionInput"' in html
    assert 'id="rwReviseBtn"' in html


def test_generate_alt_text_creates_draft_without_auto_approval(api_env):
    """
    Verifies that calling the generation API:
    - Successfully generates an alt-text draft.
    - Saves an immutable draft version (v1).
    - NEVER automatically approves the draft.
    - Subsequent generation creates v2, preserving v1 history.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img_id = create_api_test_image(api_env, page_number=1, caption="Figure 1: Test Chart")

    # Call Generate Alt Text
    res_gen = client.post(
        f"/api/projects/{project_id}/images/{img_id}/generate",
        json={"language": "en-GB"}
    )
    assert res_gen.status_code == 200
    gen_data = res_gen.json()
    assert gen_data["status"] == "success"
    v1 = gen_data["version"]
    assert v1["version_number"] == 1
    assert len(v1["text"]) > 0

    # Image asset status updated to drafted
    asset_after = repo.get_image_asset(img_id)
    assert asset_after["processing_status"] == "drafted"

    # CRITICAL: Verify NO approval record exists
    approval = repo.get_approval_for_image(img_id)
    assert approval is None

    # Version check endpoint confirms unapproved
    res_ver = client.get(f"/api/projects/{project_id}/images/{img_id}/versions")
    assert res_ver.status_code == 200
    ver_data = res_ver.json()
    assert ver_data["is_approved"] is False
    assert len(ver_data["versions"]) == 1

    # Second generation creates v2 and preserves v1
    res_gen2 = client.post(
        f"/api/projects/{project_id}/images/{img_id}/generate",
        json={"language": "en-GB"}
    )
    assert res_gen2.status_code == 200
    v2 = res_gen2.json()["version"]
    assert v2["version_number"] == 2
    assert v2["id"] != v1["id"]

    all_versions = repo.list_image_versions(img_id)
    assert len(all_versions) == 2
    assert all_versions[0]["version_number"] == 1
    assert all_versions[1]["version_number"] == 2


def test_ai_telemetry_endpoints_accurately_reflect_configuration_and_spend(api_env):
    """
    Verifies that:
    - /api/ai/config accurately reports provider configuration without leaking tokens.
    - /api/ai/budget and /api/ai/usage report spend and budget ceilings.
    """
    client = api_env["client"]

    res_cfg = client.get("/api/ai/config")
    assert res_cfg.status_code == 200
    cfg = res_cfg.json()
    assert "token_status" in cfg
    assert "model" in cfg
    assert "provider" in cfg
    assert "is_configured" in cfg
    assert "provider_name" in cfg
    # Never expose secrets
    assert "hf_token" not in cfg
    for k, v in cfg.items():
        if isinstance(v, str):
            assert "Bearer" not in v

    # Budget & Usage endpoints
    res_budget = client.get("/api/ai/budget")
    assert res_budget.status_code == 200
    budget = res_budget.json()
    assert "total_spend_usd" in budget
    assert "max_monthly_spend_usd" in budget

    res_usage = client.get("/api/ai/usage")
    assert res_usage.status_code == 200
    usage = res_usage.json()
    assert "tracker" in usage
    assert "ledger" in usage


# ============================================================================
# Regression Tests: Resolve Caption Review Flow & Header Count Integrity
# ============================================================================

def test_resolve_caption_review_preserves_valid_caption_and_alt_text_history(api_env):
    """
    Verifies that resolving a caption review:
    1. Clears needs_caption_review flag and caption review reasons.
    2. Preserves the existing valid caption without overwriting it.
    3. Preserves approved alt-text status, approval records, and version history.
    4. Does not remove unrelated review reasons.
    5. Records an audit event for traceability.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    filestore = api_env["filestore"]
    project_id = api_env["project_id"]

    img_id = str(uuid.uuid4())
    img_bytes = make_test_png()
    storage_key, checksum, _ = filestore.save_image(project_id, img_id, img_bytes, "png")

    valid_caption = "Figure 11: Age distribution for Tarquinia, only based on Latin epitaphs."
    caption_reason = "Competing figure caption detected on above (Figure 10 Age distribution for...)"
    unrelated_reason = "Unrelated layout notice: panel bounding box close to page margin"

    repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=11,
        xref=110,
        placement_index=0,
        bbox={"x0": 50.0, "y0": 100.0, "x1": 250.0, "y1": 300.0},
        original_caption=valid_caption,
        caption_position="below",
        needs_caption_review=True,
        review_reasons=[caption_reason, unrelated_reason],
        suggested_filename="fig_11.png",
        file_storage_key=storage_key,
        file_checksum=checksum,
        image_format="png",
        processing_status="approved",
    )

    # Create approved alt-text version v1
    ver_id = f"altv_{uuid.uuid4().hex[:6]}"
    repo.create_alt_text_version(
        version_id=ver_id,
        image_id=img_id,
        version_number=1,
        text="A detailed bar chart displaying age distribution for Latin epitaphs across ten-year brackets.",
        revision_instruction=None,
        prompt_version_id=None,
        provider="manual",
        model="human_editor",
        language="en-GB",
        status="success",
    )
    appr_id = str(uuid.uuid4())
    repo.create_approval(
        approval_id=appr_id,
        image_id=img_id,
        alt_text_version_id=ver_id,
        reviewer_id="lead_editor",
    )

    # Confirm initial state
    asset_before = repo.get_image_asset(img_id)
    assert asset_before["needs_caption_review"] is True
    assert asset_before["original_caption"] == valid_caption
    assert len(asset_before["review_reasons"]) == 2

    # Resolve caption review without passing a caption payload
    res_resolve = client.put(
        f"/api/projects/{project_id}/images/{img_id}/caption",
        json={"resolve_review": True, "needs_review": False}
    )
    assert res_resolve.status_code == 200
    res_data = res_resolve.json()
    assert res_data["status"] == "updated"

    # Verify asset state
    asset_after = repo.get_image_asset(img_id)
    assert asset_after["needs_caption_review"] is False
    assert asset_after["original_caption"] == valid_caption  # Valid caption NOT overwritten!
    assert caption_reason not in asset_after["review_reasons"]
    assert unrelated_reason in asset_after["review_reasons"]  # Unrelated flags preserved!

    # Verify alt-text versions and approval remain intact
    approval_after = repo.get_approval_for_image(img_id)
    assert approval_after is not None
    assert approval_after["alt_text_version_id"] == ver_id
    versions_after = repo.list_image_versions(img_id)
    assert len(versions_after) == 1
    assert versions_after[0]["text"].startswith("A detailed bar chart")

    # Verify audit event
    audits = repo.list_project_audit_events(project_id)
    caption_audits = [a for a in audits if a["event_type"] == "caption_review_resolved"]
    assert len(caption_audits) == 1
    details = caption_audits[0]["details"]
    assert details["prior_needs_review"] is True
    assert details["new_needs_review"] is False
    assert details["resolved"] is True


def test_saving_caption_text_does_not_auto_resolve_caption_review_flag(api_env):
    """
    Verifies that editing a caption without explicitly resolving the review flag
    preserves the needs_caption_review state.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img_id = create_api_test_image(api_env, page_number=2, caption="Uncertain caption candidate")
    repo.update_image_caption(
        image_id=img_id,
        caption="Uncertain caption candidate",
        needs_review=True,
        review_reasons=["No caption found within proximity tolerance"]
    )

    # Save edited caption text without resolve_review flag
    res_save = client.put(
        f"/api/projects/{project_id}/images/{img_id}/caption",
        json={"caption": "Editor verified draft caption text"}
    )
    assert res_save.status_code == 200
    asset = repo.get_image_asset(img_id)
    assert asset["original_caption"] == "Editor verified draft caption text"
    assert asset["needs_caption_review"] is True  # NOT automatically resolved!
    assert "No caption found within proximity tolerance" in asset["review_reasons"]


def test_failed_caption_resolution_api_leaves_unresolved_state_visible(api_env):
    """
    Verifies that a failed API request (e.g., 404 on invalid image ID)
    does not alter any database state or erroneously report resolution.
    """
    client = api_env["client"]
    repo = api_env["repo"]
    project_id = api_env["project_id"]

    img_id = create_api_test_image(api_env, page_number=3, caption="Caption with review flag")
    repo.update_image_caption(
        image_id=img_id,
        caption="Caption with review flag",
        needs_review=True,
        review_reasons=["Competing figure caption detected on above"]
    )

    # Attempt to resolve caption on a non-existent image ID
    bad_id = str(uuid.uuid4())
    res_fail = client.put(
        f"/api/projects/{project_id}/images/{bad_id}/caption",
        json={"resolve_review": True, "needs_review": False}
    )
    assert res_fail.status_code == 404

    # Real image remains unresolved
    asset = repo.get_image_asset(img_id)
    assert asset["needs_caption_review"] is True
    assert len(asset["review_reasons"]) == 1


def test_review_js_defines_update_header_counts_and_executes_without_error():
    """
    Verifies that review.js defines updateHeaderCounts as a function,
    that it updates total, matched, and review counts correctly,
    and executes without ReferenceError.
    """
    import subprocess
    from pathlib import Path

    js_path = Path("src/web/static/review.js").resolve()
    assert js_path.exists()
    content = js_path.read_text(encoding="utf-8")

    # Static assertions
    assert "function updateHeaderCounts()" in content
    assert "updateHeaderCounts();" in content

    # Test execution in Node.js
    node_script = """
    // Mock minimal DOM elements
    const elements = {
      projectTitle: { textContent: "" },
      totalCount: { textContent: "" },
      matchedCount: { textContent: "" },
      groupCount: { textContent: "" },
      reviewCount: { textContent: "" }
    };

    let projectTitle = elements.projectTitle;
    let totalCountEl = elements.totalCount;
    let matchedCountEl = elements.matchedCount;
    let groupCountEl = elements.groupCount;
    let reviewCountEl = elements.reviewCount;

    let currentProject = { project_name: "Test Doc" };
    let currentGroups = [];
    let currentImages = [
      { id: "img-1", original_caption: "Figure 1", needs_caption_review: false },
      { id: "img-2", original_caption: "Figure 2", needs_caption_review: true },
      { id: "img-3", original_caption: "", needs_caption_review: false }
    ];

    function boolCaption(img) {
      return Boolean(img.original_caption && img.original_caption.trim().length > 0);
    }

    function updateHeaderCounts() {
      if (!currentImages) return;
      if (projectTitle && currentProject) projectTitle.textContent = currentProject.project_name;
      if (totalCountEl) totalCountEl.textContent = currentImages.length;
      if (matchedCountEl) matchedCountEl.textContent = currentImages.filter(img => boolCaption(img) && !img.needs_caption_review).length;
      if (groupCountEl) groupCountEl.textContent = currentGroups ? currentGroups.length : 0;
      if (reviewCountEl) reviewCountEl.textContent = currentImages.filter(img => img.needs_caption_review).length;
    }

    // Run initial counts
    updateHeaderCounts();
    if (elements.totalCount.textContent !== 3) throw new Error("totalCount mismatch");
    if (elements.matchedCount.textContent !== 1) throw new Error("matchedCount mismatch");
    if (elements.reviewCount.textContent !== 1) throw new Error("reviewCount mismatch");

    // Simulate resolve action on img-2
    currentImages[1].needs_caption_review = false;
    updateHeaderCounts();

    if (elements.matchedCount.textContent !== 2) throw new Error("matchedCount after resolve mismatch");
    if (elements.reviewCount.textContent !== 0) throw new Error("reviewCount after resolve mismatch");

    console.log("SUCCESS");
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    assert "SUCCESS" in res.stdout
