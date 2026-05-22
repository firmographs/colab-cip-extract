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
OCR_ROOT  = Path(r"G:\Shared drives\CIP Staging\docs\input documents\in ocr PDF")

RESULTS_DIR  = Path(__file__).parent / "regression_results"
RESULTS_FILE = Path(__file__).parent / "regression_results.csv"  # merged output

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
    "zero_rows",        # yes if extracted_row_count == 0 despite ok status
    "row_count_match",  # yes | no | na
    "title_match_rate", # 0.0-1.0
    "dollar_total_gold",
    "dollar_total_extracted",
    "dollar_pct_error", # abs((extracted-gold)/gold) — blank if gold=0
    "dollar_blowup",    # yes if dollar_pct_error > 1e6 (column-alignment OCR artifact)
    "elapsed_seconds",
]

TITLE_SIM_THRESHOLD = 0.75  # looser than qa.py's 0.85 — gold CSVs vary in title cleaning
DOLLAR_BLOWUP_THRESHOLD = 1e6  # errors above this are OCR column-alignment artifacts


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
        dollar_blowup = "yes" if dollar_pct_error > DOLLAR_BLOWUP_THRESHOLD else "no"
    else:
        dollar_pct_error = None
        dollar_blowup = ""

    return {
        "title_match_rate": round(title_match_rate, 3) if title_match_rate is not None else "",
        "dollar_total_gold": dollar_gold,
        "dollar_total_extracted": dollar_extracted,
        "dollar_pct_error": round(dollar_pct_error, 4) if dollar_pct_error is not None else "",
        "dollar_blowup": dollar_blowup,
    }


# ---------------------------------------------------------------------------
# Agency discovery
# ---------------------------------------------------------------------------

def find_eligible_agencies(data_root: Path, weeks: list[str],
                           ocr_root: Path = OCR_ROOT) -> list[dict]:
    """
    Scan week folders and return list of agency dicts with paths to:
      pdf, guide, gold_final
    Only returns agencies that have all three.

    OCR PDFs may live either inside the agency folder OR in the shared
    CIP Staging ocr folder (ocr_root). Both locations are checked.
    """
    agencies = []
    for week in weeks:
        week_dir = data_root / week
        if not week_dir.exists():
            continue
        for agency_dir in sorted(week_dir.iterdir()):
            if not agency_dir.is_dir() or not agency_dir.name.startswith("2026"):
                continue

            guides = list(agency_dir.glob("*_guide.md"))
            finals = list(agency_dir.glob("*_final.csv"))
            finals = [f for f in finals if "pre_backfill" not in f.name
                      and "mapped" not in f.name]

            if not (guides and finals):
                continue

            # Derive agency_id from folder name
            m = re.search(r" - (.+)$", agency_dir.name)
            agency_id = m.group(1) if m else agency_dir.name

            # Look for OCR PDF — prefer agency folder, fall back to ocr_root
            pdfs = list(agency_dir.glob("*_ocr.pdf"))
            if not pdfs and ocr_root.exists():
                stem = agency_id  # e.g. cityofbowie.org_cip_2026-2031
                pdfs = list(ocr_root.glob(f"{stem}_ocr.pdf"))

            if not pdfs:
                continue

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

def week_results_file(week: str) -> Path:
    """Per-week CSV path — safe for parallel runs."""
    RESULTS_DIR.mkdir(exist_ok=True)
    return RESULTS_DIR / f"regression_{week}.csv"


def load_completed_ids(week: str) -> set[str]:
    """Return set of agency_folder values already marked ok for this week."""
    f = week_results_file(week)
    if not f.exists():
        return set()
    with open(f, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return {r["agency_folder"] for r in rows if r.get("status") == "ok"}


# ---------------------------------------------------------------------------
# Write one result row
# ---------------------------------------------------------------------------

def append_result(week: str, row: dict) -> None:
    f = week_results_file(week)
    exists = f.exists()
    with open(f, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_COLS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Merge all per-week files into regression_results.csv
# ---------------------------------------------------------------------------

def merge_results(repo_root: Path) -> Path:
    out = repo_root / "regression_results.csv"
    all_rows = []
    for week in ALL_WEEKS:
        f = week_results_file(week)
        if f.exists():
            with open(f, encoding="utf-8") as fh:
                all_rows.extend(csv.DictReader(fh))
    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    return out


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
        "zero_rows": "",
        "row_count_match": "",
        "title_match_rate": "",
        "dollar_total_gold": "",
        "dollar_total_extracted": "",
        "dollar_pct_error": "",
        "dollar_blowup": "",
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

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=900,  # 15 min per agency
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired:
        base["failure_stage"] = "timeout"
        base["failure_reason"] = "Pipeline timed out after 900s"
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
        "zero_rows": "yes" if n_ext == 0 else "no",
        "row_count_match": row_match,
        **scores,
        "elapsed_seconds": round(time.time() - t0, 1),
    })
    return base


# ---------------------------------------------------------------------------
# Live status report
# ---------------------------------------------------------------------------

