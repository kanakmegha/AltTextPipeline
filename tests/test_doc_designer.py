"""
Tests for the Word Document Designer (DOCX customization, persistence, validation, and export).
"""

import io
import uuid
import docx
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.export import (
    DocxBuilder,
    export_project_docx,
    get_default_design_config,
    validate_design_config,
)
from src.web.app import create_app


def _make_png(color: str = "navy", width: int = 240, height: int = 120) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def designer_env(tmp_path):
    db_path = str(tmp_path / "designer.db")
    storage_dir = str(tmp_path / "storage")
    app = create_app(db_path=db_path, storage_dir=storage_dir)
    client = TestClient(app)
    repo: Repository = app.state.repo
    store: FileStore = app.state.filestore

    pid = f"proj_des_{uuid.uuid4().hex[:8]}"
    src_key, src_chk, _ = store.save_source_pdf(pid, b"%PDF-1.4\n%%EOF", "handbook.pdf")
    repo.create_project(pid, "Editorial Handbook", "handbook.pdf", src_key, src_chk)

    grp_id = f"grp_{pid}"
    repo.create_figure_group(grp_id, pid, "Figure 1.1: System Diagram", 2, "below", "Figure 1.1", False, "approved", [])

    # Image 1: has figure_label, caption, generated + approved alt text
    img1_id = f"img1_{pid}"
    k1, c1, _ = store.save_image(pid, img1_id, _make_png("blue", 400, 200), "png")
    repo.create_image_asset(
        image_id=img1_id, project_id=pid, page_number=2, xref=10, placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 50},
        original_caption="Figure 1.1: Long caption " + ("detail " * 40),
        caption_position="below", needs_caption_review=False, review_reasons=[],
        suggested_filename="page02_fig1.png", file_storage_key=k1, file_checksum=c1,
        image_format="png", processing_status="approved", group_id=grp_id,
    )
    repo.create_alt_text_version(
        version_id=f"v1_{pid}", image_id=img1_id, version_number=1,
        text="Initial AI generated alt text",
    )
    repo.create_alt_text_version(
        version_id=f"v2_{pid}", image_id=img1_id, version_number=2,
        text="Exact approved alt text for diagram. " + ("Accessible detail. " * 20),
    )
    repo.create_approval(f"app1_{pid}", img1_id, f"v2_{pid}", "approved")

    # Image 2: missing optional figure_label and caption
    img2_id = f"img2_{pid}"
    k2, c2, _ = store.save_image(pid, img2_id, _make_png("green", 180, 180), "png")
    repo.create_image_asset(
        image_id=img2_id, project_id=pid, page_number=5, xref=11, placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 80, "y1": 80},
        original_caption="", caption_position=None, needs_caption_review=False, review_reasons=[],
        suggested_filename="page05_chart.png", file_storage_key=k2, file_checksum=c2,
        image_format="png", processing_status="approved",
    )
    repo.create_alt_text_version(
        version_id=f"v3_{pid}", image_id=img2_id, version_number=1,
        text="Approved alt text for green square chart.",
    )
    repo.create_approval(f"app2_{pid}", img2_id, f"v3_{pid}", "approved")

    return client, repo, store, pid


@pytest.mark.parametrize("preset", ["minimal", "editorial", "table", "professional"])
def test_all_four_presets_generate_valid_docx(designer_env, preset):
    client, _, _, pid = designer_env
    cfg = get_default_design_config("Editorial Handbook", preset=preset)
    res = client.post(f"/api/projects/{pid}/exports/docx", json={"design_config": cfg})
    assert res.status_code == 200
    data = res.json()
    dl = client.get(data["download_url"])
    assert dl.status_code == 200
    doc = docx.Document(io.BytesIO(dl.content))
    assert len(doc.inline_shapes) == 2
    if preset == "table":
        assert len(doc.tables) == 1
        assert len(doc.tables[0].rows) == 3  # header + 2 images


