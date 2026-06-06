# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — Gold: classify + resolve
# MAGIC
# MAGIC Reads `silver/bookings.parquet` and the aggregated JSON, then writes:
# MAGIC - `gold/bookings_classified.parquet` — silver + resolved portco names + driver column
# MAGIC - `gold/portfolio_kpi_monthly.parquet` — per-portco monthly revenue from JSON (covers
# MAGIC   all 4 portcos including `andijk` which has no Tx data)
# MAGIC - `gold/_meta/gl_driver_mapping.json` — auditable GL → driver mapping
# MAGIC - `gold/_meta/portco-mapping-latest.json` — confirmed silver-portco → real-portco map
# MAGIC
# MAGIC Confirmed mapping (notebook 04):
# MAGIC - `peter_ummels` ← Yuki exports
# MAGIC - `heeze` ← Exact GB 8000/8001/8002
# MAGIC - `winschoten` ← Altis dataset 2 yearly sheets
# MAGIC - `andijk` ← KPI-only (no Tx)

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow openpyxl
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io, json, re
from datetime import datetime, timezone

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

# COMMAND ----------

# MAGIC %md
# MAGIC ## Static mappings (audit-friendly)
# MAGIC
# MAGIC Two lookup tables drive the gold transformation:
# MAGIC
# MAGIC - **`PORTCO_RENAME`** — resolves the "TBC" placeholders from silver
# MAGIC   (`exact_opco_tbc`, `dataset2_opco_tbc`) into real portco names confirmed
# MAGIC   in notebook 04 by matching monthly revenue against the aggregated JSON.
# MAGIC - **`GL_DRIVER_MAP`** — each GL account → which driver it represents.
# MAGIC   Today all 80xx accounts are `milestone_in` (sales). When new GL accounts
# MAGIC   appear (materials, subcontractors, payroll), a controller adds rows here
# MAGIC   — no pipeline change needed.
# MAGIC
# MAGIC Both maps are echoed to `gold/_meta/` so an auditor can see what was in
# MAGIC effect for any given run.

# COMMAND ----------

PORTCO_RENAME = {
    "peter_ummels": "peter_ummels",
    "exact_opco_tbc": "heeze",
    "dataset2_opco_tbc": "winschoten",
}

# GL → driver mapping. Controller-editable in production via a mapping table.
GL_DRIVER_MAP = {
    "8000": ("milestone_in", "Omzet hoog 21%"),
    "8001": ("milestone_in", "Omzet verlegd"),
    "8002": ("milestone_in", "Omzet belast 9%"),
    "8004": ("milestone_in", "Omzet 0% / niet bij u belast"),
    "8005": ("milestone_in", "Omzet verlegd (subcontracting)"),
    "80020": ("milestone_in", "Omzet verlegd 21%"),
    "80000": ("milestone_in", "Omzet hoog 21%"),
}

# Journals (Dagboek) → fallback driver when gl_account is missing (dataset 2 yearly sheets)
JOURNAL_DRIVER_MAP = {
    "006 - Verkoop": "milestone_in",
    "80 - Verkoop": "milestone_in",
    "Verkoopboek 1": "milestone_in",
}


# Render the mappings as DataFrames so the team can SEE the rules at a glance
display(pd.DataFrame([{"silver_portco": k, "gold_portco": v} for k, v in PORTCO_RENAME.items()]))
display(pd.DataFrame([{"gl_account": k, "driver": d, "label": lbl} for k, (d, lbl) in GL_DRIVER_MAP.items()]))

# COMMAND ----------

def classify(gl_account, journal):
    gl = str(gl_account).strip() if gl_account is not None else ""
    if gl in GL_DRIVER_MAP:
        return GL_DRIVER_MAP[gl][0], GL_DRIVER_MAP[gl][1]
    # try by GL prefix
    if gl.startswith("80") and gl[:5].isdigit():
        return "milestone_in", f"Omzet (GL {gl})"
    # fallback by journal
    j = str(journal).strip() if journal else ""
    if j in JOURNAL_DRIVER_MAP:
        return JOURNAL_DRIVER_MAP[j], "Sales journal"
    return "other", None

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build `gold/bookings_classified`
# MAGIC
# MAGIC Three transformations on top of silver:
# MAGIC 1. **Resolve portco** — apply `PORTCO_RENAME`.
# MAGIC 2. **Classify driver** — look up `gl_account` in `GL_DRIVER_MAP`, fall back to journal,
# MAGIC    fall back to "other".
# MAGIC 3. **Clean `gl_account_text`** — for the Exact source this column was a per-booking
# MAGIC    free-text field (invoice address). Replace with the controlled label from the map
# MAGIC    so the column means the same thing in every row. Raw free text stays in
# MAGIC    `boekingstekst` for traceability.

# COMMAND ----------

silver_bytes = silver_c.get_blob_client("bookings.parquet").download_blob().readall()
silver = pd.read_parquet(io.BytesIO(silver_bytes))
print(f"silver rows: {len(silver):,}")
print("\nSilver portco counts BEFORE rename:")
print(silver["portco"].value_counts().to_string())

