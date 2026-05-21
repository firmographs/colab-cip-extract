"""
Steps 4 & 5: Run the extraction script in a subprocess sandbox.

- Step 4: single-project test (run + capture first 3 rows, show curator)
- Step 5: full extraction → _final.csv

The script is executed via subprocess so that any errors are isolated.
The script's run() function must return list[dict].
"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any


def _run_script(script_path: Path, capture_n: int | None = None) -> tuple[list[dict], str]:
    """
    Execute the extraction script and return (rows, stderr).
    The script must define run() returning list[dict].
    """
    # We inject a small wrapper that calls run() and JSON-serialises the result.
    # Stdout is redirected to stderr during import+run so any print()s in the
    # script don't corrupt the JSON we parse from stdout.
    limit_code = f"rows = rows[:{capture_n}]" if capture_n else ""
    wrapper = textwrap.dedent(f"""
import sys, json, importlib.util, io

# Redirect stdout → stderr while loading and running the script so any
# print() calls in the generated script don't corrupt our JSON output.
_real_stdout = sys.stdout
sys.stdout = sys.stderr

spec = importlib.util.spec_from_file_location("_cip_script", r{str(script_path)!r})
mod  = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
rows = mod.run()
if rows is None:
    raise RuntimeError("run() returned None — script is missing a return statement")
if not isinstance(rows, list):
    raise RuntimeError(f"run() must return list[dict], got {{type(rows).__name__}}")

sys.stdout = _real_stdout  # restore before we print JSON

{limit_code}
# Stringify any non-serialisable values
clean = [{{str(k): str(v) if not isinstance(v, (str,int,float,type(None))) else v
           for k,v in r.items()}} for r in rows]
print(json.dumps(clean))
""")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(wrapper)
        tmp = Path(f.name)

    try:
        result = subprocess.run(
            [sys.executable, str(tmp)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        stderr = result.stderr.strip()
        if result.returncode != 0:
            raise RuntimeError(f"Script exited with code {result.returncode}:\n{stderr}")
        rows = json.loads(result.stdout)
        return rows, stderr
    finally:
        tmp.unlink(missing_ok=True)


def test_run(script_path: Path, n: int = 3) -> tuple[list[dict], str]:
    """Step 4: Run the script, return first n rows for curator review."""
    return _run_script(script_path, capture_n=n)


def full_run(script_path: Path) -> tuple[list[dict], str]:
    """Step 5: Run the script on all rows."""
    return _run_script(script_path, capture_n=None)


def save_final(rows: list[dict], out_path: Path) -> None:
    """Write rows to _final.csv using the standard schema column order."""
    from .schema import FINAL_COLS

    # Build fieldnames: standard cols first, then any extras
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())

    fieldnames = [c for c in FINAL_COLS if c in all_keys]
    extras = sorted(all_keys - set(FINAL_COLS))
    fieldnames += extras

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
