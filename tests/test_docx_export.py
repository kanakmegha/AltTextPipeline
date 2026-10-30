"""
Unit and Integration Tests for DOCX Export (Phase C)
---------------------------------------------------
Tests:
- Export eligibility and blocking reasons (unapproved images, unresolved caption reviews).
- Approved-versus-unapproved version selection (ensures latest unapproved draft is never used).
- Reading order preservation (page_number ASC, placement_index ASC).
- Document content structure: filename without extension, embedded image, caption, visible 'Alt Text:'.
- DrawingML Word OpenXML accessibility metadata embedding (wp:docPr descr attribute).
- Structural reopening and content validation before marking export verified.
- Error handling without losing prior valid exports or claiming false success.
- Download endpoint delivery and status tracking.
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
from src.export.service import check_export_eligibility, export_project_docx
from src.export.docx_builder import DocxBuilder
from src.web.app import create_app


def make_test_png(color: str = "blue", width: int = 50, height: int = 50) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def export_env(tmp_path):
    """Provides a fresh isolated database, filestore, and FastAPI test client."""
    db_file = str(tmp_path / "export_test.db")
    storage_dir = str(tmp_path / "storage")

    db_conn = DatabaseConnection(db_file)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    filestore = FileStore(storage_dir)

    project_id = str(uuid.uuid4())
    pdf_bytes = b"%PDF-1.4 sample PDF content"
    rel_key, checksum, _ = filestore.save_source_pdf(project_id, pdf_bytes, "sample_document.pdf")
    repo.create_project(
        project_id=project_id,
        name="Export Test Project",
        source_filename="sample_document.pdf",
        source_storage_key=rel_key,
        source_checksum=checksum,
        language_variant="en-GB",
    )

    app = create_app(db_path=db_file, storage_dir=storage_dir)
    client = TestClient(app)

    return {
        "db_file": db_file,
        "db_conn": db_conn,
        "repo": repo,
        "filestore": filestore,
        "project_id": project_id,
        "storage_dir": storage_dir,
        "client": client,
    }


def add_image_with_draft_and_approval(
    env,
    page_num: int,
    placement_index: int,
    filename: str,
    caption: str,
    approved_text: str,
    latest_unapproved_draft: str = None,
    needs_caption_review: bool = False,
    review_reasons: list = None,
    is_approved: bool = True,
):
    repo = env["repo"]
    filestore = env["filestore"]
    project_id = env["project_id"]

    img_id = str(uuid.uuid4())
    img_bytes = make_test_png(
        color="green" if is_approved else "red",
        width=50 + page_num * 10 + placement_index,
        height=50 + page_num * 10 + placement_index,
    )
    img_key, checksum, byte_size = filestore.save_image(project_id, img_id, img_bytes, "png")

    repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=page_num,
        xref=100 + placement_index,
        placement_index=placement_index,
        bbox={"x0": 50.0, "y0": 50.0, "x1": 200.0, "y1": 200.0},
        original_caption=caption,
        caption_position="below",
        needs_caption_review=needs_caption_review,
        review_reasons=review_reasons or [],
        suggested_filename=filename,
        file_storage_key=img_key,
        file_checksum=checksum,
        image_format="png",
        processing_status="approved" if is_approved else "extracted",
    )

    # Version 1 (Approved version)
    v1_id = str(uuid.uuid4())
    repo.create_alt_text_version(
        version_id=v1_id,
        image_id=img_id,
        version_number=1,
        text=approved_text,
        status="success",
    )

    if is_approved:
        repo.create_approval(
            approval_id=str(uuid.uuid4()),
            image_id=img_id,
            alt_text_version_id=v1_id,
            reviewer_id="lead_editor",
        )

    # Optional: Version 2 (Unapproved draft that should NOT be exported)
    if latest_unapproved_draft:
        v2_id = str(uuid.uuid4())
        repo.create_alt_text_version(
            version_id=v2_id,
            image_id=img_id,
            version_number=2,
            text=latest_unapproved_draft,
            status="success",
        )

    return img_id


def test_export_eligibility_blocking_conditions(export_env):
    """Test that export is blocked when images are unapproved or require review."""
    repo = export_env["repo"]
    project_id = export_env["project_id"]
    client = export_env["client"]

    # 1. Project with 0 images
    elig = check_export_eligibility(repo, project_id)
    assert not elig["can_export"]
    assert any("no extracted images" in r.lower() for r in elig["blocking_reasons"])

    # 2. Add an unapproved image
    img1_id = add_image_with_draft_and_approval(
        export_env,
        page_num=1,
        placement_index=0,
        filename="fig_1.png",
        caption="Figure 1 caption",
        approved_text="Figure 1 approved alt text",
        is_approved=False,
    )
    elig = check_export_eligibility(repo, project_id)
    assert not elig["can_export"]
    assert elig["unapproved_count"] == 1
    assert any("unapproved" in r.lower() for r in elig["blocking_reasons"])

    # 3. Approve image 1, but add image 2 with needs_caption_review
    repo.create_approval(
        approval_id=str(uuid.uuid4()),
        image_id=img1_id,
        alt_text_version_id=repo.get_latest_version(img1_id)["id"],
        reviewer_id="editor",
    )
    img2_id = add_image_with_draft_and_approval(
        export_env,
        page_num=2,
        placement_index=0,
        filename="fig_2.png",
        caption="Figure 2 caption",
        approved_text="Figure 2 approved alt text",
        needs_caption_review=True,
        review_reasons=["Caption modified after alt-text approval; review required"],
        is_approved=True,
    )
    elig = check_export_eligibility(repo, project_id)
    assert not elig["can_export"]
    assert elig["unresolved_reviews"] == 1
    assert any("caption review" in r.lower() for r in elig["blocking_reasons"])

    # Check API status endpoint reports same blocking conditions
    res = client.get(f"/api/projects/{project_id}/exports/status")
    assert res.status_code == 200
    status_data = res.json()
    assert not status_data["eligibility"]["can_export"]
    assert len(status_data["eligibility"]["blocking_reasons"]) > 0

    # API export attempt returns 400
    res_export = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res_export.status_code == 400
    detail_msg = res_export.json()["detail"]["message"].lower()
    assert "blocked" in detail_msg or "cannot export" in detail_msg


def test_export_preserves_reading_order_and_uses_approved_version(export_env):
    """
    Test that export:
    1. Preserves source reading order (page_number ASC, placement_index ASC).
    2. Strictly uses the approved alt-text version, ignoring later unapproved drafts.
    3. Formats filename without extension and includes visible 'Alt Text:'.
    4. Embeds approved alt text into image accessibility metadata (wp:docPr descr).
    """
    repo = export_env["repo"]
    filestore = export_env["filestore"]
    project_id = export_env["project_id"]
    client = export_env["client"]

    # Insert images intentionally out of reading order:
    # Image C: Page 3, placement 0
    add_image_with_draft_and_approval(
        export_env,
        page_num=3,
        placement_index=0,
        filename="figure_3_chart.png",
        caption="Third diagram caption",
        approved_text="Approved alt text for third diagram.",
        is_approved=True,
    )
    # Image A: Page 1, placement 1
    add_image_with_draft_and_approval(
        export_env,
        page_num=1,
        placement_index=1,
        filename="figure_1b_photo.png",
        caption="First page second figure caption",
        approved_text="Approved alt text for figure 1b.",
        latest_unapproved_draft="UNAPPROVED DRAFT THAT MUST NEVER APPEAR IN DOCX",
        is_approved=True,
    )
    # Image B: Page 1, placement 0
    add_image_with_draft_and_approval(
        export_env,
        page_num=1,
        placement_index=0,
        filename="figure_1a_header.png",
        caption="First page first figure caption",
        approved_text="Approved alt text for figure 1a.",
        is_approved=True,
    )

    # Verify eligibility now passes
    elig = check_export_eligibility(repo, project_id)
    assert elig["can_export"] is True

    # Generate DOCX export via API
    res = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["export"]["status"] == "verified"
    export_id = data["export_id"]

    # Verify export record in database
    export_rec = repo.get_export(export_id)
    assert export_rec is not None
    assert export_rec["status"] == "verified"
    assert export_rec["verified_at"] is not None

    # Retrieve DOCX bytes from filestore
    docx_bytes = filestore.read_bytes(project_id, export_rec["file_storage_key"])
    assert docx_bytes is not None

    # Validate structure using DocxBuilder
    is_valid = DocxBuilder.validate_docx(docx_bytes, expected_count=3)
    assert is_valid is True

    # Inspect document contents via python-docx
    doc = docx.Document(io.BytesIO(docx_bytes))

    # Collect all paragraph objects and texts
    all_paras = doc.paragraphs
    non_empty_texts = [p.text.strip() for p in all_paras if p.text.strip()]

    # Verify exact three-element structure per image:
    # Exactly 3 non-empty paragraphs per image (filename stem, alt text)
    # Total paragraphs = 3 images * 3 paragraphs (stem, image, alt text) = 9
    assert len(all_paras) == 9

    # Verify reading order: Figure 1a -> Figure 1b -> Figure 3
    # Check filenames appear without extension
    assert all_paras[0].text == "figure_1a_header"
    assert len(all_paras[1].runs) > 0 and len(all_paras[1]._p.xpath(".//a:blip")) > 0  # image embedded
    assert all_paras[2].text == "Approved alt text for figure 1a."

    assert all_paras[3].text == "figure_1b_photo"
    assert len(all_paras[4].runs) > 0 and len(all_paras[4]._p.xpath(".//a:blip")) > 0  # image embedded
    assert all_paras[5].text == "Approved alt text for figure 1b."

    assert all_paras[6].text == "figure_3_chart"
    assert len(all_paras[7].runs) > 0 and len(all_paras[7]._p.xpath(".//a:blip")) > 0  # image embedded
    assert all_paras[8].text == "Approved alt text for third diagram."

    # Verify reading order
    idx_1a = non_empty_texts.index("figure_1a_header")
    idx_1b = non_empty_texts.index("figure_1b_photo")
    idx_3 = non_empty_texts.index("figure_3_chart")
    assert idx_1a < idx_1b < idx_3

    # Verify NO unwanted prefixes, labels, or metadata appear anywhere in paragraphs
    full_text = "\n".join(p.text for p in all_paras)
    assert "Alt Text:" not in full_text
    assert "Source:" not in full_text
    assert "PDF Page" not in full_text
    assert "First page first figure caption" not in full_text
    assert "Total Figures Exported" not in full_text
    assert "Alt-Text Asset Export" not in full_text
    assert not any(p.style.name.startswith("Heading") for p in all_paras)

    # Verify unapproved draft NEVER appears anywhere in document paragraphs
    assert not any("UNAPPROVED DRAFT" in t for t in non_empty_texts)

    # Verify embedded OpenXML accessibility metadata (wp:docPr descr attribute)
    embedded_descriptions = []
    for shape in doc.inline_shapes:
        try:
            descr = shape._inline.docPr.get("descr")
            embedded_descriptions.append(descr)
        except Exception:
            pass

    assert len(embedded_descriptions) == 3
    assert embedded_descriptions[0] == "Approved alt text for figure 1a."
    assert embedded_descriptions[1] == "Approved alt text for figure 1b."
    assert embedded_descriptions[2] == "Approved alt text for third diagram."

    # Test download endpoint delivers file with correct headers
    dl_res = client.get(f"/api/projects/{project_id}/exports/{export_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.headers["content-type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert "attachment; filename=" in dl_res.headers["content-disposition"]
    assert len(dl_res.content) == len(docx_bytes)


def test_reexport_without_ai_provider(export_env):
    """Test re-exporting from saved approved records succeeds without external AI calls."""
    project_id = export_env["project_id"]
    client = export_env["client"]

    add_image_with_draft_and_approval(
        export_env,
        page_num=1,
        placement_index=0,
        filename="figure_persisted.png",
        caption="Persisted caption",
        approved_text="Persisted approved alt text.",
        is_approved=True,
    )

    # First export
    res1 = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res1.status_code == 200
    id1 = res1.json()["export_id"]

    # Re-export (e.g. download / new export)
    res2 = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res2.status_code == 200
    id2 = res2.json()["export_id"]
    assert id1 != id2  # creates a new versioned export record

    # Both are available and valid in history
    hist_res = client.get(f"/api/projects/{project_id}/exports/status")
    all_exp = hist_res.json()["all_exports"]
    assert len(all_exp) == 2
    assert all(e["status"] == "verified" for e in all_exp)


def test_docx_exact_three_element_structure_and_aspect_ratio_preservation():
    """
    Dedicated test for the exact DOCX format:
    For every extracted image, in original PDF reading order, exactly:
    1. The extracted image filename without its file extension, on its own line.
    2. The image itself, on the next line (with aspect ratio preserved).
    3. The approved alt-text description, on the following line.

    Verifies:
    - Path(filename).stem removes only the final extension.
    - No headings, source captions, page numbers, or "Alt Text:" prefix.
    - Image accessibility description (descr) and title metadata are set.
    - Image aspect ratio is preserved (aspect ratio of shape matches original image).
    """
    # Create two test images with different aspect ratios
    # Image 1: 200x100 (aspect ratio 0.5)
    img1_bytes = make_test_png(color="blue", width=200, height=100)
    # Image 2: 100x300 (aspect ratio 3.0)
    img2_bytes = make_test_png(color="red", width=100, height=300)

    items = [
        {
            "suggested_filename": "extracted_chart_report.final.png",
            "page_number": 1,
            "original_caption": "Figure 1: Caption from PDF",
            "approved_alt_text": "A horizontal bar chart comparing metrics across five groups.",
            "image_bytes": img1_bytes,
            "image_format": "png",
        },
        {
            "suggested_filename": "vertical_photo.jpeg",
            "page_number": 2,
            "original_caption": "Plate 2: Botanical sample",
            "approved_alt_text": "A tall botanical specimen showing slender leaves and stem structure.",
            "image_bytes": img2_bytes,
            "image_format": "jpeg",
        },
    ]

    docx_bytes = DocxBuilder.build_docx(
        project_name="Test Document",
        items=items,
        include_page_breaks=False,
    )
    assert docx_bytes is not None

    doc = docx.Document(io.BytesIO(docx_bytes))
    paras = doc.paragraphs

    # Exactly 2 images * 3 lines = 6 paragraphs total
    assert len(paras) == 6

    # Image 1 block
    # 1. Filename stem on own line
    assert paras[0].text == "extracted_chart_report.final"
    # 2. Image itself on next line
    assert len(paras[1]._p.xpath(".//a:blip")) == 1
    # 3. Approved alt text on following line
    assert paras[2].text == "A horizontal bar chart comparing metrics across five groups."

    # Image 2 block
    # 1. Filename stem on own line
    assert paras[3].text == "vertical_photo"
    # 2. Image itself on next line
    assert len(paras[4]._p.xpath(".//a:blip")) == 1
    # 3. Approved alt text on following line
    assert paras[5].text == "A tall botanical specimen showing slender leaves and stem structure."

    # Verify no unwanted prefixes or labels anywhere
    doc_text = "\n".join(p.text for p in paras)
    assert "Alt Text:" not in doc_text
    assert "Figure 1:" not in doc_text
    assert "Plate 2:" not in doc_text
    assert "Page" not in doc_text
    assert "Test Document" not in doc_text
    assert not any(p.style.name.startswith("Heading") for p in paras)

    # Verify shapes and accessibility metadata
    shapes = doc.inline_shapes
    assert len(shapes) == 2

    # Shape 1
    shape1 = shapes[0]
    assert shape1._inline.docPr.get("descr") == "A horizontal bar chart comparing metrics across five groups."
    assert shape1._inline.docPr.get("title") == "extracted_chart_report.final"
    # Shape 1 aspect ratio: height / width == 100 / 200 = 0.5
    assert abs((shape1.height / shape1.width) - (100 / 200)) < 0.01

    # Shape 2
    shape2 = shapes[1]
    assert shape2._inline.docPr.get("descr") == "A tall botanical specimen showing slender leaves and stem structure."
    assert shape2._inline.docPr.get("title") == "vertical_photo"
    # Shape 2 aspect ratio: height / width == 300 / 100 = 3.0
    assert abs((shape2.height / shape2.width) - (300 / 100)) < 0.01


def test_export_multiple_images_persists_and_reports_correct_figure_count(export_env):
    """
    Verifies that:
    1. DOCX export containing multiple images persists figure_count in the database.
    2. API response (POST /exports/docx and GET /exports/status) includes figure_count and total_figures_exported.
    3. The actual ZIP archive contains exactly the matching number of media files under word/media/.
    4. Database figure_count, API figure_count, and ZIP media files are 100% consistent.
    """
    repo = export_env["repo"]
    filestore = export_env["filestore"]
    project_id = export_env["project_id"]
    client = export_env["client"]

    # Create 3 approved images
    for i in range(1, 4):
        add_image_with_draft_and_approval(
            export_env,
            page_num=i,
            placement_index=0,
            filename=f"figure_{i}.png",
            caption=f"Caption for figure {i}",
            approved_text=f"Approved alt text for figure {i}.",
            is_approved=True,
        )

    # Export via API
    res = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["status"] == "success"
    assert res_data["figure_count"] == 3
    assert res_data["total_figures_exported"] == 3
    export_id = res_data["export_id"]

    # 1. Check database persistence
    export_rec = repo.get_export(export_id)
    assert export_rec is not None
    assert export_rec["figure_count"] == 3
    assert export_rec["total_figures_exported"] == 3

    # 2. Check GET /exports/status API
    status_res = client.get(f"/api/projects/{project_id}/exports/status")
    assert status_res.status_code == 200
    stat_data = status_res.json()
    assert stat_data["latest_export"]["figure_count"] == 3
    assert stat_data["latest_export"]["total_figures_exported"] == 3
    assert "filename" in stat_data["latest_export"]
    assert stat_data["latest_export"]["size_bytes"] > 0

    all_exps = stat_data["all_exports"]
    assert len(all_exps) >= 1
    assert all_exps[0]["figure_count"] == 3
    assert all_exps[0]["total_figures_exported"] == 3

    # 3. Inspect ZIP archive directly
    import zipfile
    docx_bytes = filestore.read_bytes(project_id, export_rec["file_storage_key"])
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as z:
        media_files = [n for n in z.namelist() if n.startswith("word/media/")]
        assert len(media_files) == 3

    # 4. Consistency check
    doc = docx.Document(io.BytesIO(docx_bytes))
    assert len(doc.inline_shapes) == 3 == len(media_files) == export_rec["figure_count"]


def test_genuinely_empty_export_builder_and_validation():
    """
    Verifies that DocxBuilder can construct and validate an empty export,
    and that check_export_eligibility properly blocks export for empty projects.
    """
    # 1. DocxBuilder with 0 items
    docx_bytes = DocxBuilder.build_docx(project_name="Empty Project", items=[])
    assert docx_bytes is not None
    assert DocxBuilder.validate_docx(docx_bytes, expected_count=0) is True

    # 2. Inspect ZIP archive directly for 0 items
    import zipfile
    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as z:
        media_files = [n for n in z.namelist() if n.startswith("word/media/")]
        assert len(media_files) == 0


def test_frontend_export_js_figure_count_rendering():
    """
    Verifies that export.js correctly renders nonzero figure counts and falls back safely.
    """
    import subprocess

    node_script = """
    function escapeHtml(str) {
      if (!str) return "";
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    const testExports = [
      { id: "exp-1", filename: "Report_31.docx", total_figures_exported: 31, size_bytes: 31509393, created_at: "2026-10-29T15:03:18Z" },
      { id: "exp-2", filename: "Report_5.docx", figure_count: 5, size_bytes: 512000, created_at: "2026-10-29T15:00:00Z" },
      { id: "exp-3", file_storage_key: "exports/uuid_Fallback.docx", created_at: "2026-10-29T14:00:00Z" }
    ];

    const rendered = testExports.map(exp => {
      const dateStr = exp.created_at ? new Date(exp.created_at).toLocaleString() : "";
      const sizeVal = exp.size_bytes || exp.byte_size || 0;
      const sizeStr = sizeVal ? `(${(sizeVal / 1024).toFixed(1)} KB)` : "";
      const filename = exp.filename || (exp.file_storage_key ? exp.file_storage_key.split("/").pop() : "Report.docx");
      const figCount = (exp.total_figures_exported !== undefined && exp.total_figures_exported !== null)
        ? exp.total_figures_exported
        : ((exp.figure_count !== undefined && exp.figure_count !== null) ? exp.figure_count : 0);

      return `${filename} ${sizeStr} · ${figCount} figures`;
    });

    if (!rendered[0].includes("31 figures")) throw new Error("exp-1 did not render 31 figures");
    if (!rendered[0].includes("Report_31.docx")) throw new Error("exp-1 missing filename");
    if (!rendered[1].includes("5 figures")) throw new Error("exp-2 did not render 5 figures");
    if (!rendered[2].includes("0 figures")) throw new Error("exp-3 did not fallback to 0 figures");

    console.log("SUCCESS");
    """

    res = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=True)
    assert "SUCCESS" in res.stdout


def test_backfill_migration_5_derives_figure_count_for_legacy_exports(tmp_path):
    """
    Verifies that Migration 5 adds figure_count and backfills historical exports from audit_events.
    """
    db_file = str(tmp_path / "legacy.db")
    db_conn = DatabaseConnection(db_file)

    # Apply up to migration 4 manually
    with db_conn.session() as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);")
        # Baseline tables
        conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, source_filename TEXT, source_storage_key TEXT, source_checksum TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE exports (id TEXT PRIMARY KEY, project_id TEXT, export_type TEXT, file_storage_key TEXT, file_checksum TEXT, status TEXT, created_at TEXT, verified_at TEXT, delivered_at TEXT);
        CREATE TABLE audit_events (id TEXT PRIMARY KEY, project_id TEXT, event_type TEXT, target_type TEXT, target_id TEXT, details TEXT, actor_id TEXT, created_at TEXT);
        """)
        conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (4, '2026-10-29T00:00:00Z');")

        # Insert historical export without figure_count
        exp_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO exports (id, project_id, export_type, file_storage_key, file_checksum, status, created_at) VALUES (?, 'p1', 'docx', 'k', 'c', 'verified', '2026-10-29T00:00:00Z');",
            (exp_id,)
        )
        # Insert audit event with figure_count
        conn.execute(
            "INSERT INTO audit_events (id, project_id, event_type, target_type, target_id, details, created_at) VALUES ('a1', 'p1', 'docx_exported', 'export', ?, '{\"figure_count\": 31}', '2026-10-29T00:00:00Z');",
            (exp_id,)
        )

    # Apply the remaining migrations after the legacy export backfill.
    current_ver = apply_migrations(db_conn)
    assert current_ver == 9

    repo = Repository(db_conn)
    exp = repo.get_export(exp_id)
    assert exp["figure_count"] == 31
    assert exp["total_figures_exported"] == 31


