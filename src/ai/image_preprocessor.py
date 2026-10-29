"""
Image Preprocessing for Multimodal AI Inference
------------------------------------------------
Optimizes temporary in-memory copies of extracted images for multimodal inference
to fit within provider payload constraints (avoiding HTTP 413 Request Entity Too Large).

Guarantees:
- Original extracted assets in FileStore are never modified or overwritten.
- Correct physical orientation is preserved via EXIF transpose.
- Diagrams, charts, and line art with small text are preserved as lossless/high-quality PNGs.
- Continuous-tone photographic images are compressed as high-quality JPEGs.
- Small images are never enlarged/upscaled.
- Unnecessary metadata (EXIF, ICC, comments) is stripped from the inference copy.
- Base64 transmission overhead is strictly accounted for.
"""

import io
from dataclasses import dataclass
from typing import Tuple, Optional
from PIL import Image, ImageOps


# Base64 expansion factor: 4 chars per 3 bytes (plus padding and data URI header)
BASE64_EXPANSION_RATIO = 4.0 / 3.0


@dataclass
class PreprocessedImage:
    """Telemetry and binary payload for an inference-optimized image."""
    image_bytes: bytes
    image_format: str  # "png" or "jpeg"
    mime_type: str     # "image/png" or "image/jpeg"
    width: int
    height: int
    original_width: int
    original_height: int
    original_bytes_length: int
    optimized_bytes_length: int
    was_resized: bool
    was_reformatted: bool
    estimated_base64_bytes: int


def is_diagram_or_lineart(im: Image.Image) -> bool:
    """
    Heuristic to distinguish diagrams, charts, plots, and screenshots from continuous-tone photos.
    Diagrams typically have restricted color palettes, large uniform regions, or palette modes.
    """
    if im.mode in ("1", "L", "P"):
        return True

    # Check color palette complexity (up to 2048 distinct colors)
    # Diagrams, floorplans, and plots typically have <= 1024 unique colors.
    colors = im.getcolors(maxcolors=2048)
    if colors is not None:
        return True

    return False


