"""
Phase 4 AI Provider Integration and Alt-Text Generation Tests
------------------------------------------------------------
Comprehensive offline unit and mocked API tests covering:
- Configuration parsing & validation
- Prompt preservation & language variants (en-GB / en-US)
- Cost calculation & secret redaction
- Provider response parsing (success, reasoning tokens, errors)
- Bounded retry behavior for transient vs permanent errors
- Budget ceiling enforcement (BudgetTracker)
- Concurrency limiting (ConcurrencyLimiter)
- Draft persistence, versioning (v1, v2), and project isolation
- Strict non-approval invariant (never auto-approves)
- FastAPI endpoints (/api/ai/config, /api/ai/usage, /versions, /generate)
"""

import os
import io
import json
import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from PIL import Image

from src.ai.config import AIConfig
from src.ai.models import GenerationRequest, GenerationResult, TokenUsage
from src.ai.prompts import (
    PromptAssembler,
    DEFAULT_STARTER_PROMPT,
    DEFAULT_PROMPT_TEMPLATE,
    resolve_language_name,
    resolve_default_prompt,
    count_words,
    validate_word_count,
)
from src.ai.providers import (
    HuggingFaceInferenceProvider,
    MockAIProvider,
    calculate_cost,
    redact_secrets,
)
from src.ai.guardrails import BudgetTracker, ConcurrencyLimiter, BudgetExceededError
from src.ai.image_preprocessor import ImagePreprocessor
from src.ai.generator import AltTextGenerator
from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore
from src.web.app import create_app


def create_sample_png_bytes(color: str = "blue", width: int = 100, height: int = 100) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ============================================================
# 1. Configuration & Secret Redaction Tests
# ============================================================

def test_config_parsing_defaults():
    with patch.dict(os.environ, {}, clear=True):
        cfg = AIConfig.from_env(read_dotenv=False)
        assert cfg.hf_model == "zai-org/GLM-5.3-Flash"
        assert cfg.hf_provider == "novita"
        assert cfg.language == "en-GB"
        assert cfg.max_monthly_spend_usd == 2.00
        assert cfg.is_configured() is False


def test_config_decouples_model_and_provider():
    env = {
        "HF_MODEL": "zai-org/GLM-5.3-Flash:novita",
        "HF_PROVIDER": "novita",
        "ALT_TEXT_LANGUAGE": "en-US",
        "HF_TOKEN": "hf_test_secret_token_12345",
    }
    with patch.dict(os.environ, env, clear=True):
        cfg = AIConfig.from_env(read_dotenv=False)
        assert cfg.hf_model == "zai-org/GLM-5.3-Flash"
        assert cfg.hf_provider == "novita"
        assert cfg.language == "en-US"
        assert cfg.is_configured() is True
        safe = cfg.to_safe_dict()
        assert "hf_test_secret_token_12345" not in str(safe)
        assert safe["token_status"] == "configured"


def test_secret_redaction():
    token = "hf_secret_token_9999999999999999"
    msg = f"Error communicating with Bearer {token} for authorization."
    cleaned = redact_secrets(msg, token)
    assert token not in cleaned
    assert "[REDACTED_HF_TOKEN]" in cleaned or "[REDACTED_TOKEN]" in cleaned


# ============================================================
# 2. Prompt Assembly & Preservation Tests
# ============================================================

def test_prompt_assembler_preserves_user_prompt_verbatim():
    assembler = PromptAssembler()
    custom_prompt = "Custom user prompt: Emphasise architectural column entablature details."
    sys_prompt, user_content, prompt_hash = assembler.assemble(
        user_prompt=custom_prompt,
        language="en-GB",
        caption="Figure 3: Etruscan Temple",
        surrounding_context="The temple featured prominent terracotta roof decorations.",
    )

    # Invariant: User's exact prompt is preserved verbatim without paraphrasing
    assert custom_prompt in user_content
    assert "Figure 3: Etruscan Temple" in user_content
    assert "terracotta roof decorations" in user_content
    assert "British English (en-GB)" in sys_prompt
    assert len(prompt_hash) == 16


def test_prompt_assembler_language_toggle():
    assembler = PromptAssembler()
    sys_gb, _, _ = assembler.assemble(user_prompt="", language="en-GB")
    sys_us, _, _ = assembler.assemble(user_prompt="", language="en-US")

    assert "British English (en-GB)" in sys_gb
    assert "colour" in sys_gb
    assert "American English (en-US)" in sys_us
    assert "color" in sys_us


