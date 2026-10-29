"""
AI Generation Layer Package
---------------------------
Exports configuration, data models, provider interfaces, guardrails, and coordinator.
"""

from .config import AIConfig
from .models import GenerationRequest, GenerationResult, TokenUsage
from .prompts import PromptAssembler, DEFAULT_STARTER_PROMPT
from .providers import (
    AIProvider,
    HuggingFaceInferenceProvider,
    MockAIProvider,
    calculate_cost,
    redact_secrets,
)
from .guardrails import BudgetTracker, ConcurrencyLimiter, BudgetExceededError
from .image_preprocessor import ImagePreprocessor, PreprocessedImage
from .generator import AltTextGenerator

__all__ = [
    "AIConfig",
    "GenerationRequest",
    "GenerationResult",
    "TokenUsage",
    "PromptAssembler",
    "DEFAULT_STARTER_PROMPT",
    "AIProvider",
    "HuggingFaceInferenceProvider",
    "MockAIProvider",
    "calculate_cost",
    "redact_secrets",
    "BudgetTracker",
    "ConcurrencyLimiter",
    "BudgetExceededError",
    "ImagePreprocessor",
    "PreprocessedImage",
    "AltTextGenerator",
]
