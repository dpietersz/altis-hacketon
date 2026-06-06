# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — Adding business meaning ("Gold" layer)
# MAGIC
# MAGIC **What this notebook does, in one sentence:** it takes the cleaned-up
# MAGIC bookings from notebook 03 and adds the labels the dashboard actually
# MAGIC needs — "which portfolio company is this?" and "what kind of cash flow
# MAGIC does this represent?".
# MAGIC
# MAGIC **Why this step matters.** The dashboard won't show 31,734 anonymous
# MAGIC bookings — it will show charts grouped by company and by cash type. So
# MAGIC we need to label each booking before we can chart anything useful.
# MAGIC
# MAGIC ### The two labels we add to every booking
# MAGIC
# MAGIC **1. Portfolio company name.** In the cleaned-up silver layer, two of
# MAGIC the three companies still have placeholder names. Notebook 04 figured
# MAGIC out who's who; here we put the real names in.
# MAGIC
# MAGIC **2. Driver** — the cash-flow type. We use four categories:
# MAGIC - `milestone_in` — money coming IN from a customer paying an invoice
# MAGIC - `materials_out` — money going OUT to buy roofing materials
# MAGIC - `subcon_out` — money going OUT to subcontractors
# MAGIC - `labour_out` — money going OUT for wages
# MAGIC
# MAGIC The data the client gave us is sales-side only, so every booking we see
# MAGIC right now is `milestone_in`. The other three drivers are calculated
# MAGIC later from business assumptions (notebook 06).
# MAGIC
# MAGIC ### What comes out
# MAGIC
# MAGIC Two tables for the dashboard:
# MAGIC - `gold/bookings_classified.parquet` — every booking with its labels.
# MAGIC - `gold/portfolio_kpi_monthly.parquet` — monthly revenue per company
# MAGIC   pulled from the client's summary file. This is the only source that
# MAGIC   covers all four companies (including Andijk, for which we don't have
# MAGIC   booking-level data).

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
# MAGIC ## The two "translation tables" that drive everything
# MAGIC
# MAGIC We use two simple lookup tables. Both are shown right below as actual
# MAGIC data tables — easy to read, and easy for a controller (a real human at
# MAGIC the client) to change later without touching code.
# MAGIC
# MAGIC **1. Portfolio company rename.** Two of the three companies were tagged
# MAGIC with placeholders in silver. This table tells us which placeholder is
# MAGIC which real company.
# MAGIC
# MAGIC **2. Account category → cash-flow type.** Each Dutch accounting account
# MAGIC (the *Grootboekrekening*, or GL account) gets mapped to one of the four
# MAGIC cash-flow types. Today all the accounts the client gave us start with
# MAGIC `80` — which in the Dutch chart of accounts means "Sales" — so they all
# MAGIC map to "money coming in from customers". When the client sends us
# MAGIC materials or wage data later, those accounts get added to this same
# MAGIC table.
# MAGIC
# MAGIC We save both tables alongside the data so anyone auditing the dashboard
# MAGIC later can see exactly what rules were used.

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
# MAGIC ## Add the two labels to every booking
# MAGIC
# MAGIC We do three small things here:
# MAGIC
# MAGIC 1. **Replace the placeholder company names** with the real ones
# MAGIC    (for example `exact_opco_tbc` → `heeze`).
# MAGIC 2. **Add a "driver" column** — looking up which cash-flow type each
# MAGIC    booking belongs to.
# MAGIC 3. **Tidy up the account-description column.** In the Exact files, this
# MAGIC    column held free-form text per booking (often the invoice address) —
# MAGIC    so it meant something different on every row. We replace it with a
# MAGIC    consistent label (e.g. *"Sales 21% VAT"*) so the column means the
# MAGIC    same thing everywhere. The original free text stays in another column
# MAGIC    so we can still trace back to it.

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
# MAGIC ## Build the monthly revenue table — covers ALL FOUR companies
# MAGIC
# MAGIC The client also gave us a summary file (in JSON format) with month-by-
# MAGIC month revenue for all four portfolio companies — including **Andijk**,
# MAGIC the one we don't have booking-level data for.
# MAGIC
# MAGIC This means the dashboard can still show Andijk's revenue history
# MAGIC alongside the other three (just without the drill-down to bookings).
# MAGIC
# MAGIC The JSON is messy — each company is shaped slightly differently. We walk
# MAGIC through it and pull out every "January 2023: €X" style entry, then save
# MAGIC them all as one tidy table.

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
# MAGIC ## Double-check — do our numbers match the client's summary?
# MAGIC
# MAGIC This is the most important sanity check in this notebook.
# MAGIC
# MAGIC We add up our cleaned bookings per company per year and compare them to
# MAGIC the client's summary file. If they match within ~2%, our cleanup worked.
# MAGIC If there's a big gap, something went wrong and we need to investigate.
# MAGIC
# MAGIC **What we expect to see:**
# MAGIC - **Peter Ummels** and **Winschoten** — nearly identical (within 2%),
# MAGIC   because the client's summary came from the same data we cleaned.
# MAGIC - **Heeze** — our number should be MUCH HIGHER than the summary. That's
# MAGIC   correct: the client noted in their JSON that their Heeze numbers were
# MAGIC   just a rough estimate from a few files. We have all of Heeze's files,
# MAGIC   so we see the real total.
# MAGIC - **Andijk** — our number is zero (we don't have its booking-level data).
# MAGIC   The summary carries the real total.

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
