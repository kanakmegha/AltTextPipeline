"""
Modular Extraction Engine
-------------------------
Production-ready, headless extraction engine designed for worker processes.
Inspects digital PDF pages, extracts embedded images without full-page
rasterization, matches captions above/below using coordinate heuristics,
and generates stable internal identities and clean filenames.
"""

import os
import uuid
from typing import List, Optional, Set, Dict, Any, Generator
import pymupdf as fitz

from .models import (
    BoundingBox,
    CaptionCandidate,
    ExtractedImage,
    PageChunk,
    ExtractionResult,
)
from .heuristics import (
    find_candidate_blocks,
    assemble_multiline_caption,
    evaluate_caption_ambiguity,
)
from .naming import build_concise_filename, allocate_unique_filename


class ExtractionEngine:
    """
    Modular PDF Image & Caption Extraction Engine.
    """

    def __init__(
        self,
        max_caption_gap: float = 75.0,
        horizontal_tolerance: float = 40.0,
        chunk_size: int = 20,
    ):
        self.max_caption_gap = max_caption_gap
        self.horizontal_tolerance = horizontal_tolerance
        self.chunk_size = chunk_size

    def get_chunks(self, total_pages: int) -> List[PageChunk]:
        """Partitions document into sequential page chunks."""
        chunks = []
        chunk_idx = 0
        for start in range(1, total_pages + 1, self.chunk_size):
            end = min(start + self.chunk_size - 1, total_pages)
            chunks.append(
                PageChunk(
                    chunk_index=chunk_idx,
                    start_page=start,
                    end_page=end,
                    total_pages=total_pages,
                )
            )
            chunk_idx += 1
        return chunks

    def process_pdf(
        self,
        pdf_path: str,
        page_numbers: Optional[List[int]] = None,
        output_images_dir: Optional[str] = None,
        existing_filenames: Optional[Set[str]] = None,
    ) -> ExtractionResult:
        """
        Extracts images and captions from a PDF file.
        Can process full document or a targeted list of page numbers (1-indexed).
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF document not found: {pdf_path}")

        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        target_pages = sorted(list(set(page_numbers))) if page_numbers else list(range(1, total_pages + 1))

        if output_images_dir:
            os.makedirs(output_images_dir, exist_ok=True)

        used_filenames = set(existing_filenames or set())
        extracted_images: List[ExtractedImage] = []
        errors: List[Dict[str, Any]] = []

        image_seq_counter = 0

        for page_num in target_pages:
            if page_num < 1 or page_num > total_pages:
                continue

            try:
                page = doc[page_num - 1]
                page_width = page.rect.width
                page_height = page.rect.height
                text_blocks = page.get_text("blocks")
                image_info_list = page.get_images(full=True)

                # 1. First collect all image placements on this page for boundary isolation
                placements_on_page = []
                seen_xrefs = set()

                for img_info in image_info_list:
                    xref = img_info[0]
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)

                    rects = page.get_image_rects(xref)
                    for rect in rects:
                        placements_on_page.append(
                            (xref, BoundingBox(rect.x0, rect.y0, rect.x1, rect.y1))
                        )

                # Sort placements on page in visual reading order (top-to-bottom, left-to-right)
                placements_on_page.sort(key=lambda item: (item[1].y0, item[1].x0))

                # 2. Extract and match caption for each placement
                for placement_idx, (xref, img_bbox) in enumerate(placements_on_page, start=1):
                    image_seq_counter += 1

                    # Extract raw image bytes
                    base_image = doc.extract_image(xref)
                    if not base_image or "image" not in base_image:
                        errors.append({
                            "page": page_num,
                            "xref": xref,
                            "error": "Failed to extract image bytes from XRef"
                        })
                        continue

                    img_bytes = base_image["image"]
                    ext = base_image.get("ext", "png").lower()
                    if ext not in {"png", "jpg", "jpeg", "webp", "bmp", "tiff"}:
                        ext = "png"

                    # Identify all other image bboxes on page to avoid cross-image caption leakage
                    other_bboxes = [b for (x, b) in placements_on_page if b != img_bbox]

                    candidates = find_candidate_blocks(
                        text_blocks=text_blocks,
                        target_img_bbox=img_bbox,
                        other_img_bboxes=other_bboxes,
                        page_width=page_width,
                        page_height=page_height,
                        max_gap=self.max_caption_gap,
                        horizontal_tolerance=self.horizontal_tolerance,
                    )

                    if candidates:
                        best_candidate = candidates[0]
                        final_caption, combined_blocks = assemble_multiline_caption(
                            primary_candidate=best_candidate,
                            all_candidates=candidates,
                        )
                        caption_pos = best_candidate.position
                        caption_found = True
                        raw_blocks = [c.text for c in combined_blocks]
                    else:
                        best_candidate = None
                        final_caption = ""
                        caption_pos = None
                        caption_found = False
                        raw_blocks = []

                    needs_review, review_reasons = evaluate_caption_ambiguity(
                        winning_candidate=best_candidate,
                        all_candidates=candidates,
                    )

                    # Build clean, deterministic filename
                    base_filename = build_concise_filename(
                        caption_text=final_caption,
                        page_number=page_num,
                        image_sequence=image_seq_counter,
                    )
                    unique_filename = allocate_unique_filename(
                        base_name=base_filename,
                        extension=ext,
                        used_names=used_filenames,
                    )

                    if output_images_dir:
                        out_path = os.path.join(output_images_dir, unique_filename)
                        with open(out_path, "wb") as f:
                            f.write(img_bytes)

                    # Generate stable internal UUID
                    image_id = str(uuid.uuid4())

                    img_record = ExtractedImage(
                        image_id=image_id,
                        page_number=page_num,
                        xref=xref,
                        placement_index=placement_idx,
                        bbox=img_bbox,
                        image_bytes=img_bytes,
                        image_format=ext,
                        suggested_filename=unique_filename,
                        caption=final_caption,
                        caption_position=caption_pos,
                        caption_found=caption_found,
                        needs_caption_review=needs_review,
                        review_reasons=review_reasons,
                        caption_candidates=candidates,
                        raw_caption_blocks=raw_blocks,
                    )
                    extracted_images.append(img_record)

            except Exception as e:
                errors.append({
                    "page": page_num,
                    "error": repr(e)
                })

        doc.close()

        stats = {
            "total_images": len(extracted_images),
            "captions_found": sum(1 for img in extracted_images if img.caption_found),
            "captions_above": sum(1 for img in extracted_images if img.caption_position == "above"),
            "captions_below": sum(1 for img in extracted_images if img.caption_position == "below"),
            "review_flagged": sum(1 for img in extracted_images if img.needs_caption_review),
            "error_count": len(errors),
        }

        return ExtractionResult(
            pdf_path=pdf_path,
            total_pages=total_pages,
            pages_processed=target_pages,
            images=extracted_images,
            errors=errors,
            stats=stats,
        )
