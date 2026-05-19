"""
Step 7: Structured validation — schema checks, distributions, spot checks.

Runs after Step 6 QA. Reports summary statistics and any structural issues.
Does NOT auto-fix anything (WI §6.6).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from .schema import FINAL_COLS, REQUIRED_COLS, gate_cell7, _to_num


def run(rows: list[dict], source_label: str = "") -> str:
    """
    Full validation report. Returns formatted string.
    Raises ValueError on fatal structural failures.
    """
    lines = []
    n = len(rows)
    label = f"[{source_label}] " if source_label else ""

    lines.append(f"=== VALIDATION REPORT: {source_label} ===")
    lines.append(f"Total rows: {n}")

    # --- Schema presence ---
    cols_present = set(rows[0].keys()) if rows else set()
    missing_required = REQUIRED_COLS - cols_present
    if missing_required:
        raise ValueError(f"{label}Missing required columns: {missing_required}")

    missing_standard = set(FINAL_COLS) - cols_present
    if missing_standard:
        lines.append(f"  Missing standard cols (non-fatal): {sorted(missing_standard)}")
    else:
        lines.append("  All standard columns present.")

    # --- Run Cell 7 gate ---
    try:
        warnings = gate_cell7(rows, source_label)
        for w in warnings:
            lines.append(f"  WARN: {w}")
    except ValueError as e:
        lines.append(f"  FATAL: {e}")
        raise

    # --- Budget distribution ---
    budgets = [_to_num(r.get("Total_Project_Budget", 0)) for r in rows]
    budgets_nonzero = [b for b in budgets if b > 0]
    grand_total = sum(budgets)
    lines.append(f"\nBudget summary:")
    lines.append(f"  Grand total:     ${grand_total:>20,.0f}")
    lines.append(f"  Non-zero rows:   {len(budgets_nonzero):>6} / {n}")
    if budgets_nonzero:
        lines.append(f"  Min (non-zero):  ${min(budgets_nonzero):>20,.0f}")
        lines.append(f"  Max:             ${max(budgets_nonzero):>20,.0f}")
        lines.append(f"  Mean:            ${sum(budgets_nonzero)/len(budgets_nonzero):>20,.0f}")

    # --- Department distribution ---
    depts = [str(r.get("Client_Department", "")).strip() for r in rows]
    dept_counter = Counter(d for d in depts if d)
    if dept_counter:
        lines.append(f"\nDepartments ({len(dept_counter)} unique):")
        for dept, cnt in dept_counter.most_common(10):
            lines.append(f"  {cnt:4d}  {dept[:60]}")
        if len(dept_counter) > 10:
            lines.append(f"  ... and {len(dept_counter)-10} more")

    # --- Fund types ---
    all_funds: Counter = Counter()
    for r in rows:
        try:
            names = json.loads(r.get("Fund_Names_JSON", "[]") or "[]")
            budgets_j = json.loads(r.get("Fund_Budgets_JSON", "[]") or "[]")
            for name, amt in zip(names, budgets_j):
                all_funds[name] += _to_num(amt)
        except (json.JSONDecodeError, TypeError):
            pass
    if all_funds:
        lines.append(f"\nFund breakdown (grand total by fund):")
        for fund, amt in sorted(all_funds.items(), key=lambda x: -x[1]):
            lines.append(f"  {fund:<30s}  ${amt:>20,.0f}")

    # --- Year coverage ---
    year_totals: dict[str, float] = {}
    for r in rows:
        try:
            yearly = json.loads(r.get("Yearly_Costs_By_Category_JSON", "{}") or "{}")
            if isinstance(yearly, dict):
                for yr, amt in yearly.items():
                    year_totals[str(yr)] = year_totals.get(str(yr), 0) + _to_num(amt)
        except (json.JSONDecodeError, TypeError):
            pass
    if year_totals:
        lines.append(f"\nYearly totals:")
        for yr in sorted(year_totals):
            lines.append(f"  {yr}:  ${year_totals[yr]:>20,.0f}")

    # --- Blank field rates ---
    lines.append(f"\nField fill rates:")
    for col in FINAL_COLS:
        filled = sum(1 for r in rows if str(r.get(col, "")).strip())
        pct = filled / n * 100 if n else 0
        flag = "  " if pct >= 80 else "! " if pct >= 50 else "!!"
        lines.append(f"  {flag}{col:<40s}  {filled:5d}/{n}  ({pct:.0f}%)")

    lines.append("\n=== END VALIDATION ===")
    return "\n".join(lines)