# Resolve portco
silver["portco"] = silver["portco"].map(PORTCO_RENAME).fillna(silver["portco"])
print("\nAFTER rename:")
print(silver["portco"].value_counts().to_string())

# Classify driver
classified = silver.apply(
    lambda r: pd.Series(classify(r["gl_account"], r["journal"]), index=["driver", "driver_label"]),
    axis=1,
)
gold = pd.concat([silver, classified], axis=1)

# For Exact source, gl_account_text got noisy free-text from Boekingstekst.
# Use GL_DRIVER_MAP label as the clean text where available, keeping original
# in `boekingstekst` for traceability.
def clean_gl_text(row):
    gl = str(row["gl_account"]).strip() if row["gl_account"] is not None else ""
    if gl in GL_DRIVER_MAP:
        return GL_DRIVER_MAP[gl][1]
    return row["gl_account_text"]

gold["gl_account_text"] = gold.apply(clean_gl_text, axis=1)

# Final touch-ups
gold["ingested_at"] = datetime.now(timezone.utc)
print(f"\ngold rows: {len(gold):,}")
print("\nDriver split:")
print(gold["driver"].value_counts().to_string())

# Sample after classification — pick 2 rows per portco to eyeball the transformation
print("\nSample bookings after classify (2 per portco):")
sample_cols = ["portco", "gl_account", "gl_account_text", "driver", "booking_date", "amount_net", "boekingstekst"]
display(gold.groupby("portco", group_keys=False).head(2)[sample_cols])

# Write
buf = io.BytesIO()
gold.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="bookings_classified.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/bookings_classified.parquet")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Build `gold/portfolio_kpi_monthly` from the aggregated JSON
# MAGIC
# MAGIC The JSON gives portfolio-level Netto-omzet per portco per month, including
# MAGIC **andijk** (for which we have no transaction data). This is the only
# MAGIC source that covers all 4 portcos, so the frontend uses it for any portfolio
# MAGIC roll-up that must include andijk.
# MAGIC
# MAGIC The JSON shape differs per portco — month keys like `jan-23` live at
# MAGIC varying depths and sometimes split across GL accounts (heeze is a "floor
# MAGIC estimate" from partial data). The walker flattens everything to one row
# MAGIC per `(portco, year, month)`; for heeze we sum across its GL-specific
# MAGIC series.

# COMMAND ----------

agg = json.loads(
    raw_c.get_blob_client("received_original_data/Altis Groep — Portfolio P&L Data (Aggregated).json")
    .download_blob().readall()
)