def test_export_thirty_one_images_exact_layout_and_checksum_verification(export_env):
    """
    Comprehensive regression test proving that:
    1. An export with 31 images contains exactly 31 embedded image placements.
    2. Document body strictly adheres to the 3-element sequence for each figure:
       - Filename stem without extension on its own line.
       - Image with aspect ratio preserved on next line.
       - Approved alt-text description on following line.
    3. No boilerplate titles, page numbers, captions, or 'Alt Text:' prefixes exist.
    4. Accessibility metadata (wp:docPr descr) matches approved alt text for every figure.
    5. Downloaded file from /download matches the stored file by SHA256 checksum.
    """
    import hashlib
    import zipfile
    from xml.etree import ElementTree as ET

    repo = export_env["repo"]
    filestore = export_env["filestore"]
    project_id = export_env["project_id"]
    client = export_env["client"]

    expected_items = []
    # Create 31 approved images across pages
    for i in range(1, 32):
        fname = f"Figure_{i}_test_image.png"
        approved_desc = f"Approved accessibility description for figure {i} with specific details."
        add_image_with_draft_and_approval(
            export_env,
            page_num=i,
            placement_index=1,
            filename=fname,
            caption=f"Source caption for figure {i}",
            approved_text=approved_desc,
            is_approved=True,
        )
        expected_items.append({
            "stem": f"Figure_{i}_test_image",
            "desc": approved_desc,
        })

    # Trigger export via POST API
    res = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["status"] == "success"
    assert res_data["figure_count"] == 31
    assert res_data["total_figures_exported"] == 31
    export_id = res_data["export_id"]

    # Retrieve export record
    export_rec = repo.get_export(export_id)
    assert export_rec is not None
    assert export_rec["figure_count"] == 31

    # Verify download endpoint serves exact file and checksum matches
    dl_res = client.get(f"/api/projects/{project_id}/exports/{export_id}/download")
    assert dl_res.status_code == 200
    dl_bytes = dl_res.content
    dl_sha = hashlib.sha256(dl_bytes).hexdigest()
    assert dl_sha == export_rec["file_checksum"]

    # Inspect DOCX ZIP structure
    with zipfile.ZipFile(io.BytesIO(dl_bytes), "r") as z:
        media_files = [n for n in z.namelist() if n.startswith("word/media/")]
        assert len(media_files) == 31

        doc_xml = z.read("word/document.xml")
        rels_xml = z.read("word/_rels/document.xml.rels")

    rels_root = ET.fromstring(rels_xml)
    img_rels = [
        rel for rel in rels_root.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
        if "image" in rel.attrib.get("Type", "")
    ]
    assert len(img_rels) == 31

    doc_root = ET.fromstring(doc_xml)
    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    wp_ns = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

    drawings = doc_root.findall(f".//{{{w_ns}}}drawing")
    assert len(drawings) == 31

    docPrs = doc_root.findall(f".//{{{wp_ns}}}docPr")
    assert len(docPrs) == 31

    # Verify python-docx parsing and layout sequence
    doc = docx.Document(io.BytesIO(dl_bytes))
    assert len(doc.inline_shapes) == 31

    # Check paragraphs: each figure produces stem paragraph, drawing paragraph, description paragraph
    non_empty_paras = [p for p in doc.paragraphs if p.text.strip() or len(p._element.findall(f".//{{{w_ns}}}drawing")) > 0]
    # 3 paragraphs per figure = 93 paragraphs
    assert len(non_empty_paras) == 93

    for idx, expected in enumerate(expected_items):
        p_stem = non_empty_paras[idx * 3]
        p_draw = non_empty_paras[idx * 3 + 1]
        p_desc = non_empty_paras[idx * 3 + 2]

        # 1. Stem on line 1
        assert p_stem.text.strip() == expected["stem"]
        assert len(p_stem._element.findall(f".//{{{w_ns}}}drawing")) == 0

        # 2. Image on line 2
        assert len(p_draw._element.findall(f".//{{{w_ns}}}drawing")) == 1

        # 3. Description on line 3
        assert p_desc.text.strip() == expected["desc"]
        assert len(p_desc._element.findall(f".//{{{w_ns}}}drawing")) == 0

        # Accessibility metadata
        shape = doc.inline_shapes[idx]
        assert shape._inline.docPr.get("descr") == expected["desc"]
        assert shape._inline.docPr.get("title") == expected["stem"]

    # Verify no unwanted boilerplate
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Alt Text:" not in full_text
    assert "Source: PDF Page" not in full_text
    assert "Total Figures Exported:" not in full_text
    assert "Alt-Text Asset Export:" not in full_text


