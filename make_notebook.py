"""Generates CIP_Extraction.ipynb — the curator-facing Colab UI."""

import json
import os
import uuid

def uid():
    return str(uuid.uuid4())[:8]

def code_cell(title, code):
    lines = (f"#@title {title}\n" + code.strip() + "\n").splitlines(keepends=True)
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": uid(),
        "metadata": {"cellView": "form", "id": uid()},
        "outputs": [],
        "source": lines,
    }

def md_cell(text):
    return {
        "cell_type": "markdown",
        "id": uid(),
        "metadata": {"id": uid()},
        "source": [text],
    }

# ---------------------------------------------------------------------------
# Read cip_tools source files at generation time and embed in the notebook.
# The SETUP cell writes them to /tmp/cip_tools/ so imports work without pip.
# ---------------------------------------------------------------------------

_MOD_NAMES = ['llm', 'schema', 'ingest', 'understand', 'design', 'runner', 'qa', 'validate']
_mods = {}
for _n in _MOD_NAMES:
    _p = f'cip_tools/{_n}.py'
    if os.path.exists(_p):
        with open(_p, encoding='utf-8') as _f:
            _mods[_n] = _f.read()

# ---------------------------------------------------------------------------
# Cell source code
# SETUP is built by concatenation so we can inject the JSON dict cleanly.
# Other cells use r'''...''' to avoid triple-quote conflicts.
# ---------------------------------------------------------------------------

_SETUP_HEAD = r'''
import subprocess, sys, os

def _pip(pkg):
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-q', pkg],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"pip install failed for: {pkg}")
        print(result.stderr[-800:])
        raise RuntimeError(f"pip install failed: {pkg}")

from google.colab import userdata, drive

drive.mount('/content/drive', force_remount=False)

try:
    os.environ['ANTHROPIC_API_KEY'] = userdata.get('ANTHROPIC_API_KEY')
    print("API key loaded.")
except Exception:
    print("NOTE: Add ANTHROPIC_API_KEY in Colab Secrets (lock icon, left sidebar).")

print("Installing dependencies (first run takes ~60 s)...")
for _p in ['httpx', 'pdfplumber', 'openpyxl', 'pandas', 'beautifulsoup4']:
    _pip(_p)
'''

_SETUP_TAIL = r'''
os.makedirs('/tmp/cip_tools', exist_ok=True)
with open('/tmp/cip_tools/__init__.py', 'w') as _f:
    _f.write('"""cip_tools."""\n')
for _name, _code in _CIP_MODULES.items():
    with open(f'/tmp/cip_tools/{_name}.py', 'w', encoding='utf-8') as _f:
        _f.write(_code)
if '/tmp' not in sys.path:
    sys.path.insert(0, '/tmp')

# Create base extract folder structure on Drive
_base = '/content/drive/Shareddrives/0_cip_data/extract'
for _folder in [_base, f'{_base}/1_inbox']:
    os.makedirs(_folder, exist_ok=True)
print("Dependencies ready. Extract folders created.")
'''

SETUP = _SETUP_HEAD + f'_CIP_MODULES = {json.dumps(_mods)}\n' + _SETUP_TAIL

CONFIG = r'''
import os, datetime, ipywidgets as _w
from IPython.display import display, Markdown

_EXTRACT_ROOT = "/content/drive/Shareddrives/0_cip_data/extract"
_inbox = f"{_EXTRACT_ROOT}/1_inbox"

_files = sorted(
    f for f in os.listdir(_inbox)
    if os.path.isfile(f'{_inbox}/{f}') and not f.startswith('.')
)
if not _files:
    display(Markdown("**No files in `1_inbox/`** — upload a source file first."))
else:
    _picker  = _w.Dropdown(options=_files, description='Source file:',
                            layout=_w.Layout(width='700px'))
    _tot_box = _w.FloatText(value=0, description='Expected total ($):',
                            layout=_w.Layout(width='300px'))
    _out     = _w.Output()

    def _pick(change=None):
        global SOURCE_FILE, AGENCY_ID, OUT_DIR, SCRIPTS_DIR, EXPECTED_TOTAL
        SOURCE_FILE    = f"{_inbox}/{_picker.value}"
        AGENCY_ID      = os.path.splitext(_picker.value)[0]
        EXPECTED_TOTAL = _tot_box.value
        _today         = datetime.date.today().strftime('%Y %m %d')
        OUT_DIR        = f"{_EXTRACT_ROOT}/{_today} - {AGENCY_ID}"
        SCRIPTS_DIR    = OUT_DIR
        os.makedirs(OUT_DIR, exist_ok=True)
        _out.clear_output()
        with _out:
            _tot = "(not set)" if EXPECTED_TOTAL == 0 else f"${EXPECTED_TOTAL:,.0f}"
            display(Markdown(
                f"**Agency ID:** `{AGENCY_ID}`  \n"
                f"**Output folder:** `{OUT_DIR}`  \n"
                f"**Expected total:** {_tot}"
            ))

    _picker.observe(_pick, names='value')
    _tot_box.observe(_pick, names='value')
    display(_w.VBox([_picker, _tot_box, _out]))
    _pick()
'''

