# Databricks notebook source
# MAGIC %md
# MAGIC # 09 — Tier 1 charts
# MAGIC
# MAGIC The four charts the CFO dashboard needs for Tier 1. Each chart below is
# MAGIC paired with a one-line description so you can show this notebook in the
# MAGIC presentation as a "preview of the dashboard".
# MAGIC
# MAGIC 1. **Net cash forecast** — 13 weeks, per company, three scenarios.
# MAGIC 2. **Where the cash goes** — driver split per week, base scenario.
# MAGIC 3. **Covenant headroom** — cumulative cash vs the floor, traffic-lit.
# MAGIC 4. **Portfolio view** — all four companies combined, for the PE board.

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow plotly
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
GOLD = "gold"

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
gold_c = svc.get_container_client(GOLD)

def read_gold(name):
    return pd.read_parquet(io.BytesIO(gold_c.get_blob_client(name).download_blob().readall()))

forecast    = read_gold("cashflow_forecast_13w.parquet")
covenant    = read_gold("covenant_headroom.parquet")
consol_net  = read_gold("cashflow_consolidated_net_13w.parquet")

for df in (forecast, covenant, consol_net):
    if "week_start" in df.columns:
        df["week_start"] = pd.to_datetime(df["week_start"])

print(f"forecast: {len(forecast):,} | covenant: {len(covenant):,} | consolidated: {len(consol_net):,}")

# Consistent colours across all charts
SCENARIO_COLOURS = {"base": "#2e5d4f", "wet": "#1f5b8b", "dry": "#b85c00"}
DRIVER_COLOURS = {
    "milestone_in": "#2f7a3e",   # green = money in
    "materials_out": "#b85c00",  # orange
    "subcon_out":   "#7a5cb8",   # purple
    "labour_out":   "#b1352f",   # red
}

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 1 — Net cash forecast (the headline chart)
# MAGIC
# MAGIC One line per scenario, one panel per company. Shows how much cash the
# MAGIC company nets each week (money in minus all derived money out) over the
# MAGIC next 13 weeks.

# COMMAND ----------

net_per_week = (
    forecast.groupby(["portco", "scenario", "week_idx", "week_start"], as_index=False)["amount_eur"].sum()
    .rename(columns={"amount_eur": "net_cash_eur"})
)

fig = px.line(
    net_per_week,
    x="week_start", y="net_cash_eur",
    color="scenario", color_discrete_map=SCENARIO_COLOURS,
    facet_col="portco", facet_col_wrap=2,
    markers=True,
    title="Net cash per week — next 13 weeks (per company × scenario)",
)
fig.update_layout(height=600, legend_title_text="Scenario",
                  yaxis_title="Net cash (EUR)", xaxis_title=None)
fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
fig.update_yaxes(matches=None, showticklabels=True)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 2 — Where the cash comes from and goes (base scenario)
# MAGIC
# MAGIC Money in (green) sits above the line; the three derived "money out" bars
# MAGIC sit below. The gap between them is the net cash for that week.

# COMMAND ----------

base = forecast[forecast["scenario"] == "base"].copy()

fig = px.bar(
    base,
    x="week_start", y="amount_eur",
    color="driver", color_discrete_map=DRIVER_COLOURS,
    facet_col="portco", facet_col_wrap=2,
    title="Driver split per week — base scenario",
)
fig.update_layout(barmode="relative", height=600,
                  yaxis_title="EUR (above = in, below = out)",
                  xaxis_title=None, legend_title_text="Driver")
fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
fig.update_yaxes(matches=None, showticklabels=True)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 3 — Covenant headroom
# MAGIC
# MAGIC Cumulative cash position (line) vs the covenant floor (dashed). One line
# MAGIC per scenario, one panel per company. The dashed floor is a placeholder
# MAGIC until the real covenant document arrives.

# COMMAND ----------

portcos = sorted(covenant["portco"].unique())

from plotly.subplots import make_subplots
fig = make_subplots(
    rows=2, cols=2,
    subplot_titles=portcos,
    shared_xaxes=False,
)

for idx, portco in enumerate(portcos):
    r, c = (idx // 2) + 1, (idx % 2) + 1
    sub = covenant[covenant["portco"] == portco]
    floor = float(sub["floor_eur"].iloc[0])
    for scenario in ("base", "wet", "dry"):
        s = sub[sub["scenario"] == scenario].sort_values("week_start")
        fig.add_trace(
            go.Scatter(
                x=s["week_start"], y=s["cum_cash_eur"],
                mode="lines+markers", name=f"{portco} · {scenario}",
                line=dict(color=SCENARIO_COLOURS[scenario]),
                legendgroup=scenario,
                showlegend=(idx == 0),
            ),
            row=r, col=c,
        )
    fig.add_hline(
        y=floor, row=r, col=c, line_dash="dash", line_color="#b1352f",
        annotation_text=f"Floor: €{floor:,.0f}", annotation_position="top left",
        annotation_font_size=11,
    )

fig.update_layout(
    title="Cumulative cash vs covenant floor (placeholder)",
    height=620, legend_title_text="Scenario",
)
fig.update_yaxes(title_text=None)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Chart 4 — Portfolio view for the PE board
# MAGIC
# MAGIC All four companies combined into one running total per scenario. This is
# MAGIC the single chart that goes on the board's monthly update slide.

# COMMAND ----------

fig = px.line(
    consol_net,
    x="week_start", y="cum_cash_eur",
    color="scenario", color_discrete_map=SCENARIO_COLOURS,
    markers=True,
    title="Portfolio cumulative cash — next 13 weeks",
)
fig.update_layout(height=420, legend_title_text="Scenario",
                  yaxis_title="Cumulative net cash (EUR)", xaxis_title=None)
fig.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC These four charts cover Tier 1. The same numbers are also in Azure SQL
# MAGIC (see `HANDOFF_LOVABLE.md`) so the frontend can render the same views.
