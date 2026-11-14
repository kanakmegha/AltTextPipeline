"""
DOCX Export Builder for AltTextPipeline.
---------------------------------------
Builds standard Microsoft Word (.docx) documents containing:
For every extracted image, in original PDF reading order, exactly this sequence:
1. The extracted image filename without its file extension, on its own line.
2. The image itself, on the next line (embedded with Word OpenXML accessibility metadata wp:docPr descr).
3. The approved alt-text description, on the following line.

No extra headings, page-number labels, source captions, figure labels, visible "Alt Text:" prefixes,
or audit information are included inside the main document body.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional
import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image
from .design_config import DEFAULT_SECTION_LABELS, validate_design_config


class ExportValidationError(Exception):
    """Raised when generated DOCX fails post-generation structure validation."""
    pass


class DocxBuilder:
    """Constructs and validates DOCX exports with approved alt text and accessibility metadata."""

    @staticmethod
    def build_docx(
        project_name: str,
        items: List[Dict[str, Any]],
        include_page_breaks: bool = False,
        design_config: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        """
        Builds a DOCX document in reading order from approved items.
        When design_config is None, preserves the exact legacy 3-element layout.
        """
        if design_config is not None:
            return DocxBuilder._build_custom_docx(
                project_name=project_name,
                items=items,
                design_config=design_config,
                include_page_breaks=include_page_breaks,
            )
        doc = docx.Document()

        for idx, item in enumerate(items):
            # 1. Extracted image filename without extension on its own line
            filename = item.get("suggested_filename", f"figure_{idx + 1}.png")
            stem = Path(filename).stem

            # Identity/source context used for meaningful failure messages.
            image_id = item.get("image_id", f"figure_{idx + 1}")
            page_number = item.get("page_number")
            source_path = item.get("source_path", item.get("file_storage_key", "<not recorded>"))

            p_stem = doc.add_paragraph()
            p_stem.paragraph_format.space_before = Pt(14) if idx > 0 else Pt(0)
            p_stem.paragraph_format.space_after = Pt(4)
            p_stem.add_run(stem)

            # 2. Original extracted image on the next line
            img_bytes = item.get("image_bytes")
            if not img_bytes:
                raise ValueError(
                    f"Missing image bytes for figure '{stem}' "
                    f"(image_id={image_id}, page={page_number}, source={source_path}); "
                    "refusing to export a text-only document."
                )

            # Verify the source image is a real, decodable image file before embedding.
            # Never silently fall back to placeholder dimensions: an unreadable source
            # must fail the export with a message that identifies the figure and source.
            try:
                with Image.open(io.BytesIO(img_bytes)) as pil_img:
                    w_px, h_px = pil_img.size
            except Exception as exc:
                raise ValueError(
                    f"Source image for figure '{stem}' "
                    f"(image_id={image_id}, page={page_number}, source={source_path}) "
                    f"is not a decodable image file: {exc}"
                ) from exc

            if w_px <= 0 or h_px <= 0:
                raise ValueError(
                    f"Source image for figure '{stem}' "
                    f"(image_id={image_id}, page={page_number}, source={source_path}) "
                    f"has invalid dimensions {w_px}x{h_px}."
                )

            aspect = h_px / w_px
            max_w_in = 5.5
            max_h_in = 7.0

            p_img = doc.add_paragraph()
            p_img.paragraph_format.space_before = Pt(4)
            p_img.paragraph_format.space_after = Pt(4)
            r_img = p_img.add_run()

            # Native size in inches at standard 96 DPI
            native_w_in = w_px / 96.0
            native_h_in = h_px / 96.0

            try:
                if native_w_in <= max_w_in and native_h_in <= max_h_in:
                    # Naturally fits within page margins; embed without upscaling to preserve crispness
                    shape = r_img.add_picture(io.BytesIO(img_bytes))
                elif max_w_in * aspect > max_h_in:
                    # Height-constrained to fit standard page height
                    shape = r_img.add_picture(io.BytesIO(img_bytes), height=Inches(max_h_in))
                else:
                    # Width-constrained to fit printable page width
                    shape = r_img.add_picture(io.BytesIO(img_bytes), width=Inches(max_w_in))
            except Exception as exc:
                raise ValueError(
                    f"Failed to embed image for figure '{stem}' "
                    f"(image_id={image_id}, page={page_number}, source={source_path}): {exc}"
                ) from exc

            # Embed Alt Text in Word OpenXML Accessibility Metadata (wp:docPr descr attribute)
            approved_alt = item.get("approved_alt_text", "").strip()
            inline = shape._inline
            inline.docPr.set("descr", approved_alt)
            inline.docPr.set("title", stem)

            # 3. Approved alt-text description on the following line
            p_alt = doc.add_paragraph()
            p_alt.paragraph_format.space_before = Pt(4)
            p_alt.paragraph_format.space_after = Pt(14)
            p_alt.add_run(approved_alt)

            if include_page_breaks and idx < len(items) - 1:
                doc.add_page_break()

        # Save to buffer
        out_stream = io.BytesIO()
        doc.save(out_stream)
        docx_bytes = out_stream.getvalue()

        # Validate structure and content immediately
        DocxBuilder.validate_docx(docx_bytes, expected_count=len(items), custom_design=False)

        return docx_bytes

    @staticmethod
    def _add_bottom_border(paragraph) -> None:
        pPr = paragraph._p.get_or_add_pPr()
        pBdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:space"), "6")
        bottom.set(qn("w:color"), "CBD5E1")
        pBdr.append(bottom)
        pPr.append(pBdr)

    @staticmethod
    def _decode_and_measure_image(item: Dict[str, Any], idx: int) -> tuple[bytes, int, int, str]:
        filename = item.get("suggested_filename", f"figure_{idx + 1}.png")
        stem = Path(filename).stem
        image_id = item.get("image_id", f"figure_{idx + 1}")
        page_number = item.get("page_number")
        source_path = item.get("source_path", item.get("file_storage_key", "<not recorded>"))

        img_bytes = item.get("image_bytes")
        if not img_bytes:
            raise ValueError(
                f"Missing image bytes for figure '{stem}' "
                f"(image_id={image_id}, page={page_number}, source={source_path}); "
                "refusing to export a text-only document."
            )
        try:
            with Image.open(io.BytesIO(img_bytes)) as pil_img:
                w_px, h_px = pil_img.size
        except Exception as exc:
            raise ValueError(
                f"Source image for figure '{stem}' "
                f"(image_id={image_id}, page={page_number}, source={source_path}) "
                f"is not a decodable image file: {exc}"
            ) from exc
        if w_px <= 0 or h_px <= 0:
            raise ValueError(
                f"Source image for figure '{stem}' "
                f"(image_id={image_id}, page={page_number}, source={source_path}) "
                f"has invalid dimensions {w_px}x{h_px}."
            )
        return img_bytes, w_px, h_px, stem

    @staticmethod
    def _embed_scaled_image(paragraph, img_bytes: bytes, w_px: int, h_px: int, max_w_in: float, max_h_in: float, approved_alt: str, title: str) -> None:
        r_img = paragraph.add_run()
        aspect = h_px / w_px
        native_w_in = w_px / 96.0
        native_h_in = h_px / 96.0
        if native_w_in <= max_w_in and native_h_in <= max_h_in:
            shape = r_img.add_picture(io.BytesIO(img_bytes))
        elif max_w_in * aspect > max_h_in:
            shape = r_img.add_picture(io.BytesIO(img_bytes), height=Inches(max_h_in))
        else:
            shape = r_img.add_picture(io.BytesIO(img_bytes), width=Inches(max_w_in))
        inline = shape._inline
        inline.docPr.set("descr", approved_alt)
        inline.docPr.set("title", title)

    @staticmethod
    def _resolve_field_value(item: Dict[str, Any], field_key: str) -> Optional[str]:
        if field_key == "image_id":
            val = item.get("image_id")
            return str(val).strip() if val else None
        if field_key == "filename":
            val = item.get("suggested_filename")
            return str(val).strip() if val else None
        if field_key == "page_number":
            val = item.get("page_number")
            return str(val) if val is not None else None
        if field_key == "figure_label":
            val = item.get("figure_label")
            return str(val).strip() if val else None
        if field_key == "original_caption":
            val = item.get("original_caption")
            return str(val).strip() if val else None
        if field_key == "generated_alt_text":
            val = item.get("generated_alt_text")
            return str(val).strip() if val else None
        if field_key == "approved_alt_text":
            val = item.get("approved_alt_text")
            return str(val).strip() if val else None
        if field_key == "review_status":
            val = item.get("review_status")
            return str(val).strip().replace("_", " ").title() if val else None
        return None

    @staticmethod
    def _build_custom_docx(
        project_name: str,
        items: List[Dict[str, Any]],
        design_config: Dict[str, Any],
        include_page_breaks: bool = False,
    ) -> bytes:
        cfg = validate_design_config(design_config, project_name=project_name)
        doc = docx.Document()

        font_map = {"small": 9.5, "medium": 11.0, "large": 13.0}
        base_pt = font_map[cfg["font_size"]]
        font_name = cfg["font_family"]
        align_map = {
            "left": WD_ALIGN_PARAGRAPH.LEFT,
            "center": WD_ALIGN_PARAGRAPH.CENTER,
            "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
        }
        para_align = align_map[cfg["alignment"]]
        spacing_map = {"compact": 6, "normal": 12, "spacious": 20}
        entry_gap = spacing_map[cfg["entry_spacing"]]
        img_max_map = {
            "small": (3.0, 4.0),
            "medium": (4.5, 5.8),
            "large": (5.8, 7.2),
        }
        max_w_in, max_h_in = img_max_map[cfg["image_size"]]

        normal_style = doc.styles["Normal"]
        normal_style.font.name = font_name
        normal_style.font.size = Pt(base_pt)

        def _style_run(run, size_delta: float = 0.0, bold: bool = False, italic: bool = False, color: Optional[RGBColor] = None) -> None:
            run.font.name = font_name
            run.font.size = Pt(max(8.0, base_pt + size_delta))
            run.bold = bold
            run.italic = italic
            if color is not None:
                run.font.color.rgb = color

        def _add_heading_text(container, label_text: str, is_title: bool = False) -> Any:
            p = container.add_paragraph()
            p.alignment = para_align
            p.paragraph_format.space_before = Pt(4 if not is_title else 0)
            p.paragraph_format.space_after = Pt(3 if not is_title else 8)
            h_style = cfg["heading_style"]
            display = label_text.upper() if h_style == "uppercase" else label_text
            r = p.add_run(display)
            color = RGBColor(0x1E, 0x3A, 0x8A) if h_style == "accent" else None
            _style_run(
                r,
                size_delta=(6.0 if is_title else 0.5),
                bold=(is_title or h_style in ("bold", "accent", "uppercase")),
                color=color,
            )
            return p

        # Document title & introduction
        if cfg["document_title"]:
            p_title = _add_heading_text(doc, cfg["document_title"], is_title=True)
            if cfg["style_preset"] == "professional":
                DocxBuilder._add_bottom_border(p_title)

        if cfg["introduction"]:
            p_intro = doc.add_paragraph()
            p_intro.alignment = para_align
            p_intro.paragraph_format.space_after = Pt(10)
            r_intro = p_intro.add_run(cfg["introduction"])
            _style_run(r_intro)

        for phrase in cfg["custom_phrases"]:
            if phrase["placement"] == "header":
                p_ph = doc.add_paragraph()
                p_ph.alignment = para_align
                p_ph.paragraph_format.space_after = Pt(6)
                r_ph = p_ph.add_run(phrase["text"])
                _style_run(r_ph, italic=True, color=RGBColor(0x47, 0x55, 0x69))

        # Footer text
        if cfg["footer_text"]:
            for section in doc.sections:
                footer = section.footer
                fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
                fp.alignment = para_align
                fr = fp.add_run(cfg["footer_text"])
                _style_run(fr, size_delta=-1.5, color=RGBColor(0x64, 0x74, 0x8B))

        enabled_fields = [f["key"] for f in cfg["fields"] if f["enabled"]]
        labels = cfg.get("section_labels") or DEFAULT_SECTION_LABELS
        include_image_in_body = "image" in enabled_fields
        expected_shapes = 0

        if cfg["style_preset"] == "table" and items:
            show_img_col = include_image_in_body
            cols = 2 if show_img_col else 1
            table = doc.add_table(rows=1, cols=cols)
            table.style = "Table Grid"
            table.autofit = False
            hdr_cells = table.rows[0].cells
            if show_img_col:
                hdr_cells[0].width = Inches(2.4)
                hdr_cells[1].width = Inches(4.1)
                hdr_cells[0].paragraphs[0].text = "Image"
                hdr_cells[1].paragraphs[0].text = "Image Details & Alt Text"
            else:
                hdr_cells[0].width = Inches(6.5)
                hdr_cells[0].paragraphs[0].text = "Image Details & Alt Text"
            for cell in hdr_cells:
                for run in cell.paragraphs[0].runs:
                    _style_run(run, bold=True)

            for idx, item in enumerate(items):
                img_bytes, w_px, h_px, stem = DocxBuilder._decode_and_measure_image(item, idx)
                approved_alt = (item.get("approved_alt_text") or "").strip()
                row_cells = table.add_row().cells
                if show_img_col:
                    row_cells[0].width = Inches(2.4)
                    row_cells[1].width = Inches(4.1)
                    p_cell_img = row_cells[0].paragraphs[0]
                    p_cell_img.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    tbl_img_w = min(2.1, max_w_in)
                    DocxBuilder._embed_scaled_image(
                        p_cell_img, img_bytes, w_px, h_px, tbl_img_w, 3.0, approved_alt, stem
                    )
                    expected_shapes += 1
                    detail_cell = row_cells[1]
                else:
                    row_cells[0].width = Inches(6.5)
                    detail_cell = row_cells[0]

                first_para = True
                for fkey in enabled_fields:
                    if fkey == "image":
                        continue
                    if fkey == "approved_alt_text":
                        for ph in cfg["custom_phrases"]:
                            if ph["placement"] == "before_alt_text":
                                p_b = detail_cell.paragraphs[0] if first_para else detail_cell.add_paragraph()
                                first_para = False
                                r_b = p_b.add_run(ph["text"])
                                _style_run(r_b, italic=True)
                    val = DocxBuilder._resolve_field_value(item, fkey)
                    if not val:
                        continue
                    p_d = detail_cell.paragraphs[0] if first_para else detail_cell.add_paragraph()
                    first_para = False
                    p_d.alignment = para_align
                    p_d.paragraph_format.space_after = Pt(3)
                    lbl = labels.get(fkey, "")
                    if lbl:
                        r_lbl = p_d.add_run(f"{lbl}: ")
                        _style_run(r_lbl, bold=True)
                    r_val = p_d.add_run(val)
                    _style_run(r_val)
                    if fkey == "approved_alt_text":
                        for ph in cfg["custom_phrases"]:
                            if ph["placement"] == "after_alt_text":
                                p_a = detail_cell.add_paragraph()
                                r_a = p_a.add_run(ph["text"])
                                _style_run(r_a, italic=True)
        else:
            for idx, item in enumerate(items):
                img_bytes, w_px, h_px, stem = DocxBuilder._decode_and_measure_image(item, idx)
                approved_alt = (item.get("approved_alt_text") or "").strip()

                for fkey in enabled_fields:
                    if fkey == "image":
                        p_img = doc.add_paragraph()
                        p_img.alignment = para_align
                        p_img.paragraph_format.space_before = Pt(4)
                        p_img.paragraph_format.space_after = Pt(4)
                        DocxBuilder._embed_scaled_image(
                            p_img, img_bytes, w_px, h_px, max_w_in, max_h_in, approved_alt, stem
                        )
                        expected_shapes += 1
                        continue

                    if fkey == "approved_alt_text":
                        for ph in cfg["custom_phrases"]:
                            if ph["placement"] == "before_alt_text":
                                p_b = doc.add_paragraph()
                                p_b.alignment = para_align
                                r_b = p_b.add_run(ph["text"])
                                _style_run(r_b, italic=True, color=RGBColor(0x47, 0x55, 0x69))

                    val = DocxBuilder._resolve_field_value(item, fkey)
                    if not val:
                        continue

                    lbl = labels.get(fkey, "")
                    if fkey in ("approved_alt_text", "original_caption", "generated_alt_text") and lbl:
                        _add_heading_text(doc, lbl)
                        p_v = doc.add_paragraph()
                        p_v.alignment = para_align
                        p_v.paragraph_format.space_after = Pt(4)
                        r_v = p_v.add_run(val)
                        _style_run(r_v)
                    else:
                        p_v = doc.add_paragraph()
                        p_v.alignment = para_align
                        p_v.paragraph_format.space_after = Pt(3)
                        if lbl:
                            r_lbl = p_v.add_run(f"{lbl}: ")
                            _style_run(r_lbl, bold=(cfg["heading_style"] != "plain"))
                        r_v = p_v.add_run(val)
                        _style_run(r_v)

                    if fkey == "approved_alt_text":
                        for ph in cfg["custom_phrases"]:
                            if ph["placement"] == "after_alt_text":
                                p_a = doc.add_paragraph()
                                p_a.alignment = para_align
                                r_a = p_a.add_run(ph["text"])
                                _style_run(r_a, italic=True, color=RGBColor(0x47, 0x55, 0x69))

                if idx < len(items) - 1:
                    if include_page_breaks or cfg["entry_separator"] == "page_break":
                        doc.add_page_break()
                    elif cfg["entry_separator"] == "line":
                        p_sep = doc.add_paragraph()
                        p_sep.paragraph_format.space_before = Pt(max(2, entry_gap // 2))
                        p_sep.paragraph_format.space_after = Pt(entry_gap)
                        DocxBuilder._add_bottom_border(p_sep)
                    else:
                        p_sp = doc.add_paragraph()
                        p_sp.paragraph_format.space_after = Pt(entry_gap)

        for phrase in cfg["custom_phrases"]:
            if phrase["placement"] == "closing":
                p_cl = doc.add_paragraph()
                p_cl.alignment = para_align
                p_cl.paragraph_format.space_before = Pt(8)
                r_cl = p_cl.add_run(phrase["text"])
                _style_run(r_cl, italic=True)

        if cfg["closing_text"]:
            p_close = doc.add_paragraph()
            p_close.alignment = para_align
            p_close.paragraph_format.space_before = Pt(10)
            r_close = p_close.add_run(cfg["closing_text"])
            _style_run(r_close)

        out_stream = io.BytesIO()
        doc.save(out_stream)
        docx_bytes = out_stream.getvalue()

        DocxBuilder.validate_docx(docx_bytes, expected_count=expected_shapes, custom_design=True)
        return docx_bytes

    @staticmethod
    def validate_docx(docx_bytes: bytes, expected_count: int, custom_design: bool = False) -> bool:
        """
        Reopens the generated DOCX and validates:
        1. It can be parsed as a valid Word document.
        2. Contains the expected number of inline image shapes.
        3. Contains embedded accessibility metadata (descr) on the shapes.
        4. Does NOT contain unwanted headings, source captions, page numbers, or 'Alt Text:' prefix.
        """
        try:
            doc = docx.Document(io.BytesIO(docx_bytes))
        except Exception as e:
            raise ExportValidationError(f"Generated DOCX cannot be reopened: {e}") from e

        # Validate inline shapes count
        shapes = doc.inline_shapes
        if len(shapes) != expected_count:
            raise ExportValidationError(
                f"Generated DOCX contains {len(shapes)} images, expected {expected_count}"
            )

        # Validate that every image placement is backed by an embedded media file
        # referenced through a declared package relationship. Note: python-docx may
        # deduplicate identical image blobs into a single media file, so media-file
        # count need not equal placement count; relationship resolution is what matters.
        rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
            archive_names = set(z.namelist())
            rels_root = ET.fromstring(z.read("word/_rels/document.xml.rels"))

        image_targets = []
        for rel in rels_root.findall(f"{{{rel_ns}}}Relationship"):
            if rel.attrib.get("Type", "").endswith("/image"):
                target = rel.attrib.get("Target", "")
                media_path = f"word/{target}" if not target.startswith("word/") else target
                image_targets.append(media_path)

        if expected_count > 0 and not image_targets:
            raise ExportValidationError(
                f"Generated DOCX contains {expected_count} image placements but no embedded image relationships"
            )

        missing = [path for path in image_targets if path not in archive_names]
        if missing:
            raise ExportValidationError(
                f"Generated DOCX image relationships reference missing media files: {missing}"
            )

        # Validate accessibility metadata
        from src.ai.cleaner import is_contaminated_alt_text
        for idx, shape in enumerate(shapes):
            descr = shape._inline.docPr.get("descr")
            if descr is None:
                raise ExportValidationError(
                    f"Image {idx + 1} in generated DOCX is missing accessibility metadata (descr)"
                )
            if is_contaminated_alt_text(descr):
                raise ExportValidationError(
                    f"Image {idx + 1} accessibility metadata contains prompt or scratchpad contamination: '{descr[:50]}...'"
                )

        # Validate absence of unwanted boilerplate/labels for default legacy exports
        if not custom_design:
            full_text = "\n".join(p.text for p in doc.paragraphs)
            unwanted = [
                "Alt Text:",
                "Source: PDF Page",
                "Total Figures Exported:",
                "Alt-Text Asset Export:",
            ]
            for term in unwanted:
                if term in full_text:
                    raise ExportValidationError(f"Generated DOCX contains unwanted boilerplate text: '{term}'")

        return True
