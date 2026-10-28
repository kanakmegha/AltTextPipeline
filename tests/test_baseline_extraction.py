"""
Phase 1 Automated Test Suite
----------------------------
Verifies baseline extraction behavior against synthetic digital PDFs with
known ground truth geometry and caption relationships.
"""

import os
import json
import pytest
from tests.baseline_adapter import extract_from_pdf
from tests.fixtures.create_synthetic_pdfs import generate_synthetic_fixtures


@pytest.fixture(scope="session")
def synthetic_fixtures(tmp_path_factory):
    fixtures_dir = tmp_path_factory.mktemp("synthetic_pdfs")
    ground_truth = generate_synthetic_fixtures(str(fixtures_dir))
    return str(fixtures_dir), ground_truth


def test_case1_caption_below(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case1_caption_below"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 1
    rec = records[0]
    expected = case_info["expected_items"][0]

    assert rec["caption_found"] is True
    assert rec["caption"] == expected["expected_caption"]
    assert rec["caption_position"] == expected["expected_position"]
    assert rec["needs_caption_review"] is expected["expected_needs_review"]


def test_case2_caption_above(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case2_caption_above"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 1
    rec = records[0]
    expected = case_info["expected_items"][0]

    assert rec["caption_found"] is True
    assert rec["caption"] == expected["expected_caption"]
    assert rec["caption_position"] == expected["expected_position"]
    assert rec["needs_caption_review"] is expected["expected_needs_review"]


def test_case3_multiline_captions(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case3_multiline_captions"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 2

    # Page 1: multiline below
    rec1 = next(r for r in records if r["page"] == 1)
    exp1 = case_info["expected_items"][0]
    assert rec1["caption_found"] is True
    assert rec1["caption"] == exp1["expected_caption"]
    assert rec1["caption_position"] == exp1["expected_position"]

    # Page 2: multiline above
    rec2 = next(r for r in records if r["page"] == 2)
    exp2 = case_info["expected_items"][1]
    assert rec2["caption_found"] is True
    assert rec2["caption"] == exp2["expected_caption"]
    assert rec2["caption_position"] == exp2["expected_position"]


def test_case4_multiple_images_one_page(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case4_multiple_images_one_page"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 2

    # Placements ordered vertically (top to bottom)
    rec1 = records[0]
    rec2 = records[1]

    exp1 = case_info["expected_items"][0]
    exp2 = case_info["expected_items"][1]

    assert rec1["caption"] == exp1["expected_caption"]
    assert rec2["caption"] == exp2["expected_caption"]
    assert rec1["filename"] != rec2["filename"]


def test_case5_two_column_layout(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case5_two_column_layout"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 2

    # Column 1 image
    rec1 = records[0]
    exp1 = case_info["expected_items"][0]
    assert rec1["caption"] == exp1["expected_caption"]
    assert rec1["caption_position"] == exp1["expected_position"]

    # Column 2 image
    rec2 = records[1]
    exp2 = case_info["expected_items"][1]
    assert rec2["caption"] == exp2["expected_caption"]
    assert rec2["caption_position"] == exp2["expected_position"]


def test_case6_image_without_caption(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case6_image_without_caption"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 1
    rec = records[0]
    exp = case_info["expected_items"][0]

    assert rec["caption_found"] is False
    assert rec["caption"] == ""
    assert rec["caption_position"] is None
    assert rec["needs_caption_review"] is True


def test_case7_duplicate_captions(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case7_duplicate_captions"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 2

    filenames = [r["filename"] for r in records]
    assert len(filenames) == len(set(filenames)), "Filenames must be unique"
    for r in records:
        assert r["caption_found"] is True
        assert "Figure 7" in r["caption"]


def test_case8_reused_image_object(synthetic_fixtures):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case8_reused_image_object"]
    pdf_path = case_info["file"]

    records, errors, summary = extract_from_pdf(pdf_path)

    assert errors == []
    assert len(records) == 2

    # Verify both distinct placements are extracted and correctly matched
    captions = [r["caption"] for r in records]
    assert any("Figure 8" in c for c in captions)
    assert any("Figure 9" in c for c in captions)
