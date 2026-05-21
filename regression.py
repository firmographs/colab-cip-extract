"""
CIP Extraction Regression Harness
==================================
For each agency folder in weeks 13-20 that has a guide, final CSV, and OCR PDF:
  1. Run the pipeline in --auto mode against the OCR PDF
  2. Score the output against the gold _final.csv
  3. Append one row to regression_results.csv

Usage:
    python regression.py [--weeks w1326,w1426,...] [--limit N] [--out-dir DIR]
                         [--resume] [--data-root "G:/Shared drives/0_cip_data/2026"]

Run on GitHub Actions for full corpus (330+ agencies takes ~5-6 hours).
Commit partial results after each agency (--commit flag).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ALL_WEEKS = ["w1326", "w1426", "w1526", "w1626", "w1726", "w1826", "w1926", "w2026"]

DATA_ROOT = Path(r"G:\Shared drives\0_cip_data\2026")

RESULTS_FILE = Path(__file__).parent / "regression_results.csv"

RESULT_COLS = [
    "run_at_utc",
    "week",
    "agency_folder",
    "agency_id",
    "status",           # ok | failed | skipped
    "failure_stage",    # ingest | analyze | design | run | qa | (blank if ok)
    "failure_reason",   # short error message
    "gold_row_count",
    "extracted_row_count",
    "row_count_match",  # yes | no | na
    "title_match_rate", # 0.0-1.0
    "dollar_total_gold",
    "dollar_total_extracted",
    "dollar_pct_error", # abs((extracted-gold)/gold) — blank if gold=0
    "elapsed_seconds",
]

TITLE_SIM_THRESHOLD = 0.75  # looser than qa.py's 0.85 — gold CSVs vary in title cleaning


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_num(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(re.sub(r"[,$\s]", "", str(v)) or 0)
    except ValueError:
        return 0.0


def _title_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")


def _load_gold_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _gold_title_col(rows: list[dict]) -> str | None:
    """Find the project title column in a gold CSV (varies by agency)."""
    for candidate in ["Project_Title", "Project_Name", "ProjectTitle", "project_title",
                      "Project Name", "Project", "Title", "Description"]:
        if rows and candidate in rows[0]:
            return candidate
    # Fall back to any column with 'title' or 'name' or 'project'
    if rows:
        for col in rows[0]:
            if re.search(r"title|name|project|description", col, re.I):
                return col
    return None


def _gold_budget_col(rows: list[dict]) -> str | None:
    """Find the total budget column in a gold CSV."""
    for candidate in ["Total_Project_Budget", "Total", "Budget", "TotalBudget",
                      "Estimated_Total_Cost", "TOTAL", "total_budget"]:
        if rows and candidate in rows[0]:
            return candidate
    if rows:
        for col in rows[0]:
            if re.search(r"total|budget|cost|amount", col, re.I):
                return col
    return None


def score_against_gold(extracted: list[dict], gold: list[dict]) -> dict:
    """
    Compare extracted rows against gold CSV.
    Returns dict with title_match_rate, dollar_total_gold, dollar_total_extracted,
    dollar_pct_error.
    """
    title_col = _gold_title_col(gold)
    budget_col = _gold_budget_col(gold)

    gold_titles = [str(r.get(title_col, "")).strip() for r in gold] if title_col else []
    ext_titles  = [str(r.get("Project_Title", "")).strip() for r in extracted]

    # Title match rate: for each gold title, find best match in extracted
    matched = 0
    for gt in gold_titles:
        if not gt:
            continue
        best = max((_title_sim(gt, et) for et in ext_titles), default=0.0)
        if best >= TITLE_SIM_THRESHOLD:
            matched += 1
    title_match_rate = matched / len(gold_titles) if gold_titles else None

    # Dollar totals
    dollar_gold = sum(
        _to_num(r.get(budget_col, 0)) for r in gold
    ) if budget_col else 0.0

    dollar_extracted = sum(
        _to_num(r.get("Total_Project_Budget", 0)) for r in extracted
    )

    if dollar_gold > 0:
        dollar_pct_error = abs(dollar_extracted - dollar_gold) / dollar_gold
    else:
        dollar_pct_error = None

    return {
        "title_match_rate": round(title_match_rate, 3) if title_match_rate is not None else "",
        "dollar_total_gold": dollar_gold,
        "dollar_total_extracted": dollar_extracted,
        "dollar_pct_error": round(dollar_pct_error, 4) if dollar_pct_error is not None else "",
    }


# ---------------------------------------------------------------------------
# Agency discovery
# ---------------------------------------------------------------------------

def find_eligible_agencies(data_root: Path, weeks: list[str]) -> list[dict]:
    """
    Scan week folders and return list of agency dicts with paths to:
      pdf, guide, gold_final
    Only returns agencies that have all three.
    """
    agencies = []
    for week in weeks:
        week_dir = data_root / week
        if not week_dir.exists():
            continue
        for agency_dir in sorted(week_dir.iterdir()):
            if not agency_dir.is_dir() or not agency_dir.name.startswith("2026"):
                continue
            # Find files
            pdfs   = list(agency_dir.glob("*_ocr.pdf"))
            guides = list(agency_dir.glob("*_guide.md"))
            finals = list(agency_dir.glob("*_final.csv"))

            # Skip pre_backfill files etc — prefer shortest-named final
            finals = [f for f in finals if "pre_backfill" not in f.name
                      and "mapped" not in f.name]

            if not (pdfs and guides and finals):
                continue

            # Derive agency_id from folder name
            m = re.search(r" - (.+)$", agency_dir.name)
            agency_id = m.group(1) if m else agency_dir.name

            agencies.append({
                "week": week,
                "agency_folder": agency_dir.name,
                "agency_id": agency_id,
                "pdf_path": pdfs[0],
                "guide_path": guides[0],
                "gold_path": finals[0],
                "agency_dir": agency_dir,
            })
    return agencies


# ---------------------------------------------------------------------------
# Already-run check
# ---------------------------------------------------------------------------

def load_completed_ids(results_file: Path) -> set[str]:
    """Return set of agency_folder values already in results file."""
    if not results_file.exists():
        return set()
    with open(results_file, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {r["agency_folder"] for r in rows if r.get("status") == "ok"}


# ---------------------------------------------------------------------------
# Write one result row
# ---------------------------------------------------------------------------

def append_result(results_file: Path, row: dict) -> None:
    exists = results_file.exists()
    with open(results_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Git commit one file
# ---------------------------------------------------------------------------

def git_commit(repo_root: Path, filepath: Path, message: str) -> None:
    try:
        subprocess.run(
            ["git", "add", str(filepath)],
            cwd=repo_root, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_root, check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
        pass  # Nothing to commit or git error — non-fatal


# ---------------------------------------------------------------------------
# Run one agency through the pipeline
# ---------------------------------------------------------------------------

def run_agency(agency: dict, repo_root: Path, auto: bool = True) -> dict:
    """
    Run the cip pipeline against one agency's OCR PDF.
    Returns a result dict ready for append_result().
    """
    t0 = time.time()
    out_dir = repo_root / "regression_outputs" / agency["week"] / agency["agency_id"]
    out_dir.mkdir(parents=True, exist_ok=True)

    base = {
        "run_at_utc": _now_utc(),
        "week": agency["week"],
        "agency_folder": agency["agency_folder"],
        "agency_id": agency["agency_id"],
        "status": "failed",
        "failure_stage": "",
        "failure_reason": "",
        "gold_row_count": "",
        "extracted_row_count": "",
        "row_count_match": "",
        "title_match_rate": "",
        "dollar_total_gold": "",
        "dollar_total_extracted": "",
        "dollar_pct_error": "",
        "elapsed_seconds": "",
    }

    # Load gold
    try:
        gold = _load_gold_csv(agency["gold_path"])
        base["gold_row_count"] = len(gold)
    except Exception as e:
        base["failure_stage"] = "gold_load"
        base["failure_reason"] = str(e)[:200]
        base["elapsed_seconds"] = round(time.time() - t0, 1)
        return base

    # Run pipeline
    cmd = [
        sys.executable, "-m", "cip_tools", "process",
        str(agency["pdf_path"]),
        "--agency-id", agency["agency_id"],
        "--out-dir", str(out_dir),
    ]
    if auto:
        cmd.append("--auto")

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min per agency
            encoding="utf-8",
            errors="replace",
        )
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired:
        base["failure_stage"] = "timeout"
        base["failure_reason"] = "Pipeline timed out after 300s"
        base["elapsed_seconds"] = round(time.time() - t0, 1)
        return base
    except Exception as e:
        base["failure_stage"] = "subprocess"
        base["failure_reason"] = str(e)[:200]
        base["elapsed_seconds"] = round(time.time() - t0, 1)
        return base

    # Detect failure stage from stdout/stderr
    if result.returncode != 0:
        combined = (stdout + stderr)[-800:]
        # Guess stage from output
        stage = "unknown"
        for marker, name in [
            ("Step 1", "ingest"), ("Step 2", "analyze"), ("Step 3", "design"),
            ("Step 4", "test_run"), ("Step 5", "full_run"), ("Step 6", "qa"),
            ("Step 7", "validate"),
        ]:
            if marker in stdout:
                stage = name
        base["failure_stage"] = stage
        base["failure_reason"] = combined[-300:]
        base["elapsed_seconds"] = round(time.time() - t0, 1)
        return base

    # Find produced final CSV
    final_path = out_dir / f"{agency['agency_id']}_final.csv"
    if not final_path.exists():
        base["failure_stage"] = "output_missing"
        base["failure_reason"] = "Pipeline exited 0 but no _final.csv produced"
        base["elapsed_seconds"] = round(time.time() - t0, 1)
        return base

    # Load extracted CSV
    try:
        with open(final_path, encoding="utf-8-sig") as f:
            extracted = list(csv.DictReader(f))
    except Exception as e:
        base["failure_stage"] = "output_load"
        base["failure_reason"] = str(e)[:200]
        base["elapsed_seconds"] = round(time.time() - t0, 1)
        return base

    # Score
    scores = score_against_gold(extracted, gold)
    n_ext = len(extracted)
    n_gold = len(gold)
    row_match = "yes" if abs(n_ext - n_gold) / max(n_gold, 1) <= 0.05 else "no"

    base.update({
        "status": "ok",
        "extracted_row_count": n_ext,
        "row_count_match": row_match,
        **scores,
        "elapsed_seconds": round(time.time() - t0, 1),
    })
    return base


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CIP regression harness")
    parser.add_argument("--weeks", default=",".join(ALL_WEEKS),
                        help="Comma-separated week codes (default: all)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max agencies to run (for testing)")
    parser.add_argument("--data-root", default=str(DATA_ROOT),
                        help="Root path to week folders")
    parser.add_argument("--resume", action="store_true",
                        help="Skip agencies already marked ok in results file")
    parser.add_argument("--commit", action="store_true",
                        help="Git-commit results file after each agency")
    parser.add_argument("--no-auto", action="store_true",
                        help="Run pipeline interactively (not --auto)")
    args = parser.parse_args()

    weeks = [w.strip() for w in args.weeks.split(",")]
    data_root = Path(args.data_root)
    repo_root = Path(__file__).parent
    auto = not args.no_auto

    print(f"Scanning {data_root} for weeks: {weeks}")
    agencies = find_eligible_agencies(data_root, weeks)
    print(f"Found {len(agencies)} eligible agencies (guide + final + ocr PDF)")

    completed = load_completed_ids(RESULTS_FILE) if args.resume else set()
    if completed:
        print(f"Resuming — skipping {len(completed)} already-completed agencies")

    todo = [a for a in agencies if a["agency_folder"] not in completed]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Running {len(todo)} agencies\n")

    for i, agency in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {agency['week']} / {agency['agency_id']}")
        try:
            result = run_agency(agency, repo_root, auto=auto)
        except Exception:
            result = {
                "run_at_utc": _now_utc(),
                "week": agency["week"],
                "agency_folder": agency["agency_folder"],
                "agency_id": agency["agency_id"],
                "status": "failed",
                "failure_stage": "harness_exception",
                "failure_reason": traceback.format_exc()[-300:],
                "elapsed_seconds": "",
                **{c: "" for c in RESULT_COLS if c not in [
                    "run_at_utc","week","agency_folder","agency_id",
                    "status","failure_stage","failure_reason","elapsed_seconds"
                ]},
            }

        status = result.get("status", "?")
        icon = "✓" if status == "ok" else "✗"
        print(f"  {icon} {status}  rows={result.get('extracted_row_count','?')}/{result.get('gold_row_count','?')}  "
              f"title_match={result.get('title_match_rate','?')}  "
              f"dollar_err={result.get('dollar_pct_error','?')}  "
              f"({result.get('elapsed_seconds','?')}s)")

        append_result(RESULTS_FILE, result)

        if args.commit:
            msg = (f"regression: {agency['week']}/{agency['agency_id']} "
                   f"— {status} ({i}/{len(todo)})")
            git_commit(repo_root, RESULTS_FILE, msg)

    print(f"\nDone. Results: {RESULTS_FILE}")

    # Summary
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE, encoding="utf-8") as f:
            all_rows = list(csv.DictReader(f))
        ok = sum(1 for r in all_rows if r["status"] == "ok")
        failed = sum(1 for r in all_rows if r["status"] == "failed")
        print(f"Total: {len(all_rows)}  OK: {ok}  Failed: {failed}")

        # Failure breakdown
        from collections import Counter
        stages = Counter(r["failure_stage"] for r in all_rows if r["status"] == "failed")
        if stages:
            print("Failure stages:")
            for stage, count in stages.most_common():
                print(f"  {stage}: {count}")


if __name__ == "__main__":
    main()
