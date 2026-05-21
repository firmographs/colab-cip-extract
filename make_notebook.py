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

_MOD_NAMES = ['llm', 'schema', 'ingest', 'understand', 'design', 'guide_rag', 'runner', 'qa', 'validate']
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
import os, re, datetime, ipywidgets as _w
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
    _picker = _w.Dropdown(options=_files, description='Source file:',
                          layout=_w.Layout(width='700px'))
    _out    = _w.Output()

    def _pick(change=None):
        global SOURCE_FILE, AGENCY_ID, OUT_DIR, SCRIPTS_DIR
        SOURCE_FILE = f"{_inbox}/{_picker.value}"
        # Strip _ocr suffix from agency ID used for output naming
        _raw_id     = os.path.splitext(_picker.value)[0]
        AGENCY_ID   = re.sub(r'_ocr$', '', _raw_id, flags=re.IGNORECASE)
        OUT_DIR     = f"{_EXTRACT_ROOT}/{AGENCY_ID}"
        SCRIPTS_DIR = OUT_DIR
        os.makedirs(OUT_DIR, exist_ok=True)
        _out.clear_output()
        with _out:
            display(Markdown(
                f"**Agency ID:** `{AGENCY_ID}`  \n"
                f"**Output folder:** `{OUT_DIR}`"
            ))

    _picker.observe(_pick, names='value')
    display(_w.VBox([_picker, _out]))
    _pick()
'''

STEP0C = r'''
from cip_tools import guide_rag
from IPython.display import display, Markdown
import os

_GUIDE_ROOT  = "/content/drive/Shareddrives/0_cip_data/extract"
_INDEX_PATH  = "/content/drive/Shareddrives/0_cip_data/extract/_guide_index.json"

# Rebuild index if stale (> 7 days) or missing
import time as _time
_needs_rebuild = not os.path.exists(_INDEX_PATH)
if not _needs_rebuild:
    _age_days = (_time.time() - os.path.getmtime(_INDEX_PATH)) / 86400
    _needs_rebuild = _age_days > 7

if _needs_rebuild:
    print("Building guide index (scans all *_guide.md files under extract/)...")
    _GUIDE_INDEX = guide_rag.build_index(_GUIDE_ROOT, _INDEX_PATH)
    display(Markdown(f"**Guide index built:** {len(_GUIDE_INDEX['guides'])} guides indexed."))
else:
    _GUIDE_INDEX = guide_rag.load_index(_INDEX_PATH)
    display(Markdown(
        f"**Guide index loaded:** {len(_GUIDE_INDEX['guides'])} guides "
        f"(built {_GUIDE_INDEX['built_at'][:10]})"
    ))
'''

STEPS12 = r'''
from cip_tools import ingest, schema, understand
from IPython.display import display, Markdown

# --- Step 1: Load & Normalize ---
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
    raise SystemExit(_c3_fatal)

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

# --- Step 2: Analyze Structure (Claude) ---
print("\nAsking Claude to analyze structure...")
_analysis = understand.analyze(
    os.path.basename(SOURCE_FILE), _raw_text, _sample_rows, _metadata
)
_analysis_summary = understand.summarize(_analysis)

