"""
Extraction package exports.
"""

from .models import (
    BoundingBox,
    CaptionCandidate,
    ExtractedImage,
    PageChunk,
    ExtractionResult,
    FigureGroup,
)
from .naming import (
    build_concise_filename,
    allocate_unique_filename,
    sanitize_filename,
)
from .heuristics import (
    is_running_header_or_footer,
    find_candidate_blocks,
    assemble_multiline_caption,
    evaluate_caption_ambiguity,
)
from .grouping import FigureGrouper
from .engine import ExtractionEngine

__all__ = [
    "BoundingBox",
    "CaptionCandidate",
    "ExtractedImage",
    "PageChunk",
    "ExtractionResult",
    "FigureGroup",
    "build_concise_filename",
    "allocate_unique_filename",
    "sanitize_filename",
    "is_running_header_or_footer",
    "find_candidate_blocks",
    "assemble_multiline_caption",
    "evaluate_caption_ambiguity",
    "FigureGrouper",
    "ExtractionEngine",
]
