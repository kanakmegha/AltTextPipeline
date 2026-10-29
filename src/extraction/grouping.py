"""
Multi-Image Caption Grouping & Cross-Page Association
----------------------------------------------------
Separates caption detection from association, enabling multiple images
to share a single caption (on the same page or across adjacent pages)
when layout geometry and contextual evidence support that relationship.
Preserves individual image identities, confidence scores, and review provenance.
"""

import re
import uuid
from typing import List, Dict, Any, Optional, Tuple, Set
from .models import BoundingBox, CaptionCandidate, ExtractedImage, FigureGroup
from .heuristics import calculate_horizontal_overlap


FIGURE_LABEL_PATTERN = re.compile(
    r'^(Figure|Fig\.?|Plate|Map|Chart|Graph|Schema|Table)\s*([\d\w.-]+)',
    re.IGNORECASE
)

CONTINUATION_PATTERN = re.compile(
    r'(?:continued|cont\.?|cont\'d|continued\s+from\s+page)',
    re.IGNORECASE
)

PANEL_LABEL_IN_TEXT_PATTERN = re.compile(
    r'^\s*[\(\[]?([a-d])[\)\]\.]?\s*$',
    re.IGNORECASE
)

PANEL_MENTIONS_IN_CAPTION_PATTERN = re.compile(
    r'[\(\[]([a-d])[\)\]]',
    re.IGNORECASE
)


def extract_figure_label(caption_text: str) -> Optional[str]:
    """Extracts standardized figure label, e.g. 'Figure 1' or 'Fig. 2.3'."""
    if not caption_text:
        return None
    m = FIGURE_LABEL_PATTERN.match(caption_text.strip())
    if m:
        prefix, num = m.groups()
        clean_num = num.rstrip(".,:; ")
        # Also clean prefix like "Fig." -> "Fig"
        clean_prefix = prefix.rstrip(".").capitalize()
        return f"{clean_prefix} {clean_num}"
    return None


def is_continuation_caption(caption_text: str) -> bool:
    """Detects if caption text explicitly indicates figure continuation."""
    return bool(CONTINUATION_PATTERN.search(caption_text or ""))


def extract_caption_panel_labels(caption_text: str) -> List[str]:
    """Finds referenced sub-figure panel markers in caption text, e.g. '(a)', '(b)'."""
    if not caption_text:
        return []
    matches = PANEL_MENTIONS_IN_CAPTION_PATTERN.findall(caption_text)
    return [m.lower() for m in matches]


def find_nearby_panel_label(
    img_bbox: BoundingBox,
    text_blocks: List[Tuple],
    max_dist: float = 35.0
) -> Optional[str]:
    """
    Looks for tiny panel labels like '(a)', 'a', '(b)' positioned near the image corners.
    """
    for block in text_blocks:
        if len(block) < 5:
            continue
        bx0, by0, bx1, by1, btext = block[:5]
        text = (btext or "").strip()
        m = PANEL_LABEL_IN_TEXT_PATTERN.match(text)
        if m:
            b_bbox = BoundingBox(bx0, by0, bx1, by1)
            # Check proximity to top-left or bottom-left of image
            if (
                abs(b_bbox.x0 - img_bbox.x0) <= max_dist
                and (abs(b_bbox.y0 - img_bbox.y0) <= max_dist or abs(b_bbox.y1 - img_bbox.y1) <= max_dist)
            ):
                return m.group(1).lower()
    return None