display(Markdown(
    f"## Structure Analysis\n\n"
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
from cip_tools import design, guide_rag
from IPython.display import display, Markdown, Code
from pathlib import Path

# Retrieve guide context (prior-year if available, else similar)
_guide_mode, _guide_context = guide_rag.retrieve(
    os.path.basename(SOURCE_FILE), _analysis, _GUIDE_INDEX,
)
if _guide_mode == "prior_year":
    display(Markdown("**Guide RAG:** Prior-year guide found — using as primary template."))
elif _guide_mode == "similar":
    display(Markdown("**Guide RAG:** Using similar past extractions as reference."))
else:
    display(Markdown("*Guide RAG: no matches — generating script from scratch.*"))

print("Asking Claude to write extraction script...")
_script_code = design.write_script(
    os.path.basename(SOURCE_FILE), _analysis, _sample_rows,
    full_path=SOURCE_FILE,
    metadata=_metadata,
    guide_context=_guide_context,
    guide_mode=_guide_mode,
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

STEPS5_8 = r'''
from cip_tools import runner, validate, design
from IPython.display import display, Markdown
from pathlib import Path
import datetime

# Re-read script from Drive in case curator edited it after the test run
_script_code = _script_path.read_text(encoding='utf-8')

# --- Step 5: Full Extraction ---
print("Running full extraction...")
_full_rows, _full_stderr = runner.full_run(_script_path)

if _full_stderr:
    display(Markdown(f"**Script warnings:**\n```\n{_full_stderr[:400]}\n```"))

_grand_total = sum(float(r.get('Total_Project_Budget') or 0) for r in _full_rows)

display(Markdown(
    f"## Extraction Complete\n\n"
    f"| | |\n|--|--|\n"
    f"| Projects extracted | **{len(_full_rows)}** |\n"
    f"| Grand total | **${_grand_total:,.0f}** |\n"
))

_published = _analysis.get('published_grand_total')
if _published:
    _diff = _grand_total - _published
    _pct  = _diff / _published * 100
    _ok   = abs(_pct) < 1.0
    display(Markdown(
        f"### Total Check: {'PASS ✓' if _ok else 'FAIL ✗'}\n\n"
        f"Published: **${_published:,.0f}** | "
        f"Extracted: **${_grand_total:,.0f}** | "
        f"Diff: **${_diff:+,.0f}** ({_pct:+.2f}%)"
    ))
else:
    display(Markdown(
        "_Published grand total not found in document — "
        "verify extracted total manually against source._"
    ))

# --- Step 6-7: QA & Validation ---
print("\nRunning QA and validation...")
_val_ok = True
try:
    _val_report = validate.run(_full_rows, source_label=AGENCY_ID)
    display(Markdown(f"```\n{_val_report}\n```"))
except ValueError as _e:
    _val_ok = False
    display(Markdown(f"## VALIDATION FAILURE\n\n**{_e}**\n\nFix the script and re-run this cell."))
    raise

display(Markdown(
    "**All discrepancies above require human review — nothing is auto-corrected.**  \n"
    "If errors are found: edit the script, re-run Step 4, then re-run this cell."
))

# --- Step 8: Save (only after validation passes) ---
import shutil

_out_dir   = Path(OUT_DIR)
_old_dir   = _out_dir / "old"
_final_path  = _out_dir / f"{AGENCY_ID}_final.csv"
_guide_path  = _out_dir / f"{AGENCY_ID}_guide.md"
_script_dest = _out_dir / f"{AGENCY_ID}_extract.py"

# Sweep any existing output files into old/ before writing new ones
_old_dir.mkdir(exist_ok=True)
_ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
_swept = []
for _existing in [_final_path, _guide_path, _script_dest]:
    if _existing.exists():
        _archived = _old_dir / f"{_existing.stem}_{_ts}{_existing.suffix}"
        _existing.rename(_archived)
        _swept.append(_archived.name)
if _swept:
    display(Markdown(f"*Archived to `old/`: {', '.join(_swept)}*"))

# Write final outputs
runner.save_final(_full_rows, _final_path)

_guide_content = design.make_guide(
    agency_id=AGENCY_ID,
    filename=os.path.basename(SOURCE_FILE),
    analysis=_analysis,
    script=_script_code,
    analysis_summary=_analysis_summary,
)
_guide_path.write_text(_guide_content, encoding='utf-8')

# Copy extraction script into output folder
shutil.copy2(_script_path, _script_dest)

# Copy source PDF into output folder
_pdf_dest = _out_dir / os.path.basename(SOURCE_FILE)
if not _pdf_dest.exists():
    shutil.copy2(SOURCE_FILE, _pdf_dest)

display(Markdown(
    f"## Done\n\n"
    f"Files saved to `{OUT_DIR}`:\n\n"
    f"| File | Description |\n|------|-------------|\n"
    f"| `{AGENCY_ID}_extract.py` | Extraction script — reuse next year |\n"
    f"| `{AGENCY_ID}_final.csv` | Standard output ({len(_full_rows)} rows) |\n"
    f"| `{AGENCY_ID}_guide.md` | Curator guide with QA checklist |\n"
    f"| `{os.path.basename(SOURCE_FILE)}` | Source PDF (copy) |\n"
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
    code_cell("Step 0c — Guide Index (run once per session)", STEP0C),
    md_cell("---\n## Extraction Steps\n\nRun each cell, review, then continue."),
    code_cell("Steps 1-2 — Load File & Analyze Structure  (Claude)", STEPS12),
    code_cell("Step 3 — Generate Extraction Script  (Claude)", STEP3),
    code_cell("Step 4 — Test Run (3 rows)", STEP4),
    code_cell("Steps 5-8 — Full Extraction, Validate & Save", STEPS5_8),
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
