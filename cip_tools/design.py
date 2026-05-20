"""
Step 3: Ask Claude to write a Python extraction script for this CIP file.

The script must:
- Have a CONFIGURATION block at top (easy to re-use next year)
- Accept a source file path and return a list of dicts
- Map source columns to the standard schema
- Handle multi-value fields (funds, years) by JSON-encoding them
- Be runnable standalone as: python <script>.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm import ask, SONNET
from .schema import FINAL_COLS

SYSTEM = """You are a senior Python developer specializing in data extraction from government documents.
Write clean, minimal extraction scripts. No unnecessary abstractions. No error suppression.
Include a CONFIGURATION block so next year's curator can update paths without reading the logic."""

USER_TEMPLATE = """Write a Python extraction script for this CIP source file.
{guide_block}
=== SOURCE FILE INFO ===
Filename:  {filename}
Full path: {full_path}
Format:    {format_type}
Approach:  {approach}

=== STRUCTURE ANALYSIS ===
{analysis_json}

=== SAMPLE ROWS (raw source) ===
{sample_rows}

=== EXTRACTED TEXT SAMPLE (first 8000 chars of CIP section) ===
{text_sample}

=== REQUIREMENTS ===
The script must:
1. Start with a CONFIGURATION block (all tuneable values as module-level constants).
   SOURCE_FILE must be set to the FULL PATH shown above (not just the filename).
   Include: DOLLAR_UNIT = {dollar_unit}
2. Define a run() function that reads the source file and returns list[dict]
3. Each dict must contain EXACTLY these keys (use empty string for missing):
   {final_cols}
4. Project_Index must be sequential integers starting at 1
5. Total_Project_Budget must be a numeric value (integer or float, no $ or commas)
6. Fund_Names_JSON: JSON array of strings e.g. '["City", "Federal", "State"]'
7. Fund_Budgets_JSON: JSON array of numbers matching Fund_Names_JSON
8. Yearly_Costs_By_Category_JSON: JSON object e.g. '{{"2026": 500000, "2027": 250000}}'
9. If __name__ == "__main__": block that runs and prints a summary
10. Handle encoding: try utf-8-sig first, fall back to latin-1
11. DOLLAR_UNIT normalization: multiply EVERY parsed dollar amount by DOLLAR_UNIT after clean_num().
    Example: total = clean_num(row.get('Total')) * DOLLAR_UNIT
    This normalizes values expressed in thousands or millions to full dollars.

=== DEFENSIVE CODING REQUIREMENTS (mandatory) ===
Include a clean_num() helper at the top of run():

    def clean_num(val):
        if val is None: return 0
        s = str(val).strip().replace(',', '').replace('$', '').replace('O', '0')
        s = ''.join(c for c in s if c.isdigit() or c in '.+-')
        try: return float(s) if s else 0
        except ValueError: return 0

Rules:
- Use clean_num() for ALL numeric fields — never float() or int() directly on raw text
- Use .get(key, '') or .get(key) or '' for ALL dict lookups — never bare dict[key]
- Treat these as zero: '', '-', 'N/A', 'n/a', None, 'CP', '*', '**'
- OCR often outputs the letter O where 0 is intended — clean_num() handles this
- When iterating rows, skip silently if required fields are blank rather than raising
- For column alignment: match columns by header name, never by fixed position index
- Strip all whitespace from header names before using them as dict keys
- QA_Note: set to "" normally; set to "yr_sum=X total=Y delta=Z" if
  abs(sum(yr_vals) - total) > 1 and total > 0 (flags year/total mismatches per row)

=== PDF-SPECIFIC REQUIREMENTS (apply when format is pdf_table or pdf_text) ===

**Tabular PDFs (financial table with column headers — the common case):**
Use pdfplumber.extract_words(x_tolerance=3, y_tolerance=3) — NOT extract_text().
Group words into visual rows by rounding w['top'] to the nearest integer.
Assign each word to its column by x0 position using boundaries derived from the
header row (set boundaries at midpoints between column center x-positions).
Concatenate words in the same column bucket WITHOUT spaces — this naturally fixes
OCR space-within-number artifacts ("7" + "5,000" → "75,000").

Column boundary derivation:
  1. Find the header row (contains year column names like "FY 2027")
  2. Record x0 of each header word — these are the column centers
  3. Set boundaries at midpoints; bin 0 (left of first boundary) = project name text
  4. Remaining bins = financial columns in order

Row classification:
  - SKIP rows: line matches header/footer/summary patterns (APPENDIX, FY 20XX-20XX,
    Total Uses, Non-Allocated, page numbers — build a SKIP_LINE_RE for these)
  - DEPT header rows: text-only, name matches known department set (KNOWN_DEPTS)
  - SUBTOTAL rows: first word of line is "$" — parse, do NOT discard (see below)
  - PROJECT rows: has at least one non-empty financial bin
  - NAME continuation rows: text-only, not a dept header — accumulate into pending_name

Subtotal rows as checksums:
  Parse the Total column value from each subtotal row into dept_subtotals[current_dept].
  After all rows are processed, compare dept_project_sums to dept_subtotals and print
  a cross-check table (dept | proj_sum | sub | OK/GAP) with a grand total footer.
  A dept-level gap is expected when a sub-department's rows are tracked separately
  but the PDF rolls them into a parent department's subtotal.

OCR-truncated rows:
  When total==0 but sum(yr_vals) > 0, check if yr_vals[j] == sum(yr_vals[:j]) for
  some j>=2. If so, OCR dropped middle-column dashes and yr_vals[j] is actually the
  Total. Shift it: total = yr_vals[j]; yr_vals[j:] = zeros.

**Split-document PDFs (descriptions and financial table in separate sections):**
When project narratives are in one page range and dollar amounts in another:
  1. Identify both page ranges from the table of contents or section headers
  2. Build a {{normalized_name: description}} lookup from the description section
  3. Extract the financial table from the financial section independently
  4. Join by fuzzy name matching: exact → substring → token overlap ≥ 50% → difflib
Use PyMuPDF (import fitz) for the description section when the PDF has font-encoded
OCR — fitz.open(f)[pg].get_text(sort=True) decodes the font correctly; pdfplumber
will garble the same text into unreadable characters.

**regex-only fallback (use ONLY for narrative/per-project-page PDFs, not tables):**
If the document has one page per project (not a tabular layout), THEN use:
  pdfplumber pg.extract_text() + re.split() to split into per-project blocks.

Write ONLY the Python script, no explanation."""