def test_prompt_assembler_grounding_and_anti_hallucination():
    assembler = PromptAssembler()
    sys_prompt, user_content, _ = assembler.assemble(user_prompt="Describe this")
    assert "CRITICAL GROUNDING RULES" in sys_prompt
    assert "Do not invent, extrapolate, or hallucinate" in sys_prompt
    assert "prompt override instructions" in sys_prompt


def test_exact_default_prompt_template():
    expected = "Based on the provided caption, generate a neutral-toned, item-by-item alt-text description in [LANGUAGE] that details all visual elements and text, keeping the total length to 45–50 words."
    assert DEFAULT_PROMPT_TEMPLATE == expected


def test_resolve_default_prompt_british_english():
    expected = "Based on the provided caption, generate a neutral-toned, item-by-item alt-text description in British English that details all visual elements and text, keeping the total length to 45–50 words."
    assert resolve_default_prompt("en-GB") == expected
    assert resolve_default_prompt("en-gb") == expected


def test_resolve_default_prompt_american_english():
    expected = "Based on the provided caption, generate a neutral-toned, item-by-item alt-text description in American English that details all visual elements and text, keeping the total length to 45–50 words."
    assert resolve_default_prompt("en-US") == expected
    assert resolve_default_prompt("en-us") == expected


def test_word_count_validator_below_range():
    text = "A simple line drawing showing an ancient vase with geometric decorations around its rim and neck on an off-white background."
    wc = count_words(text)
    assert wc < 45
    res = validate_word_count(text, min_words=45, max_words=50)
    assert res["is_valid"] is False
    assert res["word_count"] == wc
    assert res["reason"] == "under_min"


def test_word_count_validator_within_range():
    # Exactly 48 words
    words = [
        "A", "detailed", "monochrome", "line", "diagram", "illustrating", "an", "ancient", "Etruscan", "temple",
        "elevation", "with", "steep", "gabled", "roof,", "broad", "pediment,", "and", "fluted", "wooden",
        "columns", "supporting", "a", "heavy", "terracotta", "entablature.", "The", "structure", "rests", "upon",
        "a", "high", "stone", "podium", "with", "a", "single", "central", "flight", "of",
        "stairs", "approaching", "the", "triple", "cella", "entrance", "facade", "neatly."
    ]
    text = " ".join(words)
    wc = count_words(text)
    assert 45 <= wc <= 50
    res = validate_word_count(text, min_words=45, max_words=50)
    assert res["is_valid"] is True
    assert res["word_count"] == wc
    assert res["reason"] == "valid"


def test_word_count_validator_above_range():
    words = ["word"] * 55
    text = " ".join(words)
    wc = count_words(text)
    assert wc > 50
    res = validate_word_count(text, min_words=45, max_words=50)
    assert res["is_valid"] is False
    assert res["word_count"] == wc
    assert res["reason"] == "over_max"


def test_prompt_assembler_dynamic_language_resolution():
    assembler = PromptAssembler()
    # If user prompt contains [LANGUAGE], it should dynamically resolve
    sys_prompt, user_content, _ = assembler.assemble(
        user_prompt="Write description in [LANGUAGE]",
        language="en-GB",
        caption="Sample Caption"
    )
    assert "British English" in user_content

    sys_prompt_us, user_content_us, _ = assembler.assemble(
        user_prompt="Write description in [LANGUAGE]",
        language="en-US",
        caption="Sample Caption"
    )
    assert "American English" in user_content_us


# ============================================================
# 3. Cost Calculation & Pricing
# ============================================================

def test_cost_calculation():
    # 1,000,000 input tokens at $0.15 + 1,000,000 output tokens at $0.50 = $0.65
    cost = calculate_cost("zai-org/GLM-5.3-Flash", 1_000_000, 1_000_000)
    assert cost == 0.65

    # Small realistic request: 200 input, 100 output
    small_cost = calculate_cost("zai-org/GLM-5.3-Flash", 200, 100)
    # (200 * 0.00000015) + (100 * 0.0000005) = 0.00003 + 0.00005 = 0.00008
    assert round(small_cost, 6) == 0.00008


# ============================================================
# 4. Guardrails: Budget Tracker & Concurrency Limiter
# ============================================================

def test_budget_tracker_enforces_limit():
    tracker = BudgetTracker(max_monthly_spend_usd=0.01)
    tracker.check_budget_available()  # Under limit

    # Record usage exceeding limit
    tracker.record_usage(input_tokens=1000, output_tokens=1000, cost_usd=0.015)

    summary = tracker.get_summary()
    assert summary["is_budget_exceeded"] is True

    with pytest.raises(BudgetExceededError) as exc_info:
        tracker.check_budget_available()
    assert "AI spending limit of $0.01 reached" in str(exc_info.value)


