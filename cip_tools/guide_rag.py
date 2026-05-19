"""
Guide RAG: find the best past *_guide.md examples for a new CIP.

Priority:
  1. Prior-year guide for the SAME agency (exact domain match) — sole example.
  2. Similar guides retrieved by format + doc-type + year-span — fallback.

Index stored at:
  /content/drive/Shareddrives/0_cip_data/extract/_guide_index.json

Build once per session via build_index(); reload cheaply with load_index().
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------

def _extract_metadata(guide_path: str) -> dict[str, Any]:
    """Parse a guide file and return a metadata dict (regex only, no LLM)."""
    try:
        text = Path(guide_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    fname = Path(guide_path).name  # e.g. wallawallawa.gov_tip_2026-2031_guide.md
    agency_id = fname.replace("_guide.md", "")

    # Domain: everything up to and including the first TLD segment
    domain_m = re.match(r"^([a-z0-9][a-z0-9.\-]*\.[a-z]{2,6})", agency_id)
    agency_domain = domain_m.group(1) if domain_m else agency_id.split("_")[0]

    # Doc type
    if "_tip_" in agency_id or agency_id.endswith("_tip"):
        doc_type = "tip"
    elif "_cfp_" in agency_id or agency_id.endswith("_cfp"):
        doc_type = "cfp"
    else:
        doc_type = "cip"

    # Format type — check what library the embedded script uses
    if "pdfplumber" in text:
        format_type = "pdf"
    elif "read_excel" in text or "openpyxl" in text:
        format_type = "excel"
    elif "csv.DictReader" in text or "read_csv" in text:
        format_type = "csv"
    else:
        format_type = "other"

    # Year list from YEARS = [2026, 2027, ...]
    years: list[int] = []
    years_m = re.search(r"YEARS\s*=\s*\[([^\]]+)\]", text)
    if years_m:
        years = [int(y) for y in re.findall(r"\d{4}", years_m.group(1))]
    if not years:
        # Fallback: extract from agency_id year range e.g. 2026-2031
        yr_m = re.search(r"(\d{4})-(\d{4})", agency_id)
        if yr_m:
            y0, y1 = int(yr_m.group(1)), int(yr_m.group(2))
            years = list(range(y0, y1 + 1))

    # Section count from SECTION_MARKERS
    section_m = re.search(r"SECTION_MARKERS\s*=\s*\{([^}]+)\}", text, re.DOTALL)
    section_count = len(re.findall(r'"[^"]+"\s*:', section_m.group(1))) if section_m else 0

    # Has a compiled leader regex?
    has_leader_re = bool(re.search(r"LEADER_RE\s*=\s*re\.compile", text))

    return {
        "guide_path": guide_path,
        "agency_id": agency_id,
        "agency_domain": agency_domain,
        "doc_type": doc_type,
        "format_type": format_type,
        "years": years,
        "year_min": min(years) if years else 0,
        "year_max": max(years) if years else 0,
        "year_count": len(years),
        "section_count": section_count,
        "has_leader_re": has_leader_re,
        "char_count": len(text),
    }


def build_index(guide_root: str, index_path: str) -> dict:
    """Scan guide_root for *_guide.md files, extract metadata, write JSON index."""
    guide_root_p = Path(guide_root)
    print(f"Scanning {guide_root} for guide files...")

    guides = []
    for p in sorted(guide_root_p.rglob("*_guide.md")):
        meta = _extract_metadata(str(p))
        if meta:
            guides.append(meta)

    index = {
        "guides": guides,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "guide_root": guide_root,
    }
    Path(index_path).write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"Index built: {len(guides)} guides -> {index_path}")
    return index


def load_index(index_path: str) -> dict:
    """Load an existing guide index from JSON."""
    return json.loads(Path(index_path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def _parse_filename_features(filename: str) -> dict[str, Any]:
    """Extract domain, doc_type, year range from a source filename."""
    base = Path(filename).stem  # strip extension

    domain_m = re.match(r"^([a-z0-9][a-z0-9.\-]*\.[a-z]{2,6})", base)
    agency_domain = domain_m.group(1) if domain_m else base.split("_")[0]

    doc_type = "cip"
    if "_tip" in base:
        doc_type = "tip"
    elif "_cfp" in base:
        doc_type = "cfp"

    yr_m = re.search(r"(\d{4})-(\d{4})", base)
    year_min = int(yr_m.group(1)) if yr_m else 0
    year_max = int(yr_m.group(2)) if yr_m else 0
    year_count = (year_max - year_min + 1) if yr_m else 0

    return {
        "agency_domain": agency_domain,
        "doc_type": doc_type,
        "year_min": year_min,
        "year_max": year_max,
        "year_count": year_count,
    }


def find_prior_year(filename: str, index: dict) -> dict | None:
    """Return the most recent prior guide for this exact agency domain, or None."""
    feat = _parse_filename_features(filename)
    domain = feat["agency_domain"]
    candidates = [
        g for g in index["guides"]
        if g["agency_domain"] == domain
        and g.get("year_min", 0) < feat["year_min"]  # must be an older year
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda g: g.get("year_min", 0))


def find_similar(filename: str, analysis: dict, index: dict, top_k: int = 3) -> list[dict]:
    """Score all indexed guides against the new CIP and return top_k."""
    feat = _parse_filename_features(filename)
    fmt = analysis.get("format_type", "")
    # Normalise format_type: understand.py uses pdf_table / wide_excel / csv_tabular
    if "pdf" in fmt:
        fmt_norm = "pdf"
    elif "excel" in fmt or "xlsx" in fmt:
        fmt_norm = "excel"
    elif "csv" in fmt:
        fmt_norm = "csv"
    else:
        fmt_norm = fmt

    year_cols = analysis.get("year_cols", [])
    analysis_year_count = len(year_cols) if year_cols else feat["year_count"]

    scored = []
    for g in index["guides"]:
        score = 0

        # Format match is the most predictive — same PDF structure assumptions apply
        if g.get("format_type") == fmt_norm:
            score += 10

        # Doc type: TIP vs CIP is structurally significant
        if g.get("doc_type") == feat["doc_type"]:
            score += 5

        # Year count: 5-year vs 6-year changes column alignment logic
        if g.get("year_count") == analysis_year_count:
            score += 3

        # Domain tiebreaker: same top-level domain suffix (e.g. .wa.gov vs .co.gov)
        g_parts = g.get("agency_domain", "").rsplit(".", 2)
        f_parts = feat["agency_domain"].rsplit(".", 2)
        if len(g_parts) >= 2 and len(f_parts) >= 2 and g_parts[-2] == f_parts[-2]:
            score += 1  # same state suffix

        if score > 0:
            scored.append((score, g))

    scored.sort(key=lambda x: -x[0])
    return [g for _, g in scored[:top_k]]


# ---------------------------------------------------------------------------
# Guide excerpt extraction
# ---------------------------------------------------------------------------

def _extract_script_block(text: str) -> str:
    """Pull the main Python extraction script from a guide (the largest ```python block)."""
    blocks = re.findall(r"```python\s*\n([\s\S]*?)\n```", text)
    if not blocks:
        return ""
    # The extraction script is always the longest block in the guide
    return max(blocks, key=len).strip()


def _extract_config_constants(script: str) -> str:
    """Return the CONFIGURATION block — lines from start up to first class/dataclass/def run."""
    lines = script.splitlines()
    config_lines = []
    for line in lines:
        # Stop when we hit the data structures / function definitions
        if re.match(r"^(class |def |@dataclass)", line.strip()):
            break
        config_lines.append(line)
    return "\n".join(config_lines).strip()


def get_guide_excerpt(guide_path: str, mode: str = "config") -> str:
    """
    Return relevant excerpt from a guide file.

    mode="full"   — entire guide (for prior-year exact match)
    mode="config" — just the CONFIGURATION constants from the script (for similar guides)
    """
    try:
        text = Path(guide_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    if mode == "full":
        return text

    # For similar guides: return the script config constants + first few section markers
    script = _extract_script_block(text)
    if not script:
        return text[:3000]
    config = _extract_config_constants(script)
    return config[:3000]  # cap per guide so total stays manageable


# ---------------------------------------------------------------------------
# Main retrieval entry point
# ---------------------------------------------------------------------------

def retrieve(
    filename: str,
    analysis: dict,
    index: dict,
    top_k: int = 3,
) -> tuple[str, str]:
    """
    Find the best guide context for this new CIP.

    Returns (mode, context_text) where mode is "prior_year", "similar", or "none".
    """
    # 1. Prior-year exact match — highest quality, use full guide
    prior = find_prior_year(filename, index)
    if prior:
        excerpt = get_guide_excerpt(prior["guide_path"], mode="full")
        print(f"  Guide RAG: prior-year match → {prior['agency_id']}")
        return "prior_year", excerpt

    # 2. Similar guides — use config constants from top matches
    similar = find_similar(filename, analysis, index, top_k=top_k)
    if not similar:
        print("  Guide RAG: no matches found.")
        return "none", ""

    parts = []
    for g in similar:
        excerpt = get_guide_excerpt(g["guide_path"], mode="config")
        header = (
            f"--- Guide: {g['agency_id']} "
            f"(format={g['format_type']}, type={g['doc_type']}, years={g['years']}) ---"
        )
        parts.append(f"{header}\n{excerpt}")
        print(f"  Guide RAG: similar → {g['agency_id']}")

    return "similar", "\n\n".join(parts)