STEP1 = r'''
from cip_tools import ingest, schema
from IPython.display import display, Markdown

print(f"Loading: {SOURCE_FILE}")
_raw_text, _sample_rows, _metadata = ingest.load(SOURCE_FILE)

_c3_warns = []
_c3_fatal = None
try:
    _c3_warns = schema.gate_cell3(_sample_rows, AGENCY_ID, fmt=_metadata.get('format'))
except ValueError as _e:
    _c3_fatal = str(_e)

fmt   = _metadata.get('format', '?')
ncols = _metadata.get('n_cols', '?')
nrows = _metadata.get('estimated_rows', '?')

if _c3_fatal:
    display(Markdown(f"## INGESTION FAILURE\n\n**{_c3_fatal}**\n\nStop and check the source file."))
else:
    display(Markdown(
        f"## File Loaded\n\n"
        f"| | |\n|--|--|\n"
        f"| Format | `{fmt}` |\n"
        f"| Columns | {ncols} |\n"
        f"| Rows (est.) | {nrows} |\n"
    ))
    if _c3_warns:
        display(Markdown("**Warnings:**\n" + "\n".join(f"- {w}" for w in _c3_warns)))
    if _sample_rows:
        cols = list(_sample_rows[0].keys())
        col_list = "\n".join(f"{i+1}. `{c}`" for i, c in enumerate(cols[:25]))
        extra = f"\n*...and {len(cols)-25} more*" if len(cols) > 25 else ""
        display(Markdown(f"**Columns detected:**\n{col_list}{extra}"))
'''

STEP2 = r'''
from cip_tools import understand
from IPython.display import display, Markdown

print("Asking Claude to analyze structure...")
_analysis = understand.analyze(
    os.path.basename(SOURCE_FILE), _raw_text, _sample_rows, _metadata
)
_analysis_summary = understand.summarize(_analysis)

conf = _analysis.get('confidence', 0)
conf_icon = "HIGH" if conf >= 0.85 else "MEDIUM" if conf >= 0.65 else "LOW"

display(Markdown(
    f"## Structure Analysis (confidence: {conf_icon} {conf:.0%})\n\n"
    f"```\n{_analysis_summary}\n```"
))
if _analysis.get('quirks'):
    display(Markdown("**Quirks noted:**\n" + "\n".join(f"- {q}" for q in _analysis['quirks'])))
if _analysis.get('notes'):
    display(Markdown(f"**Notes:** {_analysis['notes']}"))

display(Markdown(
    "---\n"
    "*Review the above. If it looks correct, run the next cell.  \n"
    "If something is wrong, stop and contact your supervisor.*"
))
'''

STEP3 = r'''
from cip_tools import design
from IPython.display import display, Markdown, Code
from pathlib import Path

print("Asking Claude to write extraction script...")
_script_code = design.write_script(
    os.path.basename(SOURCE_FILE), _analysis, _sample_rows,
    full_path=SOURCE_FILE,
)

_script_path = Path(SCRIPTS_DIR) / f"{AGENCY_ID}_extract.py"
_script_path.write_text(_script_code, encoding='utf-8')

nlines = len(_script_code.splitlines())
display(Markdown(f"**Script generated:** {nlines} lines — saved to `{_script_path}`"))
display(Code(_script_code[:4000], language='python'))
if nlines > 60:
    display(Markdown(f"*({nlines - 60} more lines — open the file on Drive to view/edit all)*"))

display(Markdown(
    "---\n"
    "**Review the script above.**  \n"
    "If it looks correct, run the next cell.  \n"
    "If it needs changes, open the `.py` file on Drive, edit and save it, then run the next cell."
))
'''

STEP4 = r'''
from cip_tools import runner
from IPython.display import display, Markdown

# Re-read script from Drive in case curator edited it
_script_code = _script_path.read_text(encoding='utf-8')
_script_path.write_text(_script_code, encoding='utf-8')

print("Test run (first 3 rows)...")
try:
    _test_rows, _test_stderr = runner.test_run(_script_path, n=3)
except RuntimeError as _err:
    display(Markdown(f"## Script Error\n\n```\n{_err}\n```\n\nFix the script and re-run this cell."))
    raise

if _test_stderr:
    display(Markdown(f"**Script warnings:**\n```\n{_test_stderr[:400]}\n```"))

display(Markdown(f"## Test Results ({len(_test_rows)} rows extracted)"))
for _r in _test_rows:
    _budget = float(_r.get('Total_Project_Budget') or 0)
    _yearly = _r.get('Yearly_Costs_By_Category_JSON', '{}')
    _funds  = _r.get('Fund_Names_JSON', '[]')
    display(Markdown(
        f"**[{_r.get('Project_Index')}]** {_r.get('Project_Title', '?')}  \n"
        f"- ID: `{_r.get('Project_Number_Primary', '?')}`  "
        f"Dept: `{_r.get('Client_Department', '?')}`  \n"
        f"- Total budget: **${_budget:,.0f}**  \n"
        f"- Yearly: `{_yearly}`  \n"
        f"- Funds: `{_funds}`"
    ))

display(Markdown(
    "---\n"
    "*Verify these rows against your source document.*  \n"
    "*If correct, run the next cell. If wrong, edit the script and re-run this cell.*"
))
'''

