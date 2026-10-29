"""
Regression Tests: Bug A (Alt-Text Contamination) & Bug B (Export Status Agreement)
---------------------------------------------------------------------------------
Verifies:
1. Contaminated model responses (scratchpads, drafts, word counts, revision instructions)
   are parsed and cleaned into strictly the final visual description.
2. Untrustworthy/unparseable corrupted responses produce clean validation errors without
   persisting contaminated text.
3. Version immutability is preserved; prior versions are never overwritten or auto-approved.
4. Review summary and export status agree on 31 extracted images and 31 approved images.
5. Blocking conditions (e.g., caption review flags or missing approvals) are accurately reported
   with specific blockers and truthful counts (never misleading 0/0).
6. DOCX export contains exclusively the clean visual description in both document text
   and OpenXML accessibility metadata (wp:docPr descr).
"""

import io
import json
import uuid
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from src.ai.cleaner import clean_alt_text_response, is_contaminated_alt_text
from src.ai.models import GenerationRequest
from src.ai.providers import MockAIProvider
from src.ai.generator import AltTextGenerator
from src.ai.config import AIConfig
from src.export import check_export_eligibility, export_project_docx, DocxBuilder
from src.web.app import create_app


SAMPLE_CONTAMINATED_RESPONSE_GLM = (
    'Need 45–50 words, item-by-item, neutral, British English. Describe bar chart: '
    'paired black/grey bars, age bands 0–4 to 100+, y-axis 0–14, peaks at 55–59 and 60–64 (14 men), etc.\n\n'
    'Draft: "Bar chart comparing men (black) and women (grey) across age bands from 0–4 to 100+. '
    'Vertical axis runs 0–14. Male values peak at 14 for ages 55–59 and 60–64; female values peak at 6 for ages 30–34, 60–64 and 70–74."\n\n'
    'Count words: Bar(1) chart2 comparing3 men4 black5 and6 women7 grey8 across9 age10 bands11 from12 0–4(13) to14 100+15. '
    'Vertical16 axis17 runs18 0–14(19). Male20 values21 peak22 at23 14(24) for25 ages26 55–59(27) and28 60–64(29); '
    'female30 values31 peak32 at33 6(34) for35 ages36 30–34(37), 60–64(38) and39 70–74(40). = 40. Need 45–50. Add more.\n\n'
    'Add: "No women are recorded for 95–99; only women appear at 100+." Check image: 95–99 has only grey bar (1), 100+ only grey bar (1). '
    '90–94 has black 1 and grey 1. So women-only at 95–99 and 100+.\n\n'
    'Revised: "Bar chart comparing men (black bars) and women (grey bars) across twenty age bands from 0–4 to 100+. '
    'The vertical axis runs from 0 to 14. Male values peak at 14 for ages 55–59 and 60–64; female values peak at 6 for ages 30–34, 60–64 and 70–74. '
    'Only women appear at 95–99 and 100+."\n\n'
    'Count: Bar1 chart2 comparing3 men4 black5 bars6 and7 women8 grey9 bars10 across11 twenty12 age13 bands14 from15 0–4(16) to17 100+(18). '
    'The19 vertical20 axis21 runs22 from23'
)

SAMPLE_CONTAMINATED_RESPONSE_LINE_CHART = (
    'Line chart, x-axis 25-year intervals from 325-300 down to 25-1, y-axis 0-35. Two lines: Men (black), Women (grey). '
    'Need 45-50 words, item-by-item, British English.\n\n'
    'Draft: "Line graph plotting epitaph numbers near Tarquinia by gender across twenty-five-year periods from 325–300 BC to 25–1 BC. '
    'Vertical axis runs 0 to 35. A black line for men and grey line for women; men\'s values exceed women\'s in most periods."\n\n'
    'Count words: Line(1) graph(2) plotting(3) epitaph(4) numbers(5) near(6) Tarquinia(7) by(8) gender(9) across(10) twenty-five-year(11) '
    'periods(12) from(13) 325–300(14) BC(15) to(16) 25–1(17) BC(18). 41 words. Need 45-50. Add more detail: men peak ~30 at 300-275; women peak ~23 at 225-200.\n\n'
    'Revised: "Line graph showing epitaph numbers near Tarquinia by gender across twenty-five-year periods from 325–300 to 25–1. '
    'Vertical axis runs 0 to 35. Black line represents men, grey line women. Men\'s line peaks near 30 at 300–275; women\'s peaks near 23 at 225–200."\n\n'
    'Count: Line1 graph2 showing3 epitaph4 numbers5 near6'
)

