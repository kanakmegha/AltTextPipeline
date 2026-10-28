"""
Test Suite for Phase 2 Modular Extraction Engine
------------------------------------------------
Verifies:
1. All synthetic benchmark test scenarios pass against ExtractionEngine.
2. Running headers / footers are ignored.
3. Clean filenames and stable UUIDs are generated.
4. Multiline grouping and column boundaries work properly.
5. Page chunking divides document cleanly and produces consistent results.
"""

import os
import uuid
import pytest
from src.extraction.engine import ExtractionEngine
from tests.fixtures.create_synthetic_pdfs import generate_synthetic_fixtures


@pytest.fixture(scope="session")
def synthetic_fixtures(tmp_path_factory):
    fixtures_dir = tmp_path_factory.mktemp("modular_synthetic_pdfs")
    ground_truth = generate_synthetic_fixtures(str(fixtures_dir))
    return str(fixtures_dir), ground_truth


@pytest.fixture
def engine():
    return ExtractionEngine(max_caption_gap=75.0, horizontal_tolerance=40.0)


def test_modular_case1_caption_below(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case1_caption_below"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 1
    img = res.images[0]
    exp = case_info["expected_items"][0]

    assert img.caption_found is True
    assert img.caption == exp["expected_caption"]
    assert img.caption_position == exp["expected_position"]
    assert img.needs_caption_review is exp["expected_needs_review"]
    # Verify clean filename and UUID
    assert uuid.UUID(img.image_id)
    assert not img.suggested_filename.startswith("Page1_Placement1_")
    assert "Figure_1" in img.suggested_filename


def test_modular_case2_caption_above(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case2_caption_above"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 1
    img = res.images[0]
    exp = case_info["expected_items"][0]

    assert img.caption_found is True
    assert img.caption == exp["expected_caption"]
    assert img.caption_position == exp["expected_position"]
    assert img.needs_caption_review is exp["expected_needs_review"]
    assert "Figure_2" in img.suggested_filename


def test_modular_case3_multiline_captions(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case3_multiline_captions"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 2

    img1 = next(i for i in res.images if i.page_number == 1)
    exp1 = case_info["expected_items"][0]
    assert img1.caption_found is True
    assert img1.caption == exp1["expected_caption"]
    assert img1.caption_position == exp1["expected_position"]

    img2 = next(i for i in res.images if i.page_number == 2)
    exp2 = case_info["expected_items"][1]
    assert img2.caption_found is True
    assert img2.caption == exp2["expected_caption"]
    assert img2.caption_position == exp2["expected_position"]


def test_modular_case4_multiple_images_one_page(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case4_multiple_images_one_page"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 2
    img1, img2 = res.images[0], res.images[1]

    exp1, exp2 = case_info["expected_items"][0], case_info["expected_items"][1]
    assert img1.caption == exp1["expected_caption"]
    assert img2.caption == exp2["expected_caption"]
    assert img1.image_id != img2.image_id
    assert img1.suggested_filename != img2.suggested_filename


def test_modular_case5_two_column_layout(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case5_two_column_layout"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 2

    img1, img2 = res.images[0], res.images[1]
    exp1, exp2 = case_info["expected_items"][0], case_info["expected_items"][1]
    assert img1.caption == exp1["expected_caption"]
    assert img2.caption == exp2["expected_caption"]


def test_modular_case6_image_without_caption(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case6_image_without_caption"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 1
    img = res.images[0]

    assert img.caption_found is False
    assert img.caption == ""
    assert img.caption_position is None
    assert img.needs_caption_review is True
    assert "No caption found within proximity tolerance" in img.review_reasons


def test_modular_case7_duplicate_captions(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case7_duplicate_captions"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 2

    img1, img2 = res.images[0], res.images[1]
    assert img1.image_id != img2.image_id
    assert img1.suggested_filename != img2.suggested_filename
    assert img1.caption == img2.caption


def test_modular_case8_reused_image_object(synthetic_fixtures, engine):
    fixtures_dir, ground_truth = synthetic_fixtures
    case_info = ground_truth["case8_reused_image_object"]
    pdf_path = case_info["file"]

    res = engine.process_pdf(pdf_path)

    assert res.errors == []
    assert len(res.images) == 2

    img1, img2 = res.images[0], res.images[1]
    # Reused object has same xref but different placements and stable UUIDs
    assert img1.xref == img2.xref
    assert img1.image_id != img2.image_id
    assert "Figure 8" in img1.caption
    assert "Figure 9" in img2.caption


def test_chunking_utility(engine):
    chunks = engine.get_chunks(total_pages=257)
    assert len(chunks) == 13
    assert chunks[0].start_page == 1
    assert chunks[0].end_page == 20
    assert chunks[-1].start_page == 241
    assert chunks[-1].end_page == 257
