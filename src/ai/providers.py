"""
AI Provider Layer
-----------------
Defines the provider abstraction and implementations for Hugging Face Inference Providers
(with explicit Novita routing) and an offline Mock provider.
"""

import abc
import base64
import json
import logging
import time
import re
from typing import Optional, Dict, Any
import httpx

logger = logging.getLogger(__name__)

from .models import GenerationRequest, GenerationResult, TokenUsage
from .prompts import PromptAssembler
from .cleaner import clean_alt_text_response
from .config import AIConfig


# Model pricing registry (per 1,000,000 tokens)
# Novita pricing for zai-org/GLM-5.3-Flash: Input $0.15/1M, Output $0.50/1M
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "zai-org/GLM-5.3-Flash": {
        "input_per_million": 0.15,
        "output_per_million": 0.50,
    }
}


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Calculates estimated cost in USD based on verified provider pricing."""
    pricing = MODEL_PRICING.get(model, {"input_per_million": 0.15, "output_per_million": 0.50})
    input_cost = (input_tokens / 1_000_000.0) * pricing["input_per_million"]
    output_cost = (output_tokens / 1_000_000.0) * pricing["output_per_million"]
    return round(input_cost + output_cost, 7)


def redact_secrets(text: str, token: Optional[str]) -> str:
    """Ensures API tokens are never present in error messages or logs."""
    if not text:
        return ""
    if token and len(token) > 4:
        text = text.replace(token, "[REDACTED_HF_TOKEN]")
    # Redact any generic bearer tokens or hf_ pattern
    text = re.sub(r'hf_[a-zA-Z0-9]{20,}', '[REDACTED_HF_TOKEN]', text)
    text = re.sub(r'Bearer\s+[a-zA-Z0-9_\-\.]+', 'Bearer [REDACTED_TOKEN]', text)
    return text


class AIProvider(abc.ABC):
    """Abstract interface for alt-text vision generation providers."""

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def model_name(self) -> str:
        pass

    @abc.abstractmethod
    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generates alt text for the requested image and context."""
        pass


