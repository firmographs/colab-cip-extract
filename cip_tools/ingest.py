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

    return (
        "\n".join(lines[:60]),
        rows[:10],
        {"format": "csv", "n_cols": n_cols, "estimated_rows": n_rows},
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

    return (
        "\n".join(preview_lines),
        rows,
        {
            "format": "xlsx",
            "n_cols": len(col_names),
            "estimated_rows": len(df2),
            "header_row": header_row,
            "columns": col_names,
        },
    )


_CIP_TRIGGERS = [
    "capital improvement",
    "capital improvements program",
    " cip ",
    "cip project",
    "cip sheet",
]
_CIP_END_TRIGGERS = ["appendix", "glossary", "index", "debt service"]


def load_pdf(path: Path) -> tuple[str, list[dict], dict]:
    """Extract text from a CIP PDF.

    Scans the FULL document to find the CIP chapter, then returns that
    section's text so Claude can analyze the actual project data — not
    just the cover or operating-budget pages that appear first.
    """
    try:
        import pdfplumber
    except ImportError:
        return ("[pdfplumber not installed]", [], {"format": "pdf"})

    # --- Pass 1: extract text from every page (no table detection yet) ---
    all_pages: list[tuple[int, str]] = []
    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        print(f"  Scanning {total_pages} pages for CIP section...")
        for i, pg in enumerate(pdf.pages):
            text = pg.extract_text() or ""
            if text.strip():
                all_pages.append((i, normalize_text(text)))

    # --- Pass 2: find where the CIP chapter starts ---
    cip_start = None
    for idx, (page_num, text) in enumerate(all_pages):
        low = text.lower()
        if any(t in low for t in _CIP_TRIGGERS):
            cip_start = idx
            break

    if cip_start is None:
        # No CIP section found — fall back to full doc sample
        cip_start = 0

    # Collect up to 60 pages of CIP content
    cip_pages = all_pages[cip_start: cip_start + 60]
    page_num_start = cip_pages[0][0] + 1 if cip_pages else 1

    raw_text = "\n\n".join(
        f"[page {p+1}]\n{t}" for p, t in cip_pages
    )

    # Try table extraction on the first few CIP pages for sample_rows
    sample_rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for p, _ in cip_pages[:10]:
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

    print(f"  CIP section found starting at page {page_num_start} "
          f"({len(cip_pages)} pages collected).")

    return (
        raw_text[:12000],
        sample_rows[:10],
        {
            "format": "pdf",
            "total_pages": total_pages,
            "cip_section_start_page": page_num_start,
            "cip_pages_collected": len(cip_pages),
            "n_cols": len(sample_rows[0]) if sample_rows else 0,
            "estimated_rows": len(sample_rows),
            "full_cip_text": raw_text,   # available to understand.py for deep passes
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

    return (
        "\n".join(preview),
        rows,
        {"format": "html", "n_tables": len(tables), "n_cols": len(headers)},
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
