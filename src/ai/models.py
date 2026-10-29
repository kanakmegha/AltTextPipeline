"""
AI Generation Data Models
-------------------------
Structured request, response, token usage, and status definitions
for provider-independent alt-text generation.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import hashlib


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class GenerationRequest:
    image_id: str
    image_bytes: bytes
    image_format: str
    caption: str = ""
    surrounding_context: str = ""
    user_prompt: str = ""
    language: str = "en-GB"
    model: str = "zai-org/GLM-5.3-Flash"
    provider: str = "novita"
    figure_metadata: Dict[str, Any] = field(default_factory=dict)
    project_id: Optional[str] = None
    prior_draft: Optional[str] = None
    revision_instruction: Optional[str] = None
    max_output_tokens: int = 500
    temperature: float = 0.2

    @property
    def prompt_hash(self) -> str:
        """Returns SHA-256 hash of the user prompt for auditing and versioning."""
        return hashlib.sha256(self.user_prompt.encode("utf-8")).hexdigest()[:16]


@dataclass
class GenerationResult:
    draft_text: str
    provider: str
    model: str
    language: str
    status: str  # "success" or "failed"
    error_message: Optional[str] = None
    token_usage: Optional[TokenUsage] = None
    estimated_cost_usd: float = 0.0
    duration_seconds: float = 0.0
    provider_request_id: Optional[str] = None
    assembled_prompt: str = ""
    prompt_hash: str = ""
    word_count_validation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "draft_text": self.draft_text,
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "status": self.status,
            "error_message": self.error_message,
            "token_usage": self.token_usage.to_dict() if self.token_usage else None,
            "estimated_cost_usd": self.estimated_cost_usd,
            "duration_seconds": self.duration_seconds,
            "provider_request_id": self.provider_request_id,
            "prompt_hash": self.prompt_hash,
            "word_count_validation": self.word_count_validation,
        }