class HuggingFaceInferenceProvider(AIProvider):
    """
    Adapter for Hugging Face Inference Providers routing explicitly to Novita (or configured provider).
    Uses the OpenAI-compatible chat completions endpoint at https://router.huggingface.co/v1/chat/completions.
    """

    HF_API_URL = "https://router.huggingface.co/v1/chat/completions"

    def __init__(self, config: AIConfig, prompt_assembler: Optional[PromptAssembler] = None):
        self.config = config
        self.assembler = prompt_assembler or PromptAssembler()

    @property
    def provider_name(self) -> str:
        return self.config.hf_provider

    @property
    def model_name(self) -> str:
        return self.config.hf_model

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not self.config.is_configured():
            return GenerationResult(
                draft_text="",
                provider=self.provider_name,
                model=self.model_name,
                language=request.language,
                status="failed",
                error_message="Hugging Face token (HF_TOKEN) is not configured or is invalid.",
                assembled_prompt="",
                prompt_hash=request.prompt_hash,
            )

        # 1. Assemble prompts
        system_prompt, user_content_text, prompt_hash = self.assembler.assemble(
            user_prompt=request.user_prompt,
            language=request.language,
            caption=request.caption,
            surrounding_context=request.surrounding_context,
            figure_metadata=request.figure_metadata,
            revision_instruction=request.revision_instruction,
            prior_draft=request.prior_draft,
        )

        # 2. Encode image as base64 data URI
        fmt = request.image_format.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        b64_image = base64.b64encode(request.image_bytes).decode("utf-8")
        data_uri = f"data:image/{fmt};base64,{b64_image}"

        # 3. Build payload according to verified Hugging Face Inference Providers format
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_content_text},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
            "max_tokens": request.max_output_tokens or self.config.max_output_tokens,
            "temperature": request.temperature,
        }

        # 4. Serialize and enforce pre-flight payload limit
        raw_body = json.dumps(payload).encode("utf-8")
        payload_size = len(raw_body)

        if payload_size > self.config.max_payload_bytes:
            logger.warning(
                "Pre-flight payload limit exceeded: project_id=%s, image_id=%s, serialized_body_bytes=%d "
                "> max_payload_bytes=%d, model=%s, provider=%s. Aborted before transmission.",
                request.project_id,
                request.image_id,
                payload_size,
                self.config.max_payload_bytes,
                self.model_name,
                self.provider_name,
            )
            return GenerationResult(
                draft_text="",
                provider=self.provider_name,
                model=self.model_name,
                language=request.language,
                status="failed",
                error_message=(
                    f"Serialized multimodal request size ({payload_size} bytes) exceeds configured "
                    f"maximum payload limit of {self.config.max_payload_bytes} bytes. Aborted prior to transmission to prevent HTTP 413."
                ),
                duration_seconds=0.0,
                assembled_prompt=user_content_text,
                prompt_hash=prompt_hash,
            )

        # 5. Prepare headers with explicit provider routing
        headers = {
            "Authorization": f"Bearer {self.config.hf_token}",
            "Content-Type": "application/json",
        }
        if self.config.hf_provider:
            headers["X-HF-Provider"] = self.config.hf_provider

        orig_bytes = request.figure_metadata.get("original_bytes_length", len(request.image_bytes))
        orig_dims = request.figure_metadata.get("original_dimensions", [])
        opt_bytes = request.figure_metadata.get("inference_bytes_length", len(request.image_bytes))
        opt_dims = request.figure_metadata.get("inference_dimensions", [])

        logger.info(
            "Sending AI inference request: project_id=%s, image_id=%s, orig_bytes=%s, "
            "orig_dims=%s, opt_bytes=%s, opt_dims=%s, opt_format=%s, serialized_bytes=%d, "
            "payload_ceiling=%d, model=%s, provider=%s",
            request.project_id,
            request.image_id,
            orig_bytes,
            orig_dims,
            opt_bytes,
            opt_dims,
            request.image_format,
            payload_size,
            self.config.max_payload_bytes,
            self.model_name,
            self.provider_name,
        )

        # 6. Execute with bounded retries on transient errors
        start_time = time.time()
        attempts = 0
        max_retries = max(0, self.config.max_retries)
        last_error = ""

        while attempts <= max_retries:
            attempts += 1
            try:
                with httpx.Client(timeout=self.config.request_timeout_seconds) as client:
                    resp = client.post(self.HF_API_URL, headers=headers, content=raw_body)

                duration = time.time() - start_time
                sanitized_summary = (
                    "OK" if resp.status_code == 200 else redact_secrets(resp.text[:200], self.config.hf_token)
                )
                logger.info(
                    "Received AI inference response: project_id=%s, image_id=%s, http_status=%d, "
                    "duration=%.2fs, error_summary=%s",
                    request.project_id,
                    request.image_id,
                    resp.status_code,
                    duration,
                    sanitized_summary,
                )

                # Check status code
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        return GenerationResult(
                            draft_text="",
                            provider=self.provider_name,
                            model=self.model_name,
                            language=request.language,
                            status="failed",
                            error_message="API returned empty choices list.",
                            duration_seconds=duration,
                            assembled_prompt=user_content_text,
                            prompt_hash=prompt_hash,
                        )

                    message = choices[0].get("message", {})
                    content = message.get("content") or ""
                    # If reasoning model emitted reasoning_content but empty content
                    if not content and message.get("reasoning_content"):
                        content = message["reasoning_content"].strip()

                    # Extract usage
                    usage_data = data.get("usage", {})
                    usage = TokenUsage(
                        input_tokens=usage_data.get("prompt_tokens", 0),
                        output_tokens=usage_data.get("completion_tokens", 0),
                        total_tokens=usage_data.get("total_tokens", 0),
                    )

                    cost = calculate_cost(self.model_name, usage.input_tokens, usage.output_tokens)
                    request_id = resp.headers.get("x-request-id") or data.get("id")

                    # Clean and validate response to ensure no prompt echoes or scratchpads
                    cleaned_draft, is_valid, err_msg = clean_alt_text_response(content)
                    if not is_valid:
                        logger.warning(
                            "AI output validation failed for project=%s, image=%s: %s",
                            request.project_id,
                            request.image_id,
                            err_msg,
                        )
                        return GenerationResult(
                            draft_text="",
                            provider=self.provider_name,
                            model=self.model_name,
                            language=request.language,
                            status="failed",
                            error_message=f"Alt-text response validation failed: {err_msg}",
                            duration_seconds=duration,
                            assembled_prompt=user_content_text,
                            prompt_hash=prompt_hash,
                        )

                    return GenerationResult(
                        draft_text=cleaned_draft,
                        provider=self.provider_name,
                        model=self.model_name,
                        language=request.language,
                        status="success",
                        token_usage=usage,
                        estimated_cost_usd=cost,
                        duration_seconds=duration,
                        provider_request_id=request_id,
                        assembled_prompt=user_content_text,
                        prompt_hash=prompt_hash,
                    )

                # Permanent failures (no retry): 400, 401, 403, 404, 413, 422
                if resp.status_code in {400, 401, 403, 404, 413, 422}:
                    if resp.status_code == 413:
                        err_msg = (
                            f"HTTP 413 Request Entity Too Large: The serialized multimodal request ({payload_size} bytes) "
                            f"exceeded the provider gateway size limit. Please ensure the image is preprocessed or compressed."
                        )
                    else:
                        err_msg = f"HTTP {resp.status_code}: {resp.text}"

                    return GenerationResult(
                        draft_text="",
                        provider=self.provider_name,
                        model=self.model_name,
                        language=request.language,
                        status="failed",
                        error_message=redact_secrets(err_msg, self.config.hf_token),
                        duration_seconds=time.time() - start_time,
                        assembled_prompt=user_content_text,
                        prompt_hash=prompt_hash,
                    )

                # Transient failures (429, 500, 502, 503, 504) -> retry if attempts remain
                last_error = f"HTTP {resp.status_code}: {resp.text}"
                if attempts <= max_retries:
                    time.sleep(1.0 * attempts)

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = f"Network/Timeout error: {str(exc)}"
                if attempts <= max_retries:
                    time.sleep(1.0 * attempts)
            except Exception as exc:
                last_error = f"Unexpected generation error: {str(exc)}"
                break

        return GenerationResult(
            draft_text="",
            provider=self.provider_name,
            model=self.model_name,
            language=request.language,
            status="failed",
            error_message=redact_secrets(last_error, self.config.hf_token),
            duration_seconds=time.time() - start_time,
            assembled_prompt=user_content_text,
            prompt_hash=prompt_hash,
        )


