# Databricks notebook source
# MAGIC %md
# MAGIC # 12 — Tier 2 inspiration charts
# MAGIC
# MAGIC Four charts the Tier 2 dashboard could ship.
# MAGIC
# MAGIC 1. **Work-day-ratio heatmap** — month × portco. The roofing seasonality
# MAGIC    finally measured in days, not just euros.
# MAGIC 2. **Rainfall vs revenue** — wet quarters lining up with billing dips.
# MAGIC 3. **Tier 1 vs Tier 2 net cash** — what weather actually costs.
# MAGIC 4. **Per-portco 13-week weather-adjusted forecast** — three scenarios.

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow plotly
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io
import pandas as pd
import plotly.express as px
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

climate    = read_gold("weather_climate_normals.parquet")
weekly_wx  = read_gold("weather_weekly.parquet")
bookings   = read_gold("bookings_classified.parquet")
forecast1  = read_gold("cashflow_forecast_13w.parquet")
forecast2  = read_gold("cashflow_forecast_13w_weather.parquet")

for df in (weekly_wx, forecast1, forecast2):
    if "week_start" in df.columns:
        df["week_start"] = pd.to_datetime(df["week_start"])

print(f"climate: {len(climate)} | weekly_wx: {len(weekly_wx):,} | f1: {len(forecast1)} | f2: {len(forecast2)}")

SCENARIO_COLOURS = {"base": "#2e5d4f", "wet": "#1f5b8b", "dry": "#b85c00"}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 1 — Work-day-ratio heatmap (per portco × month)
# MAGIC
# MAGIC Darker = quieter month for roofing. The pattern should match the
# MAGIC revenue seasonality we found in Tier 1 — only now in physical reality
# MAGIC (work days), not in euros.

# COMMAND ----------

heat = climate.pivot(index="month", columns="portco", values="base_ratio")
fig = px.imshow(
    heat,
    color_continuous_scale="RdYlGn",
    aspect="auto",
    labels=dict(color="work-day ratio"),
    title="Median monthly work-day ratio (2022-2024) — base climate per portco",
    text_auto=".2f",
)
fig.update_layout(height=400)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 2 — Rainfall vs revenue overlay
# MAGIC
# MAGIC One panel per portco. Bars = total rainfall that quarter, line =
# MAGIC quarterly invoiced revenue. Wet quarters should correlate with revenue
# MAGIC dips — that's the proof the weather model is meaningful.

# COMMAND ----------

# Build quarterly revenue per portco
inflows = bookings[bookings["driver"] == "milestone_in"].copy()
inflows["booking_date"] = pd.to_datetime(inflows["booking_date"])
inflows["q"] = inflows["booking_date"].dt.to_period("Q").dt.to_timestamp()
rev_q = inflows.groupby(["portco", "q"], as_index=False)["amount_net"].sum().rename(columns={"amount_net": "revenue"})

# Quarterly rainfall per portco
weekly_wx["q"] = pd.to_datetime(weekly_wx["week_start"]).dt.to_period("Q").dt.to_timestamp()
rain_q = weekly_wx.groupby(["portco", "q"], as_index=False)["total_rain_mm"].sum()

portcos = sorted(set(rev_q["portco"]) & set(rain_q["portco"]))

fig = make_subplots(
    rows=len(portcos), cols=1,
    subplot_titles=portcos,
    shared_xaxes=False,
    specs=[[{"secondary_y": True}] for _ in portcos],
)

for i, p in enumerate(portcos, start=1):
    rev = rev_q[rev_q["portco"] == p].sort_values("q")
    rain = rain_q[rain_q["portco"] == p].sort_values("q")
    fig.add_trace(
        go.Bar(x=rain["q"], y=rain["total_rain_mm"], name=f"{p} rainfall",
               marker_color="#7aa1c8", opacity=0.55, showlegend=(i==1)),
        row=i, col=1, secondary_y=True,
    )
    fig.add_trace(
        go.Scatter(x=rev["q"], y=rev["revenue"], mode="lines+markers",
                   name=f"{p} revenue", line=dict(color="#2e5d4f", width=2),
                   showlegend=(i==1)),
        row=i, col=1, secondary_y=False,
    )
    fig.update_yaxes(title_text="Revenue €", row=i, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Rain mm", row=i, col=1, secondary_y=True)

fig.update_layout(height=260 * len(portcos),
                  title="Quarterly rainfall (blue bars) vs invoiced revenue (green line)")
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 3 — What does the weather model cost? Tier 1 vs Tier 2
# MAGIC
# MAGIC Side-by-side bars per portco × scenario. The gap between the bars is
# MAGIC the revenue Tier 1 implicitly assumed was rain-free.

# COMMAND ----------

t1_net = forecast1.groupby(["portco", "scenario"], as_index=False)["amount_eur"].sum().assign(model="Tier 1 (no weather)")
t2_net = forecast2.groupby(["portco", "scenario"], as_index=False)["amount_eur"].sum().assign(model="Tier 2 (weather)")
compare = pd.concat([t1_net, t2_net], ignore_index=True)

fig = px.bar(
    compare, x="scenario", y="amount_eur",
    color="model", barmode="group",
    facet_col="portco", facet_col_wrap=2,
    color_discrete_map={"Tier 1 (no weather)": "#5e6470", "Tier 2 (weather)": "#2e5d4f"},
    title="13-week net cash — Tier 1 vs Tier 2 (weather adjusted)",
)
fig.update_layout(height=620, yaxis_title="Net cash €", xaxis_title=None)
fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
fig.update_yaxes(matches=None, showticklabels=True)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 4 — Weather-adjusted 13-week net cash per portco × scenario
# MAGIC
# MAGIC Same layout as the Tier 1 headline chart, but the inflows have been
# MAGIC scaled by the climate-normal work-day ratio for the month each week
# MAGIC falls in.

# COMMAND ----------

net_t2 = (
    forecast2.groupby(["portco", "scenario", "week_idx", "week_start"], as_index=False)["amount_eur"].sum()
    .rename(columns={"amount_eur": "net_cash_eur"})
)

fig = px.line(
    net_t2,
    x="week_start", y="net_cash_eur",
    color="scenario", color_discrete_map=SCENARIO_COLOURS,
    facet_col="portco", facet_col_wrap=2, markers=True,
    title="Weather-adjusted net cash per week (Tier 2)",
)
fig.update_layout(height=600, legend_title_text="Scenario",
                  yaxis_title="Net cash (EUR)", xaxis_title=None)
fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
fig.update_yaxes(matches=None, showticklabels=True)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC The Tier 2 numbers also land in Azure SQL (`gold_weather_weekly`,
# MAGIC `gold_weather_climate_normals`, `gold_cashflow_forecast_13w_weather`)
# MAGIC after notebook 08 is rerun.