NL_MONTHS = {"jan": 1, "feb": 2, "mrt": 3, "apr": 4, "mei": 5, "jun": 6,
             "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}

MONTH_KEY_RE = re.compile(r"^(jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec)-(\d{2})$")


def extract_kpi(d, portco, prefix=""):
    """Yield (portco, year, month, value, path) tuples from any month-keyed dict."""
    if isinstance(d, dict):
        for k, v in d.items():
            new_prefix = f"{prefix}/{k}" if prefix else k
            ks = str(k).lower().strip()
            m = MONTH_KEY_RE.match(ks)
            if m and isinstance(v, (int, float)):
                month = NL_MONTHS[m.group(1)]
                year = 2000 + int(m.group(2))
                yield (portco, year, month, float(v), new_prefix)
            elif isinstance(v, (dict, list)):
                yield from extract_kpi(v, portco, new_prefix)
    elif isinstance(d, list):
        for i, v in enumerate(d):
            yield from extract_kpi(v, portco, f"{prefix}[{i}]")


kpi_rows = []
for portco in ("winschoten", "andijk", "peter_ummels", "heeze"):
    block = agg.get(portco, {})
    for pc, yr, mo, val, path in extract_kpi(block, portco):
        # For heeze (and any others), there can be multiple paths per (year, month).
        # Tag with `series_path` so the consumer can pick the right one.
        kpi_rows.append({
            "portco": pc,
            "year": yr,
            "month": mo,
            "revenue_eur": round(val, 2),
            "series_path": path,
        })

kpi = pd.DataFrame(kpi_rows)
print(f"KPI rows: {len(kpi):,}")
print(kpi.groupby("portco").size().to_string())

# For each (portco, year, month) keep the row whose series_path most likely represents total revenue:
# - winschoten / andijk / peter_ummels: a single revenue series
# - heeze: multiple GL-specific series; we keep all but mark which one
# Strategy: produce one row per (portco, year, month) summing across GL paths when needed.
def is_gl_specific(path: str) -> bool:
    return "_gl" in path.lower() or path.lower().endswith("_partial") or "floor_estimate" in path.lower()

# For non-heeze: just dedup keeping first (one series per month)
non_heeze = kpi[kpi["portco"] != "heeze"].drop_duplicates(subset=["portco", "year", "month"], keep="first")
# For heeze: sum across GL paths (floor estimate)
heeze = (
    kpi[kpi["portco"] == "heeze"]
    .groupby(["portco", "year", "month"], as_index=False)["revenue_eur"]
    .sum()
    .round(2)
)
heeze["series_path"] = "summed_gl_partials"

kpi_clean = pd.concat([
    non_heeze[["portco", "year", "month", "revenue_eur", "series_path"]],
    heeze[["portco", "year", "month", "revenue_eur", "series_path"]],
], ignore_index=True).sort_values(["portco", "year", "month"]).reset_index(drop=True)

kpi_clean["period_start"] = pd.to_datetime(
    kpi_clean["year"].astype(str) + "-" + kpi_clean["month"].astype(str) + "-01"
).dt.date

buf = io.BytesIO()
kpi_clean.to_parquet(buf, index=False)
buf.seek(0)
gold_c.upload_blob(name="portfolio_kpi_monthly.parquet", data=buf.getvalue(), overwrite=True)
print("wrote gold/portfolio_kpi_monthly.parquet")

# Annual totals per portco — quick eyeball of which portcos are present and at what scale
display(
    kpi_clean.groupby(["portco", "year"], as_index=False)["revenue_eur"].sum()
    .pivot(index="portco", columns="year", values="revenue_eur").round(0)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Cross-check: gold transactional totals vs KPI JSON
# MAGIC
# MAGIC The single most important validation step. We sum `amount_net` per (portco,
# MAGIC year, month) from gold bookings and compare to the JSON KPI. **Δ within ~2%
# MAGIC means parsing + dedup are honest.** Bigger gaps are flagged for follow-up.
# MAGIC
# MAGIC Expected behaviour:
# MAGIC - **peter_ummels** / **winschoten**: tight match (silver came from same source as JSON).
# MAGIC - **heeze**: gold should be much HIGHER than JSON (the JSON note says its
# MAGIC   heeze numbers are a "floor estimate" from partial data; we have full GBs).
# MAGIC - **andijk**: gold = 0 (no Tx data); KPI carries the value.

# COMMAND ----------

gold_monthly = (
    gold.assign(year=pd.to_datetime(gold["booking_date"]).dt.year,
                month=pd.to_datetime(gold["booking_date"]).dt.month)
    .groupby(["portco", "year", "month"], as_index=False)["amount_net"].sum().round(2)
    .rename(columns={"amount_net": "amount_net_tx"})
)

kpi_compare = kpi_clean.rename(columns={"revenue_eur": "revenue_kpi"})
joined = pd.merge(gold_monthly, kpi_compare[["portco", "year", "month", "revenue_kpi"]],
                  on=["portco", "year", "month"], how="outer")
joined["delta"] = (joined["amount_net_tx"].fillna(0) - joined["revenue_kpi"].fillna(0)).round(2)
joined["delta_pct"] = (joined["delta"] / joined["revenue_kpi"].replace(0, pd.NA) * 100).round(2)

# Summary by portco-year — Δ% column makes the validation result obvious
summary = (
    joined.groupby(["portco", "year"], as_index=False)
    .agg(tx_total=("amount_net_tx", "sum"),
         kpi_total=("revenue_kpi", "sum"),
         delta_total=("delta", "sum"))
    .round(2)
)
summary["delta_pct"] = (summary["delta_total"] / summary["kpi_total"].replace(0, pd.NA) * 100).round(2)
display(summary)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write meta snapshots

# COMMAND ----------

now = datetime.now(timezone.utc)

portco_map_snap = {
    "captured_at": now.isoformat(timespec="seconds"),
    "mapping": {
        "peter_ummels": {"city": "Brunssum, Limburg", "source": "Yuki", "tx_data": True},
        "heeze": {"city": "Heeze, Noord-Brabant", "source": "Exact GB", "tx_data": True},
        "winschoten": {"city": "Winschoten, Groningen", "source": "dataset 2 yearly", "tx_data": True},
        "andijk": {"city": "Andijk, Noord-Holland", "source": "dataset 1 KPI roll-up", "tx_data": False},
    },
    "silver_to_gold_rename": PORTCO_RENAME,
    "tx_total_vs_kpi": summary.to_dict(orient="records"),
}
gold_c.upload_blob(name="_meta/portco-mapping-latest.json",
                   data=json.dumps(portco_map_snap, indent=2, default=str).encode(), overwrite=True)

gl_map_snap = {
    "captured_at": now.isoformat(timespec="seconds"),
    "gl_account_to_driver": {gl: {"driver": d, "label": lbl} for gl, (d, lbl) in GL_DRIVER_MAP.items()},
    "journal_to_driver": JOURNAL_DRIVER_MAP,
    "fallback": "GL prefix 80* → milestone_in",
}
gold_c.upload_blob(name="_meta/gl_driver_mapping.json",
                   data=json.dumps(gl_map_snap, indent=2, default=str).encode(), overwrite=True)

print("wrote gold/_meta/portco-mapping-latest.json")
print("wrote gold/_meta/gl_driver_mapping.json")

# COMMAND ----------

dbutils.notebook.exit(json.dumps({
    "gold_bookings_rows": int(len(gold)),
    "kpi_rows": int(len(kpi_clean)),
    "summary_tx_vs_kpi": summary.to_dict(orient="records"),
    "portco_mapping": portco_map_snap["mapping"],
    "gl_mapping_count": len(GL_DRIVER_MAP),
}, default=str))
