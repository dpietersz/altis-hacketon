# Databricks notebook source
# MAGIC %md
# MAGIC # 12 — Tier 2 charts: Workable days vs revenue
# MAGIC
# MAGIC The Tier 2 headline KPI: for each month, how many days were workable
# MAGIC (Mon-Fri, no extreme weather), versus how much we invoiced.
# MAGIC
# MAGIC One chart shown four times here so you can see the shape per portco
# MAGIC plus the all-companies portfolio view. Same data structure powers the
# MAGIC Lovable dashboard's company filter.
# MAGIC
# MAGIC **Workable-day rules**
# MAGIC - max temp ≤ 28 °C and min temp ≥ 5 °C
# MAGIC - wind ≤ Beaufort 6 (≤ 13.8 m/s)
# MAGIC - rainfall ≤ 5 mm/day
# MAGIC - Mon–Fri only (Sat/Sun never count)

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow plotly
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
GOLD = "gold"

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
gold_c = svc.get_container_client(GOLD)

def read_gold(n): return pd.read_parquet(io.BytesIO(gold_c.get_blob_client(n).download_blob().readall()))

workable = read_gold("workable_days_monthly.parquet")
workable["period_start"] = pd.to_datetime(workable["period_start"])
print(f"workable_days_monthly: {len(workable):,} rows")
display(workable.head(10))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart maker — one function, used for every view
# MAGIC
# MAGIC Bars: workable days that month. Line: revenue that month. Two y-axes
# MAGIC because they're on completely different scales.

# COMMAND ----------

def workable_vs_revenue_chart(df, title):
    df = df.sort_values("period_start")
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(
        go.Bar(
            x=df["period_start"], y=df["workable_days"],
            name="Workable days", marker_color="#7aa1c8", opacity=0.7,
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=df["period_start"], y=df["revenue_eur"],
            name="Revenue", mode="lines+markers",
            line=dict(color="#2e5d4f", width=2.5),
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=title,
        height=420,
        legend=dict(orientation="h", y=-0.18),
        bargap=0.15,
        xaxis_title=None,
    )
    fig.update_yaxes(title_text="Workable days", secondary_y=False, rangemode="tozero")
    fig.update_yaxes(title_text="Revenue (EUR)", secondary_y=True, rangemode="tozero")
    return fig

# COMMAND ----------

# MAGIC %md
# MAGIC ## One chart per portco

# COMMAND ----------

for portco in sorted(workable["portco"].unique()):
    sub = workable[workable["portco"] == portco]
    src = sub["weather_source"].iloc[0]
    tag = "" if src == "knmi" else "  (weather = portfolio average)"
    fig = workable_vs_revenue_chart(sub, f"{portco} — workable days vs revenue{tag}")
    fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## All companies combined — the "portfolio" view
# MAGIC
# MAGIC For the "all companies" filter on the dashboard:
# MAGIC - bars: **average workable days** across the four portcos that month
# MAGIC - line: **summed revenue** across the four portcos that month
# MAGIC
# MAGIC (Average makes sense for workable days because they're already a count
# MAGIC per company — summing them would push the bar past the calendar max.)

# COMMAND ----------

portfolio = (
    workable.groupby("period_start", as_index=False)
    .agg(
        workable_days=("workable_days", "mean"),
        revenue_eur=("revenue_eur", "sum"),
    )
    .round({"workable_days": 1})
)

fig = workable_vs_revenue_chart(portfolio, "All companies — average workable days vs total revenue")
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC `gold_workable_days_monthly` is the only table the Lovable dashboard needs
# MAGIC to render the per-company and "all" versions of this chart. See
# MAGIC `HANDOFF_LOVABLE.md` for the SQL.
