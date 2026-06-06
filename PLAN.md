# Altis Hackathon — Plan (Tier 1, with Tier 2/3 hooks)

> Living doc. Updated after notebook 02 inventory pass.

## TL;DR

- We have **sales-side transaction data for 3 portcos** (no AP, no payment dates).
- Two source-system exports (Yuki and Exact) with **different schemas**, plus aggregator sheets.
- For Tier 1 we forecast **bottom-up from historical AR** + a **DSO assumption for receipts** + a **gross-margin assumption for outflows**. Everything traceable and toggleable.
- **No ML.** Driver-decomposition with empirical seasonality. Defensible to a controller. (Auditability is 17/60 challenge points.)

## What's in `raw/received_original_data/`

| Source | Files | Schema | Rows | Notes |
|--------|-------|--------|------|-------|
| `Altis Groep — Portfolio P&L Data (Aggregated).json` | 1 | dict — 4 portcos (`winschoten`, `andijk`, `peter_ummels`, `heeze`) + metadata | small | aggregated P&L; sanity-check ground truth |
| `datasets/Altis dataset 1.xlsx` | 1 | KPI roll-up: GL rows × month cols × yearly sheets | tiny (3 rows/sheet) | monthly revenue summary; **sanity check** |
| `datasets/Altis dataset 2.xlsx` | 1 (6 sheets) | sheet `Totaal` = 41-col monthly KPI matrix; sheets `2023..2026` = transactions (6 cols); `Company E 2026` = AR invoice list | ~12k transactions | likely a third portco (uses journal `006 - Verkoop`) |
| `portfolio company 2 data/` (Peter Ummels, admin 82604) | 11 xlsx | **Schema A (Yuki)** — 7 cols, header row 12, meta rows 0-11 contain GL & period filter | ~15k raw | **overlapping period exports → must dedup on booking number** |
| `portfolio company data/` (other opco, GL 8000/8001/8002) | ~16 xlsx | **Schema B (Exact)** — 11 cols, header row 0, `Boekingstekst` gives driver text | ~14k | clean, GL embedded in filename and `Rekening` col |

## Schema map

### Schema A — Yuki export (Peter Ummels)
- 12 meta rows then `Nr · Per · Datum · Bkst.nr · Dagboek · Debet · Credit`
- Meta row 0: `Administratie: 82604 - Dakdekkersbedrijf Peter Ummels`
- Meta row 6: `Grootboekrekening | <GL> | Boekjaar | <year> | Periode | <range>`
- GLs seen: **8002** (Omzet belast 9%), **8004** (omzet belast met 0% of niet bij u belast), **8005** (omzet waarbij de heffing naar u is verlegd = subcontracting reverse-charge)
- **DUPLICATE RISK**: For GL 8005 every year there are two exports — one full-year (period 1-12) and one period 1-5. Dedup on `(administration, gl, booking_number, debit, credit)`.

### Schema B — Exact export (other opco, the GB files)
- `Rekening · Periode · Datum · Boeknummer · Trek · Debet · Credit · Boekingstekst · Dagboek · BTW · BTW-srt`
- Header row 0, no meta noise
- GL inferred from `Rekening` column and filename (`GB 8000` etc.)
- GLs seen: **8000** (Omzet hoog 21%), **8001** (Omzet verlegd), **8002** (Omzet laag 9%)
- `Boekingstekst` gives the human-readable driver classification — gold!

### Schema C — Dataset 2 yearly sheets
- `Datum · Bkst.nr · Dagboek · Debet · Credit · Btw-bedrag`
- Header row 0
- All `006 - Verkoop` (sales journal 006)
- Probably a **third portco** — different journal naming convention from Peter Ummels (which uses `80 - Verkoop`)

### Schema D — Dataset 2 Company E sheet
- `Factuurdatum · _ · Factuurnummer · _ · _ · Factuurbedrag`
- An invoice list (not a journal), with a meta row `Totaal vorige pagina` at row 2
- Useful as **AR positions** for the receipts forecast

### Schema E — Dataset 1 (KPI roll-up)
- Wide format: GL row × month col × yearly sheet
- Sanity check only — not part of the unified ledger

## Portco → source mapping (CONFIRMED — notebook 04)

| Portco | City | Source | Coverage |
|--------|------|--------|----------|
| `peter_ummels` | Brunssum, Limburg | `portfolio company 2 data/` (Yuki, admin 82604) | full Tx, ~9.8K rows |
| `heeze` | Noord-Brabant | `portfolio company data/` (Exact, GB 8000/8001/8002) | full Tx, ~12.1K rows |
| `winschoten` | Groningen | `datasets/Altis dataset 2.xlsx` yearly sheets | full Tx, ~12.3K rows |
| `andijk` | Noord-Holland | `datasets/Altis dataset 1.xlsx` (KPI roll-up only) | **NO Tx — monthly KPI only** |

Confirmation: JSON `description` fields + revenue matching against `winschoten.revenue` (2023 sum €7.16M ↔ silver dataset2 2023 net €7.16M = exact match).

For andijk we render historical aggregates only; forecast features show "aggregate only — no transactional data" for that portco.

## Critical gap: NO AP-side data

Every GL we've seen is in the 80xx Omzet (sales) range. No materials (typical 70xx), no subcontractor cost (typical 60xx), no payroll (40xx). No customer payment-date column either — only invoice/booking date.

**Implication for forecasting:** receipts come from AR shift; outflows are derived. Don't pretend we have AP data we don't. Concretely:

- `cash_in[w] = Σ invoices invoiced before w with empirical DSO landing in w`
- `cash_out[w] ≈ cash_in[w-shift] × (1 − gross_margin) × allocation[driver]`
  - Allocation split materials / subcontractor / labour via fixed proportions from the aggregated P&L JSON
