
# Install dependencies
!pip install --upgrade "pillow>=12.2.0" pymupdf --quiet

import os
import re
import io
import json
import textwrap
import zipfile
import fitz  # PyMuPDF
from PIL import Image
from collections import defaultdict

# ============================================================
# CONFIGURATION
# ============================================================

pdf_path = "PDF_NAME.pdf" # user provided PDF name
folder_name = "FOLDER_TO_STORE_IMAGES_WITH_CAPTIONS" # user provided folder name
images_folder = os.path.join(folder_name, "images")
metadata_folder = os.path.join(folder_name, "metadata")

MAX_CAPTION_GAP = 80
HORIZONTAL_TOLERANCE = 40

# Set True to print potential caption matches for debugging
DEBUG_CAPTIONS = True

os.makedirs(images_folder, exist_ok=True)
os.makedirs(metadata_folder, exist_ok=True)

if not os.path.exists(pdf_path):
    raise FileNotFoundError(
        f"PDF not found: {pdf_path}. Upload it to Colab first."
    )

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalize_text(text):
    """Normalize whitespace in extracted PDF text."""
    return " ".join((text or "").split()).strip()


def get_concise_filename(text):
    """
    Build a concise filename from the caption.
    Preserves the original naming logic where possible.
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

    # Remove filesystem-invalid characters across common operating systems
    clean_text = re.sub(r'[\\/:*?"<>|]', "", concise)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()
    clean_text = clean_text.rstrip(". ")

    # Avoid empty names and keep names manageable
    clean_text = clean_text[:80].rstrip(". ")

    return clean_text or None


def make_unique_filename(base_name, extension, used_names):
    """Avoid duplicate filenames, including duplicates within this run."""
    candidate = f"{base_name}{extension}"
    counter = 1

    while candidate.lower() in used_names or os.path.exists(
        os.path.join(images_folder, candidate)
    ):
        candidate = f"{base_name}_{counter}{extension}"
        counter += 1

    used_names.add(candidate.lower())
    return candidate


def get_caption_candidates(blocks, img_rect):
    """
    Find nearby text blocks above OR below an image.

    PDF coordinate system:
      - y0 is the top of a block/image.
      - y1 is the bottom of a block/image.
      - y increases down the page.

    This returns candidates, not guaranteed caption matches.
    """
    potential_captions = []

    for block in blocks:
        # PyMuPDF text blocks usually have:
        # x0, y0, x1, y1, text, block_no, block_type
        if len(block) < 5:
            continue

        bx0, by0, bx1, by1, btext = block[:5]
        text = normalize_text(btext)

        if not text:
            continue

        # Ignore blocks that do not horizontally overlap or sit
        # reasonably close to the image's horizontal span.
        if (
            bx1 < img_rect.x0 - HORIZONTAL_TOLERANCE
            or bx0 > img_rect.x1 + HORIZONTAL_TOLERANCE
        ):
            continue

        # Caption below image:
        # The text block's top is near the image's bottom.
        below_gap = by0 - img_rect.y1

        if -5 <= below_gap <= MAX_CAPTION_GAP:
            potential_captions.append({
                "position": "below",
                "gap": max(0, below_gap),
                "y": by0,
                "x": bx0,
                "text": text
            })
            continue

        # Caption above image:
        # The text block's bottom is near the image's top.
        above_gap = img_rect.y0 - by1

        if -5 <= above_gap <= MAX_CAPTION_GAP:
            potential_captions.append({
                "position": "above",
                "gap": max(0, above_gap),
                "y": by0,
                "x": bx0,
                "text": text
            })

    # Closest blocks first
    potential_captions.sort(key=lambda item: item["gap"])
    return potential_captions


def combine_caption_lines(candidates):
    """
    Combine nearby candidate blocks into caption text.

    Keeps the closest candidate and adds nearby blocks on the same
    side when their vertical positions suggest consecutive lines.
    This is a heuristic and may need review for complex layouts.
    """
    if not candidates:
        return "", None, []

    # Use the side of the closest candidate as the initial choice.
    chosen_side = candidates[0]["position"]

    side_candidates = [
        item for item in candidates
        if item["position"] == chosen_side
    ]

    # Sort in reading order within the selected side.
    side_candidates.sort(key=lambda item: (item["y"], item["x"]))

    selected = []
    previous_y = None

    for item in side_candidates:
        if not selected:
            selected.append(item)
            previous_y = item["y"]
            continue

        # Avoid combining unrelated blocks that are too far apart.
        if abs(item["y"] - previous_y) > 28:
            break

        # Avoid adding exact duplicate text blocks.
        if item["text"] not in [x["text"] for x in selected]:
            selected.append(item)

        previous_y = item["y"]

    caption = normalize_text(
        " ".join(item["text"] for item in selected)
    )

    return caption, chosen_side, selected


# ============================================================
# PROCESS PDF
# ============================================================

doc = fitz.open(pdf_path)

used_names = set()
fallback_count = 0
success_count = 0
error_count = 0
records = []
errors = []

print(f"Processing PDF: {pdf_path}")
print("Searching for captions above and below images...\n")

for page_index, page in enumerate(doc):
    page_number = page_index + 1
    blocks = page.get_text("blocks")

    # xrefs identify embedded image objects. One object may appear
    # more than once on a page, so inspect all its placements.
    image_list = page.get_images(full=True)
    seen_xrefs = set()

    for img_info in image_list:
        xref = img_info[0]

        # Avoid processing the same XRef repeatedly if listed more than once.
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

            # Keep an appropriate image extension.
            if original_ext not in {
                "png", "jpg", "jpeg", "webp", "bmp", "tiff"
            }:
                original_ext = "png"

            # Each placement is treated as a separate occurrence.
            for placement_index, img_rect in enumerate(rects, start=1):
                candidates = get_caption_candidates(blocks, img_rect)

                if DEBUG_CAPTIONS:
                    print(
                        f"\nPage {page_number}, XRef {xref}, "
                        f"placement {placement_index}"
                    )
                    if candidates:
                        for candidate in candidates:
                            print(
                                f"  Candidate {candidate['position']} "
                                f"(gap={candidate['gap']:.1f}): "
                                f"{candidate['text']}"
                            )
                    else:
                        print("  No nearby caption candidates found.")

                caption_text, caption_position, selected_blocks = (
                    combine_caption_lines(candidates)
                )

                # Build a filename from the caption.
                safe_title = get_concise_filename(caption_text)

                if safe_title:
                    base_name = safe_title
                else:
                    fallback_count += 1
                    base_name = (
                        f"Figure_Page{page_number}_"
                        f"Image{fallback_count}"
                    )

                # Include page and placement in the name to make
                # repeated placements easier to distinguish.
                base_name = (
                    f"Page{page_number}_"
                    f"Placement{placement_index}_{base_name}"
                )

                filename = make_unique_filename(
                    base_name,
                    f".{original_ext}",
                    used_names
                )

                output_path = os.path.join(images_folder, filename)

                # Save original extracted image bytes without burning
                # caption text into the image.
                with open(output_path, "wb") as image_file:
                    image_file.write(image_bytes)

                record = {
                    "page": page_number,
                    "xref": xref,
                    "placement": placement_index,
                    "filename": filename,
                    "image_path": os.path.relpath(
                        output_path, folder_name
                    ),
                    "caption": caption_text,
                    "caption_position": caption_position,
                    "caption_candidates": candidates,
                    "selected_caption_blocks": selected_blocks,
                    "caption_found": bool(caption_text),
                    "needs_caption_review": (
                        not caption_text or len(candidates) > 1
                    ),
                    "image_rect": {
                        "x0": round(img_rect.x0, 2),
                        "y0": round(img_rect.y0, 2),
                        "x1": round(img_rect.x1, 2),
                        "y1": round(img_rect.y1, 2)
                    }
                }

                records.append(record)
                success_count += 1

                print(
                    f"Saved: {filename}\n"
                    f"  Caption position: "
                    f"{caption_position or 'not found'}\n"
                    f"  Caption: {caption_text or '[NO CAPTION FOUND]'}"
                )

        except Exception as e:
            error_count += 1
            error_record = {
                "page": page_number,
                "xref": xref,
                "error": repr(e)
            }
            errors.append(error_record)
            print(
                f"ERROR on page {page_number}, XRef {xref}: {e}"
            )

doc.close()

# ============================================================
# SAVE METADATA AND ERROR LOG
# ============================================================

metadata_path = os.path.join(metadata_folder, "extraction_results.json")

with open(metadata_path, "w", encoding="utf-8") as f:
    json.dump(records, f, indent=2, ensure_ascii=False)

errors_path = os.path.join(metadata_folder, "extraction_errors.json")

with open(errors_path, "w", encoding="utf-8") as f:
    json.dump(errors, f, indent=2, ensure_ascii=False)

# Create a plain-text caption report for easy inspection.
report_path = os.path.join(metadata_folder, "caption_report.txt")

with open(report_path, "w", encoding="utf-8") as f:
    for record in records:
        f.write(f"Filename: {record['filename']}\n")
        f.write(f"Page: {record['page']}\n")
        f.write(
            f"Caption position: "
            f"{record['caption_position'] or 'not found'}\n"
        )
        f.write(f"Caption: {record['caption'] or '[NO CAPTION FOUND]'}\n")
        f.write(
            f"Needs review: {record['needs_caption_review']}\n"
        )
        f.write("-" * 70 + "\n")

# ============================================================
# ZIP OUTPUT
# ============================================================

zip_path = f"{folder_name}_with_captions.zip"

with zipfile.ZipFile(
    zip_path, "w", compression=zipfile.ZIP_DEFLATED
) as zipf:
    for root, dirs, files in os.walk(folder_name):
        for filename in files:
            full_path = os.path.join(root, filename)
            arcname = os.path.relpath(full_path, os.path.dirname(folder_name))
            zipf.write(full_path, arcname=arcname)

# ============================================================
# SUMMARY
# ============================================================

print("\n" + "=" * 60)
print("EXTRACTION SUMMARY")
print("=" * 60)
print(f"PDF: {pdf_path}")
print(f"Image placements saved: {success_count}")
print(f"Errors: {error_count}")
print(
    "Captions found:",
    sum(1 for r in records if r["caption_found"])
)
print(
    "Images needing caption review:",
    sum(1 for r in records if r["needs_caption_review"])
)
print(f"Output folder: {folder_name}")
print(f"ZIP archive: {zip_path}")
print(f"Caption report: {report_path}")
print(f"Metadata: {metadata_path}")

from google.colab import files
files.download(zip_path)