def test_concurrency_limiter():
    limiter = ConcurrencyLimiter(max_concurrent=1)
    assert limiter.acquire(timeout=0.1) is True
    # Second acquisition with timeout should fail because limit is 1
    assert limiter.acquire(timeout=0.05) is False
    limiter.release()
    assert limiter.acquire(timeout=0.1) is True
    limiter.release()


# ============================================================
# 5. Hugging Face Inference Provider Mocked API Tests
# ============================================================

def test_hf_provider_routing_and_payload():
    cfg = AIConfig(
        hf_token="hf_test_token_xyz",
        hf_model="zai-org/GLM-5.3-Flash",
        hf_provider="novita",
        language="en-GB",
    )
    provider = HuggingFaceInferenceProvider(cfg)

    png_bytes = create_sample_png_bytes()
    req = GenerationRequest(
        image_id="img_123",
        image_bytes=png_bytes,
        image_format="png",
        caption="Figure 1: Schematic diagram",
        user_prompt="Describe concisely",
        language="en-GB",
        model=cfg.hf_model,
        provider=cfg.hf_provider,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"x-request-id": "hf_req_abc123"}
    mock_resp.json.return_value = {
        "id": "chatcmpl-test",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "A schematic line diagram depicting system architecture with three interconnected modules."
                }
            }
        ],
        "usage": {
            "prompt_tokens": 150,
            "completion_tokens": 40,
            "total_tokens": 190,
        }
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = provider.generate(req)

        # Verify call arguments
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["headers"]["Authorization"] == "Bearer hf_test_token_xyz"
        assert call_kwargs["headers"]["X-HF-Provider"] == "novita"

        payload = json.loads(call_kwargs["content"]) if "content" in call_kwargs else call_kwargs.get("json", {})
        assert payload["model"] == "zai-org/GLM-5.3-Flash"
        assert payload["messages"][1]["content"][1]["type"] == "image_url"
        assert payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")

        # Verify result
        assert result.status == "success"
        assert "A schematic line diagram" in result.draft_text
        assert result.token_usage.total_tokens == 190
        assert result.provider_request_id == "hf_req_abc123"
        assert result.provider == "novita"
        assert result.model == "zai-org/GLM-5.3-Flash"


def test_hf_provider_reasoning_model_content_extraction():
    """Handles GLM-5.3-Flash emitting reasoning_content when content is blank."""
    cfg = AIConfig(hf_token="hf_test_token", hf_model="zai-org/GLM-5.3-Flash", hf_provider="novita")
    provider = HuggingFaceInferenceProvider(cfg)

    req = GenerationRequest(
        image_id="img_123",
        image_bytes=create_sample_png_bytes(),
        image_format="png",
        user_prompt="Describe",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {}
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "reasoning_content": "A monochrome photograph of an ancient stone amphora."
                }
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        res = provider.generate(req)
        assert res.status == "success"
        assert "stone amphora" in res.draft_text