def test_save_restore_reset_and_validation(designer_env):
    client, _, _, pid = designer_env
    get_res = client.get(f"/api/projects/{pid}/doc-design")
    assert get_res.status_code == 200
    payload = get_res.json()
    assert payload["is_saved"] is False
    assert payload["sample_item"] is not None
    assert payload["total_images"] == 2
    avail_keys = {f["key"] for f in payload["available_fields"]}
    assert {"image", "filename", "figure_label", "original_caption", "approved_alt_text"}.issubset(avail_keys)

    # Invalid preset rejected
    bad = client.put(f"/api/projects/{pid}/doc-design", json={"design_config": {"style_preset": "invalid_style"}})
    assert bad.status_code == 400

    # Save custom settings
    cfg = payload["design_config"]
    cfg["style_preset"] = "editorial"
    cfg["document_title"] = "Accessibility Review"
    cfg["introduction"] = "Introductory editorial notes."
    cfg["footer_text"] = "Internal Publishing Document"
    cfg["closing_text"] = "End of review."
    cfg["font_family"] = "Georgia"
    cfg["font_size"] = "large"
    cfg["heading_style"] = "uppercase"
    cfg["alignment"] = "center"
    cfg["entry_spacing"] = "spacious"
    cfg["entry_separator"] = "page_break"
    cfg["image_size"] = "small"
    cfg["section_labels"]["approved_alt_text"] = "Alternative Text"
    cfg["custom_phrases"] = [
        {"text": "Prepared for Editorial Review", "placement": "header"},
        {"text": "Editor Note:", "placement": "before_alt_text"},
    ]
    # Reorder: put approved_alt_text before original_caption and enable generated_alt_text
    cfg["fields"] = [
        {"key": "figure_label", "enabled": True},
        {"key": "approved_alt_text", "enabled": True},
        {"key": "original_caption", "enabled": True},
        {"key": "generated_alt_text", "enabled": True},
        {"key": "image", "enabled": True},
        {"key": "filename", "enabled": False},
        {"key": "page_number", "enabled": True},
        {"key": "image_id", "enabled": False},
        {"key": "review_status", "enabled": False},
    ]

    put_res = client.put(f"/api/projects/{pid}/doc-design", json={"design_config": cfg})
    assert put_res.status_code == 200
    assert put_res.json()["is_saved"] is True

    # Restored automatically on subsequent GET
    restored = client.get(f"/api/projects/{pid}/doc-design").json()
    assert restored["is_saved"] is True
    assert restored["design_config"]["document_title"] == "Accessibility Review"
    assert restored["design_config"]["fields"][0]["key"] == "figure_label"

    # Generate DOCX using saved settings (no body needed)
    exp_res = client.post(f"/api/projects/{pid}/exports/docx")
    assert exp_res.status_code == 200
    dl_bytes = client.get(exp_res.json()["download_url"]).content
    doc = docx.Document(io.BytesIO(dl_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "ACCESSIBILITY REVIEW" in full_text
    assert "Prepared for Editorial Review" in full_text
    assert "ALTERNATIVE TEXT" in full_text
    assert "End of review." in full_text
    # Approved alt text preserved verbatim without injecting custom phrases into descr
    descr0 = doc.inline_shapes[0]._inline.docPr.get("descr")
    assert descr0.startswith("Exact approved alt text for diagram.")
    assert "Prepared for Editorial Review" not in descr0
    # Footer text present
    assert "Internal Publishing Document" in doc.sections[0].footer.paragraphs[0].text
    # Ordering: approved_alt_text appears before original_caption
    assert full_text.index("ALTERNATIVE TEXT") < full_text.index("ORIGINAL CAPTION")
    # Missing optional caption/figure_label on Image 2 must not emit empty labels
    assert "Page: 5" in full_text
    # Reset restores default
    rst_res = client.delete(f"/api/projects/{pid}/doc-design")
    assert rst_res.status_code == 200
    assert rst_res.json()["is_saved"] is False


def test_unavailable_fields_hidden_when_missing_in_project(tmp_path):
    app = create_app(db_path=str(tmp_path / "min.db"), storage_dir=str(tmp_path / "st"))
    client = TestClient(app)
    repo: Repository = app.state.repo
    store: FileStore = app.state.filestore
    pid = "proj_min_fields"
    sk, sc, _ = store.save_source_pdf(pid, b"%PDF-1.4\n%%EOF", "min.pdf")
    repo.create_project(pid, "Minimal Fields", "min.pdf", sk, sc)
    ik, ic, _ = store.save_image(pid, "i1", _make_png(), "png")
    repo.create_image_asset(
        image_id="i1", project_id=pid, page_number=1, xref=1, placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 10, "y1": 10}, original_caption="",
        caption_position=None, needs_caption_review=False, review_reasons=[],
        suggested_filename="i1.png", file_storage_key=ik, file_checksum=ic, image_format="png",
    )
    res = client.get(f"/api/projects/{pid}/doc-design").json()
    avail = {f["key"] for f in res["available_fields"]}
    assert "figure_label" not in avail
    assert "original_caption" not in avail
    assert "generated_alt_text" not in avail


def _seed_approved_project(repo: Repository, store: FileStore, pid: str, name: str, owner_id: str | None = None):
    sk, sc, _ = store.save_source_pdf(pid, b"%PDF-1.4\n%%EOF", f"{pid}.pdf")
    repo.create_project(pid, name, f"{pid}.pdf", sk, sc, owner_id=owner_id)
    ik, ic, _ = store.save_image(pid, f"img_{pid}", _make_png("teal", 300, 150), "png")
    repo.create_image_asset(
        image_id=f"img_{pid}",
        project_id=pid,
        page_number=1,
        xref=1,
        placement_index=1,
        bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 50},
        original_caption=f"Caption for {name}",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename=f"{pid}_fig1.png",
        file_storage_key=ik,
        file_checksum=ic,
        image_format="png",
        processing_status="approved",
    )
    repo.create_alt_text_version(f"v_{pid}", f"img_{pid}", 1, f"Approved alt text for {name}.")
    repo.create_approval(f"a_{pid}", f"img_{pid}", f"v_{pid}", "approved")


