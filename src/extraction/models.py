"""
Extraction Data Models
----------------------
Defines immutable data structures for extracted images, caption candidates,
page chunks, and extraction summaries.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import uuid


@dataclass
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def to_dict(self) -> Dict[str, float]:
        return {
            "x0": round(self.x0, 2),
            "y0": round(self.y0, 2),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
        }


@dataclass
class CaptionCandidate:
    text: str
    position: str  # "above" or "below"
    gap: float
    bbox: BoundingBox
    score: float
    has_figure_prefix: bool
    is_running_header_footer: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "position": self.position,
            "gap": round(self.gap, 2),
            "score": round(self.score, 2),
            "has_figure_prefix": self.has_figure_prefix,
            "is_running_header_footer": self.is_running_header_footer,
            "bbox": self.bbox.to_dict(),
        }


@dataclass
class FigureGroup:
    group_id: str
    caption_text: str
    caption_page_number: int
    caption_position: Optional[str] = "below"
    figure_label: Optional[str] = None
    image_ids: List[str] = field(default_factory=list)
    is_cross_page: bool = False
    review_status: str = "candidate"  # "candidate" or "confirmed"
    review_reasons: List[str] = field(default_factory=list)
    confidence: float = 1.0
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "group_id": self.group_id,
            "caption_text": self.caption_text,
            "caption_page_number": self.caption_page_number,
            "caption_position": self.caption_position,
            "figure_label": self.figure_label,
            "image_ids": self.image_ids,
            "image_count": len(self.image_ids),
            "is_cross_page": self.is_cross_page,
            "review_status": self.review_status,
            "review_reasons": self.review_reasons,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
        }


@dataclass
class ExtractedImage:
    image_id: str  # Stable UUID independent of filename
    page_number: int
    xref: int
    placement_index: int
    bbox: BoundingBox
    image_bytes: bytes = field(repr=False)
    image_format: str  # png, jpg, etc.
    suggested_filename: str
    caption: str
    caption_position: Optional[str]  # "above", "below", or None
    caption_found: bool
    needs_caption_review: bool
    review_reasons: List[str] = field(default_factory=list)
    caption_candidates: List[CaptionCandidate] = field(default_factory=list)
    raw_caption_blocks: List[str] = field(default_factory=list)
    group_id: Optional[str] = None
    association_type: str = "single"  # "single", "group", "uncaptioned"
    association_confidence: float = 1.0
    panel_label: Optional[str] = None

    def to_dict(self, include_bytes: bool = False) -> Dict[str, Any]:
        data = {
            "image_id": self.image_id,
            "page_number": self.page_number,
            "xref": self.xref,
            "placement_index": self.placement_index,
            "bbox": self.bbox.to_dict(),
            "image_format": self.image_format,
            "suggested_filename": self.suggested_filename,
            "caption": self.caption,
            "caption_position": self.caption_position,
            "caption_found": self.caption_found,
            "needs_caption_review": self.needs_caption_review,
            "review_reasons": self.review_reasons,
            "candidate_count": len(self.caption_candidates),
            "caption_candidates": [c.to_dict() for c in self.caption_candidates],
            "raw_caption_blocks": self.raw_caption_blocks,
            "group_id": self.group_id,
            "association_type": self.association_type,
            "association_confidence": round(self.association_confidence, 2),
            "panel_label": self.panel_label,
        }
        if include_bytes:
            data["byte_size"] = len(self.image_bytes)
        return data


@dataclass
class PageChunk:
    chunk_index: int
    start_page: int
    end_page: int
    total_pages: int


@dataclass
class ExtractionResult:
    pdf_path: str
    total_pages: int
    pages_processed: List[int]
    images: List[ExtractedImage]
    errors: List[Dict[str, Any]]
    stats: Dict[str, Any]
    groups: List[FigureGroup] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pdf_path": self.pdf_path,
            "total_pages": self.total_pages,
            "pages_processed": self.pages_processed,
            "images_extracted": len(self.images),
            "captions_found": sum(1 for img in self.images if img.caption_found),
            "needs_review": sum(1 for img in self.images if img.needs_caption_review),
            "groups_found": len(self.groups),
            "errors": self.errors,
            "images": [img.to_dict(include_bytes=True) for img in self.images],
            "groups": [g.to_dict() for g in self.groups],
            "stats": self.stats,
        }
