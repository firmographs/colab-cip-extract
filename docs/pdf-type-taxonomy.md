# CIP Source File Type Taxonomy

Used by: `guide_rag.py` (scoring), `design.py` (extraction instructions),
Step 2.5 of `CIP_Extraction.ipynb` (curator confirmation).

---

## Type Definitions

### `pdf_table`
Financial data is in a columnar table with named column headers (year columns,
total column). Projects are rows. May have department header rows and subtotal
rows interspersed. The table section is typically 1–10 pages within a larger
document. Descriptions, if present, are in the same rows as dollar amounts (or
absent entirely).

**Key discriminator:** Is there a column header row with year labels (e.g.
"FY 2027 FY 2028 … Total")? If yes → `pdf_table` or `pdf_table_split`.

**Observed prevalence (50-agency sample):** 70% (35 of 50 docs)

---

### `pdf_table_split`
Same as `pdf_table` for the financial data, but project descriptions/narratives
are in a *separate section* of the same PDF at a different page range. The two
sections must be joined by fuzzy project name matching after extracting each
independently.

**Key discriminator:** Does the document have a dedicated "Project Detail" or
"Recommended Capital Initiatives" narrative section *separate* from the Appendix
or summary table that holds the dollar amounts? If yes → `pdf_table_split`.

**Observed prevalence (50-agency sample):** 10% (5 of 50 docs)

---

### `pdf_dot_stip_blocks`
State DOT STIP/CIP documents where each project occupies exactly **3 lines**:
CN (construction), PE (preliminary engineering), and RW (right-of-way). Each
line carries year-by-year cost columns and a Total column. Costs are expressed
in thousands of dollars. The column header row says something like
`Ph 2026 2027 2028 … PREL Total Federal Match`.

A PREL column holds costs for unfunded future phases (no year assigned); use
`max(PREL, Total)` per phase row because PREL-only rows have Total=0.

Projects deduplicate by Key No. (5-digit number on the PE line); transit
projects may repeat the same key across fund sources — keep first occurrence.

**Key discriminator:** Does the PDF show 3-line CN/PE/RW blocks with year
columns, a PREL column, and costs in thousands? If yes → `pdf_dot_stip_blocks`.

---

### `pdf_project_blocks`
Each project occupies 1–2 pages with a consistent repeating template containing
narrative description, project metadata (manager, dates, status), and an inline
cost table. The template is anchored by a recognizable heading (e.g. "Overview",
project title repeated, or a labeled field block). Department summary rollup tables
may appear between project sheets but are not themselves project records.

**Key discriminator:** Does flipping through the PDF show a repeating per-project
template with title → metadata fields → cost table, spanning 1–2 pages per project?
If yes → `pdf_project_blocks`.

**Note on `pdf_one_per_page`:** This type was not observed in the 50-agency sample.
In practice, docs with one project per page all use the `pdf_project_blocks` template
structure. `pdf_one_per_page` may be retired or merged into `pdf_project_blocks`.

**Observed prevalence (50-agency sample):** 18% (9 of 50 docs)

---

### `pdf_one_per_page`
Each project occupies its own page (or a consistent fixed number of pages). The
page boundary *is* the project boundary. Fields appear as labeled key-value pairs
(e.g. "Project Name:", "Department:", "Total Cost:") rather than table columns.

**Key discriminator:** Does flipping through the PDF show one project per page
with a repeating field layout? If yes → `pdf_one_per_page`.

**Observed prevalence (50-agency sample):** 0% — not observed; see note under
`pdf_project_blocks` above.

---

### `image_chart`
The CIP data is presented primarily as a chart, graph, or image rather than
extractable text. pdfplumber extracts zero or near-zero rows from the relevant
pages. The financial values cannot be extracted via OCR text parsing.

**Key discriminator:** Does pdfplumber return empty or near-empty text for the
CIP pages while the PDF visually shows a bar chart, pie chart, or infographic?
If yes → `image_chart`. Skip and flag; do not attempt numeric extraction.

