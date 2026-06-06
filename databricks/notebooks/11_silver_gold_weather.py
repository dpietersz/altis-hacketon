# Databricks notebook source
# MAGIC %md
# MAGIC # 11 — Weather: silver + gold + weather-adjusted forecast
# MAGIC
# MAGIC Reads the three KNMI files, normalizes the columns, joins them to the
# MAGIC three portcos with booking-level data (peter_ummels, heeze, winschoten),
# MAGIC computes a per-day **work-day score**, rolls up to weekly, then re-runs
# MAGIC the 13-week forecast with weather adjustments per scenario.
# MAGIC
# MAGIC See `PLAN_TIER2.md` for the model (rainfall + temp + wind → work_day_ratio).

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io, json
from datetime import datetime, timezone, timedelta, date

import numpy as np
import pandas as pd
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
RAW = "raw"; SILVER = "silver"; GOLD = "gold"

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
raw_c = svc.get_container_client(RAW)
silver_c = svc.get_container_client(SILVER)
gold_c = svc.get_container_client(GOLD)

# Map each KNMI file to the portco it represents
PORTCO_STATION_MAP = {
    "weather_data/brunssum.txt":    {"portco": "peter_ummels", "city": "Brunssum",   "sep": ","},
    "weather_data/eindhoven.txt":   {"portco": "heeze",        "city": "Eindhoven",  "sep": ","},
    "weather_data/windschoten.txt": {"portco": "winschoten",   "city": "Winschoten", "sep": "\t"},
}

