# Databricks notebook source
# MAGIC %md
# MAGIC # 06 — Gold: 13-week cash-flow forecast
# MAGIC
# MAGIC Bottom-up direct method, driver-decomposition. No ML.
# MAGIC
# MAGIC Inputs:
# MAGIC - `gold/bookings_classified.parquet`
# MAGIC - `gold/portfolio_kpi_monthly.parquet` (used for andijk where no Tx exists)
# MAGIC
# MAGIC Outputs:
# MAGIC - `gold/cashflow_history_weekly.parquet`
# MAGIC - `gold/cashflow_forecast_13w.parquet`
# MAGIC - `gold/assumptions.parquet` — per-portco knobs + scenario shifts
# MAGIC
# MAGIC ### Per-week per-driver math
# MAGIC
# MAGIC ```
# MAGIC for portco p, future week W:
# MAGIC   invoice_date_window = W − dso_days(p, scenario)
# MAGIC   if invoice_date_window ≤ anchor_date(p):
# MAGIC     cash_in_observed = sum(amount_net) over bookings of p in that window
# MAGIC     confidence = "observed"
# MAGIC   else:
# MAGIC     cash_in_projected = avg_weekly(p, last 13w) × seasonality(p, month(window)) × growth × scenario_rev_mult(W)
# MAGIC     confidence = "projected"
# MAGIC
# MAGIC   cash_out_total = cash_in × (1 − gross_margin(p, scenario))
# MAGIC   materials_out  = cash_out_total × materials_share
# MAGIC   subcon_out     = cash_out_total × subcon_share
# MAGIC   labour_out     = cash_out_total × labour_share
# MAGIC ```

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io, json
from datetime import date, timedelta, datetime, timezone

import numpy as np
import pandas as pd
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
GOLD = "gold"

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
gold_c = svc.get_container_client(GOLD)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Assumptions & scenarios (the only knobs that move the forecast)
# MAGIC
# MAGIC Two dicts drive everything below:
# MAGIC
# MAGIC - **`ASSUMPTIONS`** — per-portco steady-state values. DSO is CFO-stated (30 days
# MAGIC   uniform). Gross margin and outflow shares are starting defaults the controller
# MAGIC   will refine.
# MAGIC - **`SCENARIOS`** — `base` / `wet` / `dry` shifts on top of the steady-state.
# MAGIC   Wet weather slows acceptance → slower receipts (DSO +7d) and trims revenue 10%
# MAGIC   for the first 6 weeks; dry weather pulls receipts in (-3d) and lifts revenue 5%.
# MAGIC
# MAGIC Every forecast figure later traces back to exactly these numbers — they show up
# MAGIC in the output as `assumption_dso_days` / `assumption_gross_margin` columns.

# COMMAND ----------

# CFO-stated process (2026-06-06):
#   - Invoice is sent ≤ 7 days after signing the acceptance document
#   - Invoices are paid within 30 days
# So total cash lag from acceptance ≈ 37 days. Our ledger `booking_date` is the
# invoice booking date, so DSO from booking_date = 30 days. The 7-day
# acceptance→invoice lag is implicit in the historical invoicing baseline we
# project from (we forecast invoicing rate, not acceptance rate).
ASSUMPTIONS = {
    "peter_ummels": {"dso_days": 30, "invoice_lag_days": 7, "gross_margin": 0.22,
                     "materials_share": 0.45, "subcon_share": 0.35, "labour_share": 0.20,
                     "growth_yoy": 0.00},
    "heeze":        {"dso_days": 30, "invoice_lag_days": 7, "gross_margin": 0.22,
                     "materials_share": 0.45, "subcon_share": 0.35, "labour_share": 0.20,
                     "growth_yoy": 0.00},
    "winschoten":   {"dso_days": 30, "invoice_lag_days": 7, "gross_margin": 0.22,
                     "materials_share": 0.45, "subcon_share": 0.35, "labour_share": 0.20,
                     "growth_yoy": 0.00},
    "andijk":       {"dso_days": 30, "invoice_lag_days": 7, "gross_margin": 0.22,
                     "materials_share": 0.45, "subcon_share": 0.35, "labour_share": 0.20,
                     "growth_yoy": 0.00},
}

