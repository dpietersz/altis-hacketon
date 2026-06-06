# Altis Hackathon — Plan (Tier 1, with Tier 2/3 hooks)

## Decision summary

- **Forecast approach**: bottom-up direct method, driver-decomposition with empirical
  payment-lag distributions. **No statistical/ML black box** — judges reward auditability
  (17/60 challenge points) and a SARIMA fit is harder to defend than `sum(open AR) × lag(driver, customer)`.
- **Weather**: optional for Tier 1 baseline. Structure the data so weather → project-day-shift →
  milestone-date-shift → cash-date-shift drops in cleanly; do NOT use a flat multiplier when added.
- **Architecture**: raw → silver → gold, with `gold/_meta/` for schema snapshots and run audit.

## What we have

| Source | Status | Useful for |
|--------|--------|------------|
| `raw/database.xlsx` | analyzed — 13,776 rows, sales-only (`80 - Verkoop`), single portco | AR-side projection only |
| `raw/received_original_data/*` | uploaded, not yet analyzed | bronze input to silver |

`database.xlsx` is downstream of the received data. We rebuild silver/gold from received, deprecate `database.xlsx` once the silver pipeline works.

## What we need (gap)

| Need | Why | Source candidate |
|------|-----|---|
| AP-side bookings | materials + subcontractor outflows are drivers 1 & 2 | per-portco xlsx files |
| Invoice → receipt lag | payment behaviour is driver 4 | per-portco AR ledger with payment dates |
| GL → driver mapping | classification of each booking into a driver | challenge-provided chart-of-accounts xlsx (TBC, may need to derive) |
| Project link on bookings | drives milestone billing (driver 3) and weather attribution | project & WIP data (TBC) |
| Covenant terms | headroom calc + alert threshold | challenge-provided docx (TBC) |

## Architecture

```
raw/received_original_data/    ← bronze, as-received
silver/<source>/<table>        ← cleaned per source, header rows detected, source-tagged
gold/bookings                  ← unified bookings ledger, driver-classified
gold/cashflow_history_weekly   ← historical weekly cash by driver
gold/cashflow_forecast_13w     ← per-week forecast by driver
gold/covenant_headroom         ← week-by-week covenant slack
gold/_meta/schema-*.json       ← schema snapshots per run (drift detection)
```

## Notebook roadmap

| # | Notebook | Output | Status |
|---|----------|--------|--------|
| 01 | `01_connect_storage.py` | explore `raw/database.xlsx` | ✅ done |
| 02 | `02_explore_received_data.py` | inventory all of `raw/received_original_data/` | 🏗️ building |
| 03 | `03_silver_cleanse.py` | per-source silver tables | next |
| 04 | `04_silver_union_portco.py` | union per-portco bookings | next |
| 05 | `05_gold_unify_classify.py` | `gold.bookings` + driver class | **🛑 Tier 1 data foundation** |
| 06 | `06_gold_forecast_13w.py` | `gold.cashflow_forecast_13w` | |
| 07 | `07_gold_covenant_scenario.py` | covenant headroom + base scenario | **🛑 Tier 1 submittable** |
| 08+ | weather, second opco, scenarios | Tier 2 reach | |

## Forecasting math (Tier 1 baseline)

For each driver `d` ∈ {materials_out, subcon_out, milestone_in, customer_lag, other}:
- `forecast[d, week_t] = scheduled_known[d, t] + Σ over open_positions p in driver d of expected_amount(p) × P(lag(d) = t - issue_date(p))`
- `lag(d)` is the empirical distribution from historical bookings (`actual_settlement_date - issue_date`)
- `expected_amount(p)` for AR = open invoice amount × historical collection rate per segment

Output: one row per (week, driver) — sums across drivers give the 13-week net cash projection. Click any cell → see the source positions that produced it.

## Open questions

1. Do the received xlsx files include payment/settlement dates (needed for lag distribution)?
2. Are bookings linked to projects (needed for milestone driver and weather attribution)?
3. Is there a separate chart-of-accounts mapping file, or do we derive GL → driver from ledger codes?
4. What's the covenant headroom rule? (Will determine the alert math in notebook 07.)

These get answered by notebook 02's inventory pass.

## Data retention

Per challenge: all Altis data deleted within 3 days after the event. Includes `raw/`, `silver/`, `gold/`, all snapshots, local downloads, and the secret in the `altis` scope. Schedule a teardown task post-event.
