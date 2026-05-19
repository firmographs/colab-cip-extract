"""
Step 2: Ask Claude to analyze the structure of a CIP source file.

Iterates until confidence >= 0.85. Each pass uses a more focused prompt
and a different slice of the document. For PDFs, uses the full CIP section
text stored in metadata by ingest.load_pdf().
"""

from __future__ import annotations

from typing import Any

from .llm import ask_json, ask, SONNET

CONFIDENCE_TARGET = 0.85
MAX_PASSES = 7

SYSTEM = """You are a capital infrastructure plan (CIP) data extraction expert.
You analyze government CIP documents — PDFs, spreadsheets, CSVs — and describe their structure
so a Python script can extract one row per project into a standard schema.

Standard output schema (every _final.csv must contain these columns):
  Project_Index              integer, 1-based row number
  Project_Title              string, human-readable project name
  Project_Number_Primary     string, the main ID/code for this project (e.g. "HW-123")
  Project_Number_All_JSON    JSON array of all codes found (can be ["HW-123"])
  Client_Department          string, owning department/agency
  Total_Project_Budget       number, total dollar amount (all years, all funds combined)
  Fund_Names_JSON            JSON array of funding source names (e.g. ["City", "Federal"])
  Fund_Budgets_JSON          JSON array of amounts matching Fund_Names_JSON
  Yearly_Costs_By_Category_JSON  JSON object {"2026": amount, "2027": amount, ...}
  Description                string, free-text description of the project
  Scope                      string, scope, location, or additional detail

Return ONLY a JSON object — no prose, no markdown fences."""

SCHEMA_JSON = """{
  "format_type": "wide_excel|csv_tabular|pdf_table|long_csv|web_html|other",
  "confidence": 0.0-1.0,
  "header_row_index": null_or_integer,
  "project_name_col": "column name or null",
  "project_id_col": "column name or null",
  "department_col": "column name or null",
  "description_col": "column name or null",
  "scope_col": "column name or null",
  "total_budget_col": "column name or null",
  "year_cols": ["col_name_2026", "col_name_2027"],
  "fund_cols": [{"col": "col_name", "fund_name": "City Notes", "year": null_or_year}],
  "project_count_estimate": integer,
  "published_grand_total": null_or_number,
  "multi_row_projects": true_or_false,
  "quirks": ["list of unusual features"],
  "extraction_approach": "detailed step-by-step description of how to extract rows",
  "notes": "anything the curator should know"
}"""

PASS1_TEMPLATE = """Analyze this CIP source file and return a JSON object describing its structure.

=== FILE: {filename} ===
=== FORMAT: {fmt} ===
=== METADATA: {metadata} ===

=== CONTENT (CIP section) ===
{preview}

=== SAMPLE ROWS (as parsed dicts) ===
{sample_rows}

For published_grand_total: look for a summary table or total line. Return the number only (no $ or commas), or null.

Return this JSON:
{schema}"""

PASS2_TEMPLATE = """Previous analysis of this CIP document had low confidence ({confidence:.0%}).
Study this content carefully and find 3 complete project examples.

=== FILE: {filename} ===

=== DOCUMENT CONTENT (pages {page_start}-{page_end}) ===
{content}

For each project you find, identify:
- Exact text that marks the START of a new project
- Where the project title/name appears
- Where the project number/ID appears
- Where dollar amounts appear and how they are labeled
- Where yearly costs appear
- Where department/fund information appears

Then return updated JSON with higher confidence:
{schema}"""

PASS3_TEMPLATE = """Still analyzing this CIP document. Previous confidence: {confidence:.0%}.

Focus only on this question: what does ONE complete project entry look like?
Quote the EXACT text from the document for a single project from start to finish.

=== DOCUMENT CONTENT (pages {page_start}-{page_end}) ===
{content}

Based on the actual text above, describe the extraction pattern precisely.
Set confidence >= 0.85 only when you can clearly describe how to find every field.

Return updated JSON:
{schema}"""

DEFAULTS = {
    "format_type": "unknown",
    "confidence": 0.5,
    "header_row_index": None,
    "project_name_col": None,
    "project_id_col": None,
    "department_col": None,
    "description_col": None,
    "scope_col": None,
    "total_budget_col": None,
    "year_cols": [],
    "fund_cols": [],
    "project_count_estimate": 0,
    "published_grand_total": None,
    "multi_row_projects": False,
    "quirks": [],
    "extraction_approach": "",
    "notes": "",
}


def _chunk(text: str, start_char: int, length: int = 8000) -> tuple[str, int, int]:
    """Return a chunk of text and approximate page range from [page N] markers."""
    import re
    chunk = text[start_char: start_char + length]
    pages = re.findall(r'\[page (\d+)\]', chunk)
    p_start = int(pages[0]) if pages else 0
    p_end = int(pages[-1]) if pages else 0
    return chunk, p_start, p_end