class FigureGrouper:
    """
    Coordinates multi-image caption association and cross-page candidate detection.
    """

    def __init__(
        self,
        max_cluster_gap: float = 120.0,
        max_caption_gap: float = 85.0,
        horizontal_tolerance: float = 60.0,
    ):
        self.max_cluster_gap = max_cluster_gap
        self.max_caption_gap = max_caption_gap
        self.horizontal_tolerance = horizontal_tolerance

    def group_figures(
        self,
        images: List[ExtractedImage],
        text_blocks_by_page: Optional[Dict[int, List[Tuple]]] = None,
        page_dimensions: Optional[Dict[int, Tuple[float, float]]] = None,
    ) -> Tuple[List[ExtractedImage], List[FigureGroup]]:
        """
        Convenience wrapper to group a flat list of extracted images across pages.
        """
        images_by_page: Dict[int, List[ExtractedImage]] = {}
        for img in images:
            images_by_page.setdefault(img.page_number, []).append(img)
        t_blocks = text_blocks_by_page or {}
        p_dims = page_dimensions or {p: (600.0, 800.0) for p in images_by_page}
        return self.group_images_and_captions(images_by_page, t_blocks, p_dims)

    def group_images_and_captions(
        self,
        images_by_page: Dict[int, List[ExtractedImage]],
        text_blocks_by_page: Dict[int, List[Tuple]],
        page_dimensions: Dict[int, Tuple[float, float]],
    ) -> Tuple[List[ExtractedImage], List[FigureGroup]]:
        """
        Processes images and captions across pages to create figure groups and assign
        associations (single, group, or uncaptioned) non-destructively.
        """
        all_groups: List[FigureGroup] = []
        all_updated_images: List[ExtractedImage] = []

        sorted_pages = sorted(images_by_page.keys())

        # Step 1: Same-page grouping and association
        for page_num in sorted_pages:
            page_images = images_by_page[page_num]
            text_blocks = text_blocks_by_page.get(page_num, [])
            page_w, page_h = page_dimensions.get(page_num, (600.0, 800.0))

            updated_imgs, page_groups = self._process_same_page(
                page_num=page_num,
                images=page_images,
                text_blocks=text_blocks,
                page_width=page_w,
                page_height=page_h,
            )
            all_groups.extend(page_groups)
            all_updated_images.extend(updated_imgs)

        # Step 2: Cross-page candidate detection (bounded to adjacent pages N and N+1)
        all_updated_images, all_groups = self._detect_cross_page_candidates(
            images=all_updated_images,
            groups=all_groups,
            text_blocks_by_page=text_blocks_by_page,
            page_dimensions=page_dimensions,
        )

        return all_updated_images, all_groups

    def _process_same_page(
        self,
        page_num: int,
        images: List[ExtractedImage],
        text_blocks: List[Tuple],
        page_width: float,
        page_height: float,
    ) -> Tuple[List[ExtractedImage], List[FigureGroup]]:
        """
        Analyzes a single page for multi-image groups vs single matched images.
        """
        if not images:
            return [], []

        # Find panel labels for all images on page
        for img in images:
            panel = find_nearby_panel_label(img.bbox, text_blocks)
            if panel:
                img.panel_label = panel

        # Collect distinct detected captions on the page
        captions_map: Dict[str, Dict[str, Any]] = {}
        for img in images:
            if img.caption and img.caption_found:
                clean_cap = img.caption.strip()
                if clean_cap not in captions_map:
                    captions_map[clean_cap] = {
                        "caption": clean_cap,
                        "position": img.caption_position,
                        "candidates": img.caption_candidates,
                        "images": [img],
                    }
                else:
                    if img not in captions_map[clean_cap]["images"]:
                        captions_map[clean_cap]["images"].append(img)

        # If there are no captions found on this page
        if not captions_map:
            for img in images:
                img.association_type = "uncaptioned"
                img.association_confidence = 0.0
                img.group_id = None
            return images, []

        page_groups: List[FigureGroup] = []

        # Case 1: Multiple images sharing one caption
        for cap_text, cap_info in captions_map.items():
            matched_imgs = cap_info["images"]
            fig_label = extract_figure_label(cap_text)
            caption_panels = extract_caption_panel_labels(cap_text)

            # Check if other uncaptioned images on the page belong to this figure
            uncaptioned_on_page = [
                img for img in images
                if not img.caption_found or img.caption == ""
            ]

            candidate_group_images = list(matched_imgs)

            for uncap_img in uncaptioned_on_page:
                if uncap_img in candidate_group_images:
                    continue

                # Evidence for inclusion in group:
                # 1. Nearby panel label (e.g. caption has '(a)' and '(b)', uncaptioned image has 'b')
                has_panel_match = False
                if uncap_img.panel_label and uncap_img.panel_label in caption_panels:
                    has_panel_match = True

                # 2. Spatial alignment with matched images (horizontal row or vertical column)
                is_aligned = any(
                    self._are_images_aligned(uncap_img.bbox, m_img.bbox)
                    for m_img in matched_imgs
                )

                # 3. Gap between images is small (< max_cluster_gap)
                gap_ok = any(
                    self._distance_between_bboxes(uncap_img.bbox, m_img.bbox) <= self.max_cluster_gap
                    for m_img in matched_imgs
                )

                # 4. No competing figure caption between this image and the caption
                no_competing_caption = True
                if len(captions_map) > 1:
                    dist_to_this = min(self._distance_to_caption(uncap_img.bbox, m.bbox) for m in matched_imgs)
                    for other_cap, other_info in captions_map.items():
                        if other_cap != cap_text:
                            dist_to_other = min(self._distance_to_caption(uncap_img.bbox, m.bbox) for m in other_info["images"])
                            if dist_to_other < dist_to_this - 10:
                                no_competing_caption = False
                                break

                is_group_caption = (
                    bool(caption_panels) or
                    any(w in cap_text.lower() for w in [
                        "figures", "figs.", "figs ", "plates", "views", "panels",
                        "(left)", "(right)", "(top)", "(bottom)", "left:", "right:", "top:", "bottom:"
                    ]) or
                    bool(re.search(r'\b[a-d][\)\.]', cap_text, re.IGNORECASE))
                )

                # Check if caption bounding box spans across the uncaptioned image as well
                caption_spans = False
                if cap_info.get("bbox"):
                    cb = cap_info["bbox"]
                    caption_spans = (cb.x0 <= uncap_img.bbox.x0 + 30 and cb.x1 >= uncap_img.bbox.x1 - 30)

                should_group = has_panel_match or (
                    is_aligned and gap_ok and no_competing_caption and (is_group_caption or caption_spans)
                )

                if should_group:
                    candidate_group_images.append(uncap_img)

            # If 2 or more images belong to this caption -> Create FigureGroup
            if len(candidate_group_images) >= 2:
                group_id = f"figgrp_{uuid.uuid4().hex[:12]}"
                image_ids = [img.image_id for img in candidate_group_images]

                # Assign panel labels (a, b, c) in reading order if not explicitly assigned
                candidate_group_images.sort(key=lambda img: (img.bbox.y0, img.bbox.x0))
                letters = ["a", "b", "c", "d", "e", "f", "g"]
                for idx, img in enumerate(candidate_group_images):
                    if not img.panel_label and idx < len(letters):
                        img.panel_label = letters[idx]
                    img.group_id = group_id
                    img.caption = cap_text
                    img.caption_found = True
                    img.caption_position = cap_info["position"] or "below"
                    img.association_type = "group_matched"
                    img.association_confidence = 0.90 if caption_panels else 0.80

                # Ambiguity evaluation for the group
                review_reasons = []
                needs_review = False
                if len(captions_map) > 1:
                    needs_review = True
                    review_reasons.append("Multiple figure captions present on the same page")
                if not caption_panels and len(candidate_group_images) > 2:
                    needs_review = True
                    review_reasons.append("Multi-image cluster without explicit panel letter labels in caption")

                for img in candidate_group_images:
                    if needs_review:
                        img.needs_caption_review = True
                        img.review_reasons = list(set(img.review_reasons + review_reasons))

                fig_group = FigureGroup(
                    group_id=group_id,
                    caption_text=cap_text,
                    caption_page_number=page_num,
                    caption_position=cap_info["position"] or "below",
                    figure_label=fig_label,
                    image_ids=image_ids,
                    is_cross_page=False,
                    review_status="candidate" if needs_review else "confirmed",
                    review_reasons=review_reasons,
                    confidence=0.90 if caption_panels else 0.80,
                    evidence={
                        "same_page": True,
                        "panel_labels": [img.panel_label for img in candidate_group_images],
                        "image_count": len(candidate_group_images),
                        "caption_panels_found": caption_panels,
                    }
                )
                page_groups.append(fig_group)

            elif len(candidate_group_images) == 1:
                # Single matched image
                single_img = candidate_group_images[0]
                single_img.association_type = "single_matched"
                single_img.association_confidence = 1.0
                single_img.group_id = None

        # Any remaining unassociated images are explicitly uncaptioned
        for img in images:
            if not img.caption_found or not img.caption:
                img.association_type = "uncaptioned"
                img.association_confidence = 0.0
                img.group_id = None

        return images, page_groups

    def _detect_cross_page_candidates(
        self,
        images: List[ExtractedImage],
        groups: List[FigureGroup],
        text_blocks_by_page: Dict[int, List[Tuple]],
        page_dimensions: Dict[int, Tuple[float, float]],
    ) -> Tuple[List[ExtractedImage], List[FigureGroup]]:
        """
        Inspects adjacent pages (N and N+1) for candidate cross-page figures.
        Cross-page candidates are ALWAYS created with review_status='candidate'
        and needs_caption_review=True.
        """
        images_by_id = {img.image_id: img for img in images}
        images_by_page: Dict[int, List[ExtractedImage]] = {}
        for img in images:
            images_by_page.setdefault(img.page_number, []).append(img)

        sorted_pages = sorted(images_by_page.keys())

        for idx in range(len(sorted_pages) - 1):
            p1 = sorted_pages[idx]
            p2 = sorted_pages[idx + 1]

            if p2 != p1 + 1:
                continue

            imgs_p1 = images_by_page[p1]
            imgs_p2 = images_by_page[p2]

            # Find any caption on page 2 that indicates continuation
            for img2 in imgs_p2:
                if img2.caption and is_continuation_caption(img2.caption):
                    fig_label2 = extract_figure_label(img2.caption)
                    for img1 in imgs_p1:
                        fig_label1 = extract_figure_label(img1.caption)
                        if fig_label1 and fig_label2 and (fig_label1.split()[-1] == fig_label2.split()[-1]):
                            self._create_or_join_cross_page_group(
                                img1=img1,
                                img2=img2,
                                groups=groups,
                                reason=f"Cross-page continuation caption '{img2.caption[:40]}...' on page {p2} matches {fig_label1} on page {p1}",
                                evidence_type="continuation_caption"
                            )

            # Check for uncaptioned image at the top of page 2 matching a multi-panel figure on page 1
            groups_on_p1 = [g for g in groups if g.caption_page_number == p1]
            for g1 in groups_on_p1:
                caption_panels = extract_caption_panel_labels(g1.caption_text)
                p1_panels = [images_by_id[iid].panel_label for iid in g1.image_ids if iid in images_by_id and images_by_id[iid].panel_label]

                uncaptioned_p2 = [img for img in imgs_p2 if img.association_type == "uncaptioned" or not img.caption_found]
                for uimg in uncaptioned_p2:
                    is_panel_sequence = (
                        uimg.panel_label and (
                            uimg.panel_label in caption_panels or
                            (p1_panels and ord(uimg.panel_label) == max(ord(p) for p in p1_panels) + 1)
                        )
                    )
                    is_top_of_page = uimg.bbox.y0 <= 250.0

                    if is_panel_sequence and is_top_of_page:
                        self._add_image_to_cross_page_group(
                            group=g1,
                            new_image=uimg,
                            reason=f"Candidate cross-page panel ({uimg.panel_label}) on page {p2} continues {g1.figure_label or 'Figure'} from page {p1}",
                            evidence_type="panel_continuation"
                        )

        return images, groups

    def _create_or_join_cross_page_group(
        self,
        img1: ExtractedImage,
        img2: ExtractedImage,
        groups: List[FigureGroup],
        reason: str,
        evidence_type: str,
    ):
        """Creates or links images across pages into a candidate cross-page FigureGroup."""
        existing_group = None
        if img1.group_id:
            existing_group = next((g for g in groups if g.group_id == img1.group_id), None)

        if existing_group:
            self._add_image_to_cross_page_group(existing_group, img2, reason, evidence_type)
        else:
            group_id = f"figgrp_{uuid.uuid4().hex[:12]}"
            img1.group_id = group_id
            img1.association_type = "group_matched"
            img1.needs_caption_review = True
            img1.review_reasons.append(reason)

            img2.group_id = group_id
            img2.association_type = "group_matched"
            img2.needs_caption_review = True
            img2.review_reasons.append(reason)
            img2.caption = img1.caption
            img2.caption_found = True

            new_group = FigureGroup(
                group_id=group_id,
                caption_text=img1.caption,
                caption_page_number=img1.page_number,
                caption_position=img1.caption_position,
                figure_label=extract_figure_label(img1.caption),
                image_ids=[img1.image_id, img2.image_id],
                is_cross_page=True,
                review_status="candidate",
                review_reasons=[reason],
                confidence=0.75,
                evidence={
                    "evidence_type": evidence_type,
                    "pages": [img1.page_number, img2.page_number],
                    "requires_editor_confirmation": True,
                }
            )
            groups.append(new_group)

    def _add_image_to_cross_page_group(
        self,
        group: FigureGroup,
        new_image: ExtractedImage,
        reason: str,
        evidence_type: str,
    ):
        """Adds a candidate image to an existing FigureGroup across pages."""
        if new_image.image_id not in group.image_ids:
            group.image_ids.append(new_image.image_id)
        group.is_cross_page = True
        group.review_status = "candidate"
        if reason not in group.review_reasons:
            group.review_reasons.append(reason)

        new_image.group_id = group.group_id
        new_image.caption = group.caption_text
        new_image.caption_found = True
        new_image.caption_position = group.caption_position
        new_image.association_type = "group"
        new_image.association_confidence = 0.70
        new_image.needs_caption_review = True
        if reason not in new_image.review_reasons:
            new_image.review_reasons.append(reason)

    def _are_images_aligned(self, b1: BoundingBox, b2: BoundingBox) -> bool:
        """Checks horizontal alignment (side-by-side) or vertical alignment (stacked column)."""
        vert_overlap = max(0.0, min(b1.y1, b2.y1) - max(b1.y0, b2.y0))
        if vert_overlap > 15.0:
            return True
        horiz_overlap = calculate_horizontal_overlap(b1, b2)
        if horiz_overlap > 15.0:
            return True
        return False

    def _distance_between_bboxes(self, b1: BoundingBox, b2: BoundingBox) -> float:
        """Calculates Euclidean-like minimum distance between two bounding boxes."""
        dx = max(0.0, max(b1.x0, b2.x0) - min(b1.x1, b2.x1))
        dy = max(0.0, max(b1.y0, b2.y0) - min(b1.y1, b2.y1))
        return (dx ** 2 + dy ** 2) ** 0.5

    def _distance_to_caption(self, img_bbox: BoundingBox, caption_img_bbox: BoundingBox) -> float:
        return self._distance_between_bboxes(img_bbox, caption_img_bbox)