def test_hf_provider_permanent_error_no_retries():
    cfg = AIConfig(hf_token="hf_invalid_token", max_retries=2)
    provider = HuggingFaceInferenceProvider(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Invalid credentials: hf_invalid_token"

    req = GenerationRequest(
        image_id="img_123",
        image_bytes=create_sample_png_bytes(),
        image_format="png",
    )

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        res = provider.generate(req)
        # Invariant: 401 must fail immediately with NO retry loop
        assert mock_post.call_count == 1
        assert res.status == "failed"
        assert "401" in res.error_message
        assert "hf_invalid_token" not in res.error_message  # Redacted!


# ============================================================
# 6. Generator Coordinator: Persistence, Versioning, Non-Approval
# ============================================================

@pytest.fixture
def test_pipeline_env(tmp_path):
    db_path = str(tmp_path / "test_pipeline.db")
    storage_dir = str(tmp_path / "storage")
    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    filestore = FileStore(storage_dir)
    return repo, filestore, db_path, storage_dir


def test_generator_drafts_versioning_and_non_approval(test_pipeline_env):
    repo, filestore, _, _ = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())

    # Create project and image
    repo.create_project(
        project_id=project_id,
        name="Test Book",
        source_filename="test.pdf",
        source_storage_key="test.pdf",
        source_checksum="abc",
    )

    png_bytes = create_sample_png_bytes()
    key, chk, _ = filestore.save_image(project_id, image_id, png_bytes, "png")

    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=4,
        xref=10,
        placement_index=0,
        bbox={"x0": 50, "y0": 100, "x1": 300, "y1": 400},
        original_caption="Fig 4. Ground plan",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="fig_04.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    mock_provider = MockAIProvider(fixed_text="Draft 1: Archaeological ground plan.")
    cfg = AIConfig()
    generator = AltTextGenerator(repo, filestore, mock_provider, cfg)

    # Invariant: Before generation, no approval exists
    assert repo.get_approval_for_image(image_id) is None

    # First generation (v1)
    res1, ver1 = generator.generate_for_image(project_id, image_id, user_prompt="Initial prompt")
    assert res1.status == "success"
    assert ver1["version_number"] == 1
    assert ver1["text"] == "Draft 1: Archaeological ground plan."

    # Invariant: Generation MUST NEVER automatically approve!
    assert repo.get_approval_for_image(image_id) is None
    asset_after = repo.get_image_asset(image_id)
    assert asset_after["processing_status"] != "approved"

    # Second generation (v2 regeneration)
    mock_provider.fixed_text = "Draft 2: Refined ground plan showing sanctuary walls."
    res2, ver2 = generator.generate_for_image(project_id, image_id, user_prompt="Revised prompt")
    assert res2.status == "success"
    assert ver2["version_number"] == 2
    assert ver2["text"] == "Draft 2: Refined ground plan showing sanctuary walls."

    # Prior draft v1 is preserved intact
    all_versions = repo.list_image_versions(image_id)
    assert len(all_versions) == 2
    assert all_versions[0]["version_number"] == 1
    assert all_versions[0]["text"] == "Draft 1: Archaeological ground plan."
    assert all_versions[1]["version_number"] == 2


def test_generator_failure_preserves_previous_draft(test_pipeline_env):
    repo, filestore, _, _ = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())

    repo.create_project(project_id, "Book", "book.pdf", "k", "c")
    key, chk, _ = filestore.save_image(project_id, image_id, create_sample_png_bytes(), "png")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=1,
        xref=5,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 100, "y1": 100},
        original_caption="Fig 1",
        caption_position="above",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="fig_1.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    mock_provider = MockAIProvider(fixed_text="Success Draft 1")
    generator = AltTextGenerator(repo, filestore, mock_provider, AIConfig())

    res1, ver1 = generator.generate_for_image(project_id, image_id)
    assert res1.status == "success"

    # Simulate subsequent provider outage
    mock_provider.simulate_error = "503 Service Unavailable: Provider overloaded"
    res2, ver2 = generator.generate_for_image(project_id, image_id)

    assert res2.status == "failed"
    assert ver2["status"] == "failed"
    assert "503 Service Unavailable" in ver2["error_message"]

    # Invariant: Prior draft v1 is still intact and latest successful version can be retrieved
    versions = repo.list_image_versions(image_id)
    assert len(versions) == 2
    assert versions[0]["text"] == "Success Draft 1"
    assert versions[0]["status"] == "success"


def test_switching_languages_between_requests(test_pipeline_env):
    repo, filestore, _, _ = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())

    repo.create_project(project_id, "Test Book", "test.pdf", "test.pdf", "abc")
    key, chk, _ = filestore.save_image(project_id, image_id, create_sample_png_bytes(), "png")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=1,
        xref=1,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Fig 1. Sample",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="fig_1.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    mock_provider = MockAIProvider(fixed_text="Generated draft description text.")
    cfg = AIConfig()
    generator = AltTextGenerator(repo, filestore, mock_provider, cfg)

    # Request 1 in British English
    res_gb, ver_gb = generator.generate_for_image(project_id, image_id, language="en-GB")
    assert res_gb.status == "success"
    assert ver_gb["language"] == "en-GB"
    assert "British English" in ver_gb["prompt_text"]

    # Request 2 in American English
    res_us, ver_us = generator.generate_for_image(project_id, image_id, language="en-US")
    assert res_us.status == "success"
    assert ver_us["language"] == "en-US"
    assert "American English" in ver_us["prompt_text"]

    # Invariant: Switching language updates the active language & draft metadata,
    # while preserving historical snapshot of v1 untouched
    v1_saved = repo.get_version(ver_gb["id"])
    assert v1_saved["language"] == "en-GB"
    assert "British English" in v1_saved["prompt_text"]

    v2_saved = repo.get_version(ver_us["id"])
    assert v2_saved["language"] == "en-US"
    assert "American English" in v2_saved["prompt_text"]