SCENARIOS = {
    "base": {"dso_delta": 0,  "rev_mult_next_6w": 1.00, "margin_delta": 0.0},
    "wet":  {"dso_delta": 7,  "rev_mult_next_6w": 0.90, "margin_delta": -0.02},  # wet → schedule slips, margins squeezed
    "dry":  {"dso_delta": -3, "rev_mult_next_6w": 1.05, "margin_delta": 0.00},
}

DRIVER_INFLOW = "milestone_in"
DRIVER_OUTFLOWS = ["materials_out", "subcon_out", "labour_out"]

# Render the knobs as tables so the team sees the inputs before the math
display(pd.DataFrame.from_dict(ASSUMPTIONS, orient="index").reset_index().rename(columns={"index": "portco"}))
display(pd.DataFrame.from_dict(SCENARIOS, orient="index").reset_index().rename(columns={"index": "scenario"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Load gold inputs

# COMMAND ----------

gold_bytes = gold_c.get_blob_client("bookings_classified.parquet").download_blob().readall()
bookings = pd.read_parquet(io.BytesIO(gold_bytes))
bookings["booking_date"] = pd.to_datetime(bookings["booking_date"])
print(f"bookings: {len(bookings):,}")

kpi_bytes = gold_c.get_blob_client("portfolio_kpi_monthly.parquet").download_blob().readall()
kpi = pd.read_parquet(io.BytesIO(kpi_bytes))
print(f"kpi rows: {len(kpi):,}")

# Sales only for now (only inflow driver we can classify from data)
inflows = bookings[bookings["driver"] == DRIVER_INFLOW].copy()
inflows = inflows[inflows["amount_net"] > 0]  # drop reversals/credit-notes for forecast volume
print(f"net inflow bookings: {len(inflows):,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Historical weekly cash by portco × driver
# MAGIC
# MAGIC We aggregate `amount_net` per Monday-week per portco for the **observed**
# MAGIC inflow driver (`milestone_in`). Then we derive the three outflow series
# MAGIC (materials / subcontractor / labour) from gross margin and share splits.
# MAGIC The `is_observed` column makes this distinction explicit so the dashboard
# MAGIC can show inflows as solid lines and derived outflows as dashed/labelled.

# COMMAND ----------

def monday(d):
    d = pd.Timestamp(d).date()
    return d - timedelta(days=d.weekday())

inflows["week_start"] = inflows["booking_date"].apply(monday)

weekly_in = (
    inflows.groupby(["portco", "week_start"], as_index=False)["amount_net"].sum()
    .rename(columns={"amount_net": "amount_eur"})
)
weekly_in["driver"] = DRIVER_INFLOW
weekly_in["is_observed"] = True

# Derived historical outflows (margin-derived)
def derive_outflows(df_in):
    rows = []
    for _, r in df_in.iterrows():
        a = ASSUMPTIONS.get(r["portco"])
        if not a:
            continue
        out_total = r["amount_eur"] * (1 - a["gross_margin"])
        rows.extend([
            {"portco": r["portco"], "week_start": r["week_start"],
             "driver": "materials_out", "amount_eur": -out_total * a["materials_share"], "is_observed": False},
            {"portco": r["portco"], "week_start": r["week_start"],
             "driver": "subcon_out", "amount_eur": -out_total * a["subcon_share"], "is_observed": False},
            {"portco": r["portco"], "week_start": r["week_start"],
             "driver": "labour_out", "amount_eur": -out_total * a["labour_share"], "is_observed": False},
        ])
    return pd.DataFrame(rows)

weekly_out = derive_outflows(weekly_in)
history = pd.concat([weekly_in, weekly_out], ignore_index=True).sort_values(["portco", "week_start", "driver"]).reset_index(drop=True)
history["amount_eur"] = history["amount_eur"].round(2)
print(f"history rows: {len(history):,}")

# Show one recent week per portco so you can see all 4 drivers side-by-side
recent = history[history["week_start"] >= history["week_start"].max() - pd.Timedelta(weeks=8)]
display(recent.pivot_table(index=["portco", "week_start"], columns="driver", values="amount_eur", aggfunc="sum").round(0))

# COMMAND ----------

# Write history
buf = io.BytesIO()
history.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="cashflow_history_weekly.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/cashflow_history_weekly.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Seasonality factors per portco (month-of-year)
# MAGIC
# MAGIC We compute each portco's mean monthly inflow across the complete years
# MAGIC 2023-2025 and express each month as a ratio of that portco's overall
# MAGIC monthly mean. A factor > 1.0 means "this month is busier than average for
# MAGIC this portco". Roofing has a strong summer peak — expect Jul/Aug/Sep > 1.0
# MAGIC and Dec/Jan < 1.0.
# MAGIC
# MAGIC The factor is applied to projected future weeks only — observed weeks come
# MAGIC directly from the ledger.

# COMMAND ----------

inflows_m = inflows.copy()
inflows_m["month"] = inflows_m["booking_date"].dt.month
inflows_m["year"] = inflows_m["booking_date"].dt.year

monthly = inflows_m.groupby(["portco", "year", "month"], as_index=False)["amount_net"].sum()
# Use complete years only (2023..2025) for seasonality so 2026 partial doesn't skew
seasonal_base = monthly[monthly["year"].isin([2023, 2024, 2025])]
month_avg = seasonal_base.groupby(["portco", "month"], as_index=False)["amount_net"].mean().rename(columns={"amount_net": "avg_monthly"})
yearly_avg = seasonal_base.groupby("portco", as_index=False)["amount_net"].mean().rename(columns={"amount_net": "overall_monthly_avg"})
seasonality = month_avg.merge(yearly_avg, on="portco")
seasonality["seasonal_factor"] = (seasonality["avg_monthly"] / seasonality["overall_monthly_avg"]).round(3)
display(seasonality.pivot(index="month", columns="portco", values="seasonal_factor"))

# Lookup helper
def get_seasonal(portco, month):
    row = seasonality[(seasonality["portco"] == portco) & (seasonality["month"] == month)]
    if not row.empty:
        return float(row["seasonal_factor"].iloc[0])
    return 1.0

# COMMAND ----------

# MAGIC %md
# MAGIC ## Per-portco anchor date + recent avg weekly invoicing
# MAGIC
# MAGIC `anchor` = the latest `booking_date` we observed for each portco. Anything
# MAGIC after that is projection. `recent_weekly_avg` is the mean weekly invoicing
# MAGIC over the 13 weeks before the anchor — this is the baseline that
# MAGIC seasonality multiplies against to project future invoicing.

# COMMAND ----------

anchors = inflows.groupby("portco", as_index=False)["booking_date"].max().rename(columns={"booking_date": "anchor"})
anchors["anchor"] = anchors["anchor"].dt.date

# Average weekly invoicing over the last 13 weeks of observed data, per portco
def recent_weekly_avg(portco, anchor_d):
    start = anchor_d - timedelta(weeks=13)
    win = inflows[(inflows["portco"] == portco) &
                  (inflows["booking_date"].dt.date >= start) &
                  (inflows["booking_date"].dt.date <= anchor_d)]
    weeks = max(1, (anchor_d - start).days // 7)
    return float(win["amount_net"].sum()) / weeks

anchors["recent_weekly_avg"] = anchors.apply(lambda r: recent_weekly_avg(r["portco"], r["anchor"]), axis=1)
display(anchors.round({"recent_weekly_avg": 0}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Forecast 13 weeks per (portco, scenario, driver)
# MAGIC
# MAGIC For each future cash week W and each portco × scenario combo we compute
# MAGIC the **invoice window** that should land receipts in W (window = `W − DSO`).
# MAGIC Three branches:
# MAGIC - **observed** — invoice window entirely before the anchor → sum the real
# MAGIC   bookings in that window. Deterministic, traceable to source rows.
# MAGIC - **mixed** — window straddles the anchor → observed for the historical
# MAGIC   part + projection for the future part.
# MAGIC - **projected** — window entirely after the anchor → seasonality × recent
# MAGIC   weekly avg × scenario revenue multiplier.
# MAGIC
# MAGIC The `confidence` column records which branch applied for every row, so the
# MAGIC dashboard can colour-code certainty. Outflows are always `derived` because
# MAGIC they come from the margin assumption, not observed cash.

# COMMAND ----------

# Anchor at the LATEST date across all portcos so the forecast window is aligned
global_anchor = max(anchors["anchor"])
# Week 0 starts the Monday after the anchor
def next_monday(d):
    days_ahead = (7 - d.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return d + timedelta(days=days_ahead)

week0 = next_monday(global_anchor)
weeks = [week0 + timedelta(weeks=i) for i in range(13)]
print(f"global anchor: {global_anchor}; week 0 starts {week0}")

# Pre-aggregate observed bookings by (portco, exact booking date) for the window-sum lookup
inflows["booking_date_only"] = inflows["booking_date"].dt.date
by_date = inflows.groupby(["portco", "booking_date_only"], as_index=False)["amount_net"].sum().rename(columns={"booking_date_only": "d"})

def observed_sum(portco, d_from, d_to):
    df = by_date[(by_date["portco"] == portco) & (by_date["d"] >= d_from) & (by_date["d"] <= d_to)]
    return float(df["amount_net"].sum())

forecast_rows = []

for portco in ASSUMPTIONS.keys():
    a = ASSUMPTIONS[portco]
    a_anchor_row = anchors[anchors["portco"] == portco]
    if a_anchor_row.empty:
        # andijk has no Tx data; estimate weekly avg from KPI
        kpi_p = kpi[kpi["portco"] == portco]
        if kpi_p.empty:
            continue
        kpi_p = kpi_p.sort_values(["year", "month"])
        last_12m = kpi_p.tail(12)["revenue_eur"].mean()  # avg monthly
        avg_w = last_12m / 4.345  # avg weeks per month
        portco_anchor = pd.Timestamp(kpi_p.iloc[-1]["period_start"]).date() + timedelta(days=30)  # rough end of latest month
        use_kpi_only = True
    else:
        portco_anchor = a_anchor_row.iloc[0]["anchor"]
        avg_w = a_anchor_row.iloc[0]["recent_weekly_avg"]
        use_kpi_only = False

    for scenario_name, s in SCENARIOS.items():
        dso = a["dso_days"] + s["dso_delta"]
        margin = max(0.0, min(0.95, a["gross_margin"] + s["margin_delta"]))

        for w_idx, w_start in enumerate(weeks):
            w_end = w_start + timedelta(days=6)
            # Invoice window that would produce receipts in this cash week
            inv_from = w_start - timedelta(days=dso)
            inv_to = w_end - timedelta(days=dso)

            # Decide observed vs projected
            if not use_kpi_only and inv_to <= portco_anchor:
                amt_in = observed_sum(portco, inv_from, inv_to)
                confidence = "observed"
            elif not use_kpi_only and inv_from <= portco_anchor < inv_to:
                # Mixed window: observed portion + projected portion
                obs = observed_sum(portco, inv_from, portco_anchor)
                proj_days = (inv_to - portco_anchor).days
                # weekly avg scaled to that fraction × seasonality
                month_factor = get_seasonal(portco, w_start.month)
                rev_mult = s["rev_mult_next_6w"] if w_idx < 6 else 1.0
                proj = avg_w * (proj_days / 7.0) * month_factor * (1 + a["growth_yoy"]) * rev_mult
                amt_in = obs + proj
                confidence = "mixed"
            else:
                month_factor = get_seasonal(portco, w_start.month) if not use_kpi_only else 1.0
                rev_mult = s["rev_mult_next_6w"] if w_idx < 6 else 1.0
                amt_in = avg_w * month_factor * (1 + a["growth_yoy"]) * rev_mult
                confidence = "projected" if not use_kpi_only else "kpi_projected"

            # Inflow row
            forecast_rows.append({
                "portco": portco,
                "week_idx": w_idx,
                "week_start": w_start,
                "driver": DRIVER_INFLOW,
                "scenario": scenario_name,
                "amount_eur": round(amt_in, 2),
                "confidence": confidence,
                "assumption_dso_days": dso,
                "assumption_gross_margin": round(margin, 4),
                "invoice_date_from": inv_from,
                "invoice_date_to": inv_to,
            })

            # Derived outflows
            out_total = amt_in * (1 - margin)
            for drv, share_key in (("materials_out", "materials_share"),
                                   ("subcon_out", "subcon_share"),
                                   ("labour_out", "labour_share")):
                forecast_rows.append({
                    "portco": portco,
                    "week_idx": w_idx,
                    "week_start": w_start,
                    "driver": drv,
                    "scenario": scenario_name,
                    "amount_eur": round(-out_total * a[share_key], 2),
                    "confidence": "derived",
                    "assumption_dso_days": dso,
                    "assumption_gross_margin": round(margin, 4),
                    "invoice_date_from": None,
                    "invoice_date_to": None,
                })

forecast = pd.DataFrame(forecast_rows)
print(f"forecast rows: {len(forecast):,}  (4 portcos × 13 weeks × 4 drivers × 3 scenarios = 624)")

# Show what the forecast looks like for the FIRST portco's base scenario, all 13 weeks × 4 drivers
example_portco = list(ASSUMPTIONS.keys())[0]
example = forecast[(forecast["portco"] == example_portco) & (forecast["scenario"] == "base")]
display(example.pivot(index=["week_idx", "week_start"], columns="driver", values="amount_eur").round(0))

# Confidence distribution — how much of the forecast is observed vs projected
display(forecast[forecast["driver"] == "milestone_in"].groupby(["portco", "confidence"], as_index=False).size().rename(columns={"size": "weeks"}))

buf = io.BytesIO()
forecast.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="cashflow_forecast_13w.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/cashflow_forecast_13w.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Assumptions table

# COMMAND ----------

assump_rows = []
for portco, a in ASSUMPTIONS.items():
    for s_name, s in SCENARIOS.items():
        assump_rows.append({
            "portco": portco,
            "scenario": s_name,
            "dso_days": a["dso_days"] + s["dso_delta"],
            "gross_margin": max(0.0, min(0.95, a["gross_margin"] + s["margin_delta"])),
            "materials_share": a["materials_share"],
            "subcon_share": a["subcon_share"],
            "labour_share": a["labour_share"],
            "growth_yoy": a["growth_yoy"],
            "rev_mult_first_6w": s["rev_mult_next_6w"],
        })
assump = pd.DataFrame(assump_rows)
buf = io.BytesIO()
assump.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="assumptions.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/assumptions.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary

# COMMAND ----------

# Per (portco, scenario, week) total net cash
forecast["net_eur"] = forecast["amount_eur"]
totals = (
    forecast.groupby(["portco", "scenario", "week_idx", "week_start"], as_index=False)["amount_eur"].sum()
    .rename(columns={"amount_eur": "net_cash"})
)

# 13w net totals per (portco, scenario) — the headline number for the CFO
totals_13w = totals.groupby(["portco", "scenario"], as_index=False)["net_cash"].sum().round(2)
display(totals_13w.pivot(index="portco", columns="scenario", values="net_cash"))

# COMMAND ----------

# Run snapshot
now = datetime.now(timezone.utc)
snap = {
    "captured_at": now.isoformat(timespec="seconds"),
    "global_anchor": str(global_anchor),
    "week_0_start": str(week0),
    "assumptions": list(ASSUMPTIONS.keys()),
    "scenarios": list(SCENARIOS.keys()),
    "history_rows": int(len(history)),
    "forecast_rows": int(len(forecast)),
    "totals_13w": totals_13w.to_dict(orient="records"),
    "anchors": anchors.to_dict(orient="records"),
}
gold_c.upload_blob(name=f"_meta/forecast-build-{now.strftime('%Y%m%dT%H%M%SZ')}.json",
                   data=json.dumps(snap, indent=2, default=str).encode(), overwrite=True)
dbutils.notebook.exit(json.dumps(snap, default=str))
