# Handoff — Connect Lovable to the Altis SQL data

For the frontend engineer building the dashboard in Lovable. Everything you need to point at our gold tables is in this file.

---

## 1. Connection

| Field | Value |
|---|---|
| Server | `atlis.database.windows.net` |
| Database | `altis` |
| Port | `1433` |
| User | `sasqladmin` |
| Password | `Mosquitto023` |
| Encrypt | `true` |
| Driver | ODBC Driver 18 for SQL Server, or `mssql`/`tedious` for Node |

**ADO.NET style connection string** (paste-ready):

```
Server=tcp:atlis.database.windows.net,1433;Initial Catalog=altis;Persist Security Info=False;User ID=sasqladmin;Password=Mosquitto023;MultipleActiveResultSets=False;Encrypt=True;TrustServerCertificate=False;Connection Timeout=30;
```

**Node (mssql / tedious)**:

```js
const config = {
  server: "atlis.database.windows.net",
  port: 1433,
  database: "altis",
  user: "sasqladmin",
  password: "Mosquitto023",
  options: { encrypt: true, trustServerCertificate: false },
};
```

> Password is hackathon-only and will be rotated. Do not commit it to a public repo.

---

## 2. What I might need from Dimitri

- [ ] **SQL firewall** — currently "Allow Azure services" is on. If your Lovable function runs from outside Azure and gets a login error, ping me and I'll open the firewall (1 click in the portal: *SQL server `atlis` → Networking → Public access → All networks*).
- [ ] **Read-only user** — for production we'd give you a non-admin login. For the hackathon `sasqladmin` is fine.

That's the only help I need from you. Everything else is in the database already.

---

## 3. The tables you have

All eight tables live in the `dbo` schema. None of them have a primary key — they're flat read-only data for the dashboard.

| Table | Rows | What it is |
|---|---|---|
| `gold_bookings_classified` | 31,734 | Every accounting booking with company name + cash-flow type. Use for drill-down / traceability. |
| `gold_portfolio_kpi_monthly` | 141 | Monthly revenue per portco. **Only source that covers Andijk** (which has no booking-level data). |
| `gold_cashflow_history_weekly` | 1,908 | Past weekly cash per company × driver. Use for the "history" portion of trend charts. |
| `gold_cashflow_forecast_13w` | 624 | The next 13 weeks per (portco × driver × scenario). Use for the headline forecast chart. |
| `gold_assumptions` | 12 | The knobs per (portco × scenario): DSO, gross margin, driver shares. Use for tooltips and the assumptions panel. |
| `gold_covenant_headroom` | 156 | Cumulative cash, floor, headroom and traffic-light status per (portco × scenario × week). Use for the safety gauges. |
| `gold_cashflow_consolidated_13w` | 156 | Forecast summed across all four portcos per (scenario × week × driver). PE board lens. |
| `gold_cashflow_consolidated_net_13w` | 39 | Same but already netted: one row per (scenario × week) with cumulative. The PE board's "one line" chart. |
| **Tier 2 — workable days (the headline)** | | |
| `gold_workable_days_monthly` | 312 | **The Tier 2 KPI table.** Per `(portco, year, month)`: `workable_days`, `total_weekdays`, `workable_ratio`, `revenue_eur`, `weather_source`. One table powers BOTH the per-company filter and the all-companies view. |
| **Tier 2 — supporting weather data** | | |
| `gold_weather_daily` | ~7,000 | Per-portco × day: rainfall mm, max/min temp, wind, `work_day_score` (0/1). Use for drill-down. |
| `gold_weather_weekly` | ~1,000 | Per-portco × Monday-week: `work_day_ratio`, total rainfall, wet days, lost days. |
| `gold_weather_climate_normals` | 36 | Per-portco × month: median/wet/dry `work_day_ratio` from 2022-2024. |
| `gold_cashflow_forecast_13w_weather` | 624 | The Tier 1 forecast adjusted by weather. Same shape as Tier 1 plus a `weather_ratio` column. |

---

## 4. Sample queries — one per chart on the dashboard

### Headline net cash forecast (CFO view)
Used for the "Net cash per week" line chart. Three lines per company.

```sql
SELECT
  portco,
  scenario,
  week_idx,
  week_start,
  SUM(amount_eur) AS net_cash_eur
FROM gold_cashflow_forecast_13w
GROUP BY portco, scenario, week_idx, week_start
ORDER BY portco, scenario, week_idx;
```

### Driver split per week (base scenario)
Used for the stacked bar chart "where the cash comes from and goes".

```sql
SELECT portco, week_start, driver, amount_eur
FROM gold_cashflow_forecast_13w
WHERE scenario = 'base'
ORDER BY portco, week_start, driver;
```