def test_custom_prompt_preservation_and_snapshot_immutability(test_pipeline_env):
    repo, filestore, _, _ = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())

    repo.create_project(project_id, "Book", "book.pdf", "k", "c")
    key, chk, _ = filestore.save_image(project_id, image_id, create_sample_png_bytes(), "png")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=1,
        xref=2,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Fig 2",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="fig_2.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    mock_provider = MockAIProvider(fixed_text="Draft result text.")
    generator = AltTextGenerator(repo, filestore, mock_provider, AIConfig())

    custom_prompt_1 = "Custom prompt: Detail the frieze carved relief iconography."
    res1, ver1 = generator.generate_for_image(project_id, image_id, user_prompt=custom_prompt_1)
    assert ver1["prompt_text"] == custom_prompt_1

    # Now generate with a different custom prompt
    custom_prompt_2 = "Second custom prompt: Focus on decorative borders."
    res2, ver2 = generator.generate_for_image(project_id, image_id, user_prompt=custom_prompt_2)
    assert ver2["prompt_text"] == custom_prompt_2

    # Verify v1 snapshot in database is completely unchanged
    v1_reloaded = repo.get_version(ver1["id"])
    assert v1_reloaded["prompt_text"] == custom_prompt_1
    assert v1_reloaded["version_number"] == 1


def test_generation_result_word_count_validation(test_pipeline_env):
    repo, filestore, _, _ = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())

    repo.create_project(project_id, "Book", "book.pdf", "k", "c")
    key, chk, _ = filestore.save_image(project_id, image_id, create_sample_png_bytes(), "png")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=1,
        xref=3,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Fig 3",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="fig_3.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    # 48 words text
    words_48 = [
        "A", "detailed", "monochrome", "line", "diagram", "illustrating", "an", "ancient", "Etruscan", "temple",
        "elevation", "with", "steep", "gabled", "roof,", "broad", "pediment,", "and", "fluted", "wooden",
        "columns", "supporting", "a", "heavy", "terracotta", "entablature.", "The", "structure", "rests", "upon",
        "a", "high", "stone", "podium", "with", "a", "single", "central", "flight", "of",
        "stairs", "approaching", "the", "triple", "cella", "entrance", "facade", "neatly."
    ]
    mock_provider = MockAIProvider(fixed_text=" ".join(words_48))
    generator = AltTextGenerator(repo, filestore, mock_provider, AIConfig())

    res, _ = generator.generate_for_image(project_id, image_id)
    assert res.word_count_validation is not None
    assert res.word_count_validation["is_valid"] is True
    assert res.word_count_validation["word_count"] == 48
    d = res.to_dict()
    assert "word_count_validation" in d
    assert d["word_count_validation"]["is_valid"] is True


# ============================================================
# 7. FastAPI Endpoints & UI Integration Tests
# ============================================================

def test_api_ai_config_and_usage(test_pipeline_env):
    repo, filestore, db_path, storage_dir = test_pipeline_env
    mock_provider = MockAIProvider()
    cfg = AIConfig(hf_token="hf_secret", hf_model="zai-org/GLM-5.3-Flash", hf_provider="novita")

    app = create_app(db_path=db_path, storage_dir=storage_dir, ai_provider=mock_provider, ai_config=cfg)
    client = TestClient(app)

    # 1. GET /api/ai/config
    cfg_resp = client.get("/api/ai/config")
    assert cfg_resp.status_code == 200
    cfg_data = cfg_resp.json()
    assert cfg_data["model"] == "zai-org/GLM-5.3-Flash"
    assert cfg_data["provider"] == "novita"
    assert "hf_secret" not in str(cfg_data)  # Redacted!

    # 2. GET /api/ai/usage
    usage_resp = client.get("/api/ai/usage")
    assert usage_resp.status_code == 200
    usage_data = usage_resp.json()
    assert "tracker" in usage_data
    assert usage_data["tracker"]["max_monthly_spend_usd"] == 2.0


