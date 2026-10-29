"""
Synthetic PDF Generator for Phase 1 Test Fixtures
-------------------------------------------------
Generates digital PDFs with known ground-truth geometry, image placements,
and caption relationships to benchmark the baseline extraction engine.
"""

import os
import io
import json
import pymupdf as fitz
from PIL import Image, ImageDraw


def _create_sample_image(color="blue", size=(300, 200), label="Sample"):
    """Create in-memory PNG bytes with distinct visual colors."""
    img = Image.new("RGB", size, color=color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([5, 5, size[0]-5, size[1]-5], outline="white", width=3)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _safe_insert_textbox(page, rect, text, fontsize=11):
    """Insert text with automatic height safety check to ensure it is not silently omitted."""
    rc = page.insert_textbox(rect, text, fontsize=fontsize)
    if rc < 0:
        # Retry with expanded height
        expanded_rect = fitz.Rect(rect.x0, rect.y0, rect.x1 + 100, rect.y1 + 40)
        rc = page.insert_textbox(expanded_rect, text, fontsize=fontsize)
        if rc < 0:
            raise RuntimeError(f"Failed to insert text in PDF: {text}")


def generate_synthetic_fixtures(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    ground_truth = {}

    img_blue = _create_sample_image("navy", (300, 200), "Fig 1")
    img_green = _create_sample_image("darkgreen", (300, 200), "Fig 2")
    img_red = _create_sample_image("firebrick", (300, 200), "Fig 3")
    img_purple = _create_sample_image("purple", (300, 200), "Fig 4")
    img_amber = _create_sample_image("darkgoldenrod", (300, 200), "Fig 5")

    # ============================================================
    # Case 1: Caption Below Image
    # ============================================================
    p1 = os.path.join(output_dir, "case1_caption_below.pdf")
    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    page.insert_image(fitz.Rect(100, 100, 400, 300), stream=img_blue)
    _safe_insert_textbox(
        page,
        fitz.Rect(100, 315, 450, 355),
        "Figure 1. Archaeological site plan of the central acropolis.",
        fontsize=11
    )
    doc.save(p1)
    doc.close()

    ground_truth["case1_caption_below"] = {
        "file": p1,
        "description": "Single image with standard caption positioned below",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "Figure 1. Archaeological site plan of the central acropolis.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            }
        ]
    }

    # ============================================================
    # Case 2: Caption Above Image
    # ============================================================
    p2 = os.path.join(output_dir, "case2_caption_above.pdf")
    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    _safe_insert_textbox(
        page,
        fitz.Rect(100, 75, 450, 105),
        "Figure 2. Elevation contour map of the northern ridge.",
        fontsize=11
    )
    page.insert_image(fitz.Rect(100, 115, 400, 315), stream=img_green)
    doc.save(p2)
    doc.close()

    ground_truth["case2_caption_above"] = {
        "file": p2,
        "description": "Single image with standard caption positioned above",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "Figure 2. Elevation contour map of the northern ridge.",
                "expected_position": "above",
                "expected_caption_found": True,
                "expected_needs_review": False
            }
        ]
    }

    # ============================================================
    # Case 3: Multiline Captions (Below & Above)
    # ============================================================
    p3 = os.path.join(output_dir, "case3_multiline_captions.pdf")
    doc = fitz.open()

    # Page 1: Multiline below
    # Two lines within same block or consecutive blocks within 28pt
    page1 = doc.new_page(width=500, height=700)
    page1.insert_image(fitz.Rect(100, 100, 400, 300), stream=img_red)
    _safe_insert_textbox(
        page1,
        fitz.Rect(100, 312, 450, 332),
        "Figure 3. Stratigraphic sequence of Trench IV showing multiple",
        fontsize=11
    )
    _safe_insert_textbox(
        page1,
        fitz.Rect(100, 334, 450, 354),
        "sedimentary layers and archaeological horizons from the late bronze age.",
        fontsize=11
    )

    # Page 2: Multiline above
    page2 = doc.new_page(width=500, height=700)
    _safe_insert_textbox(
        page2,
        fitz.Rect(100, 68, 450, 88),
        "Plate 1. Microscopic analysis of ceramic temper showing quartz inclusions",
        fontsize=11
    )
    _safe_insert_textbox(
        page2,
        fitz.Rect(100, 90, 450, 110),
        "and organic matter preserved in the matrix.",
        fontsize=11
    )
    page2.insert_image(fitz.Rect(100, 120, 400, 320), stream=img_purple)

    doc.save(p3)
    doc.close()

    ground_truth["case3_multiline_captions"] = {
        "file": p3,
        "description": "Multiline captions above and below across consecutive pages",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "Figure 3. Stratigraphic sequence of Trench IV showing multiple sedimentary layers and archaeological horizons from the late bronze age.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            },
            {
                "page": 2,
                "placement": 1,
                "expected_caption": "Plate 1. Microscopic analysis of ceramic temper showing quartz inclusions and organic matter preserved in the matrix.",
                "expected_position": "above",
                "expected_caption_found": True,
                "expected_needs_review": False
            }
        ]
    }

    # ============================================================
    # Case 4: Multiple Images on One Page
    # ============================================================
    p4 = os.path.join(output_dir, "case4_multiple_images_one_page.pdf")
    doc = fitz.open()
    page = doc.new_page(width=500, height=750)
    # Image 1 (top)
    page.insert_image(fitz.Rect(100, 80, 400, 240), stream=img_blue)
    _safe_insert_textbox(
        page,
        fitz.Rect(100, 255, 450, 285),
        "Figure 4A. Eastern entrance portal overlooking the lower terrace.",
        fontsize=11
    )
    # Image 2 (bottom)
    page.insert_image(fitz.Rect(100, 350, 400, 510), stream=img_green)
    _safe_insert_textbox(
        page,
        fitz.Rect(100, 525, 450, 555),
        "Figure 4B. Western perimeter wall foundation trench.",
        fontsize=11
    )
    doc.save(p4)
    doc.close()

    ground_truth["case4_multiple_images_one_page"] = {
        "file": p4,
        "description": "Two distinct images with independent captions on a single page",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "Figure 4A. Eastern entrance portal overlooking the lower terrace.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            },
            {
                "page": 1,
                "placement": 2,
                "expected_caption": "Figure 4B. Western perimeter wall foundation trench.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            }
        ]
    }

    # ============================================================
    # Case 5: Two-Column Layout
    # ============================================================
    p5 = os.path.join(output_dir, "case5_two_column_layout.pdf")
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    # Column 1 (x: 50..270): Image at y=100..250, caption at y=260..290
    page.insert_image(fitz.Rect(50, 100, 270, 250), stream=img_blue)
    _safe_insert_textbox(
        page,
        fitz.Rect(50, 260, 270, 300),
        "Figure 5. Column 1 ceramic vessel with incised geometric ornament.",
        fontsize=10
    )
    # Column 2 (x: 320..540): Body text adjacent to Column 1 image, then image below
    _safe_insert_textbox(
        page,
        fitz.Rect(320, 100, 540, 250),
        "This text block represents column two narrative discussion. It is horizontally aligned with the image in column one but belongs to the adjacent column flow.",
        fontsize=10
    )
    page.insert_image(fitz.Rect(320, 320, 540, 470), stream=img_green)
    _safe_insert_textbox(
        page,
        fitz.Rect(320, 480, 540, 520),
        "Figure 6. Column 2 bronze dagger recovered from Burial 12.",
        fontsize=10
    )
    doc.save(p5)
    doc.close()

    ground_truth["case5_two_column_layout"] = {
        "file": p5,
        "description": "Two-column layout testing horizontal isolation between adjacent columns",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "Figure 5. Column 1 ceramic vessel with incised geometric ornament.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            },
            {
                "page": 1,
                "placement": 2,
                "expected_caption": "Figure 6. Column 2 bronze dagger recovered from Burial 12.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            }
        ]
    }

    # ============================================================
    # Case 6: Image Without Caption
    # ============================================================
    p6 = os.path.join(output_dir, "case6_image_without_caption.pdf")
    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    # Distant header block at top (y=30..50), gap to image is 150 points (> MAX_CAPTION_GAP 80)
    _safe_insert_textbox(
        page,
        fitz.Rect(100, 30, 400, 55),
        "Chapter 3: Regional Iconography and Visual Culture",
        fontsize=12
    )
    # Uncaptioned decorative image at y=200..400
    page.insert_image(fitz.Rect(100, 200, 400, 400), stream=img_amber)
    # Distant footer block at bottom (y=620..640), gap is 220 points
    _safe_insert_textbox(
        page,
        fitz.Rect(100, 620, 400, 645),
        "Demography in Late Antiquity — Page 45",
        fontsize=9
    )
    doc.save(p6)
    doc.close()

    ground_truth["case6_image_without_caption"] = {
        "file": p6,
        "description": "Image with no nearby text blocks within caption tolerance",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "",
                "expected_position": None,
                "expected_caption_found": False,
                "expected_needs_review": True
            }
        ]
    }

    # ============================================================
    # Case 7: Duplicate Captions
    # ============================================================
    p7 = os.path.join(output_dir, "case7_duplicate_captions.pdf")
    doc = fitz.open()
    # Page 1
    page1 = doc.new_page(width=500, height=700)
    page1.insert_image(fitz.Rect(100, 100, 400, 300), stream=img_blue)
    _safe_insert_textbox(
        page1,
        fitz.Rect(100, 315, 450, 355),
        "Figure 7. Standard specimen comparison under polarized light.",
        fontsize=11
    )
    # Page 2: Same caption text
    page2 = doc.new_page(width=500, height=700)
    page2.insert_image(fitz.Rect(100, 100, 400, 300), stream=img_green)
    _safe_insert_textbox(
        page2,
        fitz.Rect(100, 315, 450, 355),
        "Figure 7. Standard specimen comparison under polarized light.",
        fontsize=11
    )
    doc.save(p7)
    doc.close()

    ground_truth["case7_duplicate_captions"] = {
        "file": p7,
        "description": "Two separate images on different pages with identical captions (filename deduplication test)",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "Figure 7. Standard specimen comparison under polarized light.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            },
            {
                "page": 2,
                "placement": 1,
                "expected_caption": "Figure 7. Standard specimen comparison under polarized light.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            }
        ]
    }

    # ============================================================
    # Case 8: Reused Image Object at Multiple Placements
    # ============================================================
    p8 = os.path.join(output_dir, "case8_reused_image_object.pdf")
    doc = fitz.open()
    # Page 1 placement
    page1 = doc.new_page(width=500, height=700)
    page1.insert_image(fitz.Rect(100, 100, 400, 250), stream=img_red)
    _safe_insert_textbox(
        page1,
        fitz.Rect(100, 265, 450, 305),
        "Figure 8. Organizational hallmark stamp on frontispiece.",
        fontsize=11
    )
    # Page 2 placement of the EXACT SAME XRef
    page2 = doc.new_page(width=500, height=700)
    page2.insert_image(fitz.Rect(100, 120, 400, 270), stream=img_red)
    _safe_insert_textbox(
        page2,
        fitz.Rect(100, 285, 450, 325),
        "Figure 9. Secondary placement of hallmark stamp in colophon.",
        fontsize=11
    )
    doc.save(p8)
    doc.close()

    ground_truth["case8_reused_image_object"] = {
        "file": p8,
        "description": "Reused image object with separate placements and distinct captions",
        "expected_items": [
            {
                "page": 1,
                "placement": 1,
                "expected_caption": "Figure 8. Organizational hallmark stamp on frontispiece.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            },
            {
                "page": 2,
                "placement": 1,
                "expected_caption": "Figure 9. Secondary placement of hallmark stamp in colophon.",
                "expected_position": "below",
                "expected_caption_found": True,
                "expected_needs_review": False
            }
        ]
    }

    # ============================================================
    # Case 9: Same-Page Figure Group (Panels a and b)
    # ============================================================
    p9 = os.path.join(output_dir, "case9_same_page_group.pdf")
    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    page.insert_image(fitz.Rect(50, 100, 240, 280), stream=img_blue)
    page.insert_image(fitz.Rect(260, 100, 450, 280), stream=img_green)
    _safe_insert_textbox(
        page,
        fitz.Rect(50, 295, 450, 345),
        "Figure 10. Architectural elevation and plan: (a) west elevation; (b) ground plan.",
        fontsize=10
    )
    doc.save(p9)
    doc.close()

    ground_truth["case9_same_page_group"] = {
        "file": p9,
        "description": "Two panels on same page sharing a combined caption",
        "expected_items": [
            {
                "page": 1,
                "expected_figure": "Figure 10",
                "expected_group_size": 2,
                "expected_caption": "Figure 10. Architectural elevation and plan: (a) west elevation; (b) ground plan.",
            }
        ]
    }

    # ============================================================
    # Case 10: Cross-Page Figure Group Candidate
    # ============================================================
    p10 = os.path.join(output_dir, "case10_cross_page_group.pdf")
    doc = fitz.open()
    # Page 1: image at bottom with initial caption
    page1 = doc.new_page(width=500, height=700)
    page1.insert_image(fitz.Rect(60, 450, 440, 600), stream=img_red)
    _safe_insert_textbox(
        page1,
        fitz.Rect(60, 615, 440, 655),
        "Figure 11. (a) Sanctuary complex excavation phase 1.",
        fontsize=10
    )
    # Page 2: image at top with continuation caption
    page2 = doc.new_page(width=500, height=700)
    page2.insert_image(fitz.Rect(60, 60, 440, 210), stream=img_purple)
    _safe_insert_textbox(
        page2,
        fitz.Rect(60, 225, 440, 265),
        "Figure 11 (continued). (b) Stratigraphic profile phase 2.",
        fontsize=10
    )
    doc.save(p10)
    doc.close()

    ground_truth["case10_cross_page_group"] = {
        "file": p10,
        "description": "Cross-page figure group with continuation caption spanning pages 1 and 2",
        "expected_items": [
            {
                "expected_figure": "Figure 11",
                "expected_group_size": 2,
                "is_cross_page": True,
                "expected_review_status": "candidate",
            }
        ]
    }

    gt_path = os.path.join(output_dir, "synthetic_ground_truth.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)

    return ground_truth


if __name__ == "__main__":
    import sys
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "synthetic_pdfs")
    gt = generate_synthetic_fixtures(out_dir)
    print(f"Generated {len(gt)} synthetic test PDF fixtures in: {out_dir}")
