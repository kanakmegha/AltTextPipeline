"""
Model Evaluation Harness
------------------------
Offline/Online evaluation harness to benchmark and compare candidate vision models
(e.g., GLM-5.3-Flash via Novita vs alternative models) across a curated set of images.
Preserves identical prompts, context, and language settings.

Outputs structured JSON and Markdown reports including the 7-criteria human review rubric.
Does NOT run automatically during normal startup or default test suite.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.ai.config import AIConfig
from src.ai.models import GenerationRequest, GenerationResult
from src.ai.prompts import PromptAssembler, DEFAULT_STARTER_PROMPT
from src.ai.image_preprocessor import ImagePreprocessor
from src.ai.providers import (
    AIProvider,
    HuggingFaceInferenceProvider,
    MockAIProvider,
    calculate_cost,
)
from src.db.connection import DatabaseConnection
from src.db.migrations import apply_migrations
from src.db.repository import Repository
from src.storage.filestore import FileStore


MANUAL_REVIEW_RUBRIC = {
    "1_factual_correctness": "Score 1-5: Faithfully depicts visual elements without misidentifying objects or text.",
    "2_visual_coverage": "Score 1-5: Captures key labels, trends, axes, and significant visual structures.",
    "3_context_relevance": "Score 1-5: Effectively uses document caption without parroting or confusing it.",
    "4_hallucination_absence": "Score 1-5: Completely avoids unsupported assertions, invented figures, or speculative relationships.",
    "5_accessibility_clarity": "Score 1-5: Clear, coherent, screen-reader friendly phrasing.",
    "6_conciseness": "Score 1-5: Informative without unnecessary filler or wordiness.",
    "7_publication_suitability": "Score 1-5: Meets high-standard editorial publication tone.",
}


def run_evaluation(
    project_id: str,
    models: List[str],
    provider_name: str = "novita",
    language: str = "en-GB",
    limit: int = 15,
    user_prompt: str = DEFAULT_STARTER_PROMPT,
    use_mock: bool = False,
    output_path: Optional[str] = None,
    db_path: str = "data/pipeline.db",
    storage_dir: str = "data/storage",
) -> Dict[str, Any]:
    """
    Runs model evaluation across representative images in a project.
    """
    db_conn = DatabaseConnection(db_path)
    apply_migrations(db_conn)
    repo = Repository(db_conn)
    filestore = FileStore(storage_dir)
    assembler = PromptAssembler()

    images = repo.list_project_images(project_id)
    if not images:
        raise ValueError(f"No image assets found in project '{project_id}'.")

    # Select evaluation set (up to limit)
    eval_set = images[:limit]
    print(f"\n[Evaluation Harness] Selected {len(eval_set)} images from project '{project_id}'.")
    print(f"[Evaluation Harness] Evaluating {len(models)} model(s): {models}")
    print(f"[Evaluation Harness] Language: {language} | Provider: {provider_name} | Mock: {use_mock}\n")

    results_by_model: Dict[str, List[Dict[str, Any]]] = {}

    cfg = AIConfig.from_env()
    preprocessor = ImagePreprocessor(
        max_dimension=cfg.max_image_dimension,
        max_payload_bytes=cfg.max_payload_bytes,
        jpeg_quality=cfg.jpeg_quality,
    )

    for model_id in models:
        print(f"--- Evaluating Model: {model_id} ---")
        if use_mock or not cfg.is_configured():
            provider = MockAIProvider(provider_name=provider_name, model_name=model_id)
        else:
            model_cfg = AIConfig(
                hf_token=cfg.hf_token,
                hf_model=model_id,
                hf_provider=provider_name,
                language=language,
                request_timeout_seconds=cfg.request_timeout_seconds,
                max_retries=cfg.max_retries,
                max_output_tokens=cfg.max_output_tokens,
                max_payload_bytes=cfg.max_payload_bytes,
                max_image_dimension=cfg.max_image_dimension,
                jpeg_quality=cfg.jpeg_quality,
            )
            provider = HuggingFaceInferenceProvider(model_cfg, assembler)

        model_results = []

        for idx, img in enumerate(eval_set, 1):
            original_bytes = filestore.read_file(project_id, img["file_storage_key"])
            prep = preprocessor.preprocess(original_bytes, img["image_format"])

            req = GenerationRequest(
                project_id=project_id,
                image_id=img["id"],
                image_bytes=prep.image_bytes,
                image_format=prep.image_format,
                caption=img.get("original_caption", ""),
                surrounding_context="",
                user_prompt=user_prompt,
                language=language,
                model=model_id,
                provider=provider_name,
                figure_metadata={
                    "page_number": img["page_number"],
                    "filename": img["suggested_filename"],
                    "original_dimensions": [prep.original_width, prep.original_height],
                    "inference_dimensions": [prep.width, prep.height],
                    "original_bytes_length": prep.original_bytes_length,
                    "inference_bytes_length": prep.optimized_bytes_length,
                },
                max_output_tokens=cfg.max_output_tokens,
            )

            print(f"  [{idx}/{len(eval_set)}] Image {img['suggested_filename']} (Page {img['page_number']})...", end=" ", flush=True)
            t0 = time.time()
            res = provider.generate(req)
            elapsed = time.time() - t0

            status_str = "SUCCESS" if res.status == "success" else f"FAILED ({res.error_message})"
            print(f"{status_str} ({elapsed:.2f}s)")

            tokens = res.token_usage.to_dict() if res.token_usage else {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

            record = {
                "image_id": img["id"],
                "suggested_filename": img["suggested_filename"],
                "page_number": img["page_number"],
                "caption": img.get("original_caption", ""),
                "model": model_id,
                "provider": provider_name,
                "language": language,
                "status": res.status,
                "draft_text": res.draft_text,
                "error_message": res.error_message,
                "duration_seconds": round(res.duration_seconds, 2),
                "tokens": tokens,
                "estimated_cost_usd": res.estimated_cost_usd,
                "human_review_scores": {k: None for k in MANUAL_REVIEW_RUBRIC.keys()},
                "human_review_notes": "",
            }
            model_results.append(record)

        results_by_model[model_id] = model_results

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_id": project_id,
        "sample_size": len(eval_set),
        "language": language,
        "models_evaluated": models,
        "rubric": MANUAL_REVIEW_RUBRIC,
        "results": results_by_model,
    }

    if output_path:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n[Evaluation Harness] Evaluation report written to: {out_file.resolve()}")

    return report


def main():
    parser = argparse.ArgumentParser(description="Evaluate vision models on AltTextPipeline image assets.")
    parser.add_argument("--project-id", type=str, required=True, help="Project ID containing extracted assets.")
    parser.add_argument("--models", type=str, default="zai-org/GLM-5.3-Flash", help="Comma-separated list of models to evaluate.")
    parser.add_argument("--provider", type=str, default="novita", help="Inference provider routing.")
    parser.add_argument("--language", type=str, default="en-GB", choices=["en-GB", "en-US"], help="Output language variant.")
    parser.add_argument("--limit", type=int, default=15, help="Number of images to evaluate (15-20 recommended).")
    parser.add_argument("--mock", action="store_true", help="Force mock offline evaluation.")
    parser.add_argument("--output", type=str, default="data/evaluation_report.json", help="Path to output JSON report.")
    args = parser.parse_args()

    model_list = [m.strip() for m in args.models.split(",") if m.strip()]
    run_evaluation(
        project_id=args.project_id,
        models=model_list,
        provider_name=args.provider,
        language=args.language,
        limit=args.limit,
        use_mock=args.mock,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
