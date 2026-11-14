"""
Document Design Configuration & Validation for AltTextPipeline DOCX Exports.
---------------------------------------------------------------------------
Defines presets, available field definitions, and strict validation for
user-customizable Word document layouts without modifying approved alt-text content.
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional


STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "minimal": {
        "id": "minimal",
        "name": "Minimal",
        "description": "Clean, simple document with minimal decoration.",
        "defaults": {
            "style_preset": "minimal",
            "font_family": "Calibri",
            "font_size": "medium",
            "heading_style": "plain",
            "alignment": "left",
            "entry_spacing": "compact",
            "entry_separator": "none",
            "image_size": "medium",
            "fields": [
                {"key": "filename", "enabled": True},
                {"key": "image", "enabled": True},
                {"key": "approved_alt_text", "enabled": True},
                {"key": "original_caption", "enabled": False},
                {"key": "figure_label", "enabled": False},
                {"key": "page_number", "enabled": False},
                {"key": "generated_alt_text", "enabled": False},
                {"key": "image_id", "enabled": False},
                {"key": "review_status", "enabled": False},
            ],
        },
    },
    "editorial": {
        "id": "editorial",
        "name": "Editorial",
        "description": "Structured document with clear headings and detailed image information.",
        "defaults": {
            "style_preset": "editorial",
            "font_family": "Georgia",
            "font_size": "medium",
            "heading_style": "accent",
            "alignment": "left",
            "entry_spacing": "spacious",
            "entry_separator": "line",
            "image_size": "large",
            "fields": [
                {"key": "figure_label", "enabled": True},
                {"key": "page_number", "enabled": True},
                {"key": "image", "enabled": True},
                {"key": "original_caption", "enabled": True},
                {"key": "approved_alt_text", "enabled": True},
                {"key": "filename", "enabled": False},
                {"key": "generated_alt_text", "enabled": False},
                {"key": "image_id", "enabled": False},
                {"key": "review_status", "enabled": False},
            ],
        },
    },
    "table": {
        "id": "table",
        "name": "Table",
        "description": "Compact tabular presentation for multiple images.",
        "defaults": {
            "style_preset": "table",
            "font_family": "Calibri",
            "font_size": "small",
            "heading_style": "bold",
            "alignment": "left",
            "entry_spacing": "compact",
            "entry_separator": "none",
            "image_size": "small",
            "fields": [
                {"key": "image", "enabled": True},
                {"key": "figure_label", "enabled": True},
                {"key": "page_number", "enabled": True},
                {"key": "original_caption", "enabled": True},
                {"key": "approved_alt_text", "enabled": True},
                {"key": "filename", "enabled": False},
                {"key": "generated_alt_text", "enabled": False},
                {"key": "image_id", "enabled": False},
                {"key": "review_status", "enabled": False},
            ],
        },
    },
    "professional": {
        "id": "professional",
        "name": "Professional",
        "description": "Formal report-style presentation with a title and structured sections.",
        "defaults": {
            "style_preset": "professional",
            "font_family": "Arial",
            "font_size": "medium",
            "heading_style": "uppercase",
            "alignment": "left",
            "entry_spacing": "normal",
            "entry_separator": "line",
            "image_size": "medium",
            "fields": [
                {"key": "figure_label", "enabled": True},
                {"key": "filename", "enabled": True},
                {"key": "page_number", "enabled": True},
                {"key": "image", "enabled": True},
                {"key": "original_caption", "enabled": True},
                {"key": "approved_alt_text", "enabled": True},
                {"key": "review_status", "enabled": True},
                {"key": "generated_alt_text", "enabled": False},
                {"key": "image_id", "enabled": False},
            ],
        },
    },
}

FIELD_CATALOG: List[Dict[str, str]] = [
    {"key": "image", "label": "Extracted image", "default_heading": ""},
    {"key": "image_id", "label": "Image ID", "default_heading": "Image ID"},
    {"key": "filename", "label": "Filename", "default_heading": "Filename"},
    {"key": "page_number", "label": "Page number", "default_heading": "Page"},
    {"key": "figure_label", "label": "Figure number", "default_heading": "Figure"},
    {"key": "original_caption", "label": "Original caption", "default_heading": "Original Caption"},
    {"key": "generated_alt_text", "label": "Generated alt text", "default_heading": "Generated Alt Text"},
    {"key": "approved_alt_text", "label": "Approved alt text", "default_heading": "Alternative Text"},
    {"key": "review_status", "label": "Review status", "default_heading": "Review Status"},
]

ALLOWED_FIELD_KEYS = {f["key"] for f in FIELD_CATALOG}
DEFAULT_SECTION_LABELS = {f["key"]: f["default_heading"] for f in FIELD_CATALOG if f["default_heading"]}

ALLOWED_FONT_FAMILIES = {"Calibri", "Arial", "Georgia", "Times New Roman", "Verdana"}
ALLOWED_FONT_SIZES = {"small", "medium", "large"}
ALLOWED_HEADING_STYLES = {"plain", "bold", "accent", "uppercase"}
ALLOWED_ALIGNMENTS = {"left", "center", "justify"}
ALLOWED_ENTRY_SPACINGS = {"compact", "normal", "spacious"}
ALLOWED_ENTRY_SEPARATORS = {"none", "line", "page_break"}
ALLOWED_IMAGE_SIZES = {"small", "medium", "large"}
ALLOWED_PHRASE_PLACEMENTS = {"header", "before_alt_text", "after_alt_text", "closing"}


def get_default_design_config(
    project_name: str = "Accessibility Review",
    preset: str = "minimal",
) -> Dict[str, Any]:
    """Build a normalized default design configuration for a given preset."""
    preset_key = preset if preset in STYLE_PRESETS else "minimal"
    base = deepcopy(STYLE_PRESETS[preset_key]["defaults"])
    return {
        "style_preset": base["style_preset"],
        "fields": base["fields"],
        "document_title": (project_name or "Accessibility Review").strip()[:200],
        "introduction": "",
        "section_labels": deepcopy(DEFAULT_SECTION_LABELS),
        "custom_phrases": [],
        "footer_text": "",
        "closing_text": "",
        "font_family": base["font_family"],
        "font_size": base["font_size"],
        "heading_style": base["heading_style"],
        "alignment": base["alignment"],
        "entry_spacing": base["entry_spacing"],
        "entry_separator": base["entry_separator"],
        "image_size": base["image_size"],
    }


def validate_design_config(
    raw: Optional[Dict[str, Any]],
    project_name: str = "Accessibility Review",
) -> Dict[str, Any]:
    """
    Strictly validate and normalize an incoming document design configuration.
    Raises ValueError with an actionable message if any field is invalid.
    """
    if raw is None:
        return get_default_design_config(project_name=project_name)
    if not isinstance(raw, dict):
        raise ValueError("Design configuration must be a JSON object.")

    preset = raw.get("style_preset", "minimal")
    if preset not in STYLE_PRESETS:
        raise ValueError(
            f"Unsupported style_preset '{preset}'. Allowed: {sorted(STYLE_PRESETS.keys())}."
        )

    defaults = get_default_design_config(project_name=project_name, preset=preset)

    # Validate enums
    def _enum(key: str, allowed: set[str]) -> str:
        val = raw.get(key, defaults[key])
        if not isinstance(val, str) or val not in allowed:
            raise ValueError(f"Invalid {key} '{val}'. Allowed: {sorted(allowed)}.")
        return val

    font_family = _enum("font_family", ALLOWED_FONT_FAMILIES)
    font_size = _enum("font_size", ALLOWED_FONT_SIZES)
    heading_style = _enum("heading_style", ALLOWED_HEADING_STYLES)
    alignment = _enum("alignment", ALLOWED_ALIGNMENTS)
    entry_spacing = _enum("entry_spacing", ALLOWED_ENTRY_SPACINGS)
    entry_separator = _enum("entry_separator", ALLOWED_ENTRY_SEPARATORS)
    image_size = _enum("image_size", ALLOWED_IMAGE_SIZES)

    # Validate text fields
    def _text(key: str, max_len: int, default: str = "") -> str:
        val = raw.get(key, default)
        if val is None:
            return ""
        if not isinstance(val, str):
            raise ValueError(f"Field '{key}' must be a string.")
        cleaned = val.strip()
        if len(cleaned) > max_len:
            raise ValueError(f"Field '{key}' exceeds maximum length of {max_len} characters.")
        return cleaned

    document_title = _text("document_title", 200, defaults["document_title"])
    introduction = _text("introduction", 2000, "")
    footer_text = _text("footer_text", 200, "")
    closing_text = _text("closing_text", 2000, "")

    # Validate fields ordering and enabled flags
    raw_fields = raw.get("fields", defaults["fields"])
    if not isinstance(raw_fields, list) or not raw_fields:
        raise ValueError("Field 'fields' must be a non-empty list.")
    seen_keys = set()
    normalized_fields: List[Dict[str, Any]] = []
    for entry in raw_fields:
        if not isinstance(entry, dict):
            raise ValueError("Each item in 'fields' must be an object with 'key' and 'enabled'.")
        fkey = entry.get("key")
        if fkey not in ALLOWED_FIELD_KEYS:
            raise ValueError(f"Unknown field key '{fkey}'.")
        if fkey in seen_keys:
            continue
        seen_keys.add(fkey)
        normalized_fields.append({
            "key": fkey,
            "enabled": bool(entry.get("enabled", False)),
        })
    # Ensure any catalog keys omitted by older clients are appended as disabled
    for fdef in FIELD_CATALOG:
        if fdef["key"] not in seen_keys:
            normalized_fields.append({"key": fdef["key"], "enabled": False})

    if not any(f["enabled"] for f in normalized_fields):
        raise ValueError("At least one information field must be enabled.")

    # Validate section_labels
    raw_labels = raw.get("section_labels", defaults["section_labels"])
    if raw_labels is None:
        raw_labels = {}
    if not isinstance(raw_labels, dict):
        raise ValueError("Field 'section_labels' must be an object.")
    section_labels = deepcopy(DEFAULT_SECTION_LABELS)
    for k, v in raw_labels.items():
        if k not in ALLOWED_FIELD_KEYS:
            raise ValueError(f"Unknown section_labels key '{k}'.")
        if v is None:
            section_labels[k] = ""
        elif not isinstance(v, str):
            raise ValueError(f"Section label for '{k}' must be a string.")
        else:
            lbl = v.strip()
            if len(lbl) > 100:
                raise ValueError(f"Section label for '{k}' exceeds 100 characters.")
            section_labels[k] = lbl

    # Validate custom_phrases
    raw_phrases = raw.get("custom_phrases", [])
    if raw_phrases is None:
        raw_phrases = []
    if not isinstance(raw_phrases, list):
        raise ValueError("Field 'custom_phrases' must be a list.")
    if len(raw_phrases) > 10:
        raise ValueError("Maximum of 10 custom phrases allowed.")
    custom_phrases: List[Dict[str, str]] = []
    for item in raw_phrases:
        if not isinstance(item, dict):
            raise ValueError("Each custom phrase must be an object with 'text' and 'placement'.")
        txt = (item.get("text") or "").strip()
        placement = item.get("placement", "header")
        if placement not in ALLOWED_PHRASE_PLACEMENTS:
            raise ValueError(
                f"Invalid phrase placement '{placement}'. Allowed: {sorted(ALLOWED_PHRASE_PLACEMENTS)}."
            )
        if len(txt) > 300:
            raise ValueError("Custom phrase text exceeds 300 characters.")
        if txt:
            custom_phrases.append({"text": txt, "placement": placement})

    return {
        "style_preset": preset,
        "fields": normalized_fields,
        "document_title": document_title,
        "introduction": introduction,
        "section_labels": section_labels,
        "custom_phrases": custom_phrases,
        "footer_text": footer_text,
        "closing_text": closing_text,
        "font_family": font_family,
        "font_size": font_size,
        "heading_style": heading_style,
        "alignment": alignment,
        "entry_spacing": entry_spacing,
        "entry_separator": entry_separator,
        "image_size": image_size,
    }
