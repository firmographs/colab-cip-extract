"""
Step 6: Exhaustive QA — source inventory vs extracted inventory.

Per the WI §6:
- Build source inventory: ID + title + total amount for every project in the raw source
- Build extract inventory: same from _final.csv rows
- One-to-one matching: exact ID match, fuzzy title ≥85%, dollar tolerance $1 for amounts ≥$1M
- Report every discrepancy; NEVER auto-correct (WI §6.6)

Two modes:
  qa_totals()    — fast: compare aggregate sums only (for PDF-extracted data)
  qa_full()      — thorough: one-to-one row matching (for tabular data)
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_num(v: Any) -> float:
    if v is None:
        return 0.0
    try:
        s = re.sub(r"[,$\s]", "", str(v))
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _title_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ---------------------------------------------------------------------------
# Source inventory builder (for tabular files: CSV/Excel)
# ---------------------------------------------------------------------------

def build_source_inventory(rows: list[dict], id_col: str, title_col: str, amount_col: str) -> list[dict]:
    """Build source inventory from raw rows."""
    inv = []
    for i, r in enumerate(rows, 1):
        inv.append({
            "index":  i,
            "id":     str(r.get(id_col, "")).strip(),
            "title":  str(r.get(title_col, "")).strip(),
            "amount": _to_num(r.get(amount_col, 0)),
        })
    return inv


def build_extract_inventory(rows: list[dict]) -> list[dict]:
    """Build extract inventory from _final.csv rows."""
    inv = []
    for r in rows:
        inv.append({
            "index":  r.get("Project_Index", ""),
            "id":     str(r.get("Project_Number_Primary", "")).strip(),
            "title":  str(r.get("Project_Title", "")).strip(),
            "amount": _to_num(r.get("Total_Project_Budget", 0)),
        })
    return inv


# ---------------------------------------------------------------------------
# Full one-to-one QA
# ---------------------------------------------------------------------------

def qa_full(
    source_inv: list[dict],
    extract_inv: list[dict],
    id_match: bool = True,
    title_threshold: float = 0.85,
    amount_tolerance: float = 1.0,
    large_amount_threshold: float = 1_000_000,
) -> dict:
    """
    Match source_inv rows against extract_inv rows.
    Returns a dict with: ok, warnings, errors, matches, unmatched_source, unmatched_extract
    """
    result = {
        "ok": [],
        "warnings": [],
        "errors": [],
        "matched_pairs": [],
        "unmatched_source": [],
        "unmatched_extract": [],
    }

    used_extract = set()

    for src in source_inv:
        best_match = None
        best_score = 0.0

        for i, ext in enumerate(extract_inv):
            if i in used_extract:
                continue

            # ID match (if IDs are present)
            if id_match and src["id"] and ext["id"]:
                if src["id"].upper() == ext["id"].upper():
                    best_match, best_score = i, 1.0
                    break

            # Title fuzzy match
            sim = _title_sim(src["title"], ext["title"])
            if sim > best_score:
                best_score, best_match = sim, i

        if best_match is None or best_score < title_threshold:
            result["errors"].append({
                "type": "UNMATCHED_SOURCE",
                "source": src,
                "best_score": best_score,
            })
            result["unmatched_source"].append(src)
            continue

        used_extract.add(best_match)
        ext = extract_inv[best_match]
        result["matched_pairs"].append({"source": src, "extract": ext, "title_sim": best_score})

        # Amount check
        s_amt, e_amt = src["amount"], ext["amount"]
        threshold = amount_tolerance if max(s_amt, e_amt) >= large_amount_threshold else amount_tolerance
        if abs(s_amt - e_amt) > threshold:
            result["errors"].append({
                "type": "AMOUNT_DIFF",
                "source": src,
                "extract": ext,
                "diff": e_amt - s_amt,
            })
        else:
            result["ok"].append(src["title"][:60])

    # Anything in extract that wasn't matched
    for i, ext in enumerate(extract_inv):
        if i not in used_extract:
            result["unmatched_extract"].append(ext)
            result["warnings"].append({
                "type": "EXTRA_EXTRACT",
                "extract": ext,
            })

    return result


# ---------------------------------------------------------------------------
# Totals-only QA (for PDF extracts or when no row-level source is available)
# ---------------------------------------------------------------------------

def qa_totals(
    expected: dict[str, float],
    extracted_rows: list[dict],
    tolerance: float = 1.0,
) -> dict:
    """
    Compare aggregate sums.
    expected: {"total": X, "2026": Y, "2027": Z, ...}
    """
    result = {"ok": [], "errors": [], "warnings": []}

    # Compute extracted totals
    actual: dict[str, float] = {}

    # Grand total
    total = sum(_to_num(r.get("Total_Project_Budget", 0)) for r in extracted_rows)
    actual["total"] = total

    # Year totals from Yearly_Costs_By_Category_JSON
    for r in extracted_rows:
        try:
            yearly = json.loads(r.get("Yearly_Costs_By_Category_JSON", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            yearly = {}
        for yr, amt in yearly.items():
            actual[str(yr)] = actual.get(str(yr), 0.0) + _to_num(amt)

    for key, exp_val in expected.items():
        act_val = actual.get(str(key), 0.0)
        diff = act_val - exp_val
        label = f"{key}: expected {exp_val:,.0f}, got {act_val:,.0f} (diff {diff:+,.0f})"
        if abs(diff) <= tolerance:
            result["ok"].append(label)
        else:
            result["errors"].append({"key": key, "expected": exp_val, "actual": act_val, "diff": diff})

    return result


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def format_report(qa_result: dict) -> str:
    lines = []

    ok = qa_result.get("ok", [])
    errs = qa_result.get("errors", [])
    warns = qa_result.get("warnings", [])

    lines.append(f"QA RESULT:  {len(ok)} OK  |  {len(errs)} ERR  |  {len(warns)} WARN")

    if errs:
        lines.append("\n--- ERRORS (must review) ---")
        for e in errs:
            t = e.get("type", "ERROR")
            if t == "AMOUNT_DIFF":
                src = e["source"]
                lines.append(
                    f"  DIFF  {src['id']:15s}  {src['title'][:40]:40s}  "
                    f"src={e['source']['amount']:>15,.0f}  "
                    f"ext={e['extract']['amount']:>15,.0f}  "
                    f"diff={e['diff']:+,.0f}"
                )
            elif t == "UNMATCHED_SOURCE":
                src = e["source"]
                lines.append(f"  UNMATCHED  {src['id']:15s}  {src['title'][:50]}  (best sim={e['best_score']:.0%})")
            else:
                lines.append(f"  {e}")

    if warns:
        lines.append("\n--- WARNINGS ---")
        for w in warns:
            if isinstance(w, dict):
                t = w.get("type", "WARN")
                if t == "EXTRA_EXTRACT":
                    ext = w["extract"]
                    lines.append(f"  EXTRA  {ext['id']:15s}  {ext['title'][:50]}")
            else:
                lines.append(f"  {w}")

    unmatched = qa_result.get("unmatched_source", [])
    if unmatched:
        lines.append(f"\n  {len(unmatched)} source projects not found in extract.")

    return "\n".join(lines)
