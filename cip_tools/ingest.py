"""
Step 1: Load and normalize a CIP source file.

Supports: .csv, .xlsx, .xls, .pdf, .html/.htm
Returns: (raw_text, sample_rows, metadata)

raw_text    - normalized text representation for LLM analysis
sample_rows - list of dicts (first 10 rows) for LLM and quick checks
metadata    - dict: {format, n_cols, estimated_rows, sheet_name, ...}
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

# Common mojibake sequences (UTF-8 decoded as latin-1) -> correct chars
MOJIBAKE_MAP = {
    "a€“": "--",    # en-dash mojibake
    "a€™": "'",     # right single quote mojibake
    "a€œ": '"',     # left double quote mojibake
    "a€\x9d": '"',       # right double quote mojibake
    "A°": "°",      # degree sign mojibake
    "Aé": "é",      # e acute mojibake
    "Â ": " ",      # non-breaking space mojibake
    "–": "--",            # en-dash
    "—": "--",            # em-dash
    "‘": "'",             # left single quote
    "’": "'",             # right single quote
    "“": '"',             # left double quote
    "”": '"',             # right double quote
    " ": " ",             # non-breaking space
}


# Patterns that definitively indicate the unit of raw dollar values in the document.
# Checked against column headers AND free text (first ~8 000 chars).
_DOLLAR_UNIT_PATTERNS: list[tuple[int, list[str]]] = [
    (1_000_000, [
        r"\bin\s+millions\b",
        r"\$\s*millions?\b",
        r"\(in\s+\$\s*millions?\)",
        r"\(\$\s*000,?000s?\)",
        r"amounts?\s+in\s+millions",
        r"\$\s*000,?000",
    ]),
    (1_000, [
        r"\bin\s+thousands\b",
        r"\$\s*thousands?\b",
        r"\(in\s+thousands\)",
        r"\(\$\s*000s?\)",
        r"\(\$000\)",
        r"amounts?\s+in\s+thousands",
        r"\$\s*000\b",
        r"\(000s?\)",
        r"thousands\s+of\s+dollars",
    ]),
]


def _detect_dollar_unit(text: str, columns: list[str] | None = None) -> int:
    """Return 1, 1000, or 1000000 by scanning column names and document text."""
    haystack = " ".join(columns or []) + " " + text[:8000]
    low = haystack.lower()
    for unit, patterns in _DOLLAR_UNIT_PATTERNS:
        for pat in patterns:
            if re.search(pat, low):
                return unit
    return 1


def normalize_text(text: str) -> str:
    for bad, good in MOJIBAKE_MAP.items():
        text = text.replace(bad, good)
    # Collapse runs of whitespace except newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _try_encodings(path: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def load_csv(path: Path) -> tuple[str, list[dict], dict]:
    raw = _try_encodings(path)
    text = normalize_text(raw)
    lines = text.splitlines()

    rows = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(r) for r in reader]
    except Exception:
        pass

    n_rows = len(rows)
    n_cols = len(rows[0]) if rows else (len(lines[0].split(",")) if lines else 0)

    cols = list(rows[0].keys()) if rows else []
    dollar_unit = _detect_dollar_unit(text, cols)
    if dollar_unit != 1:
        print(f"  Dollar unit detected: x{dollar_unit} (values expressed in {'thousands' if dollar_unit == 1000 else 'millions'})")
    return (
        "\n".join(lines[:60]),
        rows[:10],
        {"format": "csv", "n_cols": n_cols, "estimated_rows": n_rows, "dollar_unit_hint": dollar_unit},
    )


def load_excel(path: Path) -> tuple[str, list[dict], dict]:
    import pandas as pd

    # Use pandas for full read
    try:
        df = pd.read_excel(path, sheet_name=0, header=None)
    except Exception as e:
        return (str(e), [], {"format": "xlsx", "error": str(e)})

    # Auto-detect header row (first row with >50% non-null, mostly string values)
    header_row = 0
    for i, row in df.iterrows():
        non_null = row.notna().sum()
        if non_null >= max(3, len(df.columns) * 0.3):
            str_vals = sum(1 for v in row if isinstance(v, str) and v.strip())
            if str_vals >= non_null * 0.5:
                header_row = i
                break

    df2 = pd.read_excel(path, sheet_name=0, header=header_row)
    df2 = df2.dropna(how="all").dropna(axis=1, how="all")

    rows = df2.head(10).to_dict("records")
    rows = [{str(k): ("" if str(v) == "nan" else str(v)) for k, v in r.items()} for r in rows]

    col_names = list(df2.columns)
    preview_lines = [" | ".join(str(c) for c in col_names)]
    for _, row in df2.head(5).iterrows():
        preview_lines.append(" | ".join(str(v)[:40] for v in row))

    dollar_unit = _detect_dollar_unit("\n".join(preview_lines), [str(c) for c in col_names])
    if dollar_unit != 1:
        print(f"  Dollar unit detected: x{dollar_unit} (values expressed in {'thousands' if dollar_unit == 1000 else 'millions'})")
    return (
        "\n".join(preview_lines),
        rows,
        {
            "format": "xlsx",
            "n_cols": len(col_names),
            "estimated_rows": len(df2),
            "header_row": header_row,
            "columns": col_names,
            "dollar_unit_hint": dollar_unit,
        },
    )


_CIP_TRIGGERS = [
    "capital improvement",
    "capital improvements program",
    " cip ",
    "cip project",
    "cip sheet",
]

# Strong body triggers: appear on actual project-data pages, rarely in a TOC
_CIP_STRONG_TRIGGERS = [
    "recommended capital",
    "capital projects detail",
    "project number",
    "funding source",
    "total project cost",
    "annual action plan",
    "unfunded capital",
    "cip detail",
    "project listing",
]


def _is_toc_page(text: str) -> bool:
    low = text.lower()
    if "table of contents" in low or "contents\n" in low:
        return True
    dots = text.count("......")
    lines = text.count("\n") or 1
    return dots / lines > 0.3


def _toc_cip_page(all_pages: list[tuple[int, str]]) -> int | None:
    """Scan TOC pages and extract the document page number where the CIP chapter starts.

    TOC lines look like:  "Capital Improvement Program ........... 197"
    We parse the trailing integer and convert from 1-based doc page to 0-based index.
    Returns the 0-based index into all_pages, or None if not found.
    """
    for _idx, (_pnum, text) in enumerate(all_pages[:30]):
        if not _is_toc_page(text):
            continue
        for line in text.splitlines():
            low = line.lower()
            if not any(t in low for t in _CIP_TRIGGERS):
                continue
            # Find trailing page number: last run of digits on the line
            m = re.search(r'(\d+)\s*$', line.strip())
            if not m:
                continue
            doc_page = int(m.group(1))  # 1-based page number from TOC
            # Find the matching index in all_pages (page numbers are 0-based there)
            for idx, (pnum, _) in enumerate(all_pages):
                if pnum + 1 == doc_page:
                    return idx
            # If exact match not found (roman numeral offset), search nearby
            for idx, (pnum, _) in enumerate(all_pages):
                if abs((pnum + 1) - doc_page) <= 5:
                    return idx
    return None


def load_pdf(path: Path) -> tuple[str, list[dict], dict]:
    """Extract text from a CIP PDF.

    Scans the FULL document to find the CIP chapter, then returns that
    section's text so Claude can analyze the actual project data.

    Detection priority:
    1. TOC page number reference (most reliable for large budget PDFs)
    2. First page with a strong body trigger (project-data keywords)
    3. First non-TOC page with a weak CIP trigger
    4. Fall back to start of document
    """
    try:
        import pdfplumber
    except ImportError:
        return ("[pdfplumber not installed]", [], {"format": "pdf"})

    # --- Pass 1: extract text from every page ---
    all_pages: list[tuple[int, str]] = []
    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        print(f"  Scanning {total_pages} pages for CIP section...")
        for i, pg in enumerate(pdf.pages):
            text = pg.extract_text() or ""
            if text.strip():
                all_pages.append((i, normalize_text(text)))

    # --- Pass 2: find where the CIP chapter starts ---
    toc_start = _toc_cip_page(all_pages)

    strong_start = None
    weak_start = None
    if toc_start is None:
        for idx, (page_num, text) in enumerate(all_pages):
            low = text.lower()
            if strong_start is None and any(t in low for t in _CIP_STRONG_TRIGGERS):
                strong_start = idx
                break
            if weak_start is None and any(t in low for t in _CIP_TRIGGERS):
                if not _is_toc_page(text):
                    weak_start = idx

    if toc_start is not None:
        cip_start, method = toc_start, "TOC"
    elif strong_start is not None:
        cip_start, method = strong_start, "strong trigger"
    elif weak_start is not None:
        cip_start, method = weak_start, "weak trigger"
    else:
        cip_start, method = 0, "fallback"

    # Collect up to 100 pages of CIP content
    cip_pages = all_pages[cip_start: cip_start + 100]
    page_num_start = cip_pages[0][0] + 1 if cip_pages else 1

    print(f"  CIP section found at page {page_num_start} ({method}, "
          f"{len(cip_pages)} pages collected).")

    full_cip_text = "\n\n".join(
        f"[page {p+1}]\n{t}" for p, t in cip_pages
    )

    # --- Pass 3: find per-project detail pages ---
    # Some CIPs have a summary chapter (what the TOC pointed to) plus a separate Appendix of
    # per-project sheets located ELSEWHERE in the document.  Scan the whole PDF for those.
    _PROJECT_PAGE_SIGNALS = [
        "project number", "project no", "project id",
        "department:", "funding source:", "description:",
        "scope:", "total cost:", "project title",
        "project name", "project manager",
    ]
    _SUMMARY_PAGE_SIGNALS = [
        "table of contents", "executive summary", "by department", "by fund",
        "total uses", "total sources", "not recommended",
        "grand total", "five-year summary", "5-year summary",
    ]

    cip_page_indices = {p for p, _ in cip_pages}

    # Find detail pages inside the collected CIP section
    project_detail_start = 0
    for i, (_, text) in enumerate(cip_pages):
        low = text.lower()
        sig = sum(1 for s in _PROJECT_PAGE_SIGNALS if s in low)
        summ = sum(1 for s in _SUMMARY_PAGE_SIGNALS if s in low)
        if (sig >= 3 and summ == 0) or sig >= 5:
            project_detail_start = i
            break

    # Whole-document sweep: find detail pages NOT already in the CIP section.
    # Handles docs where Appendix A lives in a different chapter than the CIP summary.
    extra_detail_pages: list[tuple[int, str]] = []
    for page_num, text in all_pages:
        if page_num in cip_page_indices:
            continue
        low = text.lower()
        sig = sum(1 for s in _PROJECT_PAGE_SIGNALS if s in low)
        summ = sum(1 for s in _SUMMARY_PAGE_SIGNALS if s in low)
        if sig >= 4 and summ == 0:
            extra_detail_pages.append((page_num, text))

    if extra_detail_pages:
        extra_text = "\n\n".join(f"[page {p+1}]\n{t}" for p, t in extra_detail_pages[:50])
        full_cip_text = extra_text + "\n\n" + full_cip_text
        print(f"  Found {len(extra_detail_pages)} project-detail pages outside CIP section "
              f"(pages {extra_detail_pages[0][0]+1}–{extra_detail_pages[-1][0]+1}).")

    # 5-page sample: prefer extra_detail_pages if they exist, else fall back to cip_pages
    if extra_detail_pages:
        sample_pages = extra_detail_pages[:5]
    else:
        sample_pages = cip_pages[project_detail_start: project_detail_start + 5]

    project_sample_text = "\n\n".join(
        f"[page {p+1}]\n{t}" for p, t in sample_pages
    )
    sample_page_start = sample_pages[0][0] + 1 if sample_pages else page_num_start
    print(f"  Project detail sample: pages {sample_page_start}-"
          f"{sample_pages[-1][0]+1 if sample_pages else sample_page_start} "
          f"({len(project_sample_text)} chars).")

    # Try table extraction on the sample project pages for sample_rows
    sample_rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for p, _ in sample_pages[:5]:
            tables = pdf.pages[p].extract_tables()
            for tbl in tables[:1]:
                if tbl and len(tbl) > 1:
                    headers = [str(c or "").strip() for c in tbl[0]]
                    for row in tbl[1:6]:
                        d = {headers[j]: str(v or "").strip()
                             for j, v in enumerate(row) if j < len(headers)}
                        sample_rows.append(d)
            if len(sample_rows) >= 10:
                break

    dollar_unit = _detect_dollar_unit(full_cip_text[:8000])
    if dollar_unit != 1:
        print(f"  Dollar unit detected: x{dollar_unit} (values expressed in {'thousands' if dollar_unit == 1000 else 'millions'})")
    return (
        project_sample_text[:12000],   # raw_text shown in Step 1 = actual project pages
        sample_rows[:10],
        {
            "format": "pdf",
            "total_pages": total_pages,
            "cip_section_start_page": page_num_start,
            "cip_pages_collected": len(cip_pages),
            "project_sample_start_page": sample_page_start,
            "n_cols": len(sample_rows[0]) if sample_rows else 0,
            "estimated_rows": len(sample_rows),
            "full_cip_text": full_cip_text,        # all CIP pages, for the extraction script
            "project_sample_text": project_sample_text,  # 5 project pages, for analysis prompts
            "dollar_unit_hint": dollar_unit,
        },
    )


def load_html(path: Path) -> tuple[str, list[dict], dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ("[beautifulsoup4 not installed]", [], {"format": "html"})

    raw = _try_encodings(path)
    soup = BeautifulSoup(raw, "html.parser")
    tables = soup.find_all("table")

    if not tables:
        text = normalize_text(soup.get_text(" ", strip=True))
        return text[:3000], [], {"format": "html", "n_tables": 0}

    tbl = tables[0]
    header_row = tbl.find("tr")
    headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])] if header_row else []

    rows = []
    for tr in tbl.find_all("tr")[1:11]:
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if cells:
            row = {headers[i] if i < len(headers) else str(i): v for i, v in enumerate(cells)}
            rows.append(row)

    preview = [" | ".join(headers)] + [" | ".join(str(v)[:30] for v in r.values()) for r in rows[:4]]

    preview_text = "\n".join(preview)
    dollar_unit = _detect_dollar_unit(preview_text, headers)
    if dollar_unit != 1:
        print(f"  Dollar unit detected: x{dollar_unit} (values expressed in {'thousands' if dollar_unit == 1000 else 'millions'})")
    return (
        preview_text,
        rows,
        {"format": "html", "n_tables": len(tables), "n_cols": len(headers), "dollar_unit_hint": dollar_unit},
    )


def load(path: str | Path) -> tuple[str, list[dict], dict]:
    """
    Dispatch to the right loader based on file extension.
    Returns (raw_text, sample_rows, metadata).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Source file not found: {p}")

    suffix = p.suffix.lower()
    if suffix == ".csv":
        return load_csv(p)
    if suffix in (".xlsx", ".xls"):
        return load_excel(p)
    if suffix == ".pdf":
        return load_pdf(p)
    if suffix in (".html", ".htm"):
        return load_html(p)

    # Try CSV as fallback for unknown extensions
    return load_csv(p)