- DSO and gross-margin are **toggleable assumptions**, not model parameters. Scenario = changing the toggles.

## Architecture (with Spark Connect constraint)

Serverless = Spark Connect = `fs.azure.account.key.*` is on the deny-list. We do NOT use Spark for blob I/O. We use `azure-storage-blob` + pandas for read/write, then `spark.createDataFrame(pdf)` for any heavy joining/aggregation. Output back to blob via pandas/pyarrow → upload bytes.

```
raw/received_original_data/   ← bronze, as-received
silver/by_source/*.parquet    ← parsed per source, per file, traceable
silver/bookings.parquet       ← unified canonical ledger (this is the silver KPI)
gold/ar_open.parquet          ← open AR positions snapshot
gold/cashflow_history_weekly.parquet
gold/cashflow_forecast_13w.parquet
gold/covenant_headroom.parquet
gold/_meta/*.json             ← schema snapshots and run audits
```

## Canonical silver schema (`silver/bookings.parquet`)

| col | dtype | notes |
|---|---|---|
| source_file | string | full blob path, for click-through traceability |
| source_schema | string | `yuki` / `exact` / `dataset2_yearly` / `company_e` |
| portco | string | `peter_ummels` / `winschoten` / `andijk` / `heeze` / `<unknown>` |
| administration | string | e.g. `82604` |
| gl_account | string | e.g. `8000`, `8005` |
| gl_account_text | string | `Omzet hoog`, `omzet waarbij de heffing naar u is verlegd`, ... |
| journal | string | `Dagboek` — `80 - Verkoop`, `Verkoopboek 1`, `006 - Verkoop` |
| period | int | 1-12 |
| booking_date | date | |
| booking_number | string | `Bkst.nr.` / `Boeknummer` — natural key for dedup |
| debit | decimal(18,2) | |
| credit | decimal(18,2) | |
| amount_net | decimal(18,2) | `credit - debit` (positive = revenue) |
| vat_amount | decimal(18,2) | nullable |
| vat_type | string | nullable: `hoog` / `laag` / `verlegd` / `geen` |
| boekingstekst | string | nullable, free text |
| ingested_at | timestamp | UTC |

## Driver classification (gold)

| GL pattern | driver | direction |
|---|---|---|
| `8000` Omzet hoog | milestone_in (AR) | inflow |
| `8001` Omzet verlegd / `8005` ... verlegd | milestone_in (subcontracted, BTW-shifted) | inflow |
| `8002` Omzet laag / belast 9% | milestone_in | inflow |
| `8004` Omzet 0%/niet belast | milestone_in | inflow |
| `006 - Verkoop` / `80 - Verkoop` / `Verkoopboek 1` (when GL unknown) | milestone_in | inflow |
| (NOT IN DATA) materials / subcontractor cost | derived from margin assumption | outflow |

A controller can edit this mapping in `databricks/notebooks/lib/gl_mapping.py` (TBD) without touching pipeline code.

## Forecasting math — Tier 1

For each portco `p` and week `w` (w = next 13 weeks):

```
cash_in_milestone[p, w] =
  Σ over historical bookings b in portco p where
    invoice_date(b) + dso(p) lands in week w
  of amount_net(b)   ─── deterministic for past invoices

cash_in_future[p, w] =
  Σ over projected_invoices(p, week w − dso(p))   ─── projection from seasonality

cash_out_derived[p, w] =
  cash_in_total[p, w − margin_lag] × (1 − gross_margin(p))
  split across materials_share, subcontractor_share, labour_share

net_cash[p, w] = cash_in[p, w] − cash_out[p, w]
covenant_headroom[p, w] = covenant_baseline(p) − rolling_metric(net_cash[p, w−12 .. w])
```

Toggles: `dso(p)`, `gross_margin(p)`, `materials_share / subcon_share / labour_share`, `revenue_growth_assumption`. Each toggle has a default sourced from the aggregated JSON.

Scenarios: Base / Wet-quarter / Dry-quarter. Wet-quarter shifts `dso` up by ~7 days for outdoor work and reduces revenue projection by ~10% for the wet quarter (defaults — controller can change).

## Notebook roadmap

| # | Notebook | Output | Status |
|---|----------|--------|--------|
| 01 | `01_connect_storage.py` | explore `raw/database.xlsx` (now legacy) | ✅ done |
| 02 | `02_explore_received_data.py` | full inventory snapshot | ✅ done |
| 03 | `03_silver_cleanse.py` | `silver/by_source/*.parquet` + `silver/bookings.parquet` | 🏗️ next |
| 04 | `04_silver_validate.py` | cross-check silver vs. aggregated JSON totals | next |
| 05 | `05_gold_classify.py` | `gold/bookings_classified.parquet` (driver per row) | **🛑 Tier 1 data foundation** |
| 06 | `06_gold_forecast_13w.py` | `gold/cashflow_forecast_13w.parquet` + assumptions table | |
| 07 | `07_gold_covenant_scenario.py` | covenant headroom + base / wet / dry scenarios | **🛑 Tier 1 submittable** |
| 08+ | weather attribution, role-marts, dashboard | Tier 2 reach | |

## Open questions for the team (track here, don't block)

1. Which Exact-export folder maps to which portco (`winschoten` / `andijk` / `heeze`)? — answer by joining monthly totals to aggregated JSON.
2. Is there AP-side data we haven't received yet? — ask sponsor.
3. Are there project/WIP/invoice files? — search inbox / SharePoint.
4. Covenant terms? — need the docx/pdf the brief mentioned.

## Data retention

All `raw/`, `silver/`, `gold/`, secret scope, local downloads → deleted ≤3 days after event. GitHub history of any data files is a liability — pre-event teardown also covers `git filter-repo` if files leaked into history.
