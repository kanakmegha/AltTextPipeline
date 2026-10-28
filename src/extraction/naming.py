"""
Deterministic Filename Sanitization and Resolution
--------------------------------------------------
Builds clean, filesystem-safe filenames derived from captions, with deterministic
collision resolution and fallback patterns.
"""

import re
from typing import Set, Optional


FIGURE_PREFIX_PATTERN = re.compile(
    r'^(Figure|Fig\.?|Plate|Map|Chart|Graph|Schema|Table)\s*([\d\w.-]+)?',
    re.IGNORECASE
)

INVALID_CHARS_PATTERN = re.compile(r'[\\/:*?"<>|#%&{}\$\'!`@+`=]')


def normalize_whitespace(text: str) -> str:
    """Normalize and collapse arbitrary whitespace."""
    return " ".join((text or "").split()).strip()


def sanitize_filename(name: str, max_length: int = 60) -> str:
    """Sanitize a raw string into a valid, readable filesystem base name."""
    if not name:
        return "Figure"

    # Replace invalid chars with space or remove
    cleaned = INVALID_CHARS_PATTERN.sub("", name)
    cleaned = normalize_whitespace(cleaned)

    # Replace spaces with underscores for clean terminal and export filenames
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = cleaned.strip("._-")

    if not cleaned:
        cleaned = "Figure"

    return cleaned[:max_length].rstrip("._-")


def build_concise_filename(
    caption_text: Optional[str],
    page_number: int,
    image_sequence: int
) -> str:
    """
    Derives a concise, professional filename from caption text.
    If caption matches standard figure prefixes (e.g., Figure 10), it prioritizes
    the prefix and key descriptor words.
    """
    if not caption_text:
        return f"Figure_p{page_number}_{image_sequence}"

    text = normalize_whitespace(caption_text)
    match = FIGURE_PREFIX_PATTERN.match(text)

    if match:
        prefix_type = match.group(1).capitalize()
        prefix_num = match.group(2) or ""
        prefix = f"{prefix_type}_{prefix_num}".strip("_")

        remainder = text[match.end():].strip(".:;, -")
        remainder_words = remainder.split()[:3]
        desc = "_".join(remainder_words)

        if desc:
            raw_name = f"{prefix}_{desc}"
        else:
            raw_name = prefix
    else:
        words = text.split()[:4]
        raw_name = "_".join(words)

    return sanitize_filename(raw_name)


def allocate_unique_filename(
    base_name: str,
    extension: str,
    used_names: Set[str]
) -> str:
    """
    Deterministically resolves collisions using numeric suffix `_1`, `_2`, etc.
    Maintains case-insensitive uniqueness check.
    """
    clean_ext = extension.lower().lstrip(".")
    candidate = f"{base_name}.{clean_ext}"
    counter = 1

    while candidate.lower() in used_names:
        candidate = f"{base_name}_{counter}.{clean_ext}"
        counter += 1

    used_names.add(candidate.lower())
    return candidate
