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
from typing import Any, Dict, List
import docx
from docx.shared import Inches, Pt
from PIL import Image


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
    ) -> bytes:
        """
        Builds a DOCX document in reading order from approved items.

        Each item must contain:
        - suggested_filename: str
        - approved_alt_text: str
        - image_bytes: bytes
        - image_format: str (e.g. 'png', 'jpeg')

        The following optional fields are used to build meaningful failure messages:
        - image_id: str
        - page_number: int
        - source_path: str  # storage key of the original extracted image file
        """
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
        DocxBuilder.validate_docx(docx_bytes, expected_count=len(items))

        return docx_bytes

    @staticmethod
    def validate_docx(docx_bytes: bytes, expected_count: int) -> bool:
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

        # Validate absence of unwanted boilerplate/labels
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
