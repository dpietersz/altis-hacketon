# Databricks notebook source
# MAGIC %md
# MAGIC # 07 — Gold: covenant headroom + consolidated view (Tier 1 ship-stop)
# MAGIC
# MAGIC Inputs:
# MAGIC - `gold/cashflow_forecast_13w.parquet`
# MAGIC - `gold/cashflow_history_weekly.parquet`
# MAGIC
# MAGIC Outputs:
# MAGIC - `gold/covenant_headroom.parquet`   per (portco, scenario, week_idx) rolling slack
# MAGIC - `gold/cashflow_consolidated_13w.parquet`   portfolio-level sum per scenario × week
# MAGIC
# MAGIC ### Covenant model (placeholder until terms doc arrives)
# MAGIC
# MAGIC Without real terms, we use a **simple cash-floor proxy**: the cumulative net
# MAGIC cash position over the 13 weeks must stay above a per-portco floor (defaults
# MAGIC sized as 6 weeks of recent historical inflows, controller-tunable).
# MAGIC
# MAGIC ```
# MAGIC starting_cash[p]    placeholder (€1M default — controller-set per portco)
# MAGIC floor[p]            placeholder — 6× recent_weekly_avg(p)
# MAGIC cum_cash[p, s, w]   = starting_cash[p] + Σ week_net[p, s, 0..w]
# MAGIC headroom[p, s, w]   = cum_cash[p, s, w] − floor[p]
# MAGIC status              = "breach" if cum_cash < floor
# MAGIC                       "watch"  if cum_cash < floor × 1.2
# MAGIC                       "ok"     else
# MAGIC ```

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io, json
from datetime import datetime, timezone

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

