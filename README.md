# DEPRECATED — colab-cip-extract

> **This repository is archived. No further development or maintenance planned.**

## Replaced by

**[firmographs/fg-cip-extract](https://github.com/firmographs/fg-cip-extract)** — vision-first CIP extraction pipeline built on Cloudflare Workers + Cloud Run. Supersedes this notebook as of 2026-05-26.

The new system provides:
- Automated vision verification (Gemini 2.5 Flash) with per-row image cropping
- Iterative verify-fix loop with stalemate detection
- Persistent R2 artifact storage per job for auditability and rerun
- React UI for job management and curator escalation
- Canonical script fast-path for repeat extractions

## What this was

A Google Colab notebook pipeline that used Claude to analyze CIP PDF structure and generate a pdfplumber extraction script. Extracted project data from PDFs into `_final.csv` for downstream scoring notebooks (`colab-cip-prepare`, `colab-cip-tam`, `colab-cip-sam`, `colab-cip-bake`).

The downstream scoring notebooks are **not** deprecated — they continue to consume the CSV output produced by fg-cip-extract.
