# Frontend Brief — Altis Cash Flow Dashboard (Lovable mockup)

> Read CHALLENGE.md first. This file tells you what data is real, what is mocked, and how to label things honestly. We are shipping **Tier 1 (CFO 13-week cash forecast)** with a graceful nod toward the other roles.

## What we have (real data)

- **~31,734 deduplicated booking rows** across **3 portcos** of sales-side ledger data.
- Source systems: **Yuki** (Peter Ummels) and **Exact** (other opco), plus a third opco from `Altis dataset 2.xlsx`. All transactional.
- **GL accounts 8000–8005** only — all `Omzet` (revenue) variants: `Omzet hoog 21%`, `Omzet laag 9%`, `Omzet verlegd` (reverse-charge subcontracting), `Omzet 0%/niet belast`.
- Per booking: `booking_date`, `booking_number`, `debit`, `credit`, `amount_net`, `vat_amount`, `vat_type`, `journal`, `boekingstekst` (free-text driver hint, Exact only).
- The **aggregated JSON** lists 4 portcos: `winschoten` (Groningen), `andijk` (Noord-Holland), `peter_ummels` (Brunssum, Limburg), `heeze` (Noord-Brabant). **Mapping now confirmed** via JSON `description` fields + revenue-matching:
  - `peter_ummels` ← Yuki exports (admin 82604)
  - `heeze` ← Exact GB 8000/8001/8002 exports (Valkenswaard/Bergeijk/Waalre area)
  - `winschoten` ← `Altis dataset 2.xlsx` yearly sheets
  - `andijk` ← **KPI roll-up only** from `Altis dataset 1.xlsx`; **no transactional data**