def _print_status() -> None:
    from collections import Counter
    all_rows = []
    print(f"\n{'Week':<8} {'Done':>5} {'OK':>5} {'Fail':>5} {'Last agency'}")
    print("-" * 70)
    for week in ALL_WEEKS:
        f = week_results_file(week)
        if not f.exists():
            print(f"{week:<8} {'—':>5}")
            continue
        with open(f, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        ok   = sum(1 for r in rows if r["status"] == "ok")
        fail = sum(1 for r in rows if r["status"] == "failed")
        last = rows[-1]["agency_id"] if rows else ""
        print(f"{week:<8} {len(rows):>5} {ok:>5} {fail:>5}   {last[:50]}")
        all_rows.extend(rows)

    if not all_rows:
        print("No results yet.")
        return

    ok_rows  = [r for r in all_rows if r["status"] == "ok"]
    fail_rows = [r for r in all_rows if r["status"] == "failed"]
    total = len(all_rows)
    ok    = len(ok_rows)

    print("-" * 70)
    print(f"{'TOTAL':<8} {total:>5} {ok:>5} {len(fail_rows):>5}")

    # Quality metrics
    zero     = sum(1 for r in ok_rows if r.get("zero_rows") == "yes")
    row_match = sum(1 for r in ok_rows if r.get("row_count_match") == "yes")
    blowup   = sum(1 for r in ok_rows if r.get("dollar_blowup") == "yes")
    d_rows   = [r for r in ok_rows if r.get("dollar_pct_error") not in ("", None)
                and r.get("dollar_blowup") != "yes"]
    u10      = sum(1 for r in d_rows if float(r["dollar_pct_error"]) < 0.10)
    t_rows   = [r for r in ok_rows if r.get("title_match_rate") not in ("", None)]
    avg_title = sum(float(r["title_match_rate"]) for r in t_rows) / len(t_rows) if t_rows else 0

    print(f"\nQuality ({ok} OK agencies):")
    print(f"  Zero rows extracted : {zero:>3} / {ok}")
    print(f"  Row count match ±5% : {row_match:>3} / {ok}  ({row_match/ok*100:.0f}%)" if ok else "")
    print(f"  Dollar blowup (OCR) : {blowup:>3} / {ok}")
    print(f"  Dollar error <10%   : {u10:>3} / {len(d_rows)}  ({u10/len(d_rows)*100:.0f}%)" if d_rows else "")
    print(f"  Avg title match     : {avg_title:.3f}")

    # Failure breakdown
    stages = Counter(r["failure_stage"] for r in fail_rows)
    if stages:
        print(f"\nFailures by stage:")
        for stage, count in stages.most_common():
            print(f"  {stage:<20} {count}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="CIP regression harness")
    parser.add_argument("--weeks", default=",".join(ALL_WEEKS),
                        help="Comma-separated week codes (default: all)")
    parser.add_argument("--week", dest="weeks",
                        help="Single week code — alias for --weeks")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max agencies to run (for testing)")
    parser.add_argument("--data-root", default=str(DATA_ROOT),
                        help="Root path to week folders")
    parser.add_argument("--resume", action="store_true",
                        help="Skip agencies already marked ok in per-week results file")
    parser.add_argument("--commit", action="store_true",
                        help="Git-commit per-week results file after each agency")
    parser.add_argument("--no-auto", action="store_true",
                        help="Run pipeline interactively (not --auto)")
    parser.add_argument("--merge", action="store_true",
                        help="Merge all per-week files into regression_results.csv and exit")
    parser.add_argument("--status", action="store_true",
                        help="Print live progress summary from per-week files and exit")
    args = parser.parse_args()

    repo_root = Path(__file__).parent

    if args.status:
        _print_status()
        return

    if args.merge:
        out = merge_results(repo_root)
        with open(out, encoding="utf-8") as f:
            all_rows = list(csv.DictReader(f))
        ok = sum(1 for r in all_rows if r["status"] == "ok")
        failed = sum(1 for r in all_rows if r["status"] == "failed")
        print(f"Merged {len(all_rows)} rows -> {out}")
        print(f"Total: {len(all_rows)}  OK: {ok}  Failed: {failed}")
        from collections import Counter
        stages = Counter(r["failure_stage"] for r in all_rows if r["status"] == "failed")
        if stages:
            print("Failure stages:")
            for stage, count in stages.most_common():
                print(f"  {stage}: {count}")
        return

    weeks = [w.strip() for w in args.weeks.split(",")]
    data_root = Path(args.data_root)
    auto = not args.no_auto

    print(f"Scanning {data_root} for weeks: {weeks}")
    agencies = find_eligible_agencies(data_root, weeks)
    print(f"Found {len(agencies)} eligible agencies (guide + final + ocr PDF)")

    # Per-week resume: load completed from each week's own file
    if args.resume:
        completed = set()
        for w in weeks:
            completed |= load_completed_ids(w)
        print(f"Resuming — skipping {len(completed)} already-completed agencies")
    else:
        completed = set()

    todo = [a for a in agencies if a["agency_folder"] not in completed]
    if args.limit:
        todo = todo[: args.limit]

    print(f"Running {len(todo)} agencies\n")

    for i, agency in enumerate(todo, 1):
        week = agency["week"]
        print(f"[{i}/{len(todo)}] {week} / {agency['agency_id']}")
        try:
            result = run_agency(agency, repo_root, auto=auto)
        except Exception:
            result = {
                "run_at_utc": _now_utc(),
                "week": week,
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
        icon = "OK" if status == "ok" else "FAIL"
        print(f"  [{icon}] rows={result.get('extracted_row_count','?')}/{result.get('gold_row_count','?')}  "
              f"title_match={result.get('title_match_rate','?')}  "
              f"dollar_err={result.get('dollar_pct_error','?')}  "
              f"({result.get('elapsed_seconds','?')}s)")

        append_result(week, result)

        if args.commit:
            wf = week_results_file(week)
            msg = (f"regression: {week}/{agency['agency_id']} — {status} ({i}/{len(todo)})")
            git_commit(repo_root, wf, msg)

    week_str = weeks[0] if len(weeks) == 1 else f"{weeks[0]}-{weeks[-1]}"
    print(f"\nDone. Per-week files in: {RESULTS_DIR}/")
    print("Run with --merge to combine into regression_results.csv")


if __name__ == "__main__":
    main()
