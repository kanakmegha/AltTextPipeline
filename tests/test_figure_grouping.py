"""
Phase 4.1 Multi-Image Caption Grouping & Cross-Page Candidate Tests
------------------------------------------------------------------
Comprehensive offline unit and integration tests covering:
- Multiple images sharing one caption on the same page
- Caption belonging to only one of several nearby images (isolated)
- Competing captions preventing improper grouping
- Multi-column layout separation
- Figure panels labelled (a), (b), and (c)
- Figure groups spanning adjacent pages (cross-page candidate)
- Unrelated image on preceding page not grouped
- Ambiguous cross-page associations marked as candidate requiring review
- Confirming, undoing, splitting, and removing images from groups
- Moving an image between groups
- Independent alt-text drafts for images sharing a caption
- Persistence across database reconnection (process restart)
- Project isolation and migration safety
- Binary asset immutability (original bytes unchanged)
"""

import os
import io
import uuid
import pytest
from PIL import Image

from src.extraction.models import BoundingBox, CaptionCandidate, ExtractedImage, FigureGroup
from src.extraction.grouping import (
    FigureGrouper,
    extract_figure_label,
    extract_caption_panel_labels,
    is_continuation_caption,
)
from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.ai.config import AIConfig
from src.ai.providers import MockAIProvider
from src.ai.generator import AltTextGenerator


def make_test_png(color: str = "red", width: int = 80, height: int = 80) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_dummy_extracted_image(
    image_id: str,
    page_number: int,
    bbox: BoundingBox,
    caption: str = "",
    caption_position: str = "below",
    needs_review: bool = False,
    panel_label: str = None,
) -> ExtractedImage:
    has_cap = bool(caption and caption.strip())
    candidates = []
    if has_cap:
        candidates.append(
            CaptionCandidate(
                text=caption,
                position=caption_position,
                gap=12.0,
                bbox=BoundingBox(bbox.x0, bbox.y1 + 10, bbox.x1, bbox.y1 + 30),
                score=10.0,
                has_figure_prefix=bool(extract_figure_label(caption)),
            )
        )
    return ExtractedImage(
        image_id=image_id,
        page_number=page_number,
        xref=100,
        placement_index=0,
        bbox=bbox,
        image_bytes=make_test_png(),
        image_format="png",
        suggested_filename=f"page_{page_number}_{image_id[:6]}.png",
        caption=caption,
        caption_position=caption_position if has_cap else None,
        caption_found=has_cap,
        needs_caption_review=needs_review,
        review_reasons=["Uncertain match"] if needs_review else [],
        caption_candidates=candidates,
        panel_label=panel_label,
    )


@pytest.fixture
def repo_and_store(tmp_path):
    db_path = str(tmp_path / "grouping_test.db")
    storage_dir = str(tmp_path / "storage")
    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    filestore = FileStore(storage_dir)
    return repo, filestore, db_path, storage_dir


# ============================================================
# 1. Same-Page Multiple Images Sharing One Caption
# ============================================================

def test_multiple_images_sharing_caption_same_page():
    """
    Two side-by-side images (Panels a and b) on page 4 sharing
    a single common caption: 'Figure 4. (a) Ground plan; (b) Elevation.'
    """
    caption = "Figure 4. (a) Ground plan; (b) Elevation."
    img_a = create_dummy_extracted_image(
        image_id="img-a-1",
        page_number=4,
        bbox=BoundingBox(x0=50, y0=100, x1=200, y1=250),
        caption=caption,
        panel_label="a",
    )
    img_b = create_dummy_extracted_image(
        image_id="img-b-2",
        page_number=4,
        bbox=BoundingBox(x0=220, y0=100, x1=370, y1=250),
        caption="",  # initially uncaptioned because matcher assigned only to img_a
        panel_label="b",
    )

    grouper = FigureGrouper()
    grouped_images, groups = grouper.group_figures([img_a, img_b])

    assert len(groups) == 1
    grp = groups[0]
    assert grp.figure_label == "Figure 4"
    assert "img-a-1" in grp.image_ids
    assert "img-b-2" in grp.image_ids
    assert grp.is_cross_page is False

    # Both images now reference the group and share the caption
    assert grouped_images[0].group_id == grp.group_id
    assert grouped_images[1].group_id == grp.group_id
    assert grouped_images[1].caption == caption
    assert grouped_images[0].panel_label == "a"
    assert grouped_images[1].panel_label == "b"
    assert grouped_images[0].association_type == "group_matched"
    assert grouped_images[1].association_type == "group_matched"