- Approx annual revenue (2024, from data + JSON):
  - peter_ummels ~ €11.5M
  - winschoten ~ €7.7M (also: 2023 €7.2M, 2025 €8.2M)
  - heeze ~ €12.1M (likely understated in JSON's "floor estimate")
  - andijk ~ €15.8M (KPI only)
- **Cash-lag policy (CFO-confirmed 2026-06-06):** Invoice is sent **≤ 7 days after the client signs the acceptance document**, and **invoices are paid within 30 days**. Total cash lag from acceptance ≈ **37 days**. Our `booking_date` is the invoice booking date, so **DSO from `booking_date` is 30 days exactly**, uniform across all portcos — no longer a controller-guessed parameter, it's an observed business policy. The 7-day acceptance→invoice lag is implicit in the historical invoicing baseline we forecast from.

## What we DON'T have (label these as mocked / placeholder)

- **No AP-side data.** No materials cost, no subcontractor cost, no payroll. Outflows are **derived from a gross-margin assumption**, not observed.
- **No payment-date column.** Receipts are modelled as `invoice_date + DSO`, not as observed bank dates.
- **No project/WIP table.** Project-Lead milestone views are entirely synthetic.
- **No covenant terms document yet.** Threshold is a **placeholder** until the docx arrives.
- **No weather data wired in yet.** Wet/dry scenarios are toggle-driven shifts on `DSO` and `revenue_growth`, not a real weather model.
- No 4th portco transactional data (1 of the 4 JSON portcos has aggregates only).

## Forecast approach (so you can label it correctly)

Bottom-up **direct-method, driver-decomposition** forecast. **No ML, no black box.** Toggleable assumptions per portco:

- `dso` (days sales outstanding) — shifts invoices into receipt weeks
- `gross_margin` — derives outflows from inflows
- `materials_share` / `subcon_share` / `labour_share` — splits derived outflows across drivers
- `revenue_growth_assumption` — projects future invoicing from historical seasonality

**Label in the UI as:** `Forecast (driver-decomposed)` — never "AI Forecast" or "ML Forecast".

## Gold tables (design against these — most do not exist yet)

All paths are conceptual; only `silver/bookings.parquet` exists today. Build the mockup against these shapes.

### `gold/bookings_classified.parquet` — per-booking, driver-classified
Granularity: one row per booking. Real data.

| col | type | example |
|---|---|---|
| `source_file` | string | `portfolio company 2 data/8005-2024.xlsx` |
| `source_schema` | string | `yuki` \| `exact` \| `dataset2_yearly` |
| `portco` | string | `peter_ummels` \| `heeze` \| `winschoten` (`andijk` exists only in `gold/portfolio_kpi_monthly.parquet`) |
| `gl_account` | string | `8000`, `8002`, `8005` |
| `gl_account_text` | string | `Omzet hoog`, `omzet waarbij de heffing naar u is verlegd` |
| `journal` | string | `80 - Verkoop`, `006 - Verkoop`, `Verkoopboek 1` |
| `booking_date` | date | `2025-03-14` |
| `booking_number` | string | natural key |
| `amount_net` | decimal(18,2) | `credit - debit`, positive = revenue |
| `vat_type` | string \| null | `hoog` \| `laag` \| `verlegd` \| `geen` |
| `boekingstekst` | string \| null | free-text driver hint (Exact only) |
| `driver` | string | `milestone_in` (only inflow driver we can classify from real data) |

### `gold/cashflow_history_weekly.parquet` — historical weekly cash by portco × driver
Granularity: `(portco, iso_week, driver)`. Derived from real bookings.

| col | type | notes |
|---|---|---|
| `portco` | string | |
| `week_start` | date | Monday |
| `iso_year_week` | string | `2025-W11` |
| `driver` | string | `milestone_in`, `materials_out` (derived), `subcontractor_out` (derived), `labour_out` (derived) |
| `amount_eur` | decimal(18,2) | inflows positive, outflows negative |
| `is_observed` | bool | **TRUE for milestone_in only; FALSE for derived outflows** — surface this in tooltips |

### `gold/cashflow_forecast_13w.parquet` — 13-week forward forecast
Granularity: `(portco, week_idx ∈ 0..12, driver, scenario)`.

| col | type | notes |
|---|---|---|
| `portco` | string | |
| `week_idx` | int | 0 = current week, 12 = 13th |
| `week_start` | date | |
| `driver` | string | same set as history |
| `scenario` | string | `base` \| `wet` \| `dry` |
| `amount_eur` | decimal(18,2) | |
| `assumption_dso_days` | int | trace value for tooltip |
| `assumption_gross_margin` | float | trace value for tooltip |
| `confidence` | string | `derived` for outflows, `projected` for future inflows, `observed` for in-flight AR |

### `gold/covenant_headroom.parquet` — rolling slack per portco per week
| col | type | notes |
|---|---|---|
| `portco` | string | |
| `week_start` | date | |
| `scenario` | string | |
| `headroom_eur` | decimal(18,2) | |
| `threshold_eur` | decimal(18,2) | **placeholder until covenant doc arrives** |
| `status` | string | `ok` \| `watch` \| `breach` |

### `gold/_meta/portco-mapping-latest.json`
Lookup: `silver_portco → confirmed_portco_name`. Use this to render portco filters once identities are resolved.

## Role coverage

| Role | What we can support honestly | What to fake gracefully |
|---|---|---|
| **CFO** | Full Tier 1. Per-portco and consolidated 13-week forecast, driver split, scenario toggle, traceability drill-down to bookings. | Covenant threshold value (placeholder); weather attribution (toggle-only). |
| **PE Board** | Consolidated headroom view across 3 portcos; cross-portco comparison. | 4th portco shown as "aggregate-only, no transactional data"; covenant threshold flagged as placeholder. |
| **Opco MD** | Per-opco revenue history and forecast for the 3 portcos with data. | WIP exposure, project-level risk, subcontractor commitments — **fully synthetic, label as "demo data"**. |
| **Project Lead** | Almost nothing real. | Entire view is synthetic — next milestone, materials outflows ahead of execution, weather schedule risk. Label as "illustrative mockup" or hide behind a "Tier 3 preview" badge. |

## Mock data shape suggestions

Use these ranges to seed realistic-looking synthetic data in Lovable. Numbers are order-of-magnitude based on the aggregated P&L and observed row counts — not from real per-portco totals.

- **Portcos to render:** `peter_ummels`, `winschoten`, `andijk`, `heeze` (mark the last one as "aggregate only").
- **Annual revenue per portco:** €3M–€12M range; pick one each.
- **Bookings per portco per year:** 2k–6k.
- **Weekly inflows (historical):** mean ~€60k–€220k per portco, with a clear summer peak (May–Sep ~1.5×) and winter dip (Dec–Feb ~0.6×). Roofing is weather-seasonal.
- **DSO defaults per portco:** 35, 42, 50 days. Toggle range 20–90.
- **Gross margin defaults:** 18%, 22%, 26%. Toggle range 5–45%.
- **Outflow driver split:** materials 45%, subcontractor 35%, labour 20% (controller-editable).
- **Scenarios:**
  - `base` — defaults
  - `wet` — DSO +7 days, revenue −10% for next 6 weeks
  - `dry` — DSO −3 days, revenue +5% for next 6 weeks
- **Covenant placeholder threshold:** show as `€ X` with a tooltip "placeholder — awaiting covenant terms doc". Use ~€500k–€1.5M ranges so the gauge looks credible.
- **GL accounts to show in drill-downs:** `8000`, `8001`, `8002`, `8004`, `8005`.

## Traceability UX (this is 17/60 of the score — do not skip)

Every forecast number on screen should be clickable down to:
1. **Driver** (e.g. `milestone_in`)
2. **Assumption value** (e.g. `DSO = 42 days`, `gross_margin = 22%`)
3. **Source bookings** (list of `booking_number` + `source_file` + `amount_net`) for observed numbers, or "projected from seasonality" for future inflows.

In the mockup this can be a side-panel that opens on click. Show the chain: `figure → driver → assumption → source rows`.

## Open questions (mark in UI; do not block)

- 4th portco transactional data — does it exist?
- AP-side data (materials, subcontractor invoices, payment dates) — does it exist?
- Covenant threshold + calculation rule — awaiting doc.
- Project/WIP table — awaiting file.
- Weather data source — KNMI vs Open-Meteo TBD.

## Honest labels to use in the UI

- "Forecast (driver-decomposed)" — never "AI Forecast"
- "Derived from gross-margin assumption" — on every outflow figure
- "Placeholder threshold" — on covenant gauge
- "Illustrative — no project data yet" — on Project Lead view
- "Aggregate only — no transactional data" — on the 4th portco
- "Observed" vs "Projected" badges on history vs forecast weeks
