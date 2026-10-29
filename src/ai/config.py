"""
AI Provider and Generation Configuration
----------------------------------------
Loads, validates, and manages environment configuration for the AI generation layer.
Enforces secret redaction and separates model identifiers from provider routing.
"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field


VALID_LANGUAGES = {"en-GB", "en-US"}
VALID_ROUTING_POLICIES = {"provider", "direct", "auto"}


def load_dotenv_values(env_file_path: Optional[Path] = None) -> Dict[str, str]:
    """Loads key-value pairs from .env file if present."""
    if env_file_path is None:
        root_dir = Path(__file__).parent.parent.parent
        env_file = root_dir / ".env"
    else:
        env_file = env_file_path

    values = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip("'\"")
    return values


@dataclass
class AIConfig:
    hf_token: Optional[str] = field(default=None, repr=False)
    hf_model: str = "zai-org/GLM-5.3-Flash"
    hf_provider: str = "novita"
    hf_routing_policy: str = "provider"
    language: str = "en-GB"
    request_timeout_seconds: int = 90
    max_retries: int = 2
    max_concurrent_requests: int = 2
    max_output_tokens: int = 500
    max_monthly_spend_usd: float = 2.00
    enable_live_tests: bool = False
    max_image_dimension: int = 1536
    max_payload_bytes: int = 3_500_000
    jpeg_quality: int = 85

    @classmethod
    def from_env(cls, read_dotenv: bool = True, env_file_path: Optional[Path] = None) -> "AIConfig":
        file_env = load_dotenv_values(env_file_path) if read_dotenv else {}

        def get_val(key: str, default: str) -> str:
            return os.environ.get(key) or file_env.get(key) or default

        token = os.environ.get("HF_TOKEN") or file_env.get("HF_TOKEN") or None

        raw_model = get_val("HF_MODEL", "zai-org/GLM-5.3-Flash")
        explicit_provider = get_val("HF_PROVIDER", "novita")

        # Decouple model name and provider if formatted as model:provider
        if ":" in raw_model:
            model_name, parsed_provider = raw_model.split(":", 1)
            model_name = model_name.strip()
            provider_name = parsed_provider.strip() or explicit_provider
        else:
            model_name = raw_model.strip()
            provider_name = explicit_provider.strip()

        routing_policy = get_val("HF_ROUTING_POLICY", "provider").lower()
        if routing_policy not in VALID_ROUTING_POLICIES:
            routing_policy = "provider"

        lang = get_val("ALT_TEXT_LANGUAGE", "en-GB")
        if lang not in VALID_LANGUAGES:
            lang = "en-GB"

        timeout = int(get_val("AI_REQUEST_TIMEOUT_SECONDS", "90"))
        retries = int(get_val("AI_MAX_RETRIES", "2"))
        concurrency = int(get_val("AI_MAX_CONCURRENT_REQUESTS", "2"))
        max_tokens = int(get_val("AI_MAX_OUTPUT_TOKENS", "500"))
        spend_limit = float(get_val("AI_MAX_MONTHLY_SPEND_USD", "2.00"))
        live_tests = get_val("AI_ENABLE_LIVE_TESTS", "false").lower() in {"true", "1", "yes"}

        max_dim = int(get_val("AI_MAX_IMAGE_DIMENSION", "1536"))
        max_payload = int(get_val("AI_MAX_PAYLOAD_BYTES", "3500000"))
        quality = int(get_val("AI_JPEG_QUALITY", "85"))

        return cls(
            hf_token=token,
            hf_model=model_name,
            hf_provider=provider_name,
            hf_routing_policy=routing_policy,
            language=lang,
            request_timeout_seconds=timeout,
            max_retries=retries,
            max_concurrent_requests=concurrency,
            max_output_tokens=max_tokens,
            max_monthly_spend_usd=spend_limit,
            enable_live_tests=live_tests,
            max_image_dimension=max_dim,
            max_payload_bytes=max_payload,
            jpeg_quality=quality,
        )

    def is_configured(self) -> bool:
        """Returns True if a valid Hugging Face token is present."""
        return bool(self.hf_token and len(self.hf_token.strip()) > 5)

    def to_safe_dict(self) -> Dict[str, Any]:
        """Returns safe representation for UI and logs without revealing credentials."""
        token_status = "configured" if self.is_configured() else "missing"
        return {
            "token_status": token_status,
            "model": self.hf_model,
            "provider": self.hf_provider,
            "routing_policy": self.hf_routing_policy,
            "language": self.language,
            "request_timeout_seconds": self.request_timeout_seconds,
            "max_retries": self.max_retries,
            "max_concurrent_requests": self.max_concurrent_requests,
            "max_output_tokens": self.max_output_tokens,
            "max_monthly_spend_usd": self.max_monthly_spend_usd,
            "live_tests_enabled": self.enable_live_tests,
            "max_image_dimension": self.max_image_dimension,
            "max_payload_bytes": self.max_payload_bytes,
            "jpeg_quality": self.jpeg_quality,
        }
