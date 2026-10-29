"""
Alt-Text Response Cleaner and Validator
---------------------------------------
Parses, cleans, and validates raw AI provider completions to guarantee that
only the final visual description is saved and exported. Strips model reasoning,
prompt echoes, intermediate drafts, revision instructions, and word-counting arithmetic.
Never silently truncates valid text; rejects untrustworthy or corrupted responses.
"""

import re
from typing import Tuple, Optional, List


# Patterns that indicate prompt echoes, chain-of-thought, or intermediate scratchpad
CONTAMINATION_PATTERNS = [
    r"Need\s+\d+[\u2013-]\d+\s+words",
    r"\bDraft\s*:",
    r"\bRevised\s*:",
    r"\bTrim\s*:",
    r"\bShorter\s*:",
    r"\bCount\s+words\s*:",
    r"\bCount\s*:\s*(?:[A-Za-z0-9]+\s*,\s*)*[A-Za-z0-9]+",
    r"\bCount\s*:\s*[A-Za-z]+(?:\(\d+\)|\d{1,3})",
    r"\bWord\(\d+\)",
    r"\b[A-Za-z]+(?:\(\d+\)|\d{1,3}\b)(?:\s+[A-Za-z]+(?:\(\d+\)|\d{1,3}\b)){3,}",
    r"incorporating\s+revision\s*:",
    r"\bToo\s+long\s*\(\d+\)",
    r"\bLet\s+me\s+(?:write|adjust|expand|trim|count)",
    r"=\s*\d+\s*\.\s*Need\s+\d+",
]


def is_contaminated_alt_text(text: str) -> bool:
    """Returns True if the text contains prompt echoes, scratchpads, or word-counting arithmetic."""
    if not text:
        return False
    return any(re.search(p, text, re.IGNORECASE) for p in CONTAMINATION_PATTERNS)


def clean_alt_text_response(raw_text: str) -> Tuple[str, bool, Optional[str]]:
    """
    Parses and cleans a raw model completion into a publication-grade alt-text description.

    Returns:
        (cleaned_text, is_valid, error_message)
    """
    if not raw_text or not raw_text.strip():
        return "", False, "Empty or blank response from AI provider."

    text = raw_text.strip()

    # 1. Strip reasoning and thinking tokens (<think>...</think> or <thought>...</thought>)
    text = re.sub(r"<(?:think|thought)>.*?</(?:think|thought)>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()

    # 2. Strip markdown code fences if wrapped
    fence_match = re.match(r"^```(?:text|markdown)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # 3. If response is completely free of contamination markers, perform standard normalization
    if not is_contaminated_alt_text(text):
        cleaned = re.sub(r"^(?:Alt(?:\s*Text)?|Alternative\s*Text|Description)\s*:\s*", "", text, flags=re.IGNORECASE).strip()
        cleaned = cleaned.strip("\"'\u201c\u201d")
        if not cleaned:
            return "", False, "Empty alt-text description."
        return cleaned, True, None

    # 4. Response contains scratchpad, draft iterations, or word counts.
    # Extract candidate descriptions from labeled sections and quoted blocks.
    candidates: List[str] = []

    # Priority A: Explicit labeled and quoted candidates (e.g. Revised: "...", Draft: "...", Trim: "...")
    labeled_quoted_pattern = r"(?:Revised|Trim|Shorter|Draft|Final|Output|Description)\s*:\s*[\"\u201c]([^\"\u201d\r\n]+)[\"\u201d]"
    for m in re.finditer(labeled_quoted_pattern, text, flags=re.IGNORECASE):
        candidates.append(m.group(1).strip())

    # Priority B: General quoted descriptions of substantial length (>= 50 chars)
    quoted_pattern = r"[\"\u201c]([^\"\u201d]{50,})[\"\u201d]"
    for m in re.finditer(quoted_pattern, text):
        cand = m.group(1).strip()
        if cand not in candidates:
            candidates.append(cand)

    # Priority C: Labeled unquoted blocks (e.g. Revised: Bar chart... up to next section or Count:)
    labeled_unquoted_pattern = r"(?:Revised|Draft)\s*:\s*([^\r\n]+(?:\n[^\r\n]+)*?)(?=\n\s*(?:Count|Need|Draft|Revised|Add|Trim|Shorter|Too long)|$)"
    for m in re.finditer(labeled_unquoted_pattern, text, flags=re.IGNORECASE):
        cand = m.group(1).strip().strip("\"'\u201c\u201d")
        if cand not in candidates:
            candidates.append(cand)

    # 5. Filter candidates: must be complete, non-contaminated, and meaningful length
    valid_candidates: List[str] = []
    for cand in candidates:
        cand_norm = cand.strip().strip("\"'\u201c\u201d")
        # Strip any leading label if retained
        cand_norm = re.sub(r"^(?:Alt(?:\s*Text)?|Alternative\s*Text|Description)\s*:\s*", "", cand_norm, flags=re.IGNORECASE).strip()

        # Must end with sentence-ending punctuation (cannot be truncated mid-sentence)
        if not re.search(r"[\.!\?][\"'\u201c\u201d]?$", cand_norm):
            continue

        # Must not contain residual contamination
        if is_contaminated_alt_text(cand_norm):
            continue

        # Must have at least 15 words
        words = cand_norm.split()
        if len(words) < 15:
            continue

        valid_candidates.append(cand_norm)

    if not valid_candidates:
        return "", False, (
            "Model response contained prompt instructions, scratchpad, or intermediate drafts "
            "that could not be reliably parsed into a verified description."
        )

    # The most mature complete revision is the last valid candidate
    best_candidate = valid_candidates[-1]
    return best_candidate, True, None
