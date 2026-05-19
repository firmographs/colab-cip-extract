"""
CIP extraction CLI — 7-step Work Instruction implemented in Python + Claude.

Usage:
    python -m cip_tools process <source_file> [--agency-id ID] [--out-dir DIR] [--auto]

Steps:
  1. Load + normalize source file
  2. Claude analyzes structure  → confirm
  3. Claude writes extraction script  → confirm / edit
  4. Test run (3 rows)  → confirm
  5. Full extraction  → _final.csv
  6. QA (totals check)
  7. Validation report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import tempfile
from pathlib import Path


def _confirm(prompt: str, auto: bool) -> bool:
    """Ask y/n. Returns True if confirmed. In --auto mode, always continues."""
    if auto:
        print(f"[auto] {prompt} -> y")
        return True
    while True:
        ans = input(f"{prompt} [y/n/q]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        if ans in ("q", "quit"):
            print("Aborted.")
            sys.exit(0)


def _edit_in_editor(text: str, suffix: str = ".py") -> str:
    """Open text in $EDITOR and return the edited version."""
    editor = os.environ.get("EDITOR", "notepad" if sys.platform == "win32" else "nano")
    import subprocess
    with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8") as f:
        f.write(text)
        tmp = Path(f.name)
    subprocess.run([editor, str(tmp)])
    result = tmp.read_text(encoding="utf-8")
    tmp.unlink(missing_ok=True)
    return result


def process(
    source_file: str,
    agency_id: str | None,
    out_dir: str | None,
    auto: bool,
    expected_totals: dict | None = None,
):
    from . import ingest, understand, design, runner, qa as qa_mod, validate, schema

    source = Path(source_file)
    if not source.exists():
        print(f"ERROR: File not found: {source}")
        sys.exit(1)

    out = Path(out_dir) if out_dir else source.parent
    out.mkdir(parents=True, exist_ok=True)

    ag_id = agency_id or source.stem
    script_path = out / f"{ag_id}_extract.py"
    final_path  = out / f"{ag_id}_final.csv"
    guide_path  = out / f"{ag_id}_guide.md"

    print(f"\n{'='*60}")
    print(f"CIP Extraction: {source.name}")
    print(f"Agency ID:      {ag_id}")
    print(f"Output dir:     {out}")
    print(f"{'='*60}\n")

    # -----------------------------------------------------------------------
    # Step 1: Load + normalize
    # -----------------------------------------------------------------------
    print("Step 1: Loading source file...")
    raw_text, sample_rows, metadata = ingest.load(source)
    print(f"  Format: {metadata.get('format')}  |  Cols: {metadata.get('n_cols')}  |  Rows: {metadata.get('estimated_rows')}")

    # Cell 3 gate
    try:
        c3_warns = schema.gate_cell3(sample_rows if len(sample_rows) >= 3 else sample_rows, source_label=ag_id)
        for w in c3_warns:
            print(f"  WARN (cell3): {w}")
    except ValueError as e:
        print(f"  FATAL (cell3): {e}")
        if not _confirm("Ingestion gate failed. Continue anyway?", auto):
            sys.exit(1)

    # -----------------------------------------------------------------------
    # Step 2: Understand structure
    # -----------------------------------------------------------------------
    print("\nStep 2: Asking Claude to analyze structure...")
    analysis = understand.analyze(source.name, raw_text, sample_rows, metadata)
    summary = understand.summarize(analysis)
    print(textwrap.indent(summary, "  "))

    if not _confirm("\nStep 2 analysis looks correct?", auto):
        print("  (You can manually edit the analysis JSON and re-run, or adjust the source file)")
        # Save analysis for manual edit
        analysis_file = out / f"{ag_id}_analysis.json"
        analysis_file.write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        print(f"  Saved to: {analysis_file}")
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Step 3: Design extraction script
    # -----------------------------------------------------------------------
    print("\nStep 3: Asking Claude to write extraction script...")
    script_code = design.write_script(source.name, analysis, sample_rows, full_path=str(source.resolve()))

    print(f"\n--- Generated script ({len(script_code.splitlines())} lines) ---")
    print(textwrap.indent("\n".join(script_code.splitlines()[:30]), "  "))
    if len(script_code.splitlines()) > 30:
        print(f"  ... ({len(script_code.splitlines())-30} more lines)")

    script_path.write_text(script_code, encoding="utf-8")
    print(f"\n  Saved to: {script_path}")

    ans = input("Edit script before running? [y/n/q]: ").strip().lower() if not auto else "n"
    if ans == "y":
        script_code = _edit_in_editor(script_code)
        script_path.write_text(script_code, encoding="utf-8")
    elif ans == "q":
        sys.exit(0)

    # -----------------------------------------------------------------------
    # Step 4: Test run (3 rows)
    # -----------------------------------------------------------------------
    print("\nStep 4: Test run (first 3 rows)...")
    try:
        test_rows, test_stderr = runner.test_run(script_path, n=3)
    except RuntimeError as e:
        print(f"  ERROR: {e}")
        if not _confirm("Test run failed. Edit script and retry?", auto):
            sys.exit(1)
        script_code = _edit_in_editor(script_path.read_text(encoding="utf-8"))
        script_path.write_text(script_code, encoding="utf-8")
        test_rows, test_stderr = runner.test_run(script_path, n=3)

    if test_stderr:
        print(f"  Stderr: {test_stderr[:300]}")

    print(f"\n  Test rows ({len(test_rows)} extracted):")
    for r in test_rows:
        print(f"    [{r.get('Project_Index')}] {r.get('Project_Title','?')[:60]}")
        print(f"         ID={r.get('Project_Number_Primary','?')}  Total=${r.get('Total_Project_Budget',0):,.0f}")

    if not _confirm("\nStep 4 test rows look correct?", auto):
        script_code = _edit_in_editor(script_path.read_text(encoding="utf-8"))
        script_path.write_text(script_code, encoding="utf-8")

    # -----------------------------------------------------------------------
    # Step 5: Full extraction
    # -----------------------------------------------------------------------
    print("\nStep 5: Full extraction...")
    full_rows, full_stderr = runner.full_run(script_path)
    if full_stderr:
        print(f"  Stderr: {full_stderr[:300]}")

    print(f"  Extracted {len(full_rows)} rows.")

    runner.save_final(full_rows, final_path)
    print(f"  Saved: {final_path}")

    # -----------------------------------------------------------------------
    # Step 6: QA
    # -----------------------------------------------------------------------
    print("\nStep 6: QA check...")
    if expected_totals:
        qa_result = qa_mod.qa_totals(expected_totals, full_rows)
    else:
        # Auto-compute basic totals for self-check
        total = sum(
            float(str(r.get("Total_Project_Budget", 0)).replace(",", "") or 0)
            for r in full_rows
        )
        print(f"  Grand total (extracted): ${total:,.0f}")
        qa_result = {"ok": [f"Grand total: ${total:,.0f}"], "errors": [], "warnings": []}

    report = qa_mod.format_report(qa_result)
    print(textwrap.indent(report, "  "))

    if qa_result.get("errors"):
        print("\n  QA ERRORS FOUND — per WI §6.6, no auto-correction.")
        print("  Review the errors above and decide manually.")
        _confirm("Acknowledge QA errors and continue?", auto)

    # -----------------------------------------------------------------------
    # Step 7: Validation
    # -----------------------------------------------------------------------
    print("\nStep 7: Validation...")
    try:
        val_report = validate.run(full_rows, source_label=ag_id)
        print(textwrap.indent(val_report, "  "))
    except ValueError as e:
        print(f"  FATAL validation: {e}")

    # -----------------------------------------------------------------------
    # Generate guide.md
    # -----------------------------------------------------------------------
    guide_content = design.make_guide(
        agency_id=ag_id,
        filename=source.name,
        analysis=analysis,
        script=script_code,
        analysis_summary=summary,
    )
    guide_path.write_text(guide_content, encoding="utf-8")
    print(f"\nGuide written: {guide_path}")
    print(f"\nDone. Final CSV: {final_path}  ({len(full_rows)} rows)")


def main():
    parser = argparse.ArgumentParser(description="CIP extraction pipeline")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("process", help="Process a CIP source file")
    p.add_argument("source_file", help="Path to source CIP file (CSV/Excel/PDF)")
    p.add_argument("--agency-id", help="Agency identifier (defaults to filename stem)")
    p.add_argument("--out-dir", help="Output directory (defaults to source file directory)")
    p.add_argument("--auto", action="store_true", help="Skip all confirmation prompts")
    p.add_argument("--expected-total", type=float, help="Published grand total for QA check")

    args = parser.parse_args()

    if args.command == "process":
        expected = {"total": args.expected_total} if args.expected_total else None
        process(
            source_file=args.source_file,
            agency_id=args.agency_id,
            out_dir=args.out_dir,
            auto=args.auto,
            expected_totals=expected,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