def analyze(
    filename: str,
    raw_text: str,
    sample_rows: list[dict],
    metadata: dict,
) -> dict[str, Any]:
    """Iterate until confidence >= 0.85. Sends full CIP text to Claude — no chunking."""
    import json

    full_text = metadata.get("full_cip_text", raw_text)
    fmt = metadata.get("format", "unknown")

    # For PDFs, use the focused project-page sample (5 actual project pages, ~6 000 chars).
    # For tabular files, use the raw_text preview as before.
    # Claude only needs to see the PATTERN from a few examples — the generated script
    # will apply regex to the full extracted text, not Claude.
    project_sample = metadata.get("project_sample_text") or raw_text
    preview = project_sample[:8000]

    # Pass 1 — targeted sample of real project pages
    print("  Pass 1...")
    user_msg = PASS1_TEMPLATE.format(
        filename=filename,
        fmt=fmt,
        metadata=json.dumps(
            {k: v for k, v in metadata.items()
             if k not in ("full_cip_text", "project_sample_text")},
            default=str)[:400],
        preview=preview,
        sample_rows=json.dumps(sample_rows[:10], default=str, indent=2)[:1000],
        schema=SCHEMA_JSON,
    )
    result = {**DEFAULTS, **ask_json(user_msg, system=SYSTEM, model=SONNET, max_tokens=4096)}

    # Subsequent passes: first exhaust project_sample, then jump to evenly-spaced positions
    # across full_cip_text (1/4, 1/2, 3/4, end).  Linear scanning from the start of
    # full_cip_text wastes passes on intro/summary pages before reaching the project sheets.
    pass_num = 2
    scan_text = project_sample
    scan_offset = 8000
    full_jump_offsets: list[int] = []  # populated when we switch to full_text

    while result["confidence"] < CONFIDENCE_TARGET and pass_num <= MAX_PASSES:
        print(f"  Pass {pass_num} (confidence {result['confidence']:.0%} — continuing)...")

        chunk, p_start, p_end = _chunk(scan_text, scan_offset, length=8000)

        if not chunk.strip():
            if not full_jump_offsets and full_text and len(full_text) > len(project_sample):
                # Project sample exhausted — build evenly-spaced jump offsets across full text
                fl = len(full_text)
                full_jump_offsets = [
                    fl // 4,
                    fl // 2,
                    fl * 3 // 4,
                    max(0, fl - 8000),
                ]
                scan_text = full_text
                print(f"  (project sample exhausted — sampling full CIP text at 4 positions)")
            if full_jump_offsets:
                scan_offset = full_jump_offsets.pop(0)
                chunk, p_start, p_end = _chunk(scan_text, scan_offset, length=8000)
                if not chunk.strip():
                    continue
            else:
                break

        template = PASS2_TEMPLATE if pass_num == 2 else PASS3_TEMPLATE
        user_msg = template.format(
            filename=filename,
            confidence=result["confidence"],
            content=chunk,
            page_start=p_start,
            page_end=p_end,
            schema=SCHEMA_JSON,
        )

        new_result = ask_json(user_msg, system=SYSTEM, model=SONNET, max_tokens=4096)
        # Merge: keep best fields; accept any improvement in confidence
        for k, v in new_result.items():
            if v and (not result.get(k) or new_result.get("confidence", 0) > result.get("confidence", 0)):
                result[k] = v

        scan_offset += 8000
        pass_num += 1

    conf_label = "HIGH" if result["confidence"] >= 0.85 else "MEDIUM" if result["confidence"] >= 0.65 else "LOW"
    print(f"  Analysis complete: {conf_label} confidence ({result['confidence']:.0%}) after {pass_num - 1} pass(es).")
    return {**DEFAULTS, **result}


def summarize(analysis: dict[str, Any]) -> str:
    """Return a human-readable summary of the structure analysis."""
    lines = [
        f"Format:          {analysis['format_type']} (confidence: {analysis['confidence']:.0%})",
        f"Project name:    {analysis['project_name_col']}",
        f"Project ID:      {analysis['project_id_col']}",
        f"Department:      {analysis['department_col']}",
        f"Total budget:    {analysis['total_budget_col']}",
        f"Year columns:    {analysis['year_cols']}",
        f"Fund columns:    {len(analysis['fund_cols'])} found",
        f"Est. projects:   {analysis['project_count_estimate']}",
        f"Published total: {'${:,.0f}'.format(analysis['published_grand_total']) if analysis.get('published_grand_total') else '(not found in preview)'}",
        f"Multi-row:       {analysis['multi_row_projects']}",
        f"Approach:        {analysis['extraction_approach']}",
    ]
    if analysis["quirks"]:
        lines.append(f"Quirks:          {'; '.join(analysis['quirks'])}")
    if analysis["notes"]:
        lines.append(f"Notes:           {analysis['notes']}")
    return "\n".join(lines)