display(pd.DataFrame.from_dict(PORTCO_STATION_MAP, orient="index").reset_index().rename(columns={"index": "file"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Parse each KNMI file
# MAGIC
# MAGIC KNMI convention: metadata lines start with `#`. The last `#`-line is the
# MAGIC column header; everything below is data. One file uses tab separator
# MAGIC instead of comma — we detect from `PORTCO_STATION_MAP`.

# COMMAND ----------

def parse_knmi(text: str, sep: str) -> pd.DataFrame:
    lines = text.splitlines()
    last_hash = max(i for i, l in enumerate(lines) if l.startswith("#"))
    header_line = lines[last_hash].lstrip("#")
    cols = [c.strip() for c in header_line.split(sep)]
    data_text = "\n".join(lines[last_hash + 1:])
    df = pd.read_csv(
        io.StringIO(data_text),
        names=cols, sep=sep, skipinitialspace=True,
        skip_blank_lines=True, dtype=str,
    )
    df.columns = [c.strip() for c in df.columns]
    return df

raw_per_station = {}
for blob_name, meta in PORTCO_STATION_MAP.items():
    raw_bytes = raw_c.get_blob_client(blob_name).download_blob().readall()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")
    df = parse_knmi(text, meta["sep"])
    raw_per_station[blob_name] = df
    print(f"{blob_name:>35}  rows={len(df):>6,}  cols={len(df.columns)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Normalize to a small, useful column set
# MAGIC
# MAGIC We keep date + 5 measurements (rainfall, mean/min/max temp, wind speed,
# MAGIC sunshine), convert KNMI's 0.1-unit raw values into normal units, and
# MAGIC tag every row with the portco + city.

# COMMAND ----------

WANT_COLS = ["YYYYMMDD", "RH", "TG", "TN", "TX", "FG", "SQ"]

def normalize(df: pd.DataFrame, portco: str, city: str, station: str) -> pd.DataFrame:
    keep = [c for c in WANT_COLS if c in df.columns]
    out = df[keep].copy()
    out.columns = [c.strip() for c in out.columns]
    out["date"] = pd.to_datetime(out["YYYYMMDD"].str.strip(), format="%Y%m%d", errors="coerce")
    for col in ("RH", "TG", "TN", "TX", "FG", "SQ"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col].str.strip().replace("", pd.NA), errors="coerce")
    # KNMI convention: RH = -1 means trace (< 0.05 mm). Treat as 0.
    if "RH" in out.columns:
        out.loc[out["RH"] == -1, "RH"] = 0
    if "SQ" in out.columns:
        out.loc[out["SQ"] == -1, "SQ"] = 0

    silver = pd.DataFrame({
        "portco":       portco,
        "city":         city,
        "station":      station,
        "date":         out["date"].dt.date,
        "rainfall_mm":  (out["RH"] * 0.1).round(2),
        "mean_temp_c":  (out["TG"] * 0.1).round(1),
        "min_temp_c":   (out["TN"] * 0.1).round(1),
        "max_temp_c":   (out["TX"] * 0.1).round(1),
        "wind_ms":      (out["FG"] * 0.1).round(2),
        "sunshine_h":   (out["SQ"] * 0.1).round(1),
    })
    return silver.dropna(subset=["date"]).reset_index(drop=True)

silver_parts = []
for blob_name, meta in PORTCO_STATION_MAP.items():
    df = raw_per_station[blob_name]
    silver_parts.append(normalize(df, meta["portco"], meta["city"], blob_name))

silver_weather = pd.concat(silver_parts, ignore_index=True)

# Filter to the period we actually care about (2020+ — covers our 2023-2026 ledger)
silver_weather = silver_weather[silver_weather["date"] >= pd.Timestamp("2020-01-01").date()].reset_index(drop=True)
print(f"silver weather rows (2020+): {len(silver_weather):,}")
display(silver_weather.head(10))

# COMMAND ----------

# Write silver/weather_daily.parquet
buf = io.BytesIO()
silver_weather.to_parquet(buf, index=False)
buf.seek(0)
silver_c.upload_blob(name="weather_daily.parquet", data=buf.getvalue(), overwrite=True)
print("wrote silver/weather_daily.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Score each day for roofing productivity
# MAGIC
# MAGIC The rules (controller-tunable below):
# MAGIC - **1.0** — dry, mild, no strong wind → a full work day
# MAGIC - **0.5** — light rain (1–10 mm) → partial work day
# MAGIC - **0.0** — heavy rain, freezing, or strong wind → no roofing work

# COMMAND ----------

# Roofing workable-day rules (binary). Refined with team input:
#   - Above 28°C max temp → too hot for safe roofing
#   - Below 5°C min temp → frozen / icy surfaces, dangerous
#   - Wind above Beaufort 6 upper bound (~13.8 m/s) → can't work safely on a roof
#   - Daily rainfall above 5 mm → industry rule of thumb for "stop outdoor work"
#   - Weekend (Sat/Sun) → roofers don't work
THRESHOLDS = {
    "max_temp_c":   28.0,   # daily MAX > 28 → too hot
    "min_temp_c":   5.0,    # daily MIN < 5 → too cold
    "max_wind_ms":  13.8,   # > Beaufort 6 upper bound
    "max_rain_mm":  5.0,    # > 5 mm/day → too wet
}
display(pd.DataFrame([THRESHOLDS]))

def score_row(rain, max_t, min_t, wind):
    """1.0 = workable day, 0.0 = lost day. NaN if data missing."""
    if pd.isna(rain) or pd.isna(max_t):
        return np.nan
    if max_t > THRESHOLDS["max_temp_c"]:
        return 0.0
    if not pd.isna(min_t) and min_t < THRESHOLDS["min_temp_c"]:
        return 0.0
    if not pd.isna(wind) and wind > THRESHOLDS["max_wind_ms"]:
        return 0.0
    if rain > THRESHOLDS["max_rain_mm"]:
        return 0.0
    return 1.0

silver_weather["work_day_score"] = [
    score_row(r, mx, mn, w) for r, mx, mn, w in
    zip(silver_weather["rainfall_mm"], silver_weather["max_temp_c"],
        silver_weather["min_temp_c"], silver_weather["wind_ms"])
]

# Write gold/weather_daily.parquet (silver + score)
gold_daily = silver_weather.copy()
buf = io.BytesIO()
gold_daily.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="weather_daily.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/weather_daily.parquet")

# Quick check — score distribution per portco
display(
    gold_daily.groupby("portco")["work_day_score"].value_counts(dropna=False)
    .rename("days").reset_index()
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Weekly aggregate per portco
# MAGIC
# MAGIC One Monday-week row per portco with: total rainfall, mean temp, count of
# MAGIC wet/lost days, and the headline `work_day_ratio` (sum of work-day-scores
# MAGIC over Mon-Fri, divided by 5).

# COMMAND ----------

gold_daily["dt"] = pd.to_datetime(gold_daily["date"])
gold_daily["dow"] = gold_daily["dt"].dt.dayofweek  # 0=Mon
gold_daily["week_start"] = gold_daily["dt"].apply(lambda d: (d - pd.Timedelta(days=d.dayofweek)).date())
workdays = gold_daily[gold_daily["dow"] < 5].copy()  # Mon-Fri only

weekly = (
    workdays.groupby(["portco", "week_start"])
    .agg(
        work_day_ratio=("work_day_score", lambda s: round(s.sum() / 5.0, 3) if s.notna().any() else None),
        wet_days=("rainfall_mm", lambda s: int((s > THRESHOLDS["max_rain_mm"]).sum())),
        lost_days=("work_day_score", lambda s: int((s == 0).sum())),
        total_rain_mm=("rainfall_mm", lambda s: round(s.sum(), 1)),
        mean_temp_c=("mean_temp_c", lambda s: round(s.mean(), 1)),
        max_temp_c=("max_temp_c", lambda s: round(s.max(), 1)),
    )
    .reset_index()
)
print(f"weekly rows: {len(weekly):,}")
display(weekly.head(10))

buf = io.BytesIO()
weekly.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="weather_weekly.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/weather_weekly.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Climate normals — what does a "normal" / "wet" / "dry" month look like per portco?
# MAGIC
# MAGIC We use the last 3 complete years (2022-2024) and group by month-of-year:
# MAGIC - **base** scenario weather = median work_day_ratio for that month
# MAGIC - **wet** scenario weather  = 10th-percentile work_day_ratio (worse)
# MAGIC - **dry** scenario weather  = 90th-percentile work_day_ratio (better)

# COMMAND ----------

normal_base = workdays[(workdays["dt"].dt.year >= 2022) & (workdays["dt"].dt.year <= 2024)].copy()
normal_base["month"] = normal_base["dt"].dt.month

monthly_per_year = (
    normal_base.groupby(["portco", normal_base["dt"].dt.year.rename("year"), "month"])["work_day_score"]
    .mean()
    .reset_index()
)

climate = (
    monthly_per_year.groupby(["portco", "month"])
    .agg(
        base_ratio=("work_day_score", "median"),
        wet_ratio= ("work_day_score", lambda s: s.quantile(0.10)),
        dry_ratio= ("work_day_score", lambda s: s.quantile(0.90)),
    )
    .reset_index()
    .round(3)
)
print("Per-portco × month climate normals (work_day_ratio):")
display(climate.pivot(index="month", columns="portco", values="base_ratio"))

buf = io.BytesIO()
climate.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="weather_climate_normals.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/weather_climate_normals.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Apply weather to the 13-week forecast
# MAGIC
# MAGIC We start from `gold/cashflow_forecast_13w.parquet` (the Tier 1 forecast)
# MAGIC and for each (portco × week × scenario):
# MAGIC
# MAGIC 1. Look up the climate-normal `work_day_ratio` for that portco × month × scenario.
# MAGIC 2. Scale the inflow: `amount_in × work_day_ratio`.
# MAGIC 3. Note the implied DSO delay: `(1 - work_day_ratio) × 14` days
# MAGIC    (purely for tooltip; the inflow scaling already captures the cash effect).
# MAGIC 4. Re-derive outflows (materials/subcon/labour) from the new inflow.

# COMMAND ----------

forecast_t1 = pd.read_parquet(io.BytesIO(gold_c.get_blob_client("cashflow_forecast_13w.parquet").download_blob().readall()))
forecast_t1["week_start"] = pd.to_datetime(forecast_t1["week_start"])
forecast_t1["month"] = forecast_t1["week_start"].dt.month
print(f"Tier 1 forecast rows: {len(forecast_t1):,}")

assumptions = pd.read_parquet(io.BytesIO(gold_c.get_blob_client("assumptions.parquet").download_blob().readall()))

# Build a (portco, month, scenario) → weather_ratio table
weather_lookup = climate.melt(
    id_vars=["portco", "month"],
    value_vars=["base_ratio", "wet_ratio", "dry_ratio"],
    var_name="scenario_raw",
    value_name="weather_ratio",
)
weather_lookup["scenario"] = weather_lookup["scenario_raw"].str.replace("_ratio", "")
weather_lookup = weather_lookup[["portco", "month", "scenario", "weather_ratio"]]

# For andijk we have no station data: use the average of the other three as a stand-in
if "andijk" not in weather_lookup["portco"].unique():
    avg_all = (
        weather_lookup.groupby(["month", "scenario"], as_index=False)["weather_ratio"].mean()
    )
    avg_all["portco"] = "andijk"
    weather_lookup = pd.concat([weather_lookup, avg_all], ignore_index=True)

print(f"weather lookup rows: {len(weather_lookup):,}")
display(weather_lookup.head(15))

# COMMAND ----------

inflow = forecast_t1[forecast_t1["driver"] == "milestone_in"].copy()
inflow = inflow.merge(weather_lookup, on=["portco", "month", "scenario"], how="left")
inflow["weather_ratio"] = inflow["weather_ratio"].fillna(1.0)  # safety net
inflow["amount_eur_weather"] = (inflow["amount_eur"] * inflow["weather_ratio"]).round(2)
inflow["dso_delay_days_weather"] = ((1 - inflow["weather_ratio"]) * 14).round(1)

# Build the weather-adjusted forecast: replace inflow amount, then re-derive outflows
assump_map = assumptions.set_index(["portco", "scenario"]).to_dict("index")

rows_out = []
for _, r in inflow.iterrows():
    a = assump_map.get((r["portco"], r["scenario"]))
    if not a:
        continue
    amt_in = r["amount_eur_weather"]
    rows_out.append({
        **r.drop(["amount_eur", "weather_ratio", "amount_eur_weather", "dso_delay_days_weather"]).to_dict(),
        "driver": "milestone_in",
        "amount_eur": amt_in,
        "confidence": r["confidence"] + "_weather",
        "weather_ratio": round(r["weather_ratio"], 3),
        "dso_delay_days_weather": r["dso_delay_days_weather"],
    })
    out_total = amt_in * (1 - a["gross_margin"])
    for drv, share_key in (("materials_out", "materials_share"),
                            ("subcon_out", "subcon_share"),
                            ("labour_out", "labour_share")):
        rows_out.append({
            **r.drop(["amount_eur", "driver", "weather_ratio", "amount_eur_weather", "dso_delay_days_weather"]).to_dict(),
            "driver": drv,
            "amount_eur": round(-out_total * a[share_key], 2),
            "confidence": "derived_weather",
            "weather_ratio": round(r["weather_ratio"], 3),
            "dso_delay_days_weather": r["dso_delay_days_weather"],
        })

forecast_t2 = pd.DataFrame(rows_out)
print(f"Tier 2 forecast rows: {len(forecast_t2):,}")
display(forecast_t2[["portco", "scenario", "week_idx", "driver", "amount_eur", "weather_ratio", "dso_delay_days_weather"]].head(20))

buf = io.BytesIO()
forecast_t2.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="cashflow_forecast_13w_weather.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/cashflow_forecast_13w_weather.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Summary — Tier 1 vs Tier 2 13-week net cash

# COMMAND ----------

t1_net = (forecast_t1.groupby(["portco", "scenario"], as_index=False)["amount_eur"].sum()
          .rename(columns={"amount_eur": "tier1_net"}).round(2))
t2_net = (forecast_t2.groupby(["portco", "scenario"], as_index=False)["amount_eur"].sum()
          .rename(columns={"amount_eur": "tier2_net"}).round(2))
compare = t1_net.merge(t2_net, on=["portco", "scenario"], how="outer")
compare["delta"] = (compare["tier2_net"] - compare["tier1_net"]).round(2)
display(compare.sort_values(["portco", "scenario"]))

# COMMAND ----------

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Monthly workable-days KPI (the Tier 2 headline)
# MAGIC
# MAGIC One row per (portco, year, month) with:
# MAGIC - `workable_days` — count of Mon-Fri days that passed all four weather thresholds
# MAGIC - `total_weekdays` — total Mon-Fri days in that month (for the ratio)
# MAGIC - `revenue_eur` — invoiced revenue from gold/bookings_classified
# MAGIC
# MAGIC Andijk has no KNMI file so its `workable_days` is the **average across the
# MAGIC other three portcos** for the same month. Clearly tagged via the
# MAGIC `weather_source` column.

# COMMAND ----------

# Workable days per (portco, year-month) from the daily gold table
weekday_only = gold_daily[gold_daily["dow"] < 5].copy()
weekday_only["year"] = weekday_only["dt"].dt.year
weekday_only["month"] = weekday_only["dt"].dt.month

monthly_wx = (
    weekday_only.groupby(["portco", "year", "month"], as_index=False)
    .agg(
        workable_days=("work_day_score", lambda s: int(s.fillna(0).sum())),
        total_weekdays=("work_day_score", "count"),
    )
)
monthly_wx["workable_ratio"] = (monthly_wx["workable_days"] / monthly_wx["total_weekdays"]).round(3)
monthly_wx["weather_source"] = "knmi"

# Add andijk using the average of the other three
other_avg = (
    monthly_wx.groupby(["year", "month"], as_index=False)
    .agg(
        workable_days=("workable_days", lambda s: int(round(s.mean()))),
        total_weekdays=("total_weekdays", "max"),
    )
)
other_avg["workable_ratio"] = (other_avg["workable_days"] / other_avg["total_weekdays"]).round(3)
other_avg["portco"] = "andijk"
other_avg["weather_source"] = "portfolio_avg_fallback"
monthly_wx = pd.concat([monthly_wx, other_avg[monthly_wx.columns]], ignore_index=True)

# Monthly revenue from gold bookings + KPI for andijk
bookings = pd.read_parquet(io.BytesIO(gold_c.get_blob_client("bookings_classified.parquet").download_blob().readall()))
bookings["booking_date"] = pd.to_datetime(bookings["booking_date"])
inflows_b = bookings[bookings["driver"] == "milestone_in"].copy()
inflows_b["year"] = inflows_b["booking_date"].dt.year
inflows_b["month"] = inflows_b["booking_date"].dt.month
rev_tx = (
    inflows_b.groupby(["portco", "year", "month"], as_index=False)["amount_net"].sum()
    .rename(columns={"amount_net": "revenue_eur"})
)

# Andijk revenue from KPI table
kpi = pd.read_parquet(io.BytesIO(gold_c.get_blob_client("portfolio_kpi_monthly.parquet").download_blob().readall()))
rev_kpi_andijk = (
    kpi[kpi["portco"] == "andijk"]
    .groupby(["portco", "year", "month"], as_index=False)["revenue_eur"].sum()
)
revenue_monthly = pd.concat([rev_tx, rev_kpi_andijk], ignore_index=True)
revenue_monthly["revenue_eur"] = revenue_monthly["revenue_eur"].round(2)

# Join
workable_monthly = monthly_wx.merge(revenue_monthly, on=["portco", "year", "month"], how="left")
workable_monthly["period_start"] = pd.to_datetime(
    workable_monthly["year"].astype(str) + "-" + workable_monthly["month"].astype(str) + "-01"
).dt.date
workable_monthly = workable_monthly[[
    "portco", "year", "month", "period_start",
    "workable_days", "total_weekdays", "workable_ratio",
    "revenue_eur", "weather_source",
]].sort_values(["portco", "year", "month"]).reset_index(drop=True)

print(f"workable_days_monthly rows: {len(workable_monthly):,}")
display(workable_monthly.head(15))

buf = io.BytesIO()
workable_monthly.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="workable_days_monthly.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/workable_days_monthly.parquet")

# Quick portfolio-aggregate preview
portfolio = (
    workable_monthly.groupby(["year", "month", "period_start"], as_index=False)
    .agg(
        avg_workable_days=("workable_days", "mean"),
        total_revenue_eur=("revenue_eur", "sum"),
    )
    .round({"avg_workable_days": 1, "total_revenue_eur": 2})
)
print("\nPortfolio (average workable days, summed revenue) — last 12 months:")
display(portfolio.tail(12))

# COMMAND ----------

# Save snapshot
now = datetime.now(timezone.utc)
snap = {
    "captured_at": now.isoformat(timespec="seconds"),
    "thresholds": THRESHOLDS,
    "rows": {
        "silver_weather_daily":     int(len(silver_weather)),
        "gold_weather_weekly":      int(len(weekly)),
        "climate_normals":          int(len(climate)),
        "forecast_t2":              int(len(forecast_t2)),
        "workable_days_monthly":    int(len(workable_monthly)),
    },
    "compare": compare.to_dict(orient="records"),
    "workable_sample": workable_monthly.tail(12).to_dict(orient="records"),
}
gold_c.upload_blob(name=f"_meta/weather-build-{now.strftime('%Y%m%dT%H%M%SZ')}.json",
                   data=json.dumps(snap, indent=2, default=str).encode(), overwrite=True)
dbutils.notebook.exit(json.dumps(snap, default=str))
