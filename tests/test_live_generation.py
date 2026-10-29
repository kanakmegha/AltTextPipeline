"""
Optional Opt-In Live Integration Test
-------------------------------------
Tests live inference against the configured Hugging Face Inference Provider
(zai-org/GLM-5.3-Flash via Novita).

SAFETY CONTROLS:
- Defaulted to DISABLED / SKIPPED unless AI_ENABLE_LIVE_TESTS=true and valid HF_TOKEN is present.
- Processes exactly ONE non-sensitive, synthetic test image.
- Never logs or displays the API token.
- Never falls back silently to another model or provider.
- Records elapsed duration, token usage, and estimated spend.
"""

import os
import io
import pytest
from PIL import Image

from src.ai.config import AIConfig
from src.ai.models import GenerationRequest
from src.ai.providers import HuggingFaceInferenceProvider


def make_test_diagram_bytes() -> bytes:
    """Creates a small 200x200 synthetic test image of geometric shapes."""
    img = Image.new("RGB", (200, 200), color=(240, 240, 245))
    # Draw simple contrasting shapes in pillow
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 40, 90, 160], fill=(79, 70, 229), outline=(30, 27, 75))
    draw.ellipse([110, 50, 170, 110], fill=(16, 185, 129), outline=(6, 78, 59))
    draw.text((35, 170), "Fig 1. Sample", fill=(15, 23, 42))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.live
def test_opt_in_live_inference():
    """
    Opt-in live inference test. Skipped by default.
    Enable explicitly by setting AI_ENABLE_LIVE_TESTS=true and HF_TOKEN in environment.
    """
    cfg = AIConfig.from_env()

    if not cfg.enable_live_tests:
        pytest.skip("Live AI inference test disabled by default. Set AI_ENABLE_LIVE_TESTS=true to enable.")

    if not cfg.is_configured():
        pytest.skip("Live AI inference test requires a valid HF_TOKEN.")

    print(f"\n[LIVE TEST] Initiating single inference request:")
    print(f"[LIVE TEST] Target Model:    {cfg.hf_model}")
    print(f"[LIVE TEST] Target Provider: {cfg.hf_provider}")
    print(f"[LIVE TEST] Language:        {cfg.language}")
    print(f"[LIVE TEST] Token status:    {cfg.to_safe_dict()['token_status']}")

    provider = HuggingFaceInferenceProvider(cfg)

    image_bytes = make_test_diagram_bytes()
    req = GenerationRequest(
        image_id="live_test_img_01",
        image_bytes=image_bytes,
        image_format="png",
        caption="Figure 1: Comparison of geometric shapes",
        surrounding_context="The diagram illustrates a vertical blue rectangle and a green circle.",
        user_prompt="Describe this diagram concisely for accessibility.",
        language=cfg.language,
        model=cfg.hf_model,
        provider=cfg.hf_provider,
        max_output_tokens=500,
    )

    result = provider.generate(req)

    print(f"[LIVE TEST] Response Status:   {result.status}")
    print(f"[LIVE TEST] Duration:          {result.duration_seconds:.2f}s")
    if result.token_usage:
        print(f"[LIVE TEST] Input Tokens:      {result.token_usage.input_tokens}")
        print(f"[LIVE TEST] Output Tokens:     {result.token_usage.output_tokens}")
        print(f"[LIVE TEST] Total Tokens:      {result.token_usage.total_tokens}")
    print(f"[LIVE TEST] Estimated Cost:    ${result.estimated_cost_usd:.6f}")
    print(f"[LIVE TEST] Draft Alt-Text:    {result.draft_text}\n")

    assert result.status == "success", f"Live generation failed: {result.error_message}"
    assert len(result.draft_text.strip()) > 10
    assert result.provider == cfg.hf_provider
    assert result.model == cfg.hf_model