**Observed prevalence (50-agency sample):** 2% (1 of 50 docs — greenwood.in.gov)

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
    Does pdfplumber return only images/empty text for CIP pages?
      Yes → image_chart (skip and flag)
      No (text is extractable) →
        Does it have a column header row with year labels?
          No →
            Repeating per-project template (title → metadata → cost table)?
              Yes → pdf_project_blocks
              No  → pdf_project_blocks (default for running-text projects)
          Yes (it's tabular) →
            Are projects in 3-line CN/PE/RW blocks with a PREL column (DOT STIP)?
              Yes → pdf_dot_stip_blocks
              No →
                Are descriptions in a separate section from the dollar table?
                  No  → pdf_table
                  Yes → pdf_table_split
```

---

## Type Distribution (50-Agency Sample, Weeks 13–20)

| Type | Count | % |
|------|-------|---|
| `pdf_table` | 35 | 70% |
| `pdf_project_blocks` | 9 | 18% |
| `pdf_table_split` | 5 | 10% |
| `image_chart` | 1 | 2% |
| `pdf_one_per_page` | 0 | 0% |
| `wide_excel` | 0 | 0% |
| `csv_tabular` | 0 | 0% |

**Genre breakdown:**
- `budget_attachment`: 64% (32) — CIP embedded in full operating/capital budget
- `cip_standalone`: 32% (16) — dedicated CIP document
- `tip_standalone`: 4% (2) — transportation improvement program

**Dollar unit risk:**
- 42 docs: dollar_unit=1 (raw dollars, no scaling risk)
- 6 docs: dollar_unit=HIGH risk (values in thousands or millions per column header)
- 2 docs: dollar_unit=low risk

---

## Document Audit (50-Agency Sample, Weeks 13–20)

Stratified sample across TLD and file size. `confirmed_type` is curator-verified
by reading the PDF in 4 passes (cover/TOC, CIP entry, mid-section, tail) plus
cross-checking the extraction guide and final CSV.

| Week | Agency | Pages | CIP Pages | Confirmed Type | Genre | Dollar Unit | Confidence | Evidence Summary |
|------|--------|-------|-----------|---------------|-------|-------------|------------|-----------------|
| w1726 | kinggeorgecountyva.gov | 1 | 1 | pdf_table | cip_standalone | 1 | high | 3-column table (Title/Cost/GL Code), 10 project rows, single page |
| w1326 | idaho.gov | 9 | 2 | pdf_table | budget_attachment | 1 | medium | Budget packet; project-bearing pages (8-9) are tabular with fund/cost-type columns |
| w1926 | whiteplainsny.gov | 96 | 46 | pdf_table | cip_standalone | 1 | high | Multi-dept worksheet tables with year columns (FY2025-26 through FY2031-32); descriptions inline |
| w1726 | marysvillewa.gov | 7 | 5 | pdf_table | tip_standalone | 1000 | high | TIP table with year columns (6-yr, 2026, 2027, 2028, 2029-2031); header repeats each page |
| w1426 | cid.utah.gov | 16 | 1 | pdf_table | budget_attachment | 1 | medium | Single-year budget packet; CIP is Attachment D (p.15) only: category-grouped list with 1 budget column |
| w1926 | springfield-ma.gov | 40 | 17 | pdf_table | cip_standalone | 1 | high | Appendix A project list: Grade/Dept/Project Name/Total Cost (4 columns, no per-year breakdown) |
| w1326 | covingtonky.gov | 116 | 2 | pdf_table | budget_attachment | 1 | high | Capital Budget table pp.21-22: projects as rows, fund sources as columns (11 fund cols + Total) |
| w1426 | columbiasc.gov | 50 | 1 | pdf_table | budget_attachment | 1 | high | CIP is last page of 50-page budget; project table: Project#/Name/Type/Council Districts/Funding |
| w1726 | greenwood.in.gov | 87 | 3 | image_chart | budget_attachment | 1 | low | Rolling Five-Year Capital Plan (pp.67-68) is a single embedded bar chart image; pdfplumber extracts no rows |
| w1426 | idahofallsidaho.gov | 190 | 9 | pdf_table | budget_attachment | 1 | high | 190-page city budget; CIP pp.50-58 with 3 fund-group sections; flat table per section |
| w1726 | galvestontx.gov | 568 | 71 | pdf_table | budget_attachment | 1 | high | 568-page city budget; CIP is Exhibit B pp.363-433 with 7 program sections; 7-col table per program |
| w1926 | madera.gov | 288 | 5 | pdf_table | budget_attachment | 1 | high | 288-page operating budget; CIP projects matrix pp.281-285: Dept/Project#/Priority/Year columns |
| w1726 | victorvilleca.gov | 655 | 165 | pdf_table | cip_standalone | 1 | medium | 655-page budget; CIP pp.491-655 has summary-by-category table (Proposal Name/Fund/Year columns) |
| w1726 | kentwa.gov | 416 | 28 | pdf_table | budget_attachment | 1000 | high | 416-page biennial budget; CIP pp.104-131 with 5 dept sections; flat table: Dept/Category/Project/Year cols |
| w1726 | wvc-ut.gov | 458 | 17 | pdf_project_blocks | budget_attachment | 1 | high | 458-page annual budget; CIP pp.423-439: per-project sheets with Overview/Details/Capital Cost/Funding Sources |
| w1426 | fredericksburgva.gov | 148 | 120 | pdf_project_blocks | cip_standalone | 1 | high | Each project occupies one page under FY 2025 CIP header: title/description/cost table/funding |
| w1626 | cityofplacerville.org | 17 | 9 | pdf_project_blocks | cip_standalone | 1 | high | 8 project pages each with CIP number, DESCRIPTION, COST SUMMARY, POTENTIAL FUNDING sections |
| w1826 | cdaid.org | 136 | 8 | pdf_table | budget_attachment | 1 | high | CIP pp.123-130 inside 136-page annual budget; project rows grouped by department |
| w1826 | gbww.org | 123 | 8 | pdf_table | budget_attachment | 1000 | high | Wide multi-year capital fund table (CY2025-CY2046, 21 columns) embedded in annual budget |
| w1826 | northlibertyiowa.org | 10 | 10 | pdf_table | cip_standalone | 1 | high | Standalone 5-year CIP with annual schedule sections (FY26-FY30); wide table with fund/project rows |
| w1626 | swbno.org | 9 | 9 | pdf_table | cip_standalone | 1 | high | Standalone 10-year capital program (2025-2034); 151 project rows with System/Category/Division/PM |
| w1426 | cliftonnj.org | 99 | 9 | pdf_table | budget_attachment | 1 | high | NJ municipal budget format; 6-Year CIP on pp.86-94 with Sheet 40c (year schedule) + Sheet 40d (funding) |
| w1326 | cityofbowie.org | 384 | 104 | pdf_project_blocks | budget_attachment | 1 | high | 51 projects in 384-page budget; each project occupies 2 pages (description + cost summary) |
| w1726 | emwd.org | 218 | 2 | pdf_table | budget_attachment | 1 | high | CIP is 2 pages in 218-page biennial budget: 5-year category summary table + project detail table |
| w1826 | imperialctc.org | 178 | 45 | pdf_table | tip_standalone | 1 | medium | TIP embedded in 178-page management committee meeting packet; budget table with year columns |
| w1826 | augustaks.org | 238 | 24 | pdf_project_blocks | budget_attachment | 1 | high | One project data sheet per page in Appendix D (pp.207-230); each page has project metadata + cost table |
| w1826 | spokanevalley.org | 168 | 1 | pdf_table | budget_attachment | 1 | high | Single-page capital outlay summary in 168-page budget; one row per purchase with fund/GL columns |
| w1426 | kenoshacounty.org | 572 | 161 | pdf_table | budget_attachment | 1 | high | 161-page CIP appendix in 572-page county budget; 104 projects in 17 divisions; multi-year table |
| w1326 | centralsan.org | 382 | 14 | pdf_table_split | budget_attachment | 1 | high | Two-part CIP: pp.1-11 narrative description tables + pp.213-223 multi-year financial table; joined by title matching |
| w1726 | cityofevanston.org | 427 | 4 | pdf_table | budget_attachment | 1 | high | Single-year (2026) capital outlay table in 427-page budget; 133 rows by fund/category/GL |
| w1726 | mercedid.org | 370 | 277 | pdf_project_blocks | budget_attachment | 1 | high | 68 Capital Project Justification Form pages (one per project); guide incorrectly stated csv_tabular |
| w1926 | ci.camden.nj.us | 101 | 11 | pdf_table | budget_attachment | 1 | high | NJ standard municipal budget; 6-Year CIP pp.86-96 with Sheet 40c (year schedule) + Sheet 40d (funding sources) |
| w1926 | wheaton.il.us | 164 | 164 | pdf_table_split | cip_standalone | 1 | high | Full CIP document with schedule tables AND 104 separate Project Description Worksheets; two-section join required |
| w2026 | vrf.us | 142 | 134 | pdf_project_blocks | cip_standalone | 1 | high | One project page per project with Category/Department header; FY cost rows with funding source columns |
| w1726 | apgov.us | 551 | 27 | pdf_table_split | budget_attachment | 1 | high | 5-year CIP plan table (pp.431-435) plus separate Capital Improvement Reports section (pp.441-457) |
| w1726 | co.scott.mn.us | 237 | 225 | pdf_project_blocks | cip_standalone | 1 | high | Per-project block format across Transportation, Parks, Buildings, Equipment; each block has cost table |
| w1326 | nan.usace.army.mil | 16 | 12 | pdf_table | cip_standalone | 1000000 | high | USACE FY26+ Workload Forecast; category-grouped procurement tables with cost ranges (not traditional CIP) |
| w1326 | lrd.usace.army.mil.chi | 4 | 4 | pdf_table | cip_standalone | 1000 | high | USACE LRC 4-page workload forecast; two parallel table sections with project/location/cost columns |
| w1826 | summitoh.net | 72 | 57 | pdf_table_split | cip_standalone | 1 | medium | Two distinct table sections: pp.16-67 2026-only budget tables + pp.68-71 6-year schedule; separate joins |
| w1726 | cityhs.net | 449 | 7 | pdf_table | budget_attachment | 1 | high | CIP 2027-2031 tables on pp.426-432 of 449-page budget book; department-labeled 5-year table pages |
| w1326 | netamu.com | 3 | 3 | pdf_table | cip_standalone | 1 | high | 3-page municipal utility CIP (Electric/Water/Communications); each page is a section table with year columns |
| w1426 | housingforhouston.com | 47 | 44 | pdf_table | cip_standalone | 1 | high | HUD Form 50075.2 Five-Year Capital Fund Action Plan; work items by development/property page |
| w2026 | tampaairport.com | 32 | 2 | pdf_table | budget_attachment | 1 | high | Schedule 6 (capital improvement schedule) in 32-page airport budget; single-year FY2026 table |
| w1926 | mcounty.com | 266 | 2 | pdf_table | budget_attachment | 1 | high | Fund 605 Capital Projects detail on 2 pages of 266-page Midland County TX budget; 288 CSV rows |
| w1326 | scmtd.com | 85 | 6 | pdf_table_split | budget_attachment | 1000 | high | 85-page transit budget; CIP financial table on p.62 (rotated 90 deg) + narrative descriptions pp.63-65 |
| w1926 | wtmua.com | 62 | 8 | pdf_table | budget_attachment | 1 | high | NJ Authority Budget; 5-Year CIP pp.53-60 is standard multi-year table (SEWER/WATER rows, FY2027-2032 cols) |
| w1926 | paramountcity.com | 280 | 3 | pdf_table | budget_attachment | 1 | high | CA Adopted Budget; CIP pp.207-209 flat multi-year table with [CODE] project IDs, 5 year columns |
| w1726 | lbwl.com | 73 | 4 | pdf_table | budget_attachment | 1 | high | Board meeting packet; CIP = pp.53-56 CAPITAL PORTFOLIO tables with FY2026-2031 columns (4 grouped sections) |
| w1826 | dunedingov.com | 576 | 205 | pdf_project_blocks | budget_attachment | 1 | high | FL Adopted Budget pp.298-502; per-project template: Title→Overview→Metadata→Description→Capital Cost table |
| w1826 | rfta.com | 122 | 1 | pdf_table | budget_attachment | 1000 | high | Transit annual budget; Capital Expenditures is a single page (p.79) 3-col table (in 1,000s) |
| w1926 | idaho.gov_dot | 172 | 148 | pdf_dot_stip_blocks | tip_standalone | 1000 | high | Idaho DOT STIP 2026-2032; pages 25-121 are 3-row CN/PE/RW blocks with PREL+Total columns; 577 projects, $3.487B |

---

## Open Questions — Resolved by Audit

**Q: Are there hybrid types (e.g. Excel with a PDF narrative attachment)?**
Not observed in the 50-agency sample. All documents were PDFs or embedded tables
within PDF budget books. The `wide_excel` and `csv_tabular` types were not
encountered (0 of 50). Note: mercedid.org was labeled `csv_tabular` in the pipeline
but the PDF structure is `pdf_project_blocks` — the CSV was a separate export file,
not the source format.

**Q: Do any documents have per-fund sub-tables within a `pdf_table` layout?**
Yes — common. Multiple docs (galvestontx.gov, covingtonky.gov, cliftonnj.org,
madera.gov, northlibertyiowa.org) use department or fund section headers as
`dept_header_rows` within a flat table structure. This is normal `pdf_table` with
dept_header_rows=yes; no new type needed.

**Q: Are there `pdf_table` documents where the table spans the full document
(no separate narrative pages), vs. table-at-end like Alpharetta?**
Both patterns observed frequently. Full-document CIP tables: swbno.org (9 pages,
all CIP), northlibertyiowa.org (10 pages, all CIP), netamu.com (3 pages). Table
as section within larger budget: most budget_attachment cases. No new type needed.

## New Open Questions (raised by audit)

- **`pdf_one_per_page` vs `pdf_project_blocks`**: All per-project-template docs
  in the sample were classified as `pdf_project_blocks` (not `pdf_one_per_page`).
  Consider retiring `pdf_one_per_page` or clarifying it applies only to docs where
  the page break strictly equals the project boundary with NO multi-page projects.

- **USACE Workload Forecast format**: nan.usace.army.mil and lrd.usace.army.mil.chi
  are procurement workload tables, not traditional municipal CIPs. Values are cost
  ranges (e.g., "$25-100M"), not point estimates. Consider a sub-label or note in
  the pipeline to flag this doc genre.

- **Dollar unit scaling (dollar_unit=1000 or 1000000)**: 8 of 50 docs (16%) have
  HIGH or low dollar unit risk. The `dollar_unit_how` signal "column_header" is the
  most reliable detection point; "footnote" is riskier (easy to miss). The pipeline
  should explicitly log this field for every doc.
