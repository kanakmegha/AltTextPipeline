"""
Layout & Caption Matching Heuristics
------------------------------------
Provides coordinate geometry analysis, running header/footer suppression,
multi-image boundary isolation, scoring, and ambiguity assessment.
"""

import re
from typing import List, Tuple, Optional, Dict, Any
from .models import BoundingBox, CaptionCandidate


FIGURE_PREFIX_PATTERN = re.compile(
    r'^(Figure|Fig\.?|Plate|Map|Chart|Graph|Schema|Table)\s*[\d\w.-]*',
    re.IGNORECASE
)

RUNNING_HEADER_PATTERN = re.compile(
    r'(\b\d+\s*$) criticism|(\b\d+\s+[A-Z])|(^[A-Z][a-zA-Z\s]{3,35}\s+\d+$)',
    re.IGNORECASE
)


def is_running_header_or_footer(
    bbox: BoundingBox,
    page_height: float,
    text: str,
    top_margin_ratio: float = 0.08,
    bottom_margin_ratio: float = 0.92
) -> bool:
    """
    Identifies whether a text block represents a running header (top) or footer (bottom).
    Uses coordinate margins and text structure patterns.
    """
    # Top margin (e.g. y1 within top 8% of page)
    if bbox.y1 <= page_height * top_margin_ratio:
        return True

    # Bottom margin (e.g. y0 within bottom 8% of page)
    if bbox.y0 >= page_height * bottom_margin_ratio:
        return True

    # Header pattern check for text sitting near top (< 50pt)
    if bbox.y1 <= 55.0 and len(text) < 60:
        # e.g. "Demography in Late Etruria 12"
        if re.search(r'\b\d+\s*$', text) or re.search(r'^\d+\s+[A-Z]', text):
            return True

    return False


def has_intervening_image(
    block_bbox: BoundingBox,
    target_img_bbox: BoundingBox,
    other_img_bboxes: List[BoundingBox],
    position: str
) -> bool:
    """
    Checks if another image is positioned between the text block and target image,
    or if the text block is much closer to another image than the target.
    """
    for other in other_img_bboxes:
        if position == "below":
            # Text is below target image: target.y1 <= text.y0
            # If another image is between target.y1 and text.y0, disqualify
            if target_img_bbox.y1 < other.y0 < block_bbox.y0:
                return True
            # Or if text is below the other image and sits closer to other image
            if other.y1 <= block_bbox.y0:
                dist_other = block_bbox.y0 - other.y1
                dist_target = block_bbox.y0 - target_img_bbox.y1
                if dist_other < dist_target - 5:
                    return True

        elif position == "above":
            # Text is above target image: text.y1 <= target.y0
            # If another image is between text.y1 and target.y0, disqualify
            if block_bbox.y1 < other.y1 < target_img_bbox.y0:
                return True
            # Or if text is above other image and closer to other image
            if block_bbox.y1 <= other.y0:
                dist_other = other.y0 - block_bbox.y1
                dist_target = target_img_bbox.y0 - block_bbox.y1
                if dist_other < dist_target - 5:
                    return True

    return False


def calculate_horizontal_overlap(b1: BoundingBox, b2: BoundingBox) -> float:
    """Returns horizontal overlap in points between two bounding boxes."""
    return max(0.0, min(b1.x1, b2.x1) - max(b1.x0, b2.x0))


