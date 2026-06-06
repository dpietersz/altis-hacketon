# Databricks notebook source
# MAGIC %md
# MAGIC # 07 — Loan-safety check + portfolio view (the final dashboard data)
# MAGIC
# MAGIC **What this notebook does, in one sentence:** it figures out whether
# MAGIC each company will run too low on cash in the next 13 weeks, and
# MAGIC produces one portfolio-wide view for the PE board.
# MAGIC
# MAGIC ### What's a "covenant"?
# MAGIC
# MAGIC When a private-equity firm or a bank lends money to a company, they put
# MAGIC rules in the loan contract — for example, *"you must keep at least €X in
# MAGIC the bank at all times"*. These rules are called **covenants**.
# MAGIC
# MAGIC If the company breaks a covenant, the lender can call the loan back,
# MAGIC raise the interest rate, or take control. So the CFO watches the cash
# MAGIC buffer like a hawk.
# MAGIC
# MAGIC **"Headroom"** is just the gap between the company's current cash and
# MAGIC the minimum the contract requires. Lots of headroom = relaxed. Headroom
# MAGIC shrinking = time to act (delay supplier payments, chase customers,
# MAGIC tap reserves).
# MAGIC
# MAGIC ### Important note about the numbers below
# MAGIC
# MAGIC We **don't have the real covenant document yet** — the client promised
# MAGIC to send it. So we use a placeholder rule:
# MAGIC
# MAGIC > Each company should keep cumulative cash above a "floor" equal to
# MAGIC > about 6 weeks of recent revenue, starting from €1 million in the bank.
# MAGIC
# MAGIC The dashboard MUST label these numbers as PLACEHOLDER until the real
# MAGIC covenant arrives. When it does, we just swap the placeholder rule and
# MAGIC re-run this notebook.
# MAGIC
# MAGIC ### Three "traffic light" statuses per week
# MAGIC
# MAGIC - 🟢 **OK** — cash is comfortably above the floor.
# MAGIC - 🟡 **Watch** — cash is within 20% of the floor; the CFO should pay
# MAGIC   attention.
# MAGIC - 🔴 **Breach** — cash has dropped below the floor; the covenant is
# MAGIC   broken.

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
# MAGIC ## The placeholder rules per company
# MAGIC
# MAGIC The table below shows what we're using for each company:
# MAGIC - **Starting cash** = €1,000,000 (placeholder until the client tells us
# MAGIC   the real opening balance).
# MAGIC - **Floor** = 6 × that company's recent weekly revenue. Bigger companies
# MAGIC   need more working cash, so their floor is higher.
# MAGIC
# MAGIC Again — these are PLACEHOLDERS until we get the real covenant document.

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
# MAGIC ## Cash position week by week for each company × scenario
# MAGIC
# MAGIC For each company and each scenario, we calculate four things per week:
# MAGIC
# MAGIC 1. **Net cash this week** = money in - money out (summing all four
# MAGIC    cash-flow types).
# MAGIC 2. **Running total** = starting cash + everything since week 0.
# MAGIC 3. **Headroom** = running total - floor.
# MAGIC 4. **Status** = 🟢 OK / 🟡 Watch / 🔴 Breach.
# MAGIC
# MAGIC This is the data the CFO dashboard will plot as a line chart with
# MAGIC coloured background bands.

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
# MAGIC ## The portfolio-wide view (for the PE board)
# MAGIC
# MAGIC The CFO cares about each company individually. The PE board (the owners
# MAGIC of all four companies) cares about the whole portfolio at once.
# MAGIC
# MAGIC So here we add up all four companies into one big weekly number per
# MAGIC scenario. The single line *"Cumulative portfolio cash, base scenario"*
# MAGIC is the chart that goes on the PE board's monthly update slide.

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
# MAGIC ## Headline summary — how many weeks land in each colour?
# MAGIC
# MAGIC One table that tells you, for each company × scenario, how many of the
# MAGIC 13 weeks come out green / yellow / red. This is what feeds the big
# MAGIC "covenant status" headline on the dashboard ("Heeze: 6 weeks at risk in
# MAGIC the wet scenario").

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