def test_settings_user_level_persistence_across_sessions_and_projects(tmp_path):
    db_path = str(tmp_path / "user_prefs.db")
    storage_dir = str(tmp_path / "storage")
    app1 = create_app(db_path=db_path, storage_dir=storage_dir)
    c1 = TestClient(app1)
    _seed_approved_project(app1.state.repo, app1.state.filestore, "proj_u_a", "Project Alpha")
    _seed_approved_project(app1.state.repo, app1.state.filestore, "proj_u_b", "Project Beta")

    # Settings HTML & simplified Export HTML
    settings_html = c1.get("/settings").text
    assert "Word Document Preferences" in settings_html
    assert 'id="docDesignerRoot"' in settings_html
    export_html = c1.get("/export/proj_u_a").text
    assert 'id="docDesignerRoot"' not in export_html
    assert "Change document format" in export_html

    # Save user-level preferences via Settings API
    cfg = get_default_design_config("Org Accessibility Standard", preset="editorial")
    cfg["footer_text"] = "Org Standard Footer"
    cfg["custom_phrases"] = [{"text": "Global Settings Phrase", "placement": "header"}]
    put_res = c1.put("/api/settings/doc-design", json={"design_config": cfg})
    assert put_res.status_code == 200
    assert put_res.json()["is_saved"] is True

    # New session / new app instance against same DB restores saved preferences
    app2 = create_app(db_path=db_path, storage_dir=storage_dir)
    c2 = TestClient(app2)
    restored = c2.get("/api/settings/doc-design").json()
    assert restored["is_saved"] is True
    assert restored["design_config"]["footer_text"] == "Org Standard Footer"

    # Both projects automatically use saved Settings preferences during export
    for pid in ("proj_u_a", "proj_u_b"):
        status_data = c2.get(f"/api/projects/{pid}/exports/status").json()
        assert status_data["active_doc_design"]["preset_name"] == "Editorial"
        assert status_data["active_doc_design"]["is_saved"] is True

        exp_res = c2.post(f"/api/projects/{pid}/exports/docx")
        assert exp_res.status_code == 200
        dl_bytes = c2.get(exp_res.json()["download_url"]).content
        doc = docx.Document(io.BytesIO(dl_bytes))
        full_text = "\n".join(p.text for p in doc.paragraphs)
        assert "Global Settings Phrase" in full_text
        assert "Org Standard Footer" in doc.sections[0].footer.paragraphs[0].text
        assert doc.inline_shapes[0]._inline.docPr.get("descr").startswith("Approved alt text for")

    # Updating Settings affects subsequent exports without project-by-project edits
    cfg2 = get_default_design_config("Updated Standard", preset="table")
    cfg2["custom_phrases"] = [{"text": "Updated Global Phrase", "placement": "closing"}]
    assert c2.put("/api/settings/doc-design", json={"design_config": cfg2}).status_code == 200

    exp_b2 = c2.post("/api/projects/proj_u_b/exports/docx")
    assert exp_b2.status_code == 200
    doc_b2 = docx.Document(io.BytesIO(c2.get(exp_b2.json()["download_url"]).content))
    text_b2 = "\n".join(p.text for p in doc_b2.paragraphs)
    assert "Updated Global Phrase" in text_b2


