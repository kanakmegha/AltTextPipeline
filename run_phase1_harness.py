"""
Phase 1 Test Harness & Diagnostic Runner
----------------------------------------
Executes the baseline extraction engine against:
1. Synthetic digital PDFs with known ground-truth geometry and relationships.
2. A representative, limited page sample from the real book PDF (`40345.pdf`).

Generates structured pass/fail reports and diagnostics.
"""

import os
import sys
import json
import traceback
from tests.fixtures.create_synthetic_pdfs import generate_synthetic_fixtures
from tests.baseline_adapter import extract_from_pdf


def run_synthetic_benchmarks(fixtures_dir):
    print("=" * 80)
    print("PART 1: SYNTHETIC BENCHMARK SUITE (KNOWN GROUND TRUTH)")
    print("=" * 80)

    gt_path = os.path.join(fixtures_dir, "synthetic_ground_truth.json")
    with open(gt_path, "r", encoding="utf-8") as f:
        ground_truth = json.load(f)

    results = []
    total_cases = 0
    passed_cases = 0

    for case_id, case_info in ground_truth.items():
        pdf_path = case_info["file"]
        desc = case_info["description"]
        expected_items = case_info["expected_items"]

        print(f"\nScenario: {case_id}")
        print(f"Description: {desc}")
        print("-" * 80)

        records, errors, summary = extract_from_pdf(pdf_path)

        if errors:
            print(f"  [ERROR] Extraction encountered {len(errors)} error(s): {errors}")

        for i, exp in enumerate(expected_items):
            total_cases += 1
            rec = records[i] if i < len(records) else None

            if not rec:
                status = "FAIL (Missing Record)"
                det_caption = "N/A"
                det_pos = "N/A"
                det_review = "N/A"
                is_pass = False
            else:
                det_caption = rec["caption"]
                det_pos = rec["caption_position"]
                det_review = rec["needs_caption_review"]

                # Pass criteria:
                # 1. Correct caption text matches expected
                # 2. Correct position matches expected
                caption_match = (det_caption == exp["expected_caption"])
                pos_match = (det_pos == exp["expected_position"])

                is_pass = caption_match and pos_match
                status = "PASS" if is_pass else "FAIL"

            if is_pass:
                passed_cases += 1

            res_entry = {
                "scenario": case_id,
                "placement_index": i + 1,
                "expected_caption": exp["expected_caption"],
                "detected_caption": det_caption,
                "expected_position": exp["expected_position"],
                "detected_position": det_pos,
                "needs_review": det_review,
                "status": status
            }
            results.append(res_entry)

            print(f"  Placement {i+1}: [{status}]")
            print(f"    Expected Caption:  {exp['expected_caption'] or '[NONE]'}")
            print(f"    Detected Caption:  {det_caption or '[NONE]'}")
            print(f"    Selected Position: {det_pos} (Expected: {exp['expected_position']})")
            print(f"    Needs Review Flag: {det_review}")

    print("\n" + "-" * 80)
    print(f"Synthetic Tests Summary: {passed_cases}/{total_cases} checks passed ({passed_cases/total_cases*100:.1f}%)")
    print("-" * 80)
    return results, passed_cases, total_cases


def run_book_diagnostics(book_pdf_path, sample_pages, output_folder=None):
    print("\n" + "=" * 80)
    print("PART 2: REAL-BOOK DIAGNOSTIC EXTRACTION (40345.pdf)")
    print("=" * 80)
    print(f"Source PDF: {book_pdf_path}")
    print(f"Selected Sample Pages: {sample_pages}")
    print("Selection Rationale:")
    print("  - Page 1:  Full-page book cover image (uncaptioned image baseline)")
    print("  - Page 4:  Front-matter publisher insignia/colophon mark")
    print("  - Page 33: Interior single-figure layout (demographic table/chart)")
    print("  - Page 46: Interior single-figure layout with adjacent body text")
    print("  - Page 77: Complex multi-figure layout (Figure 10 & Figure 11 on the same page)")
    print("-" * 80)

    if not os.path.exists(book_pdf_path):
        print(f"[ERROR] Book PDF not found at {book_pdf_path}")
        return []

    records, errors, summary = extract_from_pdf(
        book_pdf_path,
        output_images_folder=output_folder,
        page_numbers=sample_pages
    )

    print(f"\nDiagnostic Extraction Completed:")
    print(f"  Pages processed:     {len(sample_pages)}")
    print(f"  Images extracted:    {len(records)}")
    print(f"  Captions detected:   {summary['captions_found']}")
    print(f"  Flagged for review:  {summary['needs_caption_review']}")
    print(f"  Errors encountered:  {summary['errors']}")
    print("\nDetailed Diagnostic Breakdown per Image:")
    print("-" * 80)

    diagnostic_results = []
    for r in records:
        print(f"Page {r['page']} (Placement {r['placement']}):")
        print(f"  Saved Filename:    {r['filename']}")
        print(f"  Bounding Box:      ({r['image_rect']['x0']}, {r['image_rect']['y0']}) -> ({r['image_rect']['x1']}, {r['image_rect']['y1']})")
        print(f"  Candidate Blocks:  {len(r['caption_candidates'])} candidate(s) found")
        for cand in r['caption_candidates']:
            print(f"    - {cand['position'].upper()} (gap={cand['gap']:.1f}pt): \"{cand['text']}\"")
        print(f"  Chosen Position:   {r['caption_position'] or 'None'}")
        print(f"  Chosen Caption:    \"{r['caption'] or '[NO CAPTION FOUND]'}\"")
        print(f"  Needs Review:      {r['needs_caption_review']}")
        print("-" * 80)

        diagnostic_results.append({
            "page": r["page"],
            "placement": r["placement"],
            "filename": r["filename"],
            "image_rect": r["image_rect"],
            "candidate_count": len(r["caption_candidates"]),
            "candidates": r["caption_candidates"],
            "chosen_position": r["caption_position"],
            "chosen_caption": r["caption"],
            "needs_caption_review": r["needs_caption_review"]
        })

    return diagnostic_results


def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    fixtures_dir = os.path.join(root_dir, "tests", "fixtures", "synthetic_pdfs")
    diagnostic_img_dir = os.path.join(root_dir, "diagnostic_output", "images")

    # 1. Generate synthetic fixtures
    generate_synthetic_fixtures(fixtures_dir)

    # 2. Run synthetic tests
    synth_results, passed, total = run_synthetic_benchmarks(fixtures_dir)

    # 3. Run book diagnostics on representative pages
    book_pdf = os.path.join(root_dir, "40345.pdf")
    sample_pages = [1, 4, 33, 46, 77]
    book_results = run_book_diagnostics(book_pdf, sample_pages, diagnostic_img_dir)

    # 4. Save JSON Report
    report = {
        "synthetic_benchmarks": {
            "total_checks": total,
            "passed_checks": passed,
            "accuracy_percentage": round((passed / total) * 100, 2) if total else 0,
            "results": synth_results
        },
        "book_diagnostics": {
            "pdf_source": book_pdf,
            "sample_pages": sample_pages,
            "results": book_results,
            "note": "Diagnostic only. Expected captions require human editorial verification."
        }
    }

    report_path = os.path.join(root_dir, "phase1_baseline_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n[DONE] Full Phase 1 baseline report written to: {report_path}")


if __name__ == "__main__":
    main()
