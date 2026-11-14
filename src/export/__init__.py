"""
Export package for AltTextPipeline.
"""

from .docx_builder import DocxBuilder, ExportValidationError
from .service import (
    check_export_eligibility,
    export_project_docx,
    get_project_design_context,
    resolve_effective_doc_design,
)
from .design_config import (
    FIELD_CATALOG,
    STYLE_PRESETS,
    get_default_design_config,
    validate_design_config,
)

__all__ = [
    "DocxBuilder",
    "ExportValidationError",
    "FIELD_CATALOG",
    "STYLE_PRESETS",
    "check_export_eligibility",
    "export_project_docx",
    "get_default_design_config",
    "get_project_design_context",
    "resolve_effective_doc_design",
    "validate_design_config",
]