def find_candidate_blocks(
    text_blocks: List[Tuple],
    target_img_bbox: BoundingBox,
    other_img_bboxes: List[BoundingBox],
    page_width: float,
    page_height: float,
    max_gap: float = 75.0,
    horizontal_tolerance: float = 40.0
) -> List[CaptionCandidate]:
    """
    Finds and scores candidate text blocks for an image with geometric awareness.
    """
    candidates = []

    for block in text_blocks:
        if len(block) < 5:
            continue

        bx0, by0, bx1, by1, btext = block[:5]
        text = " ".join((btext or "").split()).strip()
        if not text:
            continue

        bbox = BoundingBox(bx0, by0, bx1, by1)

        # Check running header / footer
        is_header_footer = is_running_header_or_footer(bbox, page_height, text)
        if is_header_footer and target_img_bbox.y0 > page_height * 0.12:
            # Drop running headers unless image itself is occupying top margin
            continue

        # Check horizontal proximity / column isolation
        overlap = calculate_horizontal_overlap(bbox, target_img_bbox)
        if overlap <= 0.0:
            if (
                bbox.x1 < target_img_bbox.x0 - horizontal_tolerance
                or bbox.x0 > target_img_bbox.x1 + horizontal_tolerance
            ):
                continue

        # Evaluate position relative to target image
        below_gap = bbox.y0 - target_img_bbox.y1
        above_gap = target_img_bbox.y0 - bbox.y1

        position = None
        gap = 0.0

        if -5.0 <= below_gap <= max_gap:
            position = "below"
            gap = max(0.0, below_gap)
        elif -5.0 <= above_gap <= max_gap:
            position = "above"
            gap = max(0.0, above_gap)
        else:
            continue

        # Check if another image is between this text and target image
        if has_intervening_image(bbox, target_img_bbox, other_img_bboxes, position):
            continue

        has_prefix = bool(FIGURE_PREFIX_PATTERN.match(text))

        # Base score inversely proportional to gap distance
        score = max(0.0, 100.0 - gap * 1.5)

        # Strong bonus for explicit figure labels
        if has_prefix:
            score += 150.0

        # Overlap bonus
        overlap_ratio = overlap / max(1.0, min(bbox.width, target_img_bbox.width))
        score += overlap_ratio * 30.0

        # Header penalty if not completely filtered
        if is_header_footer:
            score -= 120.0

        candidates.append(
            CaptionCandidate(
                text=text,
                position=position,
                gap=gap,
                bbox=bbox,
                score=score,
                has_figure_prefix=has_prefix,
                is_running_header_footer=is_header_footer,
            )
        )

    # Sort candidates by descending score
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def assemble_multiline_caption(
    primary_candidate: CaptionCandidate,
    all_candidates: List[CaptionCandidate]
) -> Tuple[str, List[CaptionCandidate]]:
    """
    Groups contiguous lines belonging to the same caption on the same position side.
    """
    chosen_side = primary_candidate.position
    side_candidates = [
        c for c in all_candidates
        if c.position == chosen_side and not c.is_running_header_footer
    ]

    # Sort in reading order: top-to-bottom, left-to-right
    side_candidates.sort(key=lambda c: (c.bbox.y0, c.bbox.x0))

    selected = [primary_candidate]
    last_y1 = primary_candidate.bbox.y1
    last_y0 = primary_candidate.bbox.y0

    # Expand upwards or downwards if consecutive blocks are adjacent (< 22pt)
    for c in side_candidates:
        if c in selected:
            continue

        # If below primary:
        if 0.0 <= (c.bbox.y0 - last_y1) <= 24.0:
            # Ensure it does not introduce a competing figure prefix (which indicates a new caption)
            if not c.has_figure_prefix:
                selected.append(c)
                last_y1 = c.bbox.y1

        # If above primary:
        elif 0.0 <= (last_y0 - c.bbox.y1) <= 24.0:
            if not c.has_figure_prefix:
                selected.insert(0, c)
                last_y0 = c.bbox.y0

    # Order in visual reading order
    selected.sort(key=lambda c: (c.bbox.y0, c.bbox.x0))
    assembled_text = " ".join(c.text for c in selected)
    return assembled_text, selected


def evaluate_caption_ambiguity(
    winning_candidate: Optional[CaptionCandidate],
    all_candidates: List[CaptionCandidate]
) -> Tuple[bool, List[str]]:
    """
    Evaluates whether the matched caption is definitive or requires human review.
    """
    reasons = []

    if not winning_candidate:
        return True, ["No caption found within proximity tolerance"]

    # Filter out candidates on the same side that were combined or low-scoring
    competing = [
        c for c in all_candidates
        if c != winning_candidate and not c.is_running_header_footer
    ]

    if not competing:
        # Single candidate
        if not winning_candidate.has_figure_prefix and winning_candidate.gap > 35.0:
            return True, ["Candidate has no figure prefix and is distant from image"]
        return False, []

    # If competing candidates exist, check if any is a viable competitor
    second_best = competing[0]

    # If both have figure prefixes (e.g. two figure captions nearby)
    if winning_candidate.has_figure_prefix and second_best.has_figure_prefix:
        if abs(winning_candidate.score - second_best.score) < 50.0:
            return True, [
                f"Competing figure caption detected on {second_best.position} ({second_best.text[:30]}...)"
            ]

    # If winner has figure prefix and competitor is plain body text with much lower score
    if winning_candidate.has_figure_prefix:
        if winning_candidate.score - second_best.score > 40.0:
            # Clear winner with figure label
            return False, []

    # If winner has no figure prefix and competing text exists
    if not winning_candidate.has_figure_prefix:
        if second_best.score > winning_candidate.score * 0.75:
            return True, ["Multiple competing text blocks without explicit figure labels"]

    return False, []
