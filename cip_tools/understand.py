"""
Step 2: Ask Claude to analyze the structure of a CIP source file.

Returns a StructureAnalysis dict describing the file's layout so that
Step 3 can write a targeted extraction script.
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

USER_TEMPLATE = """Analyze this CIP source file and return a JSON object describing its structure.

=== FILE: {filename} ===
=== FORMAT: {fmt} ===
=== METADATA: {metadata} ===

=== CONTENT PREVIEW (first ~60 rows or 3000 chars) ===
{preview}

=== SAMPLE ROWS (first up to 10, as parsed dicts) ===
{sample_rows}

Return this JSON structure:
{{
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
  "fund_cols": [
    {{"col": "col_name", "fund_name": "City Notes", "year": null_or_year}}
  ],
  "project_count_estimate": integer,
  "multi_row_projects": true_or_false,
  "quirks": ["list of unusual features"],
  "extraction_approach": "one brief sentence describing how to extract rows",
  "notes": "anything the curator should know"
}}"""


def analyze(
    filename: str,
    raw_text: str,
    sample_rows: list[dict],
    metadata: dict,
) -> dict[str, Any]:
    """Call Claude to analyze the CIP file structure. Returns a StructureAnalysis dict."""
    import json

    user_msg = USER_TEMPLATE.format(
        filename=filename,
        fmt=metadata.get("format", "unknown"),
        metadata=json.dumps(metadata, default=str)[:500],
        preview=raw_text[:2500],
        sample_rows=json.dumps(sample_rows[:10], default=str, indent=2)[:1500],
    )

    result = ask_json(user_msg, system=SYSTEM, model=SONNET, max_tokens=2048)

    # Ensure required keys exist with defaults
    defaults = {
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
        "multi_row_projects": False,
        "quirks": [],
        "extraction_approach": "",
        "notes": "",
    }
    return {**defaults, **result}


def summarize(analysis: dict[str, Any]) -> str:
    """Return a human-readable summary of the structure analysis for the confirmation gate."""
    lines = [
        f"Format:          {analysis['format_type']} (confidence: {analysis['confidence']:.0%})",
        f"Project name:    {analysis['project_name_col']}",
        f"Project ID:      {analysis['project_id_col']}",
        f"Department:      {analysis['department_col']}",
        f"Total budget:    {analysis['total_budget_col']}",
        f"Year columns:    {analysis['year_cols']}",
        f"Fund columns:    {len(analysis['fund_cols'])} found",
        f"Est. projects:   {analysis['project_count_estimate']}",
        f"Multi-row:       {analysis['multi_row_projects']}",
        f"Approach:        {analysis['extraction_approach']}",
    ]
    if analysis["quirks"]:
        lines.append(f"Quirks:          {'; '.join(analysis['quirks'])}")
    if analysis["notes"]:
        lines.append(f"Notes:           {analysis['notes']}")
    return "\n".join(lines)