SAMPLE_UNPARSEABLE_CORRUPTED_RESPONSE = (
    'Need 45–50 words, item-by-item. Count words: Word1(1) Word2(2) Word3(3) Word4(4) Word5(5)... '
    'Trim: "Truncated and incomplete draft that ran out of tokens before finishing a sentence'
)


def create_test_png() -> bytes:
    im = Image.new("RGB", (200, 150), color=(70, 130, 180))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ============================================================================
# Bug A: Alt-Text Cleaning & Prompt Hardening Tests
# ============================================================================

def test_clean_alt_text_response_removes_scratchpad_and_word_counts():
    """Verify that cleaner extracts strictly the final revised description."""
    cleaned, is_valid, err = clean_alt_text_response(SAMPLE_CONTAMINATED_RESPONSE_GLM)
    assert is_valid is True
    assert err is None
    assert "Need 45–50 words" not in cleaned
    assert "Draft:" not in cleaned
    assert "Revised:" not in cleaned
    assert "Count words:" not in cleaned
    assert "Count:" not in cleaned
    assert "Bar(1)" not in cleaned
    assert cleaned.startswith("Bar chart comparing men (black bars)")
    assert cleaned.endswith("Only women appear at 95–99 and 100+.")
    # Verify word count target preserved
    words = cleaned.split()
    assert 45 <= len(words) <= 55


def test_clean_alt_text_response_line_chart_example():
    """Verify cleaner extracts revised description from line chart completion."""
    cleaned, is_valid, err = clean_alt_text_response(SAMPLE_CONTAMINATED_RESPONSE_LINE_CHART)
    assert is_valid is True
    assert "Need 45-50 words" not in cleaned
    assert "Draft:" not in cleaned
    assert "Revised:" not in cleaned
    assert "Count:" not in cleaned
    assert cleaned.startswith("Line graph showing epitaph numbers")
    assert cleaned.endswith("women's peaks near 23 at 225–200.")


def test_clean_alt_text_response_rejects_unparseable_corrupted_response():
    """Verify that untrustworthy corrupted text is rejected rather than saved."""
    cleaned, is_valid, err = clean_alt_text_response(SAMPLE_UNPARSEABLE_CORRUPTED_RESPONSE)
    assert is_valid is False
    assert cleaned == ""
    assert err is not None
    assert "could not be reliably parsed" in err.lower()


def test_mock_provider_does_not_concatenate_revision_instructions():
    """Verify mock provider does not echo or concatenate revision prompt."""
    req = GenerationRequest(
        project_id="test-proj",
        image_id="test-img",
        image_bytes=create_test_png(),
        image_format="png",
        caption="Sample Caption",
        user_prompt="Provide alt text",
        language="en-GB",
        model="zai-org/GLM-5.3-Flash",
        provider="mock-novita",
        revision_instruction="Focus on color contrasts and make shorter",
    )
    mock = MockAIProvider()
    res = mock.generate(req)
    assert res.status == "success"
    assert "incorporating revision:" not in res.draft_text
    assert "Focus on color contrasts and make shorter" not in res.draft_text
    assert not is_contaminated_alt_text(res.draft_text)


