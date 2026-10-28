"""
Baseline Adapter
----------------
Provides a headless, importable execution interface for the exact extraction
and caption matching logic defined in `baseline_extraction.py`.

`baseline_extraction.py` remains untouched as the immutable original baseline.
"""

import os
import re
import pymupdf as fitz
from collections import defaultdict


MAX_CAPTION_GAP = 80
HORIZONTAL_TOLERANCE = 40


def normalize_text(text):
    """Normalize whitespace in extracted PDF text."""
    return " ".join((text or "").split()).strip()


def get_concise_filename(text):
    """
    Build a concise filename from the caption.
    Preserves the original baseline naming logic.
    """
    if not text:
        return None

    fig_match = re.search(
        r'^(Figure|Fig\.?|Plate|Map|Chart|Graph|Schema)\s+[\d.]+',
        text,
        re.IGNORECASE
    )

    if fig_match:
        prefix = fig_match.group(0)
        remaining = text[len(prefix):].strip()
        words = remaining.split()[:3]
        concise = prefix + (" " + " ".join(words) if words else "")
    else:
        words = text.split()[:4]
        concise = " ".join(words)

    clean_text = re.sub(r'[\\/:*?"<>|]', "", concise)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    clean_text = clean_text.rstrip(". ")
    clean_text = clean_text[:80].rstrip(". ")

    return clean_text or None


def make_unique_filename(base_name, extension, used_names, images_folder=None):
    """Avoid duplicate filenames, including duplicates within this run."""
    candidate = f"{base_name}{extension}"
    counter = 1

    while candidate.lower() in used_names or (
        images_folder and os.path.exists(os.path.join(images_folder, candidate))
    ):
        candidate = f"{base_name}_{counter}{extension}"
        counter += 1

    used_names.add(candidate.lower())
    return candidate


def get_caption_candidates(blocks, img_rect, max_gap=MAX_CAPTION_GAP, h_tol=HORIZONTAL_TOLERANCE):
    """
    Find nearby text blocks above OR below an image using baseline coordinates.
    """
    potential_captions = []

    for block in blocks:
        if len(block) < 5:
            continue

        bx0, by0, bx1, by1, btext = block[:5]
        text = normalize_text(btext)

        if not text:
            continue

        if (
            bx1 < img_rect.x0 - h_tol
            or bx0 > img_rect.x1 + h_tol
        ):
            continue

        # Caption below image
        below_gap = by0 - img_rect.y1
        if -5 <= below_gap <= max_gap:
            potential_captions.append({
                "position": "below",
                "gap": max(0, below_gap),
                "y": by0,
                "x": bx0,
                "text": text
            })
            continue

        # Caption above image
        above_gap = img_rect.y0 - by1
        if -5 <= above_gap <= max_gap:
            potential_captions.append({
                "position": "above",
                "gap": max(0, above_gap),
                "y": by0,
                "x": bx0,
                "text": text
            })

    potential_captions.sort(key=lambda item: item["gap"])
    return potential_captions


def combine_caption_lines(candidates):
    """
    Combine nearby candidate blocks into caption text using baseline heuristics.
    """
    if not candidates:
        return "", None, []

    chosen_side = candidates[0]["position"]

    side_candidates = [
        item for item in candidates
        if item["position"] == chosen_side
    ]

    side_candidates.sort(key=lambda item: (item["y"], item["x"]))

    selected = []
    previous_y = None

    for item in side_candidates:
        if not selected:
            selected.append(item)
            previous_y = item["y"]
            continue

        if abs(item["y"] - previous_y) > 28:
            break

        if item["text"] not in [x["text"] for x in selected]:
            selected.append(item)

        previous_y = item["y"]

    caption = normalize_text(
        " ".join(item["text"] for item in selected)
    )

    return caption, chosen_side, selected


def extract_from_pdf(pdf_path, output_images_folder=None, page_numbers=None, debug=False):
    """
    Run baseline extraction logic on a PDF or subset of page numbers (1-indexed).
    Returns (records, errors, summary).
    """
    doc = fitz.open(pdf_path)

    used_names = set()
    fallback_count = 0
    success_count = 0
    error_count = 0
    records = []
    errors = []

    target_pages = set(page_numbers) if page_numbers else set(range(1, len(doc) + 1))

    if output_images_folder:
        os.makedirs(output_images_folder, exist_ok=True)

    for page_index, page in enumerate(doc):
        page_number = page_index + 1
        if page_number not in target_pages:
            continue

        blocks = page.get_text("blocks")
        image_list = page.get_images(full=True)
        seen_xrefs = set()

        for img_info in image_list:
            xref = img_info[0]

            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)

            try:
                rects = page.get_image_rects(xref)
                if not rects:
                    continue

                base_image = doc.extract_image(xref)
                if not base_image or "image" not in base_image:
                    errors.append({
                        "page": page_number,
                        "xref": xref,
                        "error": "Could not extract image bytes"
                    })
                    error_count += 1
                    continue

                image_bytes = base_image["image"]
                original_ext = base_image.get("ext", "png").lower()
                if original_ext not in {"png", "jpg", "jpeg", "webp", "bmp", "tiff"}:
                    original_ext = "png"

                for placement_index, img_rect in enumerate(rects, start=1):
                    candidates = get_caption_candidates(blocks, img_rect)

                    caption_text, caption_position, selected_blocks = combine_caption_lines(candidates)

                    safe_title = get_concise_filename(caption_text)
                    if safe_title:
                        base_name = safe_title
                    else:
                        fallback_count += 1
                        base_name = f"Figure_Page{page_number}_Image{fallback_count}"

                    base_name = f"Page{page_number}_Placement{placement_index}_{base_name}"
                    filename = make_unique_filename(base_name, f".{original_ext}", used_names, output_images_folder)

                    output_path = None
                    if output_images_folder:
                        output_path = os.path.join(output_images_folder, filename)
                        with open(output_path, "wb") as image_file:
                            image_file.write(image_bytes)

                    record = {
                        "page": page_number,
                        "xref": xref,
                        "placement": placement_index,
                        "filename": filename,
                        "image_path": output_path,
                        "caption": caption_text,
                        "caption_position": caption_position,
                        "caption_candidates": candidates,
                        "selected_caption_blocks": selected_blocks,
                        "caption_found": bool(caption_text),
                        "needs_caption_review": (not caption_text or len(candidates) > 1),
                        "image_rect": {
                            "x0": round(img_rect.x0, 2),
                            "y0": round(img_rect.y0, 2),
                            "x1": round(img_rect.x1, 2),
                            "y1": round(img_rect.y1, 2)
                        }
                    }

                    records.append(record)
                    success_count += 1

            except Exception as e:
                error_count += 1
                errors.append({
                    "page": page_number,
                    "xref": xref,
                    "error": repr(e)
                })

    doc.close()

    summary = {
        "pdf_path": pdf_path,
        "pages_processed": sorted(list(target_pages)),
        "images_extracted": success_count,
        "errors": error_count,
        "captions_found": sum(1 for r in records if r["caption_found"]),
        "needs_caption_review": sum(1 for r in records if r["needs_caption_review"]),
    }

    return records, errors, summary
