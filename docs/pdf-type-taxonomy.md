# CIP Source File Type Taxonomy

Used by: `guide_rag.py` (scoring), `design.py` (extraction instructions),
Step 2.5 of `CIP_Extraction.ipynb` (curator confirmation).

---

## Type Definitions

### `pdf_table`
Financial data is in a columnar table with named column headers (year columns,
total column). Projects are rows. May have department header rows and subtotal
rows interspersed. The table section is typically 2–10 pages within a larger
document. Descriptions, if present, are in the same rows as dollar amounts (or
absent entirely).

**Key discriminator:** Is there a column header row with year labels (e.g.
"FY 2027 FY 2028 … Total")? If yes → `pdf_table` or `pdf_table_split`.

---

### `pdf_table_split`
Same as `pdf_table` for the financial data, but project descriptions/narratives
are in a *separate section* of the same PDF at a different page range. The two
sections must be joined by fuzzy project name matching after extracting each
independently.

**Key discriminator:** Does the document have a dedicated "Project Detail" or
"Recommended Capital Initiatives" narrative section *separate* from the Appendix
or summary table that holds the dollar amounts? If yes → `pdf_table_split`.

---

### `pdf_one_per_page`
Each project occupies its own page (or a consistent fixed number of pages). The
page boundary *is* the project boundary. Fields appear as labeled key-value pairs
(e.g. "Project Name:", "Department:", "Total Cost:") rather than table columns.

**Key discriminator:** Does flipping through the PDF show one project per page
with a repeating field layout? If yes → `pdf_one_per_page`.

---

### `pdf_project_blocks`
Projects are stacked vertically in free-form text — not a table, not one per
page. Each project starts with a recognizable header line (project number, bold
name, or section break) and fields follow in paragraph or label:value form below
it. Multiple projects share each page.

**Key discriminator:** Are projects in running text with a repeating header
pattern, but no column structure and not one-per-page? If yes →
`pdf_project_blocks`.

---

### `wide_excel`
Spreadsheet where each row is a project and columns are explicitly named. Year
columns and fund columns appear as header names. May have merged header rows or
department grouping rows.

---

### `csv_tabular`
Same row-per-project structure as `wide_excel` but delivered as a CSV export.
No merged cells; column headers in row 1.

---

## Key Discriminator Decision Tree

```
Is it a spreadsheet / CSV?
  Yes → wide_excel or csv_tabular (check file extension)
  No (it's a PDF) →
    Does it have a column header row with year labels?
      No →
        One project per page?
          Yes → pdf_one_per_page
          No  → pdf_project_blocks
      Yes (it's tabular) →
        Are descriptions in a separate section from the dollar table?
          No  → pdf_table
          Yes → pdf_table_split
```

---

## Document Audit (Weeks 1–20)

Fill in one row per source PDF. `confirmed_type` is what a curator verified by
opening the document; `notes` captures anything unusual.

| Week | Agency ID | Pages | Confirmed Type | Notes |
|------|-----------|-------|----------------|-------|
| | | | | |

---

## Open Questions

- Are there hybrid types (e.g. Excel with a PDF narrative attachment)?
- Do any documents have per-fund sub-tables within a `pdf_table` layout?
- Are there `pdf_table` documents where the table spans the full document
  (no separate narrative pages), vs. table-at-end like Alpharetta?