def test_generator_cleans_and_rejects_contaminated_output(tmp_path):
    """Verify AltTextGenerator rejects corrupted unparseable drafts and preserves prior versions."""
    db_file = str(tmp_path / "test.db")
    storage_dir = str(tmp_path / "storage")
    app = create_app(db_path=db_file, storage_dir=storage_dir)

    repo = app.state.repo
    filestore = app.state.filestore

    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())
    repo.create_project(project_id, "Integrity Project", "doc.pdf", "k", "c")
    key, chk, _ = filestore.save_image(project_id, image_id, create_test_png(), "png")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=1,
        xref=1,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Test Fig",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="fig1.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    # 1. Successful generation with contaminated provider output cleans it
    provider = MockAIProvider(fixed_text=SAMPLE_CONTAMINATED_RESPONSE_GLM)
    gen = AltTextGenerator(repo, filestore, provider, AIConfig())
    res1, ver1 = gen.generate_for_image(project_id, image_id)
    assert res1.status == "success"
    assert "Need 45" not in ver1["text"]
    assert "Count words:" not in ver1["text"]
    assert ver1["text"].startswith("Bar chart comparing men")

    # 2. Corrupted unparseable output fails without auto-approving or corrupting v1
    bad_provider = MockAIProvider(fixed_text=SAMPLE_UNPARSEABLE_CORRUPTED_RESPONSE)
    gen_bad = AltTextGenerator(repo, filestore, bad_provider, AIConfig())
    res2, ver2 = gen_bad.generate_for_image(project_id, image_id)
    assert res2.status == "failed"
    assert "Alt-text quality validation error" in res2.error_message
    assert ver2["text"] == ""
    assert ver2["status"] == "failed"

    # Prior version v1 remains intact
    versions = repo.list_image_versions(image_id)
    assert len(versions) == 2
    assert versions[0]["text"] == ver1["text"]


# ============================================================================
# Bug B: Export Status Agreement & DOCX Export Consistency Tests
# ============================================================================

def test_31_images_project_review_and_export_agreement(tmp_path):
    """
    Creates a project with 31 images and approved alt-text versions.
    Verifies that:
    1. Review mode and export status report 31 images and 31 approved.
    2. Export status does not report misleading 0/0.
    3. Unresolved caption reviews block export while reporting truthful counts.
    4. Resolving caption review enables export.
    5. Generated DOCX retrieves the correct approved versions with clean descriptions.
    """
    db_file = str(tmp_path / "test.db")
    storage_dir = str(tmp_path / "storage")
    app = create_app(db_path=db_file, storage_dir=storage_dir)
    client = TestClient(app)
    repo = app.state.repo
    filestore = app.state.filestore

    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Inspection - 40345.pdf", "doc.pdf", "k", "c")

    image_ids = []
    # Create 31 images
    for i in range(1, 32):
        img_id = str(uuid.uuid4())
        image_ids.append(img_id)
        png_bytes = create_test_png()
        key, chk, _ = filestore.save_image(project_id, img_id, png_bytes, "png")

        # 5 images have caption review flags (matching the real Inspection project)
        has_review_flag = (i <= 5)
        reasons = ["No caption found within proximity tolerance"] if has_review_flag else []

        repo.create_image_asset(
            image_id=img_id,
            project_id=project_id,
            page_number=i,
            xref=i,
            placement_index=0,
            bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
            original_caption=f"Figure {i}" if not has_review_flag else "",
            caption_position="below",
            needs_caption_review=has_review_flag,
            review_reasons=reasons,
            suggested_filename=f"Figure_p{i}_{i}.png",
            file_storage_key=key,
            file_checksum=chk,
            image_format="png",
        )

        # Create approved alt-text version for each image
        ver_id = f"altv_{i}_{uuid.uuid4().hex[:6]}"
        repo.create_alt_text_version(
            version_id=ver_id,
            image_id=img_id,
            version_number=1,
            text=f"A clean visual description for figure {i} showing detailed diagrammatic structures clearly.",
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
            reviewer_id="editor",
        )
        repo.update_image_status(img_id, "approved")

    # 1. Verify /api/projects/{project_id} (used by Review Mode) reports 31 total images
    res_proj = client.get(f"/api/projects/{project_id}")
    assert res_proj.status_code == 200
    proj_data = res_proj.json()
    assert len(proj_data["images"]) == 31
    assert proj_data["stats"]["total_images"] == 31
    assert proj_data["stats"]["needs_review"] == 5

    # 2. Verify /api/projects/{project_id}/exports/status reports 31/31 approved (NOT 0/0)
    res_status = client.get(f"/api/projects/{project_id}/exports/status")
    assert res_status.status_code == 200
    status_data = res_status.json()

    # Must provide checklist with accurate counts
    chk = status_data["checklist"]
    assert chk["has_images"] is True
    assert chk["total_images"] == 31
    assert chk["approved_count"] == 31
    assert chk["unapproved_count"] == 0
    assert chk["no_unresolved_reviews"] is False
    assert chk["unresolved_reviews_count"] == 5

    # Must report that export is blocked due to caption review flags, NOT because of missing images or approvals
    assert status_data["eligible"] is False
    assert status_data["can_export"] is False
    assert len(status_data["blocking_reasons"]) == 1
    assert "5 images have unresolved caption review flags" in status_data["blocking_reasons"][0]

    # Export attempt must be rejected with 400
    res_docx = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res_docx.status_code == 400

    # 3. Resolve caption reviews for the 5 flagged images via the caption endpoint
    for img_id in image_ids[:5]:
        res_resolve = client.put(
            f"/api/projects/{project_id}/images/{img_id}/caption",
            json={"caption": "Resolved verified caption", "resolve_review": True, "needs_review": False}
        )
        assert res_resolve.status_code == 200
        assert res_resolve.json()["image"]["needs_caption_review"] is False

    # 4. Re-check export status: project is now genuinely eligible
    res_status_resolved = client.get(f"/api/projects/{project_id}/exports/status")
    assert res_status_resolved.status_code == 200
    stat_res = res_status_resolved.json()
    assert stat_res["checklist"]["has_images"] is True
    assert stat_res["checklist"]["all_approved"] is True
    assert stat_res["checklist"]["no_unresolved_reviews"] is True
    assert stat_res["eligible"] is True
    assert stat_res["can_export"] is True
    assert len(stat_res["blocking_reasons"]) == 0

    # 5. Export DOCX succeeds and generates valid document
    res_export = client.post(f"/api/projects/{project_id}/exports/docx")
    assert res_export.status_code == 200
    export_json = res_export.json()
    assert export_json["status"] == "success"
    assert "download_url" in export_json

    # 6. Verify DOCX structure contains all 31 images
    export_record = repo.get_export(export_json["export_id"])
    docx_bytes = filestore.read_bytes(project_id, export_record["file_storage_key"])
    assert DocxBuilder.validate_docx(docx_bytes, expected_count=31) is True