### Covenant headroom timeline
Used for the cumulative cash chart with floor + status colour.

```sql
SELECT
  portco,
  scenario,
  week_start,
  cum_cash_eur,
  floor_eur,
  headroom_eur,
  status            -- 'ok' | 'watch' | 'breach'
FROM gold_covenant_headroom
ORDER BY portco, scenario, week_idx;
```

### Portfolio view for the PE board
One line per scenario, summed across all four companies.

```sql
SELECT scenario, week_idx, week_start, net_cash_eur, cum_cash_eur
FROM gold_cashflow_consolidated_net_13w
ORDER BY scenario, week_idx;
```

### Per-portco assumptions (for tooltips)
```sql
SELECT portco, scenario, dso_days, gross_margin,
       materials_share, subcon_share, labour_share
FROM gold_assumptions;
```

### Tier 2 — Workable days vs revenue (THE headline Tier 2 chart)

Combined bar + line chart. **Bar** = workable days that month. **Line** =
invoiced revenue that month. One company at a time, or all companies combined.

**Per company** — when the filter selects a specific portco:
```sql
SELECT period_start, workable_days, total_weekdays,
       workable_ratio, revenue_eur, weather_source
FROM gold_workable_days_monthly
WHERE portco = @portco          -- e.g. 'heeze'
ORDER BY period_start;
```

**All companies combined** — when no portco is selected. Average workable
days across portcos (summing them would push above the calendar maximum)
and sum the revenue:
```sql
SELECT period_start,
       AVG(CAST(workable_days AS float)) AS avg_workable_days,
       SUM(revenue_eur)                  AS total_revenue_eur
FROM gold_workable_days_monthly
GROUP BY period_start
ORDER BY period_start;
```

Portco filter values: `peter_ummels`, `heeze`, `winschoten`, `andijk`. For
`andijk` the `weather_source` column reads `portfolio_avg_fallback` — show
a small "weather = portfolio average" badge next to the chart title.

### Tier 2 — weather-adjusted 13-week forecast (supporting view)
```sql
SELECT portco, scenario, week_idx, week_start,
       SUM(amount_eur) AS net_cash_eur
FROM gold_cashflow_forecast_13w_weather
GROUP BY portco, scenario, week_idx, week_start
ORDER BY portco, scenario, week_idx;
```

### Drill-down — bookings behind a forecast cell
For the traceability panel on the CFO view. Example: bookings that feed week 0 of the base forecast for peter_ummels.

```sql
DECLARE @anchor date = (SELECT MAX(CAST(booking_date AS date)) FROM gold_bookings_classified WHERE portco = 'peter_ummels');

SELECT TOP 50
  booking_date, booking_number, gl_account, gl_account_text,
  amount_net, source_file
FROM gold_bookings_classified
WHERE portco = 'peter_ummels'
  AND driver  = 'milestone_in'
  AND booking_date >= DATEADD(day, -30, @anchor)
ORDER BY booking_date DESC;
```

---

## 5. Labels and gotchas (please use these in the UI)

- **Forecast labelling**: call it `Forecast (driver-decomposed)`. Not "AI forecast" — we don't use ML.
- **Outflow rows** are derived from a gross-margin assumption, not observed. Tag them with a small "derived" badge or dashed line style.
- **Andijk** has no booking-level data — show its revenue history from `gold_portfolio_kpi_monthly` but no forecast / no drill-down. Label "aggregate only".
- **Covenant floor** is a PLACEHOLDER until the real covenant document arrives. Show a "placeholder" tooltip on the gauge.
- **DSO is 30 days** (CFO-confirmed). Also surface the **7-day acceptance→invoice lag** as a non-toggleable fact in the UI.
- **Tier 2 workable-day rules** (binary, day-level — show on a tooltip next to the chart):
  - max temp ≤ 28 °C and min temp ≥ 5 °C
  - wind ≤ Beaufort 6 (≤ 13.8 m/s)
  - rainfall ≤ 5 mm/day
  - Mon-Fri only (Saturdays and Sundays never count as workable, even with perfect weather)
- **Andijk has no KNMI file** — its workable_days come from the portfolio average. Show a "weather = portfolio average" badge when Andijk is selected.
- The supporting weather tables (`gold_weather_daily`, `_weekly`, `_climate_normals`, `_cashflow_forecast_13w_weather`) are still in SQL for the weather-adjusted forecast view; the headline bar+line chart only needs `gold_workable_days_monthly`.

---

## 6. Bigger picture

If you want to understand how the data flows from raw Excel to these SQL tables, read `WORKFLOW_EXPLAINED.html` at the repo root. For full schemas and labelling guidance, read `FRONTEND_BRIEF.md`.

That's it. Ping me when you've connected — happy to jump on a quick call if anything is unclear.