# ============================================================
# 2. Caption Belongs to Only One of Several Nearby Images
# ============================================================

def test_caption_belongs_only_to_one_nearby_image():
    """
    Two images on the same page, but one is in the top section with a specific caption,
    and another is far below in an unrelated paragraph without panel indicators.
    They must NOT be grouped together.
    """
    img_top = create_dummy_extracted_image(
        image_id="img-top",
        page_number=5,
        bbox=BoundingBox(x0=50, y0=50, x1=250, y1=150),
        caption="Figure 5. Detail of entablature molding.",
    )
    img_bottom = create_dummy_extracted_image(
        image_id="img-bottom",
        page_number=5,
        bbox=BoundingBox(x0=50, y0=500, x1=250, y1=600),
        caption="",  # uncaptioned ornament
    )

    grouper = FigureGrouper()
    grouped_images, groups = grouper.group_figures([img_top, img_bottom])

    assert len(groups) == 0
    # Top image remains single matched
    assert grouped_images[0].association_type == "single_matched"
    assert grouped_images[0].group_id is None
    # Bottom image remains uncaptioned
    assert grouped_images[1].association_type == "uncaptioned"
    assert grouped_images[1].group_id is None


# ============================================================
# 3. Competing Captions Prevent Improper Grouping
# ============================================================

def test_competing_captions_prevent_improper_grouping():
    """
    Two adjacent images on the same page, but each has its own distinct figure caption.
    Must not merge them into a single figure group.
    """
    img1 = create_dummy_extracted_image(
        image_id="img-fig-1",
        page_number=6,
        bbox=BoundingBox(x0=50, y0=100, x1=200, y1=250),
        caption="Figure 6. Plan of sanctuary.",
    )
    img2 = create_dummy_extracted_image(
        image_id="img-fig-2",
        page_number=6,
        bbox=BoundingBox(x0=220, y0=100, x1=370, y1=250),
        caption="Figure 7. Elevation of north gate.",
    )

    grouper = FigureGrouper()
    grouped_images, groups = grouper.group_figures([img1, img2])

    assert len(groups) == 0
    assert grouped_images[0].group_id is None
    assert grouped_images[1].group_id is None
    assert grouped_images[0].caption == "Figure 6. Plan of sanctuary."
    assert grouped_images[1].caption == "Figure 7. Elevation of north gate."


# ============================================================
# 4. Multi-Column Layouts
# ============================================================

def test_multi_column_page_layout_grouping():
    """
    Two-column page where Left column has an uncaptioned figure and Right column
    has an image with caption. Intervening column margin prevents grouping unless
    explicit panel labels match.
    """
    img_left = create_dummy_extracted_image(
        image_id="img-col1",
        page_number=7,
        bbox=BoundingBox(x0=50, y0=100, x1=200, y1=300),
        caption="",
    )
    img_right = create_dummy_extracted_image(
        image_id="img-col2",
        page_number=7,
        bbox=BoundingBox(x0=300, y0=100, x1=450, y1=300),
        caption="Figure 8. Coin type representing Janus.",
    )

    grouper = FigureGrouper()
    grouped_images, groups = grouper.group_figures([img_left, img_right])

    # Without panel markers or shared caption indication, multi-column images stay separate
    assert len(groups) == 0
    assert grouped_images[0].group_id is None
    assert grouped_images[1].group_id is None


# ============================================================
# 5. Figure Panels Labelled (a), (b), and (c)
# ============================================================

def test_figure_panels_a_b_c_grouped():
    """
    Three sub-panels: (a), (b), (c) stacked vertically and horizontally.
    Common caption: 'Figure 9. Architectural details: (a) base; (b) column; (c) capital.'
    """
    caption = "Figure 9. Architectural details: (a) base; (b) column; (c) capital."
    img_a = create_dummy_extracted_image(
        "img-a", 8, BoundingBox(50, 50, 150, 150), caption=caption, panel_label="a"
    )
    img_b = create_dummy_extracted_image(
        "img-b", 8, BoundingBox(160, 50, 260, 150), caption="", panel_label="b"
    )
    img_c = create_dummy_extracted_image(
        "img-c", 8, BoundingBox(270, 50, 370, 150), caption="", panel_label="c"
    )

    grouper = FigureGrouper()
    grouped_images, groups = grouper.group_figures([img_a, img_b, img_c])

    assert len(groups) == 1
    grp = groups[0]
    assert len(grp.image_ids) == 3
    for img in grouped_images:
        assert img.group_id == grp.group_id
        assert img.caption == caption
        assert img.association_type == "group_matched"