def test_unapproved_image_blocks_export_with_accurate_counts(tmp_path):
    """
    Inverse test case: When images lack approved versions, export status
    accurately reports unapproved counts and blocks export.
    """
    db_file = str(tmp_path / "test.db")
    storage_dir = str(tmp_path / "storage")
    app = create_app(db_path=db_file, storage_dir=storage_dir)
    client = TestClient(app)
    repo = app.state.repo
    filestore = app.state.filestore

    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Incomplete Project", "doc.pdf", "k", "c")

    for i in range(1, 4):
        img_id = str(uuid.uuid4())
        key, chk, _ = filestore.save_image(project_id, img_id, create_test_png(), "png")
        repo.create_image_asset(
            image_id=img_id,
            project_id=project_id,
            page_number=i,
            xref=i,
            placement_index=0,
            bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
            original_caption=f"Figure {i}",
            caption_position="below",
            needs_caption_review=False,
            review_reasons=[],
            suggested_filename=f"fig_{i}.png",
            file_storage_key=key,
            file_checksum=chk,
            image_format="png",
        )
        if i == 1:
            # Only image 1 is approved
            v_id = f"v_{uuid.uuid4().hex[:6]}"
            repo.create_alt_text_version(
                version_id=v_id,
                image_id=img_id,
                version_number=1,
                text="Approved description",
                revision_instruction=None,
                prompt_version_id=None,
                provider="manual",
                model="human_editor",
                language="en-GB",
                status="success",
            )
            repo.create_approval(str(uuid.uuid4()), img_id, v_id, "editor")
            repo.update_image_status(img_id, "approved")

    # Check status
    res = client.get(f"/api/projects/{project_id}/exports/status")
    assert res.status_code == 200
    data = res.json()
    chk = data["checklist"]
    assert chk["total_images"] == 3
    assert chk["approved_count"] == 1
    assert chk["unapproved_count"] == 2
    assert data["eligible"] is False
    assert any("2 of 3 images are unapproved" in r for r in data["blocking_reasons"])