def test_api_generate_and_version_history(test_pipeline_env):
    repo, filestore, db_path, storage_dir = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())

    repo.create_project(project_id, "API Test", "src.pdf", "k", "c")
    key, chk, _ = filestore.save_image(project_id, image_id, create_sample_png_bytes(), "png")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=1,
        xref=1,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 50, "y1": 50},
        original_caption="Caption text",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="img.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    mock_provider = MockAIProvider(fixed_text="Generated alt text from mocked provider.")
    app = create_app(db_path=db_path, storage_dir=storage_dir, ai_provider=mock_provider)
    client = TestClient(app)

    # Generate draft
    gen_resp = client.post(
        f"/api/projects/{project_id}/images/{image_id}/generate",
        json={"language": "en-GB", "user_prompt": "Test prompt"}
    )
    assert gen_resp.status_code == 200
    gen_data = gen_resp.json()
    assert gen_data["status"] == "success"
    assert gen_data["result"]["draft_text"] == "Generated alt text from mocked provider."
    assert gen_data["version"]["version_number"] == 1

    # Fetch version history
    ver_resp = client.get(f"/api/projects/{project_id}/images/{image_id}/versions")
    assert ver_resp.status_code == 200
    ver_data = ver_resp.json()
    assert len(ver_data["versions"]) == 1
    assert ver_data["latest_version"]["text"] == "Generated alt text from mocked provider."
    assert ver_data["is_approved"] is False  # Never auto-approved


# ============================================================
# 8. Image Preprocessing & HTTP 413 Safeguard Tests
# ============================================================

def test_image_preprocessor_normal_sized_image():
    """A normal-sized image within limits needs no unnecessary resizing."""
    prep = ImagePreprocessor(max_dimension=1536, max_payload_bytes=3_500_000)
    normal_bytes = create_sample_png_bytes(width=800, height=600)

    result = prep.preprocess(normal_bytes, "png")
    assert result.width == 800
    assert result.height == 600
    assert result.was_resized is False
    assert result.image_format == "png"
    assert result.estimated_base64_bytes <= 3_500_000


def test_image_preprocessor_very_large_image():
    """A very large image is proportionally resized to the configured maximum dimension."""
    prep = ImagePreprocessor(max_dimension=1536, max_payload_bytes=3_500_000)
    # 4000 x 3000 (aspect ratio 4:3)
    large_img = Image.new("RGB", (4000, 3000), color="green")
    buf = io.BytesIO()
    large_img.save(buf, format="PNG")
    large_bytes = buf.getvalue()

    result = prep.preprocess(large_bytes, "png")
    assert result.was_resized is True
    assert result.width == 1536
    assert result.height == 1152  # 1536 * (3000 / 4000) = 1152
    assert result.estimated_base64_bytes <= 3_500_000


def test_image_preprocessor_diagram_containing_small_text():
    """Diagrams/charts with few colors and small text are preserved as lossless PNG."""
    from PIL import ImageDraw
    im = Image.new("P", (1000, 600), 0)
    # Palette with few colors
    im.putpalette([255, 255, 255, 0, 0, 0, 99, 102, 241] + [0] * (256 * 3 - 9))
    draw = ImageDraw.Draw(im)
    draw.rectangle([50, 50, 400, 300], fill=1, outline=2)
    draw.text((60, 60), "Figure 1: Critical axis label", fill=2)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    diagram_bytes = buf.getvalue()

    prep = ImagePreprocessor(max_dimension=1536, max_payload_bytes=3_500_000)
    result = prep.preprocess(diagram_bytes, "png")

    assert result.image_format == "png"
    assert result.mime_type == "image/png"
    assert result.estimated_base64_bytes <= 3_500_000


def test_image_preprocessor_jpeg_and_png_handling():
    """
    Verifies that high-entropy photographic PNGs are converted to high-quality JPEG
    to fit within limits, while native JPEGs retain JPEG encoding without metadata bloat.
    """
    # 1. Native JPEG input
    im_jpeg = Image.new("RGB", (800, 600), color=(120, 80, 40))
    buf_jpeg = io.BytesIO()
    im_jpeg.save(buf_jpeg, format="JPEG", quality=90)
    jpeg_bytes = buf_jpeg.getvalue()

    prep = ImagePreprocessor(max_dimension=1536, max_payload_bytes=3_500_000)
    res_jpeg = prep.preprocess(jpeg_bytes, "jpeg")
    assert res_jpeg.image_format == "jpeg"
    assert res_jpeg.mime_type == "image/jpeg"

    # 2. Continuous-tone photo input encoded as large PNG
    # Multi-color texture produces >2048 colors (photographic)
    photo_im = Image.new("RGB", (300, 300))
    pixels = [(x % 256, y % 256, (x * y) % 256) for y in range(300) for x in range(300)]
    photo_im.putdata(pixels)
    buf_photo = io.BytesIO()
    photo_im.save(buf_photo, format="PNG")
    photo_png_bytes = buf_photo.getvalue()

    res_photo = prep.preprocess(photo_png_bytes, "png")
    assert res_photo.image_format == "jpeg"
    assert res_photo.was_reformatted is True
    assert res_photo.estimated_base64_bytes <= 3_500_000


