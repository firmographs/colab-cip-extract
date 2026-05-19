"""
Step 2: Ask Claude to analyze the structure of a CIP source file.

Sends N evenly-spaced samples from the document in a single call.
N scales with document size: max(1, min(6, round(cip_pages / 15))).
For PDFs, uses the full CIP section text stored in metadata by ingest.load_pdf().
"""

from __future__ import annotations

from typing import Any

from .llm import ask_json, SONNET

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
  "dollar_unit": 1,
  "multi_row_projects": true_or_false,
  "quirks": ["list of unusual features"],
  "extraction_approach": "detailed step-by-step description of how to extract rows",
  "notes": "anything the curator should know"
}"""

ANALYSIS_TEMPLATE = """Analyze this CIP source file and return a JSON object describing its structure.

=== FILE: {filename} ===
=== FORMAT: {fmt} ===
=== METADATA: {metadata} ===

=== SAMPLE ROWS (as parsed dicts) ===
{sample_rows}

{content_sections}

For published_grand_total: look for a summary table or total line. Return the number only (no $ or commas), or null.
For dollar_unit: the regex pre-scan at intake set dollar_unit_hint={dollar_unit_hint}. Treat this as strong
evidence. Also scan column headers, footnotes, and table titles for phrases like "(in thousands)", "($000s)",
"amounts in thousands", "in millions", "$ millions", "000s omitted". Consider typical project sizes —
if an infrastructure project shows a total of "500" it is almost certainly in thousands ($500,000), not $500.
Set dollar_unit to 1 (full dollars), 1000 (thousands), or 1000000 (millions).
For extraction_approach: describe step-by-step how a Python script should extract one row per project.
Set extraction_approach and other fields based on the MOST REPRESENTATIVE content section(s) you find.

Return this JSON:
{schema}"""

DEFAULTS = {
    "format_type": "unknown",
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
    "dollar_unit": 1,
    "multi_row_projects": False,
    "quirks": [],
    "extraction_approach": "",
    "notes": "",
}


def analyze(
    filename: str,
    raw_text: str,
    sample_rows: list[dict],
    metadata: dict,
) -> dict[str, Any]:
    """Send N evenly-spaced document samples in one call. N scales with doc size."""
    import json

    full_text = metadata.get("full_cip_text") or metadata.get("project_sample_text") or raw_text
    fmt = metadata.get("format", "unknown")

    # Scale N to document size: 1 sample per ~15 CIP pages, capped 1–6
    cip_pages = metadata.get("cip_pages_collected", 0)
    if cip_pages == 0:
        # Non-PDF: single sample is sufficient
        n_samples = 1
    else:
        n_samples = max(1, min(6, round(cip_pages / 15)))

    # Budget ~30 000 chars total across all samples
    sample_chars = min(30000 // n_samples, 8000)
    total_len = len(full_text)

    # Build evenly-spaced start positions (0%, 1/(n-1)%, ..., 100% - 1 window)
    if n_samples == 1:
        positions = [0]
    else:
        positions = [
            int(i * (total_len - sample_chars) / (n_samples - 1))
            for i in range(n_samples)
        ]
        positions = [max(0, min(p, total_len - sample_chars)) for p in positions]

    # Extract samples and label them
    content_sections = []
    for i, start in enumerate(positions):
        chunk = full_text[start: start + sample_chars]
        if not chunk.strip():
            continue
        pct = int(start / total_len * 100) if total_len else 0
        label = f"=== CONTENT SAMPLE {i+1}/{n_samples} (document position ~{pct}%) ==="
        content_sections.append(f"{label}\n{chunk}")

    print(f"  Sending {len(content_sections)} sample(s) "
          f"({sample_chars} chars each, {cip_pages} CIP pages)...")

    dollar_unit_hint = metadata.get("dollar_unit_hint", 1)
    user_msg = ANALYSIS_TEMPLATE.format(
        filename=filename,
        fmt=fmt,
        metadata=json.dumps(
            {k: v for k, v in metadata.items()
             if k not in ("full_cip_text", "project_sample_text")},
            default=str)[:400],
        sample_rows=json.dumps(sample_rows[:10], default=str, indent=2)[:1000],
        content_sections="\n\n".join(content_sections),
        schema=SCHEMA_JSON,
        dollar_unit_hint=dollar_unit_hint,
    )

    # Pre-seed dollar_unit from the regex hint; Claude can still override with a different value
    seed = {**DEFAULTS, "dollar_unit": dollar_unit_hint}
    result = {**seed, **ask_json(user_msg, system=SYSTEM, model=SONNET, max_tokens=4096)}
    print(f"  Analysis complete.")
    return {**DEFAULTS, **result}


def summarize(analysis: dict[str, Any]) -> str:
    """Return a human-readable summary of the structure analysis."""
    lines = [
        f"Format:          {analysis['format_type']}",
        f"Dollar unit:     x{analysis.get('dollar_unit', 1)} (raw values multiplied to normalize to full $)",
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