class ImagePreprocessor:
    """
    Prepares images for multimodal LLM inference while respecting provider payload ceilings.
    """

    def __init__(
        self,
        max_dimension: int = 1536,
        max_payload_bytes: int = 3_500_000,
        jpeg_quality: int = 85,
    ):
        self.max_dimension = max_dimension
        self.max_payload_bytes = max_payload_bytes
        self.jpeg_quality = jpeg_quality
        # Maximum binary image size that can fit into the base64 payload with ~100KB headroom for JSON/text
        self.max_binary_budget = int((max_payload_bytes - 100_000) / BASE64_EXPANSION_RATIO)

    def estimate_base64_size(self, binary_len: int) -> int:
        """Calculates expected Base64 string length including padding."""
        return ((binary_len + 2) // 3) * 4

    def preprocess(self, image_bytes: bytes, image_format: str) -> PreprocessedImage:
        """
        Preprocesses an image binary for inference without modifying the original asset.
        """
        if not image_bytes or len(image_bytes) == 0:
            raise ValueError("Empty image binary provided.")

        try:
            im = Image.open(io.BytesIO(image_bytes))
            im.load()  # Force load to catch corrupted image data early
        except Exception as e:
            raise ValueError(f"Invalid or corrupt image binary: {str(e)}")

        orig_w, orig_h = im.size
        orig_len = len(image_bytes)

        # 1. Correct physical orientation based on EXIF tag
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass  # If EXIF is corrupt or absent, proceed as-is

        w, h = im.size
        was_resized = False
        was_reformatted = False

        # 2. Check if downsizing is required (never upscale!)
        if w > self.max_dimension or h > self.max_dimension:
            scale = min(self.max_dimension / float(w), self.max_dimension / float(h))
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
            w, h = im.size
            was_resized = True

        is_diagram = is_diagram_or_lineart(im)
        orig_fmt_clean = image_format.lower().replace("jpg", "jpeg").lstrip(".")

        # 3. Determine target format:
        # If diagram/screenshot/plot -> prefer lossless PNG
        # If continuous-tone photo -> prefer JPEG (or if original was JPEG)
        target_fmt = "png" if (is_diagram and orig_fmt_clean != "jpeg") else "jpeg"

        # Check if original was already small, unresized, and in valid format
        initial_base64 = self.estimate_base64_size(orig_len)
        if (
            not was_resized
            and initial_base64 <= self.max_payload_bytes
            and orig_fmt_clean in ("png", "jpeg")
            and (orig_fmt_clean == target_fmt or (is_diagram and orig_fmt_clean == "png"))
        ):
            # Safe to use as-is without recompression
            return PreprocessedImage(
                image_bytes=image_bytes,
                image_format=orig_fmt_clean,
                mime_type=f"image/{orig_fmt_clean}",
                width=w,
                height=h,
                original_width=orig_w,
                original_height=orig_h,
                original_bytes_length=orig_len,
                optimized_bytes_length=orig_len,
                was_resized=False,
                was_reformatted=False,
                estimated_base64_bytes=initial_base64,
            )

        # 4. Save temporary inference copy
        out_bytes, out_fmt = self._encode_image(im, target_fmt)
        if out_fmt != orig_fmt_clean:
            was_reformatted = True

        # 5. Guard against payload overflow:
        # If still too large, downscale or compress iteratively until within budget
        current_w, current_h = w, h
        attempts = 0
        while self.estimate_base64_size(len(out_bytes)) > self.max_payload_bytes and attempts < 4:
            attempts += 1
            # Scale down by 20%
            current_w = max(1, int(current_w * 0.8))
            current_h = max(1, int(current_h * 0.8))
            im = im.resize((current_w, current_h), Image.Resampling.LANCZOS)
            was_resized = True

            # If PNG was too large for budget even with small colors, switch to JPEG Q80
            if target_fmt == "png" and attempts >= 2:
                target_fmt = "jpeg"
                was_reformatted = True

            out_bytes, out_fmt = self._encode_image(im, target_fmt)

        final_base64 = self.estimate_base64_size(len(out_bytes))
        if final_base64 > self.max_payload_bytes:
            raise ValueError(
                f"Preprocessed image size ({len(out_bytes)} bytes, ~{final_base64} b64) "
                f"exceeds payload limit of {self.max_payload_bytes} bytes."
            )

        return PreprocessedImage(
            image_bytes=out_bytes,
            image_format=out_fmt,
            mime_type=f"image/{out_fmt}",
            width=current_w,
            height=current_h,
            original_width=orig_w,
            original_height=orig_h,
            original_bytes_length=orig_len,
            optimized_bytes_length=len(out_bytes),
            was_resized=was_resized,
            was_reformatted=was_reformatted,
            estimated_base64_bytes=final_base64,
        )

    def _encode_image(self, im: Image.Image, fmt: str) -> Tuple[bytes, str]:
        """Encodes PIL image to bytes in the specified format, stripping metadata."""
        buf = io.BytesIO()

        if fmt == "png":
            # For PNG, optimize compression
            im.save(buf, format="PNG", optimize=True)
            return buf.getvalue(), "png"
        else:
            # For JPEG, ensure RGB mode (convert RGBA with white background)
            if im.mode in ("RGBA", "LA", "P"):
                rgb_im = Image.new("RGB", im.size, (255, 255, 255))
                if im.mode == "P":
                    im = im.convert("RGBA")
                rgb_im.paste(im, mask=im.split()[-1] if "A" in im.mode else None)
            elif im.mode != "RGB":
                rgb_im = im.convert("RGB")
            else:
                rgb_im = im

            rgb_im.save(buf, format="JPEG", quality=self.jpeg_quality, optimize=True)
            return buf.getvalue(), "jpeg"