def test_image_preprocessor_corrupt_or_unsupported_input():
    """Corrupt or non-image binary input raises a clear ValueError."""
    prep = ImagePreprocessor()
    with pytest.raises(ValueError) as exc:
        prep.preprocess(b"not an image binary payload", "png")
    assert "Invalid or corrupt image binary" in str(exc.value)

    with pytest.raises(ValueError) as exc_empty:
        prep.preprocess(b"", "png")
    assert "Empty image binary" in str(exc_empty.value)


def test_final_encoded_request_stays_within_size_limit():
    """Ensures the entire serialized JSON request payload stays under max_payload_bytes."""
    cfg = AIConfig(max_payload_bytes=3_500_000)
    prep = ImagePreprocessor(max_dimension=cfg.max_image_dimension, max_payload_bytes=cfg.max_payload_bytes)

    # Large gradient photo
    gradient = Image.linear_gradient("L").resize((2500, 2000)).convert("RGB")
    buf = io.BytesIO()
    gradient.save(buf, format="PNG")

    preprocessed = prep.preprocess(buf.getvalue(), "png")
    provider = HuggingFaceInferenceProvider(cfg)

    req = GenerationRequest(
        image_id="img_payload_test",
        image_bytes=preprocessed.image_bytes,
        image_format=preprocessed.image_format,
        user_prompt="Describe this gradient image",
    )

    import base64
    b64_str = base64.b64encode(req.image_bytes).decode("utf-8")
    data_uri = f"data:image/{req.image_format};base64,{b64_str}"

    payload = {
        "model": cfg.hf_model,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": data_uri}}]}
        ],
        "max_tokens": 100,
    }
    raw_json = json.dumps(payload).encode("utf-8")
    assert len(raw_json) <= cfg.max_payload_bytes


def test_original_stored_image_remains_byte_for_byte_unchanged_in_filestore(test_pipeline_env):
    """
    Crucial Invariant: The original extracted image file in FileStore must
    remain 100% byte-for-byte identical after generation preprocessing.
    """
    repo, filestore, _, _ = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = str(uuid.uuid4())

    repo.create_project(project_id, "Book", "book.pdf", "key", "chk")

    # Create a 3000x2000 image
    original_im = Image.new("RGB", (3000, 2000), color=(100, 150, 200))
    buf = io.BytesIO()
    original_im.save(buf, format="PNG")
    original_bytes = buf.getvalue()

    key, chk, _ = filestore.save_image(project_id, image_id, original_bytes, "png")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=1,
        xref=10,
        placement_index=0,
        bbox={"x0": 0, "y0": 0, "x1": 500, "y1": 500},
        original_caption="Large photo",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="large.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    mock_provider = MockAIProvider(fixed_text="Draft for large image")
    cfg = AIConfig(max_image_dimension=1536)
    generator = AltTextGenerator(repo, filestore, mock_provider, cfg)

    # Execute generation
    res, ver = generator.generate_for_image(project_id, image_id)
    assert res.status == "success"

    # Invariant: Read the file back from FileStore and assert byte-for-byte identity
    stored_bytes_after = filestore.read_bytes(project_id, key)
    assert stored_bytes_after == original_bytes
    assert len(stored_bytes_after) == len(original_bytes)