class MockAIProvider(AIProvider):
    """
    Deterministic offline mock provider for automated tests and development.
    Requires no internet connection or API credentials.
    """

    def __init__(
        self,
        provider_name: str = "mock-novita",
        model_name: str = "zai-org/GLM-5.3-Flash",
        simulate_error: Optional[str] = None,
        simulated_delay: float = 0.0,
        fixed_text: Optional[str] = None,
    ):
        self._provider = provider_name
        self._model = model_name
        self.simulate_error = simulate_error
        self.simulated_delay = simulated_delay
        self.fixed_text = fixed_text
        self.call_count = 0
        self.last_request: Optional[GenerationRequest] = None

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.call_count += 1
        self.last_request = request

        if self.simulated_delay > 0:
            time.sleep(self.simulated_delay)

        if self.simulate_error:
            return GenerationResult(
                draft_text="",
                provider=self.provider_name,
                model=self.model_name,
                language=request.language,
                status="failed",
                error_message=self.simulate_error,
                duration_seconds=self.simulated_delay,
                assembled_prompt="Mock assembled prompt",
                prompt_hash=request.prompt_hash,
            )

        if self.fixed_text:
            text = self.fixed_text
        else:
            caption_clause = f" based on '{request.caption}'" if request.caption else ""
            lang_clause = "in British English" if request.language == "en-GB" else "in American English"
            if request.revision_instruction:
                text = (
                    f"A detailed line graph illustrating revised trends{caption_clause}. "
                    f"The horizontal axis displays chronological increments and the vertical axis indicates percentages. "
                    f"Drafted {lang_clause} with updated visual data and precise itemized details."
                )
            else:
                text = (
                    f"A detailed line graph illustrating numerical trends{caption_clause}. "
                    f"The horizontal axis displays chronological increments and the vertical axis indicates percentages. "
                    f"Drafted {lang_clause} without unsubstantiated claims."
                )

        tokens = TokenUsage(input_tokens=180, output_tokens=52, total_tokens=232)
        cost = calculate_cost(self.model_name, tokens.input_tokens, tokens.output_tokens)

        return GenerationResult(
            draft_text=text,
            provider=self.provider_name,
            model=self.model_name,
            language=request.language,
            status="success",
            token_usage=tokens,
            estimated_cost_usd=cost,
            duration_seconds=max(0.01, self.simulated_delay),
            provider_request_id=f"mock-req-{self.call_count}",
            assembled_prompt="Mock assembled prompt",
            prompt_hash=request.prompt_hash,
        )