def test_multi_user_independent_preferences(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from src.web import auth

    monkeypatch.setattr(auth, "AUTH_ENABLED", auth.AUTH_ENABLED)
    monkeypatch.setattr(auth, "SESSION_SECRET_KEY", auth.SESSION_SECRET_KEY)
    monkeypatch.setattr(auth, "_INITIAL_AUTH_ENABLED", auth._INITIAL_AUTH_ENABLED)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SESSION_SECRET_KEY", "TestSecretKey_ForDocDesigner_9876543210!@#")
    db_path = str(tmp_path / "multi_user.db")
    storage_dir = str(tmp_path / "storage")
    app = create_app(db_path=db_path, storage_dir=storage_dir)
    repo: Repository = app.state.repo
    store: FileStore = app.state.filestore

    repo.create_user("user_1", "u1@example.com", "h1", "s1")
    repo.create_user("user_2", "u2@example.com", "h2", "s2")
    _seed_approved_project(repo, store, "proj_user_1", "User One Book", owner_id="user_1")
    _seed_approved_project(repo, store, "proj_user_2", "User Two Book", owner_id="user_2")

    def _session_client(uid: str) -> TestClient:
        raw_tok = f"tok_{uid}_{uuid.uuid4().hex}"
        exp = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
        repo.create_session(f"sess_{uid}", uid, raw_tok, exp)
        cli = TestClient(app)
        cli.cookies.set(auth.SESSION_COOKIE_NAME, auth._sign(raw_tok))
        csrf = cli.get("/api/auth/csrf-token").json()["csrf_token"]
        cli.headers["X-CSRF-Token"] = csrf
        return cli

    c1 = _session_client("user_1")
    c2 = _session_client("user_2")

    cfg1 = get_default_design_config("User 1 Format", preset="editorial")
    cfg1["custom_phrases"] = [{"text": "User 1 Banner", "placement": "header"}]
    cfg2 = get_default_design_config("User 2 Format", preset="professional")
    cfg2["custom_phrases"] = [{"text": "User 2 Banner", "placement": "header"}]

    assert c1.put("/api/settings/doc-design", json={"design_config": cfg1}).status_code == 200
    assert c2.put("/api/settings/doc-design", json={"design_config": cfg2}).status_code == 200

    assert c1.get("/api/settings/doc-design").json()["design_config"]["document_title"] == "User 1 Format"
    assert c2.get("/api/settings/doc-design").json()["design_config"]["document_title"] == "User 2 Format"

    exp1 = c1.post("/api/projects/proj_user_1/exports/docx")
    exp2 = c2.post("/api/projects/proj_user_2/exports/docx")
    assert exp1.status_code == 200 and exp2.status_code == 200
    txt1 = "\n".join(p.text for p in docx.Document(io.BytesIO(c1.get(exp1.json()["download_url"]).content)).paragraphs)
    txt2 = "\n".join(p.text for p in docx.Document(io.BytesIO(c2.get(exp2.json()["download_url"]).content)).paragraphs)
    assert "User 1 Banner" in txt1 and "User 2 Banner" not in txt1
    assert "User 2 Banner" in txt2 and "User 1 Banner" not in txt2


def test_default_export_and_legacy_project_level_compatibility(tmp_path):
    app = create_app(db_path=str(tmp_path / "compat.db"), storage_dir=str(tmp_path / "st"))
    client = TestClient(app)
    repo: Repository = app.state.repo
    store: FileStore = app.state.filestore
    _seed_approved_project(repo, store, "proj_default", "Default Project")
    _seed_approved_project(repo, store, "proj_legacy", "Legacy Project")

    # 1. Default export works when no preferences exist
    res_def = client.post("/api/projects/proj_default/exports/docx")
    assert res_def.status_code == 200

    # 2. Legacy project-level doc_design_config is still honored if no user-level preference exists
    legacy_cfg = get_default_design_config("Legacy Custom Title", preset="editorial")
    legacy_cfg["custom_phrases"] = [{"text": "Legacy Project Phrase", "placement": "header"}]
    repo.save_project_doc_design("proj_legacy", legacy_cfg)

    res_leg = client.post("/api/projects/proj_legacy/exports/docx")
    assert res_leg.status_code == 200
    doc_leg = docx.Document(io.BytesIO(client.get(res_leg.json()["download_url"]).content))
    assert "Legacy Project Phrase" in "\n".join(p.text for p in doc_leg.paragraphs)
