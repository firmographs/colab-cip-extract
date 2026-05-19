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


def load_pdf(path: Path) -> tuple[str, list[dict], dict]:
    """Extract text from PDF using pdfplumber.
    For OCR'd PDFs, pdfplumber extracts text but not tables — that's normal.
    Claude works from the raw text to understand structure and write an extractor.
    """
    try:
        import pdfplumber
    except ImportError:
        return ("[pdfplumber not installed]", [], {"format": "pdf"})

    pages_text = []
    sample_rows = []
    total_pages = 0

    with pdfplumber.open(path) as pdf:
        total_pages = len(pdf.pages)
        # Sample: first 10 pages + a mid-document slice for context
        sample_indices = list(range(min(10, total_pages)))
        if total_pages > 20:
            mid = total_pages // 2
            sample_indices += list(range(mid, min(mid + 5, total_pages)))

        for i in sample_indices:
            pg = pdf.pages[i]
            tables = pg.extract_tables()
            if tables:
                for tbl in tables[:1]:
                    if tbl and len(tbl) > 1:
                        headers = [str(c or "").strip() for c in tbl[0]]
                        for row in tbl[1:6]:
                            d = {headers[j]: str(v or "").strip() for j, v in enumerate(row) if j < len(headers)}
                            sample_rows.append(d)
                        pages_text.append(f"[page {i+1} table]\n" + " | ".join(headers))
                        for row in tbl[1:4]:
                            pages_text.append(" | ".join(str(v or "")[:40] for v in row))
            else:
                text = pg.extract_text() or ""
                if text.strip():
                    pages_text.append(f"[page {i+1}]\n" + normalize_text(text)[:800])

    has_tables = bool(sample_rows)
    return (
        "\n\n".join(pages_text)[:6000],
        sample_rows[:10],
        {
            "format": "pdf",
            "total_pages": total_pages,
            "n_cols": len(sample_rows[0]) if sample_rows else 0,
            "estimated_rows": len(sample_rows),
            "has_tables": has_tables,
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