GUIDE_TEMPLATE = """# Extraction Guide: {agency_id}

## Source File
- **File**: `{filename}`
- **Format**: {format_type}
- **Estimated projects**: {project_count}

## Structure Analysis
{analysis_summary}

## Extraction Script
```python
{script}
```

## Configuration
Key variables to update each year:
{config_notes}

## QA Checks
- [ ] Row count matches source project count
- [ ] Total budget matches published grand total (or dept-subtotals sum if doc total includes non-project rows)
- [ ] Spot-check 3 named projects against source
- [ ] No blank Project_Title values
- [ ] Fund breakdown sums match Total_Project_Budget
- [ ] QA_Note column: zero rows flagged (non-empty = yr_sum ≠ total mismatch)
- [ ] Subtotal cross-check grand total: OK (dept-level gaps expected for sub-dept groupings)

## Adaptation Notes
Next year: update `SOURCE_FILE` path and verify year column names still match.
"""


def _build_guide_block(guide_context: str, guide_mode: str) -> str:
    """Format the guide context section for injection into the prompt."""
    if not guide_context:
        return ""
    if guide_mode == "prior_year":
        return (
            "\n=== PRIOR-YEAR GUIDE (SAME AGENCY) ===\n"
            "This is the complete extraction guide for this same agency's previous CIP.\n"
            "The new CIP should have an identical structure — use this as your primary template.\n"
            "Update only: year ranges, SOURCE_FILE path, and any column names that changed.\n\n"
            f"{guide_context}\n"
        )
    return (
        "\n=== SIMILAR PAST EXTRACTION GUIDES ===\n"
        "These guides show how we extracted similar CIPs (same format / doc type).\n"
        "Study their CONFIGURATION constants — especially SECTION_MARKERS, LEADER_RE, and YEARS —\n"
        "as starting points. Adapt them to match the NEW CIP's structure in STRUCTURE ANALYSIS.\n\n"
        f"{guide_context}\n"
    )


def write_script(
    filename: str,
    analysis: dict[str, Any],
    sample_rows: list[dict],
    full_path: str = "",
    metadata: dict | None = None,
    guide_context: str = "",
    guide_mode: str = "",
) -> str:
    """Ask Claude to generate the extraction script. Returns Python source code."""
    # For PDFs, send the focused project-page sample so Claude can write concrete regex patterns.
    # For tabular files, an empty text_sample is fine (structure comes from column names).
    text_sample = ""
    if metadata:
        text_sample = metadata.get("project_sample_text", "") or metadata.get("full_cip_text", "")
    text_sample = text_sample[:8000]  # keep prompt manageable

    user_msg = USER_TEMPLATE.format(
        guide_block=_build_guide_block(guide_context, guide_mode),
        filename=filename,
        full_path=full_path or filename,
        format_type=analysis.get("format_type", "unknown"),
        approach=analysis.get("extraction_approach", ""),
        analysis_json=json.dumps(analysis, indent=2)[:2000],
        sample_rows=json.dumps(sample_rows[:5], default=str, indent=2)[:1500],
        text_sample=text_sample or "(not available — use column analysis above)",
        final_cols="\n   ".join(FINAL_COLS),
        dollar_unit=analysis.get("dollar_unit", 1),
    )
    raw = ask(user_msg, system=SYSTEM, model=SONNET, max_tokens=4096).strip()

    # Extract code: find the LARGEST ```...``` block in the response.
    # Using the largest block avoids false matches on short snippets inside the guide context.
    blocks = re.findall(r"```(?:python)?\s*\n([\s\S]+?)(?:\n```|$)", raw)
    if blocks:
        candidate = max(blocks, key=len).strip()
        if candidate and not candidate.startswith("```"):
            return candidate

    # Fallback: response has no fences at all — return as-is, or strip stray fence lines
    lines = raw.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def make_guide(
    agency_id: str,
    filename: str,
    analysis: dict[str, Any],
    script: str,
    analysis_summary: str,
) -> str:
    """Generate the _guide.md content."""
    # Pull config vars from script (lines starting with uppercase identifiers)
    import re
    config_lines = [
        l.strip() for l in script.splitlines()
        if re.match(r'^[A-Z_]+ *=', l.strip())
    ]
    config_notes = "\n".join(f"- `{l}`" for l in config_lines[:10])

    return GUIDE_TEMPLATE.format(
        agency_id=agency_id,
        filename=filename,
        format_type=analysis.get("format_type", "unknown"),
        project_count=analysis.get("project_count_estimate", "?"),
        analysis_summary=analysis_summary,
        script=script,
        config_notes=config_notes or "- Update `SOURCE_FILE` path",
    )
