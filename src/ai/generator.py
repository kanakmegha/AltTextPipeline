"""
Alt-Text Generator Coordinator
------------------------------
Coordinates the generation pipeline: image retrieval from FileStore,
prompt assembly, provider invocation, budget guardrails, draft persistence,
and audit ledger tracking without automatic approval.
"""

import logging
import uuid
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)

from .models import GenerationRequest, GenerationResult
from .prompts import (
    PromptAssembler,
    DEFAULT_STARTER_PROMPT,
    resolve_default_prompt,
    resolve_language_name,
    validate_word_count,
)
from .cleaner import clean_alt_text_response
from .providers import AIProvider
from .guardrails import BudgetTracker, ConcurrencyLimiter, BudgetExceededError
from .image_preprocessor import ImagePreprocessor
from .config import AIConfig
from ..storage.filestore import FileStore
from ..db.repository import Repository


SUPPORTED_IMAGE_FORMATS = {"png", "jpeg", "jpg", "webp", "gif"}


class AltTextGenerator:
    """
    Provider-independent generation coordinator.
    """

    def __init__(
        self,
        repository: Repository,
        file_store: FileStore,
        provider: AIProvider,
        config: AIConfig,
        budget_tracker: Optional[BudgetTracker] = None,
        concurrency_limiter: Optional[ConcurrencyLimiter] = None,
        prompt_assembler: Optional[PromptAssembler] = None,
        image_preprocessor: Optional[ImagePreprocessor] = None,
    ):
        self.repo = repository
        self.file_store = file_store
        self.provider = provider
        self.config = config
        self.budget_tracker = budget_tracker or BudgetTracker(config.max_monthly_spend_usd)
        self.concurrency_limiter = concurrency_limiter or ConcurrencyLimiter(config.max_concurrent_requests)
        self.assembler = prompt_assembler or PromptAssembler()
        self.preprocessor = image_preprocessor or ImagePreprocessor(
            max_dimension=config.max_image_dimension,
            max_payload_bytes=config.max_payload_bytes,
            jpeg_quality=config.jpeg_quality,
        )

    def generate_for_image(
        self,
        project_id: str,
        image_id: str,
        user_prompt: Optional[str] = None,
        language: Optional[str] = None,
        revision_instruction: Optional[str] = None,
    ) -> Tuple[GenerationResult, Optional[Dict[str, Any]]]:
        """
        Executes alt-text generation for a persisted image asset.

        Guarantees:
        - Original image binary is retrieved from project-isolated FileStore and preserved byte-for-byte.
        - Separate temporary copy is optimized for inference within provider payload constraints.
        - User prompt is preserved without unauthorized alteration.
        - Budget ceiling is strictly enforced.
        - Concurrency is bounded.
        - Each draft is versioned (v1, v2, ...); prior versions are never overwritten.
        - NO approval record is created.
        - Failed attempts preserve prior drafts.
        """
        # 1. Fetch image asset
        image = self.repo.get_image_asset(image_id)
        if not image:
            raise ValueError(f"Image asset '{image_id}' not found.")
        if image["project_id"] != project_id:
            raise ValueError(f"Image asset '{image_id}' does not belong to project '{project_id}'.")

        # 2. Check format and retrieve original image bytes from FileStore (UNMODIFIED)
        fmt = image["image_format"].lower()
        if fmt not in SUPPORTED_IMAGE_FORMATS:
            raise ValueError(f"Unsupported image format '{fmt}'. Supported: {SUPPORTED_IMAGE_FORMATS}")

        original_image_bytes = self.file_store.read_bytes(project_id, image["file_storage_key"])

        # 3. Optimize a separate temporary inference copy (never overwrites stored original!)
        prep = self.preprocessor.preprocess(original_image_bytes, fmt)

        # 4. Check budget guardrails
        self.budget_tracker.check_budget_available()

        # 5. Resolve settings
        active_language = language or self.config.language
        if user_prompt and user_prompt.strip():
            if "[LANGUAGE]" in user_prompt:
                active_prompt = user_prompt.replace("[LANGUAGE]", resolve_language_name(active_language)).strip()
            else:
                active_prompt = user_prompt.strip()
        else:
            active_prompt = resolve_default_prompt(active_language)

        figure_meta = {
            "page_number": image["page_number"],
            "suggested_filename": image["suggested_filename"],
            "xref": image["xref"],
            "original_dimensions": [prep.original_width, prep.original_height],
            "inference_dimensions": [prep.width, prep.height],
            "original_bytes_length": prep.original_bytes_length,
            "inference_bytes_length": prep.optimized_bytes_length,
            "was_resized": prep.was_resized,
            "was_reformatted": prep.was_reformatted,
        }

        # Fetch latest draft if this is a revision
        latest_version = self.repo.get_latest_version(image_id)
        prior_draft_text = latest_version["text"] if latest_version else ""

        # 6. Build GenerationRequest with optimized inference copy
        req = GenerationRequest(
            project_id=project_id,
            image_id=image_id,
            image_bytes=prep.image_bytes,
            image_format=prep.image_format,
            caption=image.get("original_caption", ""),
            surrounding_context="",  # Available in future extraction updates
            user_prompt=active_prompt,
            language=active_language,
            model=self.provider.model_name,
            provider=self.provider.provider_name,
            figure_metadata=figure_meta,
            prior_draft=prior_draft_text,
            revision_instruction=revision_instruction,
            max_output_tokens=self.config.max_output_tokens,
        )

        logger.info(
            "Invoking AI generator for project=%s, image=%s: orig=(%dx%d, %d bytes), "
            "optimized=(%dx%d, %s, %d bytes), model=%s, provider=%s",
            project_id,
            image_id,
            prep.original_width,
            prep.original_height,
            prep.original_bytes_length,
            prep.width,
            prep.height,
            prep.image_format,
            prep.optimized_bytes_length,
            self.provider.model_name,
            self.provider.provider_name,
        )

        # 7. Execute with concurrency throttling
        with self.concurrency_limiter:
            result = self.provider.generate(req)

        # Word count and contamination validation for editorial quality assurance
        if result.status == "success":
            cleaned_text, is_valid, err_msg = clean_alt_text_response(result.draft_text)
            if not is_valid:
                logger.warning(
                    "Generated draft failed quality validation for project=%s, image=%s: %s",
                    project_id,
                    image_id,
                    err_msg,
                )
                result.status = "failed"
                result.error_message = f"Alt-text quality validation error: {err_msg}"
                result.draft_text = ""
            else:
                result.draft_text = cleaned_text
                result.word_count_validation = validate_word_count(result.draft_text)

        logger.info(
            "AI generator finished for project=%s, image=%s: status=%s, duration=%.2fs",
            project_id,
            image_id,
            result.status,
            result.duration_seconds,
        )

        # 7. Determine version number
        existing_versions = self.repo.list_image_versions(image_id)
        next_version_num = len(existing_versions) + 1
        version_id = f"altv_{uuid.uuid4().hex[:12]}"

        # 8. If successful, record usage and persist draft
        if result.status == "success":
            tokens = result.token_usage
            in_tok = tokens.input_tokens if tokens else 0
            out_tok = tokens.output_tokens if tokens else 0

            # Record in in-memory tracker & database ledger
            self.budget_tracker.record_usage(in_tok, out_tok, result.estimated_cost_usd)
            self.repo.record_ai_usage(
                ledger_id=f"ledger_{uuid.uuid4().hex[:12]}",
                project_id=project_id,
                image_id=image_id,
                provider=result.provider,
                model=result.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=result.estimated_cost_usd,
            )

            # Persist version
            version_record = self.repo.create_alt_text_version(
                version_id=version_id,
                image_id=image_id,
                version_number=next_version_num,
                text=result.draft_text,
                revision_instruction=revision_instruction,
                prompt_version_id=None,
                provider=result.provider,
                model=result.model,
                language=result.language,
                prompt_text=active_prompt,
                prompt_hash=result.prompt_hash,
                caption_snapshot=image.get("original_caption", ""),
                context_snapshot="",
                status="success",
                error_message=None,
                input_tokens=in_tok,
                output_tokens=out_tok,
                estimated_cost_usd=result.estimated_cost_usd,
                duration_seconds=result.duration_seconds,
                provider_request_id=result.provider_request_id,
            )

            # Update image status to drafted (NOT approved!)
            self.repo.update_image_status(image_id, "drafted")
            return result, version_record

        else:
            # Failed request: record failed version for audit provenance, do NOT destroy existing drafts
            failed_version_record = self.repo.create_alt_text_version(
                version_id=version_id,
                image_id=image_id,
                version_number=next_version_num,
                text="",
                revision_instruction=revision_instruction,
                prompt_version_id=None,
                provider=result.provider,
                model=result.model,
                language=result.language,
                prompt_text=active_prompt,
                prompt_hash=result.prompt_hash,
                caption_snapshot=image.get("original_caption", ""),
                context_snapshot="",
                status="failed",
                error_message=result.error_message,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=0.0,
                duration_seconds=result.duration_seconds,
                provider_request_id=result.provider_request_id,
            )
            return result, failed_version_record