# ============================================================
# 6. Cross-Page Figures & Review Flagging
# ============================================================

def test_cross_page_figure_continuation_candidate():
    """
    Image on page 10 at bottom with caption 'Figure 10. Temple excavation.'
    Image on page 11 at top with '(continued)' text or sequential panel (b).
    Must be proposed as a candidate requiring editor review, never auto-confirmed.
    """
    img_p10 = create_dummy_extracted_image(
        image_id="img-p10",
        page_number=10,
        bbox=BoundingBox(x0=50, y0=550, x1=400, y1=750),  # near bottom of page
        caption="Figure 10. (a) Ground excavation layout.",
        panel_label="a",
    )
    img_p11 = create_dummy_extracted_image(
        image_id="img-p11",
        page_number=11,
        bbox=BoundingBox(x0=50, y0=50, x1=400, y1=250),  # near top of next page
        caption="Figure 10 (continued). (b) Stratigraphic profile.",
        panel_label="b",
    )

    grouper = FigureGrouper()
    grouped_images, groups = grouper.group_figures([img_p10, img_p11])

    assert len(groups) == 1
    grp = groups[0]
    assert grp.is_cross_page is True
    assert grp.review_status == "candidate"  # Invariant: NEVER auto-confirmed!
    assert "img-p10" in grp.image_ids
    assert "img-p11" in grp.image_ids

    # Both images require review flag
    assert grouped_images[0].needs_caption_review is True
    assert grouped_images[1].needs_caption_review is True
    assert any("Cross-page" in r for r in grouped_images[1].review_reasons)


def test_unrelated_image_on_preceding_page_not_grouped():
    """
    Image at top of page 12 has nothing in common with image at bottom of page 13.
    Pages are adjacent, but figure numbers and contexts differ.
    Must NOT associate across pages.
    """
    img_p12 = create_dummy_extracted_image(
        image_id="img-p12",
        page_number=12,
        bbox=BoundingBox(x0=50, y0=50, x1=200, y1=150),
        caption="Figure 11. Bronze mirror back.",
    )
    img_p13 = create_dummy_extracted_image(
        image_id="img-p13",
        page_number=13,
        bbox=BoundingBox(x0=50, y0=500, x1=200, y1=600),
        caption="Figure 12. Gold earring.",
    )

    grouper = FigureGrouper()
    grouped_images, groups = grouper.group_figures([img_p12, img_p13])

    assert len(groups) == 0
    assert grouped_images[0].group_id is None
    assert grouped_images[1].group_id is None


# ============================================================
# 7. Group Editing Operations: Confirm, Remove, Split, Move
# ============================================================

def test_group_confirmation_and_splitting(repo_and_store):
    repo, filestore, _, _ = repo_and_store
    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Book", "book.pdf", "k", "c")

    # Create a figure group
    group_id = str(uuid.uuid4())
    repo.create_figure_group(
        group_id=group_id,
        project_id=project_id,
        caption_text="Figure 15. Shared caption",
        caption_page_number=15,
        figure_label="Figure 15",
        review_status="candidate",
    )

    img1_id = str(uuid.uuid4())
    img2_id = str(uuid.uuid4())
    k1, c1, _ = filestore.save_image(project_id, img1_id, make_test_png(), "png")
    k2, c2, _ = filestore.save_image(project_id, img2_id, make_test_png(), "png")

    repo.create_image_asset(
        image_id=img1_id,
        project_id=project_id,
        page_number=15,
        xref=1,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Figure 15. Shared caption",
        caption_position="below",
        needs_caption_review=True,
        review_reasons=["Candidate group"],
        suggested_filename="img1.png",
        file_storage_key=k1,
        file_checksum=c1,
        image_format="png",
        group_id=group_id,
        association_type="group_matched",
    )
    repo.create_image_asset(
        image_id=img2_id,
        project_id=project_id,
        page_number=15,
        xref=2,
        placement_index=1,
        bbox={"x0": 60, "y0": 0, "x1": 110, "y1": 50},
        original_caption="Figure 15. Shared caption",
        caption_position="below",
        needs_caption_review=True,
        review_reasons=["Candidate group"],
        suggested_filename="img2.png",
        file_storage_key=k2,
        file_checksum=c2,
        image_format="png",
        group_id=group_id,
        association_type="group_matched",
    )

    # 1. Confirm group
    repo.update_group_review_status(group_id, "confirmed")
    grp = repo.get_figure_group(group_id)
    assert grp["review_status"] == "confirmed"

    # 2. Remove img2 from group
    repo.remove_image_from_group(img2_id)
    asset2 = repo.get_image_asset(img2_id)
    assert asset2["group_id"] is None
    assert asset2["association_type"] == "uncaptioned"
    assert asset2["needs_caption_review"] is True

    # img1 is still in the group
    asset1 = repo.get_image_asset(img1_id)
    assert asset1["group_id"] == group_id

    # 3. Split remaining group
    repo.remove_image_from_group(img1_id)
    repo.update_group_review_status(group_id, "split")
    assert repo.get_figure_group(group_id)["review_status"] == "split"
    assert repo.get_image_asset(img1_id)["group_id"] is None