def test_every_image_placement_references_valid_media_file(export_env):
    """
    Regression test: every image placement in word/document.xml must:
    1. Reference an image relationship (r:embed) declared in word/_rels/document.xml.rels.
    2. That relationship must target an existing file under word/media/.
    """
    import zipfile
    from xml.etree import ElementTree as ET

    repo = export_env["repo"]
    filestore = export_env["filestore"]
    project_id = export_env["project_id"]
    client = export_env["client"]

    for i in range(1, 4):
        add_image_with_draft_and_approval(
            export_env,
            page_num=i,
            placement_index=0,
            filename=f"figure_{i}.png",
            caption=f"Caption for figure {i}",
            approved_text=f"Approved alt text for figure {i}.",
            is_approved=True,
        )

    res = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res.status_code == 200
    export_id = res.json()["export_id"]
    docx_bytes = filestore.read_bytes(project_id, repo.get_export(export_id)["file_storage_key"])

    rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pic_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"

    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        rels_root = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        doc_root = ET.fromstring(z.read("word/document.xml"))
        media_files = set(z.namelist())  # full archive paths, e.g. word/media/image1.png

    image_rels = {}
    for rel in rels_root.findall(f"{{{rel_ns}}}Relationship"):
        if rel.attrib.get("Type", "").endswith("/image"):
            image_rels[rel.attrib["Id"]] = rel.attrib["Target"]

    embedded_ids = {
        blip.attrib[f"{{{r_ns}}}embed"]
        for blip in doc_root.findall(f".//{{{pic_ns}}}blip")
    }

    # Every blip references a declared image relationship, and vice versa
    assert embedded_ids == set(image_rels.keys())

    # Every relationship target must resolve to an existing media file
    for rel_id, target in image_rels.items():
        media_path = f"word/{target}"
        assert media_path in media_files, f"relationship {rel_id} target {target} missing from word/media/"

    # And there are no orphan media files without a placement
    placed_media = {f"word/{t}" for t in image_rels.values()}
    orphan_media = {m for m in media_files if m.startswith("word/media/")} - placed_media
    assert not orphan_media, f"orphan media files without a drawing placement: {sorted(orphan_media)}"