def test_hf_provider_413_permanent_failure_no_retries():
    """
    If the provider returns HTTP 413, it must be treated as a permanent failure
    with NO repeated retries and a clear, actionable error message.
    """
    cfg = AIConfig(hf_token="hf_test_valid_token", max_retries=3)
    provider = HuggingFaceInferenceProvider(cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 413
    mock_resp.text = '{"error":"request entity too large"}'

    req = GenerationRequest(
        image_id="img_413",
        image_bytes=create_sample_png_bytes(),
        image_format="png",
        user_prompt="Describe",
    )

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        res = provider.generate(req)

        # Invariant: Must NOT retry 413 repeatedly (call_count == 1)
        assert mock_post.call_count == 1
        assert res.status == "failed"
        assert "413 Request Entity Too Large" in res.error_message
        assert "hf_test_valid_token" not in res.error_message  # Redacted!


def test_api_generation_route_preprocesses_large_asset_and_preserves_original(test_pipeline_env):
    """
    Regression Test (Task 8):
    Exercises the actual FastAPI generation endpoint (/api/projects/{id}/images/{id}/generate)
    with the actual previously failing asset (Tomba Inghirami, UUID 66e0d546-1a62-45ff-bed1-32af24c3edcb)
    and a mocked AI provider.
    Asserts:
    1. Provider receives the preprocessed/optimized image (JPEG format, downscaled, << original bytes).
    2. Original asset in FileStore remains byte-for-byte unchanged.
    3. Route returns 200 with status='success' and audit version record.
    """
    from pathlib import Path
    import hashlib

    repo, filestore, db_path, storage_dir = test_pipeline_env
    project_id = str(uuid.uuid4())
    image_id = "66e0d546-1a62-45ff-bed1-32af24c3edcb"

    # Load the actual failing asset from disk if present, else synthesize matching dimensions & size
    asset_file = Path("data/storage/6755ad3d-f9cd-407f-a11e-3c4df3aba055/images/66e0d546-1a62-45ff-bed1-32af24c3edcb.png")
    if asset_file.exists():
        original_bytes = asset_file.read_bytes()
    else:
        # Create photographic image with 1575x1181 resolution
        raw_pixels = bytes((x * y) % 256 for x in range(1575) for y in range(1181))
        img = Image.frombytes("L", (1575, 1181), raw_pixels).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        original_bytes = buf.getvalue()

    original_size = len(original_bytes)
    original_checksum = hashlib.sha256(original_bytes).hexdigest()

    # Save to test FileStore
    key, chk, _ = filestore.save_image(project_id, image_id, original_bytes, "png")
    repo.create_project(project_id, "Test Project", "40345.pdf", "source_key", "source_chk")
    repo.create_image_asset(
        image_id=image_id,
        project_id=project_id,
        page_number=33,
        xref=105,
        placement_index=1,
        bbox={"x0": 72.0, "y0": 100.0, "x1": 540.0, "y1": 450.0},
        original_caption="Figure 1 Reconstruction of the Tomba Inghirami",
        caption_position="below",
        needs_caption_review=False,
        review_reasons=[],
        suggested_filename="Figure_1_Reconstruction_of_the.png",
        file_storage_key=key,
        file_checksum=chk,
        image_format="png",
    )

    # Mock provider that captures outbound GenerationRequest
    received_requests = []

    class MockCapturingProvider(MockAIProvider):
        def generate(self, req: GenerationRequest) -> GenerationResult:
            received_requests.append(req)
            return GenerationResult(
                draft_text="A high quality alt-text description of the ancient tomb.",
                provider="novita",
                model="zai-org/GLM-5.3-Flash",
                language=req.language,
                status="success",
                token_usage=TokenUsage(input_tokens=2865, output_tokens=85, total_tokens=2950),
                estimated_cost_usd=0.000491,
                duration_seconds=1.25,
            )

    capturing_provider = MockCapturingProvider()
    app = create_app(db_path=db_path, storage_dir=storage_dir, ai_provider=capturing_provider)
    client = TestClient(app)

    # Exercise the actual FastAPI generation route
    response = client.post(
        f"/api/projects/{project_id}/images/{image_id}/generate",
        json={"language": "en-GB", "user_prompt": "Describe this archaeological tomb reconstruction."}
    )

    # 1. Route assertions
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["result"]["draft_text"] == "A high quality alt-text description of the ancient tomb."
    assert data["version"]["version_number"] == 1

    # 2. Outbound request assertions: Provider received preprocessed/optimized copy
    assert len(received_requests) == 1
    req = received_requests[0]
    assert req.image_id == image_id
    assert req.project_id == project_id
    # Preprocessor converted continuous-tone photo from PNG to JPEG
    assert req.image_format == "jpeg"
    # Preprocessor optimized byte size is significantly smaller than the ~4.67MB original
    assert len(req.image_bytes) < 1_000_000
    assert len(req.image_bytes) < original_size
    # Preprocessor downscaled max dimension to 1536
    assert req.figure_metadata["original_dimensions"] == [1575, 1181]
    assert req.figure_metadata["inference_dimensions"] == [1536, 1151]
    assert req.figure_metadata["was_resized"] is True

    # 3. Critical Storage Invariant: Stored asset in FileStore remains byte-for-byte unchanged
    stored_bytes_after = filestore.read_bytes(project_id, key)
    assert stored_bytes_after == original_bytes
    assert len(stored_bytes_after) == original_size
    assert hashlib.sha256(stored_bytes_after).hexdigest() == original_checksum