def test_moving_image_between_groups(repo_and_store):
    repo, filestore, _, _ = repo_and_store
    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Book", "book.pdf", "k", "c")

    grp1_id = str(uuid.uuid4())
    grp2_id = str(uuid.uuid4())
    repo.create_figure_group(grp1_id, project_id, "Figure A", 1, figure_label="Figure A")
    repo.create_figure_group(grp2_id, project_id, "Figure B", 1, figure_label="Figure B")

    img_id = str(uuid.uuid4())
    k, c, _ = filestore.save_image(project_id, img_id, make_test_png(), "png")
    repo.create_image_asset(
        image_id=img_id,
        project_id=project_id,
        page_number=1,
        xref=1,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Figure A",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="img.png",
        file_storage_key=k,
        file_checksum=c,
        image_format="png",
        group_id=grp1_id,
        association_type="group_matched",
    )

    # Move image from grp1 to grp2
    repo.update_image_group_association(
        image_id=img_id,
        group_id=grp2_id,
        association_type="group_matched",
        panel_label="b",
        needs_caption_review=False,
    )
    repo.update_image_caption(img_id, "Figure B", "below")

    updated = repo.get_image_asset(img_id)
    assert updated["group_id"] == grp2_id
    assert updated["original_caption"] == "Figure B"
    assert updated["panel_label"] == "b"


# ============================================================
# 8. Independent Alt-Text Drafts for Grouped Images
# ============================================================