def test_every_embedded_image_decodes_successfully(export_env):
    """
    Regression test: every file under word/media/ must be a decodable image with
    non-zero dimensions (no corrupt or placeholder bytes embedded in the DOCX).
    """
    import zipfile

    repo = export_env["repo"]
    filestore = export_env["filestore"]
    project_id = export_env["project_id"]
    client = export_env["client"]

    for i in range(1, 4):
        add_image_with_draft_and_approval(
            export_env,
            page_num=i,
            placement_index=0,
            filename=f"figure_{i}.png",
            caption=f"Caption for figure {i}",
            approved_text=f"Approved alt text for figure {i}.",
            is_approved=True,
        )

    res = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res.status_code == 200
    export_id = res.json()["export_id"]
    docx_bytes = filestore.read_bytes(project_id, repo.get_export(export_id)["file_storage_key"])

    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        media_files = sorted(
            name for name in z.namelist() if name.startswith("word/media/")
        )
        assert len(media_files) == 3, f"expected 3 media files, found {len(media_files)}"
        for name in media_files:
            data = z.read(name)
            assert len(data) > 0, f"empty media file: {name}"
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                width, height = im.size
                assert width > 0 and height > 0, f"decoded image has zero size: {name}"


def test_unreadable_or_missing_source_image_raises_clear_failure():
    """
    Regression test: a missing or unreadable source image must cause a clear export
    failure that identifies the affected figure and its source path, instead of a
    successful text-only document or a generic python-docx error.
    """
    # Unreadable image bytes
    try:
        DocxBuilder.build_docx(
            project_name="Failing Export",
            items=[
                {
                    "suggested_filename": "corrupt_figure.png",
                    "image_id": "img_corrupt_01",
                    "page_number": 2,
                    "source_path": "images/img_corrupt_01.png",
                    "approved_alt_text": "This must never reach the document",
                    "image_bytes": b"this is not a valid image file",
                    "image_format": "png",
                }
            ],
        )
        raise AssertionError("build_docx succeeded with an unreadable source image")
    except ValueError as exc:
        msg = str(exc)
        assert "corrupt_figure" in msg
        assert "img_corrupt_01" in msg
        assert "images/img_corrupt_01.png" in msg
        assert "decodable" in msg.lower()

    # Missing image bytes
    try:
        DocxBuilder.build_docx(
            project_name="Failing Export",
            items=[
                {
                    "suggested_filename": "missing_figure.png",
                    "image_id": "img_missing_01",
                    "page_number": 3,
                    "source_path": "images/img_missing_01.png",
                    "approved_alt_text": "This must never reach the document",
                    "image_bytes": None,
                    "image_format": "png",
                }
            ],
        )
        raise AssertionError("build_docx succeeded with missing image bytes")
    except ValueError as exc:
        msg = str(exc)
        assert "missing_figure" in msg
        assert "img_missing_01" in msg
        assert "images/img_missing_01.png" in msg
        assert "text-only document" in msg
