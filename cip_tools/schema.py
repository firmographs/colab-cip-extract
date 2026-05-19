"""
Standard output schema for _final.csv files.

FINAL_COLS is the ordered list of columns in every _final.csv.
gate_cell3() and gate_cell7() are validation checks — raise ValueError
with a human-readable message on failure. Warnings are printed.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ---------------------------------------------------------------------------
# Standard schema
# ---------------------------------------------------------------------------

FINAL_COLS = [
    "Project_Index",
    "Project_Title",
    "Project_Number_Primary",
    "Project_Number_All_JSON",
    "Client_Department",
    "Total_Project_Budget",
    "Fund_Names_JSON",
    "Fund_Budgets_JSON",
    "Yearly_Costs_By_Category_JSON",
    "Description",
    "Scope",
]

REQUIRED_COLS = {
    "Project_Index",
    "Project_Title",
    "Total_Project_Budget",
}

# ---------------------------------------------------------------------------
# Cell 3 gate — check at ingestion, before any LLM calls
# ---------------------------------------------------------------------------

def gate_cell3(rows: list[dict[str, Any]], source_label: str = "", fmt: str | None = None) -> list[str]:
    """
    Run critical ingestion checks. Returns a list of warning strings.
    Raises ValueError for fatal problems (stop everything).
    PDFs are document-oriented; row checks don't apply — Claude works from raw text.
    """
    warnings = []
    label = f"[{source_label}] " if source_label else ""

    if fmt == "pdf":
        if not rows:
            warnings.append(f"{label}PDF: no tables detected — Claude will work from page text.")
        return warnings

    if not rows:
        raise ValueError(f"{label}Empty file — zero rows extracted.")

    n = len(rows)
    if n < 3:
        raise ValueError(f"{label}Only {n} row(s) — likely a header-detection failure.")

    if n < 10:
        warnings.append(f"{label}Only {n} rows — unusually small CIP, verify source.")

    # Check that we have at least one name-like column
    cols = list(rows[0].keys())
    name_cols = [c for c in cols if re.search(r'title|name|project|description', c, re.I)]
    if not name_cols:
        raise ValueError(
            f"{label}No column with 'title', 'name', 'project', or 'description' found. "
            f"Header detection may have failed. Columns: {cols[:10]}"
        )

    # Check that we have at least one dollar/value column
    value_cols = [c for c in cols if re.search(r'budget|cost|amount|total|value|fund|\$|fy\d{4}|\d{4}', c, re.I)]
    if not value_cols:
        raise ValueError(
            f"{label}No monetary column found. Cannot confirm this is a CIP. "
            f"Columns: {cols[:10]}"
        )

    # Check for all-numeric column names (header shifted by one row)
    numeric_col_names = [c for c in cols if re.fullmatch(r'\d+', str(c).strip())]
    if len(numeric_col_names) > 2:
        raise ValueError(
            f"{label}{len(numeric_col_names)} all-numeric column names — header row likely not detected. "
            f"Sample: {numeric_col_names[:5]}"
        )

    # Blank name rate
    first_name_col = name_cols[0]
    blank_names = sum(1 for r in rows if not str(r.get(first_name_col, "")).strip())
    blank_rate = blank_names / n
    if blank_rate > 0.5:
        raise ValueError(
            f"{label}{blank_rate:.0%} of rows have blank '{first_name_col}' — "
            "extraction failure or wrong column."
        )
    if blank_rate > 0.1:
        warnings.append(
            f"{label}{blank_rate:.0%} of rows have blank '{first_name_col}' — "
            "check for subtotals or header rows mixed in."
        )

    # Duplicate row check
    row_reprs = [str(sorted(r.items())) for r in rows]
    n_dupes = n - len(set(row_reprs))
    if n_dupes / n > 0.2:
        warnings.append(
            f"{label}{n_dupes}/{n} duplicate rows ({n_dupes/n:.0%}) — "
            "possible double-extraction or section headers repeated."
        )

    return warnings


# ---------------------------------------------------------------------------
# Cell 7 gate — check after transform, before LLM calls
# ---------------------------------------------------------------------------

def gate_cell7(rows: list[dict[str, Any]], source_label: str = "") -> list[str]:
    """
    Run post-transform checks on standard schema rows.
    Returns warnings; raises ValueError for fatal issues.
    """
    warnings = []
    label = f"[{source_label}] " if source_label else ""
    n = len(rows)

    # Project_Index must be present and sequential
    indices = [r.get("Project_Index") for r in rows]
    none_idx = sum(1 for i in indices if i is None or str(i).strip() == "")
    if none_idx > 0:
        raise ValueError(f"{label}{none_idx} rows missing Project_Index.")

    idx_vals = [int(i) for i in indices if str(i).strip().isdigit()]
    if len(idx_vals) == n:
        if sorted(idx_vals) != list(range(1, n + 1)) and sorted(idx_vals) != list(range(0, n)):
            warnings.append(f"{label}Project_Index values are not sequential 1..{n}.")

    # Project_Title uniqueness and fill
    titles = [str(r.get("Project_Title", "")).strip() for r in rows]
    blank_titles = sum(1 for t in titles if not t)
    if blank_titles > 0:
        raise ValueError(f"{label}{blank_titles} rows have blank Project_Title.")
    dupes = n - len(set(titles))
    if dupes > 0:
        warnings.append(
            f"{label}{dupes} duplicate Project_Title values — "
            "verify these are truly distinct projects."
        )

    # All-zero budget rows
    zero_budget = sum(
        1 for r in rows
        if _to_num(r.get("Total_Project_Budget", 0)) == 0
    )
    if zero_budget / n > 0.3:
        warnings.append(
            f"{label}{zero_budget}/{n} rows have zero Total_Project_Budget — "
            "check for CP (Continuing Program) rows or extraction gaps."
        )

    # Year-sum vs total consistency (when yearly JSON is present)
    mismatches = 0
    for r in rows:
        yearly = _parse_yearly(r.get("Yearly_Costs_By_Category_JSON", ""))
        total = _to_num(r.get("Total_Project_Budget", 0))
        if yearly and total > 0:
            year_sum = sum(yearly.values())
            if year_sum > 0 and abs(year_sum - total) / total > 0.05:
                mismatches += 1
    if mismatches > 0:
        warnings.append(
            f"{label}{mismatches} rows where yearly sum differs >5% from Total_Project_Budget."
        )

    return warnings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_num(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("$", "").strip() or 0)
    except ValueError:
        return 0.0


def _parse_yearly(v: Any) -> dict[str, float]:
    """Return {year_str: total_amount} from Yearly_Costs_By_Category_JSON."""
    if not v:
        return {}
    try:
        obj = json.loads(v) if isinstance(v, str) else v
    except (json.JSONDecodeError, TypeError):
        return {}

    if isinstance(obj, dict):
        return {k: _to_num(val) for k, val in obj.items()}

    if isinstance(obj, list):
        # List-of-dicts format: [{"year": 2026, "cn": 100, "federal": 0, ...}]
        result = {}
        for item in obj:
            if isinstance(item, dict):
                yr = str(item.get("year", ""))
                total = sum(_to_num(v) for k, v in item.items() if k != "year")
                if yr:
                    result[yr] = result.get(yr, 0) + total
        return result

    return {}
