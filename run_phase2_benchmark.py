"""
Phase 2 Comparative Benchmark Runner
------------------------------------
Executes both Baseline and Modular Extraction Engines on:
1. Synthetic benchmark test suite.
2. The representative page sample of `40345.pdf` (Pages 1, 4, 33, 46, 77).

Reports side-by-side comparison of candidate reduction, running header filtering,
review flagging accuracy, and filename cleanups.
"""

import os
import json
from src.extraction.engine import ExtractionEngine
from tests.baseline_adapter import extract_from_pdf as baseline_extract


def compare_book_diagnostics(book_pdf_path, sample_pages):
    print("=" * 80)
    print("COMPARATIVE EXTRACTION BENCHMARK: BASELINE vs. MODULAR ENGINE")
    print(f"Target PDF: {book_pdf_path}")
    print(f"Sample Pages: {sample_pages}")
    print("=" * 80)

    # 1. Baseline extraction
    baseline_records, _, baseline_summary = baseline_extract(
        book_pdf_path,
        page_numbers=sample_pages
    )

    # 2. Modular engine extraction
    engine = ExtractionEngine()
    modular_res = engine.process_pdf(book_pdf_path, page_numbers=sample_pages)

    print(f"\nAGGREGATE COMPARISON ACROSS {len(sample_pages)} SAMPLE PAGES:")
    print("-" * 80)
    print(f"  Metric                      | Baseline Engine | Modular Engine (Phase 2)")
    print("-" * 80)
    print(f"  Images Extracted            | {baseline_summary['images_extracted']:<15} | {len(modular_res.images)}")
    print(f"  Captions Found              | {baseline_summary['captions_found']:<15} | {modular_res.stats['captions_found']}")
    print(f"  Images Flagged for Review   | {baseline_summary['needs_caption_review']:<15} | {modular_res.stats['review_flagged']}")
    print(f"  Total Candidates Considered | {sum(len(r['caption_candidates']) for r in baseline_records):<15} | {sum(len(img.caption_candidates) for img in modular_res.images)}")
    print("-" * 80)

    comparison_details = []

    print("\nIMAGE-BY-IMAGE COMPARATIVE BREAKDOWN:")
    print("=" * 80)

    for i in range(len(baseline_records)):
        b = baseline_records[i]
        m = modular_res.images[i]

        print(f"\nPage {m.page_number} (Placement {m.placement_index}):")
        print(f"  [BASELINE] Filename:      {b['filename']}")
        print(f"             Candidates:    {len(b['caption_candidates'])} detected")
        print(f"             Chosen Caption:{b['caption'][:65]}..." if b['caption'] else "             Chosen Caption: [NONE]")
        print(f"             Needs Review:  {b['needs_caption_review']}")

        print(f"  [MODULAR]  UUID:          {m.image_id}")
        print(f"             Clean Filename:{m.suggested_filename}")
        print(f"             Candidates:    {len(m.caption_candidates)} candidate(s) after header/boundary filtering")
        for c in m.caption_candidates:
            print(f"               - {c.position.upper()} (gap={c.gap}pt, score={c.score}): \"{c.text[:55]}...\"")
        print(f"             Chosen Caption:{m.caption[:65]}..." if m.caption else "             Chosen Caption: [NONE]")
        print(f"             Needs Review:  {m.needs_caption_review} (Reasons: {m.review_reasons or 'None'})")

        comparison_details.append({
            "page": m.page_number,
            "placement": m.placement_index,
            "baseline": {
                "filename": b["filename"],
                "candidate_count": len(b["caption_candidates"]),
                "caption": b["caption"],
                "caption_position": b["caption_position"],
                "needs_review": b["needs_caption_review"]
            },
            "modular": {
                "image_id": m.image_id,
                "clean_filename": m.suggested_filename,
                "candidate_count": len(m.caption_candidates),
                "caption": m.caption,
                "caption_position": m.caption_position,
                "needs_review": m.needs_caption_review,
                "review_reasons": m.review_reasons
            }
        })

    return {
        "aggregate": {
            "baseline": baseline_summary,
            "modular": modular_res.stats
        },
        "details": comparison_details
    }


def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    book_pdf = os.path.join(root_dir, "40345.pdf")
    sample_pages = [1, 4, 33, 46, 77]

    comp_report = compare_book_diagnostics(book_pdf, sample_pages)

    out_file = os.path.join(root_dir, "phase2_comparison_report.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(comp_report, f, indent=2)

    print(f"\n[DONE] Comparative report saved to: {out_file}")


if __name__ == "__main__":
    main()