def test_independent_alt_text_drafts_for_images_sharing_caption(repo_and_store):
    """
    Invariant: Sharing a caption MUST NEVER cause multiple images to share
    or overwrite one alt-text draft. Each image has its own UUID, draft versions,
    and approval state.
    """
    repo, filestore, _, _ = repo_and_store
    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Book", "book.pdf", "k", "c")

    group_id = str(uuid.uuid4())
    repo.create_figure_group(group_id, project_id, "Figure 20. Shared", 20)

    img1_id = str(uuid.uuid4())
    img2_id = str(uuid.uuid4())

    k1, c1, _ = filestore.save_image(project_id, img1_id, make_test_png("red"), "png")
    k2, c2, _ = filestore.save_image(project_id, img2_id, make_test_png("green"), "png")

    repo.create_image_asset(
        img1_id, project_id, 20, 1, 0, {"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        "Figure 20. Shared", "below", False, [], "fig1.png", k1, c1, "png",
        group_id=group_id, panel_label="a"
    )
    repo.create_image_asset(
        img2_id, project_id, 20, 2, 1, {"x0": 60, "y0": 0, "x1": 110, "y1": 50},
        "Figure 20. Shared", "below", False, [], "fig2.png", k2, c2, "png",
        group_id=group_id, panel_label="b"
    )

    mock_provider = MockAIProvider(fixed_text="Red square representing panel a.")
    generator = AltTextGenerator(repo, filestore, mock_provider, AIConfig())

    # Generate draft for img1
    res1, ver1 = generator.generate_for_image(project_id, img1_id)
    assert ver1["text"] == "Red square representing panel a."

    # img2 still has no drafts!
    assert repo.get_latest_version(img2_id) is None
    assert len(repo.list_image_versions(img2_id)) == 0

    # Generate draft for img2
    mock_provider.fixed_text = "Green square representing panel b."
    res2, ver2 = generator.generate_for_image(project_id, img2_id)
    assert ver2["text"] == "Green square representing panel b."

    # Verify both versions exist independently
    v1_reloaded = repo.get_latest_version(img1_id)
    v2_reloaded = repo.get_latest_version(img2_id)
    assert v1_reloaded["text"] == "Red square representing panel a."
    assert v2_reloaded["text"] == "Green square representing panel b."
    assert v1_reloaded["id"] != v2_reloaded["id"]


# ============================================================
# 9. Persistence Across Reconnect and Asset Immutability
# ============================================================

def test_persistence_across_process_restart_and_asset_immutability(repo_and_store):
    repo, filestore, db_path, storage_dir = repo_and_store
    project_id = str(uuid.uuid4())
    repo.create_project(project_id, "Persistence Test", "book.pdf", "k", "c")

    group_id = str(uuid.uuid4())
    repo.create_figure_group(group_id, project_id, "Persistent Group Caption", 30)

    original_png = make_test_png("blue", 120, 120)
    img_id = str(uuid.uuid4())
    k, c, _ = filestore.save_image(project_id, img_id, original_png, "png")

    repo.create_image_asset(
        img_id, project_id, 30, 1, 0, {"x0": 0, "y0": 0, "x1": 100, "y1": 100},
        "Persistent Group Caption", "below", False, [], "pers.png", k, c, "png",
        group_id=group_id, association_type="group_matched"
    )

    # Simulate restart by instantiating new connection & repo pointing to same db_path
    new_conn = DatabaseConnection(db_path)
    new_repo = Repository(new_conn)
    new_store = FileStore(storage_dir)

    reloaded_grp = new_repo.get_figure_group(group_id)
    assert reloaded_grp is not None
    assert reloaded_grp["caption_text"] == "Persistent Group Caption"

    reloaded_img = new_repo.get_image_asset(img_id)
    assert reloaded_img["group_id"] == group_id
    assert reloaded_img["association_type"] == "group_matched"

    # Verify byte-for-byte asset immutability
    stored_path = new_store.resolve_path(project_id, reloaded_img["file_storage_key"])
    assert stored_path.read_bytes() == original_png


# ============================================================
# 10. Synthetic PDF Fixtures: Same-Page Group & Cross-Page Candidate
# ============================================================

def test_synthetic_pdf_case9_same_page_group():
    from src.extraction.engine import ExtractionEngine
    fixture_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "synthetic_pdfs", "case9_same_page_group.pdf"
    )
    assert os.path.exists(fixture_path), "case9_same_page_group.pdf fixture missing"

    engine = ExtractionEngine()
    result = engine.process_pdf(fixture_path)

    assert len(result.images) == 2
    assert len(result.groups) == 1

    grp = result.groups[0]
    assert grp.figure_label == "Figure 10"
    assert grp.is_cross_page is False
    assert len(grp.image_ids) == 2

    # Both images are linked to the group
    img1, img2 = result.images[0], result.images[1]
    assert img1.group_id == grp.group_id
    assert img2.group_id == grp.group_id
    assert "Figure 10" in img1.caption
    assert "Figure 10" in img2.caption
    assert img1.association_type == "group_matched"
    assert img2.association_type == "group_matched"


def test_synthetic_pdf_case10_cross_page_group_candidate():
    from src.extraction.engine import ExtractionEngine
    fixture_path = os.path.join(
        os.path.dirname(__file__), "fixtures", "synthetic_pdfs", "case10_cross_page_group.pdf"
    )
    assert os.path.exists(fixture_path), "case10_cross_page_group.pdf fixture missing"

    engine = ExtractionEngine()
    result = engine.process_pdf(fixture_path)

    assert len(result.images) == 2
    assert len(result.groups) == 1

    grp = result.groups[0]
    assert grp.is_cross_page is True
    assert grp.review_status == "candidate"  # Invariant: Never auto-confirmed
    assert len(grp.image_ids) == 2

    # The cross-page candidate requires editorial review
    img_p1 = [i for i in result.images if i.page_number == 1][0]
    img_p2 = [i for i in result.images if i.page_number == 2][0]
    assert img_p1.group_id == grp.group_id
    assert img_p2.group_id == grp.group_id
    assert img_p2.needs_caption_review is True
