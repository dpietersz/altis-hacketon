# Tier 2 — Weather → Cash Flow Plan

> Tier 2 adds the missing piece that Tier 1's scenarios fudged: instead of
> wet/dry being a toggle, they come from **real weather data** translated into
> work-day loss, schedule slip, billing shift, cash shift.

## What we have

Three KNMI daily weather files in `raw/weather_data/`:

| File | Station | Rows | For portco | Sep |
|---|---|---|---|---|
| `brunssum.txt` | 380 | 43,986 (1906–today) | peter_ummels (Brunssum, Limburg) | comma |
| `eindhoven.txt` | — | 27,550 | heeze (Noord-Brabant — Eindhoven is the nearest KNMI station) | comma |
| `windschoten.txt` | 286 | 13,305 (1990–today) | winschoten (Groningen) | TAB |

41 columns per file. We need a handful: `YYYYMMDD`, `RH` (rainfall, in 0.1 mm),
`TG` (mean temp, 0.1 °C), `TN` / `TX` (min/max temp), `FG` (wind, 0.1 m/s),
`SQ` (sunshine, 0.1 h). Andijk has no booking-level data anyway so no weather
file needed; we'll use a Dutch-average climate normal where Andijk shows up.

## The translation model (one paragraph)

A roofing day is "lost", "partial" or "full" depending on rainfall + wind +
temperature. We compute a daily `work_day_score`:
- **1.0** — dry, mild, no strong wind (full work day)
- **0.5** — light rain (some roofing crews work; the rest can't)
- **0.0** — heavy rain, freezing, or strong wind (no roofing work)

Thresholds (controller-tunable):
- Heavy rain: `RH > 100` (>10 mm/day)
- Light rain: `10 < RH ≤ 100` (1–10 mm/day)
- Freezing: `TG < 0`
- Strong wind: `FG > 100` (>10 m/s ≈ Bft 5)

For each week we sum `work_day_score` across the 5 working days and divide by
5 → `work_day_ratio` (0..1). That ratio drives two adjustments:

```
expected_revenue[w] = baseline_revenue[w] × work_day_ratio[w]
expected_dso_delay   = (1 − work_day_ratio[w]) × 14 days
```

So a perfect week leaves the forecast unchanged. A "50% lost" week halves
expected revenue for that week and adds ~7 days to DSO.

## Scenario redefinition (was toggle, now data-driven)

- **base** — climate normal (3-year monthly average of work_day_ratio)
- **wet** — 90th-percentile-rainfall month from history (worse than normal)
- **dry** — 10th-percentile-rainfall month from history (better than normal)

Each scenario produces a different `work_day_ratio` per future week, which
flows through into different `expected_revenue` and `dso_delay` per week.

## Tables we'll add

| Table | Granularity | Purpose |
|---|---|---|
| `silver/weather_daily.parquet` | per-station × day | source of truth, 41 cols → ~10 useful |
| `gold/weather_daily.parquet` | per-portco × day | with `work_day_score`, joined to station via map |
| `gold/weather_weekly.parquet` | per-portco × Monday-week | `work_day_ratio`, `total_rain_mm`, `mean_temp_c`, `wet_days`, `lost_days` |
| `gold/weather_climate_normals.parquet` | per-portco × month-of-year × scenario | the lookup that drives the future weather assumption |
| `gold/cashflow_forecast_13w_weather.parquet` | per-portco × week × driver × scenario | Tier 1 forecast adjusted by weather model |

These get copied to Azure SQL by notebook 08 (we add them to the manifest).

## Notebook roadmap

| # | Notebook | Output | Status |
|---|----------|--------|--------|
| 10 | `10_explore_weather.py` | KNMI discovery snapshot | ✅ done |
| 11 | `11_silver_gold_weather.py` | silver + gold weather tables + weather-adjusted forecast | 🏗️ next |
| 12 | `12_tier2_charts.py` | inspiration views for the Tier 2 dashboard | next |

## Tier 2 dashboard views the model unlocks

- **Weather overlay** — rainfall bars + revenue line per portco, history
- **Work-day-ratio heatmap** — month × portco, how many work days were lost
- **Side-by-side forecasts** — Tier 1 (toggle scenarios) vs Tier 2 (weather-driven scenarios)
- **Opco MD view** — per-opco weather-driven schedule risk for next 13 weeks
- **Wet-quarter stress test** — what does Q3 look like if it rains like the 2021 wet summer?

## Caveats (label these in the UI)

- KNMI files are HISTORICAL only — we don't have a true 13-week weather forecast.
  The next 13 weeks use "climate normal for this month" as the weather
  assumption. When we wire a forecast API (KNMI Open Data / Open-Meteo) we
  swap the normal lookup for a real forecast call. No model change needed.
- The work-day-ratio thresholds are roofing-industry rules of thumb (not from
  Altis directly). The controller can edit them in `notebook 11`.
- Andijk has no booking-level data, so weather attribution for Andijk is
  shown only at the aggregate KPI level.