STEP5 = r'''
from cip_tools import runner
from IPython.display import display, Markdown
from pathlib import Path

print("Running full extraction...")
_full_rows, _full_stderr = runner.full_run(_script_path)

if _full_stderr:
    display(Markdown(f"**Script warnings:**\n```\n{_full_stderr[:400]}\n```"))

_final_path = Path(OUT_DIR) / f"{AGENCY_ID}_final.csv"
runner.save_final(_full_rows, _final_path)

_grand_total = sum(float(r.get('Total_Project_Budget') or 0) for r in _full_rows)

display(Markdown(
    f"## Extraction Complete\n\n"
    f"| | |\n|--|--|\n"
    f"| Projects extracted | **{len(_full_rows)}** |\n"
    f"| Grand total | **${_grand_total:,.0f}** |\n"
    f"| Output | `{_final_path}` |\n"
))

if EXPECTED_TOTAL > 0:
    _diff = _grand_total - EXPECTED_TOTAL
    _pct  = _diff / EXPECTED_TOTAL * 100
    _ok   = abs(_pct) < 1.0
    display(Markdown(
        f"### Total Check: {'PASS' if _ok else 'FAIL'}\n\n"
        f"Expected: **${EXPECTED_TOTAL:,.0f}** | "
        f"Extracted: **${_grand_total:,.0f}** | "
        f"Diff: **${_diff:+,.0f}** ({_pct:+.2f}%)"
    ))
'''

STEP67 = r'''
from cip_tools import validate
from IPython.display import display, Markdown

print("Running QA and validation...")
try:
    _val_report = validate.run(_full_rows, source_label=AGENCY_ID)
    display(Markdown(f"```\n{_val_report}\n```"))
except ValueError as _e:
    display(Markdown(f"## VALIDATION FAILURE\n\n**{_e}**"))
    raise

display(Markdown(
    "---\n"
    "**All discrepancies above require human review — nothing is auto-corrected (WI §6.6).**  \n"
    "If errors are found: edit the script, re-run Steps 4-5, then re-run this cell."
))
'''

STEP8 = r'''
from cip_tools import design
from pathlib import Path
from IPython.display import display, Markdown

_guide_content = design.make_guide(
    agency_id=AGENCY_ID,
    filename=os.path.basename(SOURCE_FILE),
    analysis=_analysis,
    script=_script_code,
    analysis_summary=_analysis_summary,
)

_guide_path = Path(OUT_DIR) / f"{AGENCY_ID}_guide.md"
_guide_path.write_text(_guide_content, encoding='utf-8')

display(Markdown(
    f"## Done\n\n"
    f"Files saved to `{OUT_DIR}`:\n\n"
    f"| File | Description |\n|------|-------------|\n"
    f"| `{SCRIPTS_DIR}/{AGENCY_ID}_extract.py` | Extraction script — reuse next year |\n"
    f"| `{AGENCY_ID}_final.csv` | Standard output ({len(_full_rows)} rows) |\n"
    f"| `{AGENCY_ID}_guide.md` | Curator guide with QA checklist |\n"
))
'''

# ---------------------------------------------------------------------------
# Build notebook
# ---------------------------------------------------------------------------

cells = [
    md_cell(
        "# CIP Extraction Pipeline\n\n"
        "Run each cell in order. Review output at each step before continuing.\n\n"
        "**Before you start:** Add your `ANTHROPIC_API_KEY` to Colab Secrets "
        "(lock icon in the left sidebar)."
    ),
    code_cell("Step 0 — Setup (run once per session)", SETUP),
    code_cell("Step 0b — Configuration", CONFIG),
    md_cell("---\n## Extraction Steps\n\nRun each cell, review, then continue."),
    code_cell("Step 1 — Load & Normalize Source File", STEP1),
    code_cell("Step 2 — Analyze Structure  (Claude)", STEP2),
    code_cell("Step 3 — Generate Extraction Script  (Claude)", STEP3),
    code_cell("Step 4 — Test Run (3 rows)", STEP4),
    code_cell("Step 5 — Full Extraction", STEP5),
    code_cell("Step 6-7 — QA & Validation", STEP67),
    code_cell("Step 8 — Save Guide", STEP8),
]

nb = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "colab": {
            "provenance": [],
            "name": "CIP Extraction Pipeline",
            "collapsed_sections": [],
            "toc_visible": True,
        },
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"},
    },
    "cells": cells,
}

out = "CIP_Extraction.ipynb"
with open(out, "w", encoding="utf-8") as f:
    json.dump(nb, f, indent=1, ensure_ascii=False)

print(f"Written: {out}  ({len(cells)} cells)")