forecast = pd.read_parquet(io.BytesIO(gold_c.get_blob_client("cashflow_forecast_13w.parquet").download_blob().readall()))
history  = pd.read_parquet(io.BytesIO(gold_c.get_blob_client("cashflow_history_weekly.parquet").download_blob().readall()))
print(f"forecast: {len(forecast):,}   history: {len(history):,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Placeholder covenant parameters per portco
# MAGIC
# MAGIC We do not have the real covenant terms doc yet, so we use a defensible
# MAGIC stand-in: each portco must keep cumulative cash above a **floor** equal to
# MAGIC ~6 weeks of recent weekly inflow. Starting cash is €1M per portco
# MAGIC (placeholder — controller-set in production). The dashboard MUST label
# MAGIC these as placeholder until the real covenant terms arrive.

# COMMAND ----------

# Recent historical weekly net inflow = milestone_in only, last 13 historical weeks
hist_in = history[history["driver"] == "milestone_in"].copy()
hist_in["week_start"] = pd.to_datetime(hist_in["week_start"])

def recent_avg_in(portco):
    df = hist_in[hist_in["portco"] == portco].sort_values("week_start")
    if df.empty:
        return 0.0
    return float(df.tail(13)["amount_eur"].mean())

PLACEHOLDER_PARAMS = {}
for portco in forecast["portco"].unique():
    avg_in = recent_avg_in(portco)
    PLACEHOLDER_PARAMS[portco] = {
        "starting_cash_eur": 1_000_000.0,       # placeholder — controller-set
        "floor_eur": round(avg_in * 6, 2),      # placeholder — 6× weekly inflow
        "recent_weekly_avg_in_eur": round(avg_in, 2),
    }

display(pd.DataFrame.from_dict(PLACEHOLDER_PARAMS, orient="index").reset_index().rename(columns={"index": "portco"}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Net cash per (portco, scenario, week)
# MAGIC
# MAGIC We collapse the forecast across all drivers to get one net-cash number per
# MAGIC week, then accumulate from `starting_cash`. The `status` column trips
# MAGIC `watch` 20% above the floor and `breach` at or below the floor — the
# MAGIC dashboard uses these for amber/red badges.

# COMMAND ----------

net = (forecast.groupby(["portco", "scenario", "week_idx", "week_start"], as_index=False)["amount_eur"].sum()
       .rename(columns={"amount_eur": "net_cash_eur"}))
net = net.sort_values(["portco", "scenario", "week_idx"]).reset_index(drop=True)

# Cumulative cash with starting balance + floor + status
def annotate(group):
    portco = group["portco"].iloc[0]
    params = PLACEHOLDER_PARAMS.get(portco, {"starting_cash_eur": 0, "floor_eur": 0})
    start = params["starting_cash_eur"]
    floor = params["floor_eur"]
    group = group.copy()
    group["cum_cash_eur"] = (start + group["net_cash_eur"].cumsum()).round(2)
    group["floor_eur"] = floor
    group["headroom_eur"] = (group["cum_cash_eur"] - floor).round(2)
    group["status"] = group["cum_cash_eur"].apply(
        lambda x: "breach" if x < floor else ("watch" if x < floor * 1.2 else "ok")
    )
    return group

covenant = net.groupby(["portco", "scenario"], group_keys=False).apply(annotate).reset_index(drop=True)

# Write
buf = io.BytesIO()
covenant.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="covenant_headroom.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/covenant_headroom.parquet")

# Show one scenario at a time so the team can scan week-by-week status colours
display(
    covenant[covenant["scenario"] == "base"][
        ["portco", "week_idx", "week_start", "net_cash_eur", "cum_cash_eur", "floor_eur", "headroom_eur", "status"]
    ].round(0)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Consolidated 13-week view (PE Board lens)
# MAGIC
# MAGIC The PE board wants one number per week across the portfolio. We sum the
# MAGIC forecast across portcos for each (scenario, week, driver) and also produce
# MAGIC a per-week net cash with rolling cumulative — that's the line the board
# MAGIC actually looks at.

# COMMAND ----------

consolidated = (
    forecast.groupby(["scenario", "week_idx", "week_start", "driver"], as_index=False)["amount_eur"].sum()
    .sort_values(["scenario", "week_idx", "driver"]).reset_index(drop=True)
)
consolidated["amount_eur"] = consolidated["amount_eur"].round(2)

# Net per (scenario, week)
consolidated_net = (
    forecast.groupby(["scenario", "week_idx", "week_start"], as_index=False)["amount_eur"].sum()
    .rename(columns={"amount_eur": "net_cash_eur"}).sort_values(["scenario", "week_idx"]).reset_index(drop=True)
)
consolidated_net["net_cash_eur"] = consolidated_net["net_cash_eur"].round(2)

# Rolling cumulative for consolidated
def cum_per_scenario(df):
    df = df.copy()
    df["cum_cash_eur"] = df["net_cash_eur"].cumsum().round(2)
    return df

consolidated_net = consolidated_net.groupby("scenario", group_keys=False).apply(cum_per_scenario).reset_index(drop=True)

buf = io.BytesIO()
consolidated.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="cashflow_consolidated_13w.parquet", data=buf.getvalue(), overwrite=True)

buf = io.BytesIO()
consolidated_net.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="cashflow_consolidated_net_13w.parquet", data=buf.getvalue(), overwrite=True)

print("wrote consolidated parquets")
display(consolidated_net.pivot(index=["week_idx", "week_start"], columns="scenario", values="cum_cash_eur").round(0))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Status summary
# MAGIC
# MAGIC One row per (portco, scenario, status) telling us how many weeks land in
# MAGIC each colour. This is what feeds the headline indicators on the CFO/PE views.

# COMMAND ----------

status_summary = (
    covenant.groupby(["portco", "scenario", "status"], as_index=False)
    .agg(weeks_count=("week_idx", "count"),
         min_cum_cash=("cum_cash_eur", "min"),
         end_cum_cash=("cum_cash_eur", "last"))
    .round(2)
    .sort_values(["portco", "scenario", "status"])
).reset_index(drop=True)
display(status_summary)

# Worst-case breach across the portfolio per scenario — one row per scenario
worst = (
    covenant.groupby(["scenario"], as_index=False)
    .agg(min_headroom=("headroom_eur", "min"),
         breach_weeks=("status", lambda s: int((s == "breach").sum())),
         watch_weeks=("status", lambda s: int((s == "watch").sum())))
    .round(2)
)
display(worst)

# COMMAND ----------

now = datetime.now(timezone.utc)
snap = {
    "captured_at": now.isoformat(timespec="seconds"),
    "placeholder_params": PLACEHOLDER_PARAMS,
    "status_summary": status_summary.to_dict(orient="records"),
    "worst_case_per_scenario": worst.to_dict(orient="records"),
    "consolidated_net": consolidated_net.to_dict(orient="records"),
}
gold_c.upload_blob(name=f"_meta/covenant-build-{now.strftime('%Y%m%dT%H%M%SZ')}.json",
                   data=json.dumps(snap, indent=2, default=str).encode(), overwrite=True)
dbutils.notebook.exit(json.dumps(snap, default=str))
