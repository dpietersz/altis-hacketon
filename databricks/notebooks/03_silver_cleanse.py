# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Cleaning up the raw data ("Silver" layer)
# MAGIC
# MAGIC **What this notebook does, in one sentence:** it reads every Excel file the
# MAGIC client sent us and turns them into one tidy table that the rest of the
# MAGIC project can use.
# MAGIC
# MAGIC **Why we need this step.** The client gave us about 25 Excel files. They
# MAGIC come from two different accounting systems (Yuki and Exact) and look very
# MAGIC different from each other: different columns, different sheet names, some
# MAGIC have extra "cover page" rows at the top that aren't real data, and some
# MAGIC files overlap. We can't build a dashboard on top of that mess — so this
# MAGIC notebook tidies it up first.
# MAGIC
# MAGIC **Two terms to know:**
# MAGIC - A *booking* = one row in an accounting ledger. Think of it as a single
# MAGIC   money movement: *"on 12 March we invoiced customer X for €450"*.
# MAGIC - The *Silver layer* is a common data-engineering convention: it's the
# MAGIC   "cleaned-up but not yet enriched" version of the raw files. Notebook 05
# MAGIC   adds the business meaning on top — that's the *Gold layer*.
# MAGIC
# MAGIC **What comes out at the end:** one file called `silver/bookings.parquet`
# MAGIC with every booking from every Excel, in the same shape, with duplicates
# MAGIC removed.

# COMMAND ----------

# MAGIC %pip install --quiet openpyxl azure-storage-blob pyarrow
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io
import re
import json
import unicodedata
from datetime import datetime, timezone

import pandas as pd
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
RAW = "raw"
SILVER = "silver"
GOLD = "gold"
PREFIX = "received_original_data/"

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
raw_client = svc.get_container_client(RAW)
silver_client = svc.get_container_client(SILVER)
gold_client = svc.get_container_client(GOLD)

# COMMAND ----------

# MAGIC %md
# MAGIC ## The shape we want every file to end up in
# MAGIC
# MAGIC We pick one common set of columns and force every Excel file to fit it.
# MAGIC That way, when we stack them all on top of each other later, we have one
# MAGIC consistent table.
# MAGIC
# MAGIC The columns describe each booking:
# MAGIC - **which company** it came from (we call them "portcos" — short for
# MAGIC   portfolio companies, the four roofing businesses the PE firm owns)
# MAGIC - **which kind of money** it was (a Dutch accounting category number —
# MAGIC   notebook 05 translates these to plain English)
# MAGIC - **when** the booking happened (date and which month of the year)
# MAGIC - **how much** money was involved (debit, credit, net amount)
# MAGIC - **a few extras** like VAT amount and the booking number (the unique ID
# MAGIC   the accounting system gave it)
# MAGIC
# MAGIC We also keep the **source file name** on every row — so if someone later
# MAGIC asks "where did this number come from?", we can trace it back to a
# MAGIC specific Excel. That's the "auditability" the challenge wants.

# COMMAND ----------

CANONICAL_COLS = [
    "source_file",
    "source_schema",
    "portco",
    "administration",
    "gl_account",
    "gl_account_text",
    "journal",
    "period",
    "booking_date",
    "booking_number",
    "debit",
    "credit",
    "amount_net",
    "vat_amount",
    "vat_type",
    "boekingstekst",
    "ingested_at",
]


def to_canonical(d: dict, n_rows: int) -> pd.DataFrame:
    """Build canonical DF from per-source columns; fill missing with NaN."""
    df = pd.DataFrame({k: d.get(k, [None] * n_rows) for k in CANONICAL_COLS})
    return df


def _clean_str(s) -> str:
    if pd.isna(s):
        return None
    return unicodedata.normalize("NFKC", str(s)).strip()


def _to_num(v):
    return pd.to_numeric(v, errors="coerce")


def _to_date(v):
    return pd.to_datetime(v, errors="coerce")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reading the files from accounting system #1 — Yuki (Peter Ummels)
# MAGIC
# MAGIC One of the four portfolio companies, **Peter Ummels** (the roofing
# MAGIC company in Brunssum), uses an accounting system called **Yuki**. Their
# MAGIC Excel exports have a quirky layout:
# MAGIC
# MAGIC - **The first 12 rows are a kind of cover page** — they tell you the
# MAGIC   company name, the export date, and which "account category" the file
# MAGIC   covers (for example *Sales 21% VAT* or *Sales with VAT shifted to the
# MAGIC   buyer*).
# MAGIC - **Row 13 is the real header** (Number, Period, Date, Booking number,
# MAGIC   Journal, Debit, Credit).
# MAGIC - **Rows 14 onwards are the actual bookings.**
# MAGIC
# MAGIC So our reader opens each file twice — once to peek at the cover page (so
# MAGIC we know which account category this file is for), once to read the
# MAGIC bookings themselves. We also drop the "grand total" row at the very
# MAGIC bottom of each file.

# COMMAND ----------

def parse_yuki(data: bytes, source_file: str) -> pd.DataFrame:
    raw = pd.read_excel(io.BytesIO(data), sheet_name=0, header=None)

    administration_raw = _clean_str(raw.iloc[0, 0]) or ""
    administration = administration_raw.replace("Administratie:", "").strip().split(" - ", 1)[0].strip()

    gl_raw = _clean_str(raw.iloc[6, 1]) or ""
    if " - " in gl_raw:
        gl_code, gl_text = gl_raw.split(" - ", 1)
    else:
        gl_code, gl_text = gl_raw, ""

    # Header is row 12; data starts row 13
    body = pd.read_excel(io.BytesIO(data), sheet_name=0, header=12)
    body.columns = [str(c).strip() for c in body.columns]
    body = body.dropna(how="all")

    # Drop rows where date is non-parseable (footers like "Eindtotaal")
    body["Datum"] = _to_date(body["Datum"])
    body = body[body["Datum"].notna()].reset_index(drop=True)

    n = len(body)
    return to_canonical({
        "source_file": [source_file] * n,
        "source_schema": ["yuki"] * n,
        "portco": ["peter_ummels"] * n,
        "administration": [administration] * n,
        "gl_account": [gl_code.strip()] * n,
        "gl_account_text": [gl_text.strip()] * n,
        "journal": [_clean_str(v) for v in body.get("Dagboek", [None] * n)],
        "period": _to_num(body.get("Per.", body.get("Per"))).astype("Int64"),
        "booking_date": body["Datum"].dt.date,
        "booking_number": [_clean_str(v) for v in body["Bkst.nr."]],
        "debit": _to_num(body["Debet"]),
        "credit": _to_num(body["Credit"]),
    }, n)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reading the files from accounting system #2 — Exact (Heeze)
# MAGIC
# MAGIC Another portfolio company, **Heeze** (in Noord-Brabant), uses **Exact**.
# MAGIC Their files are much easier — the header is on row 1, no cover page.
# MAGIC
# MAGIC We also pick up two extras these files give us:
# MAGIC - **VAT amount** (the Dutch sales tax on each booking)
# MAGIC - **VAT type** (standard 21%, lower 9%, or "shifted to the buyer" — a
# MAGIC   common construction-industry rule where the buyer pays the VAT instead
# MAGIC   of the seller)
# MAGIC - **Booking description** — a free-form text (often the invoice address)

# COMMAND ----------

def parse_exact_gb(data: bytes, source_file: str) -> pd.DataFrame:
    body = pd.read_excel(io.BytesIO(data), sheet_name=0, header=0)
    body.columns = [str(c).strip() for c in body.columns]
    body = body.dropna(how="all")

    body["Datum"] = _to_date(body["Datum"])
    body = body[body["Datum"].notna()].reset_index(drop=True)

    # GL from Rekening column (matches the filename GL marker)
    gl_account = body["Rekening"].astype(str).str.strip()

    n = len(body)
    return to_canonical({
        "source_file": [source_file] * n,
        "source_schema": ["exact"] * n,
        "portco": ["exact_opco_tbc"] * n,
        "administration": [None] * n,
        "gl_account": gl_account.tolist(),
        "gl_account_text": [_clean_str(v) for v in body.get("Boekingstekst", [None] * n)],
        "journal": [_clean_str(v) for v in body.get("Dagboek", [None] * n)],
        "period": body["Datum"].dt.month.astype("Int64"),
        "booking_date": body["Datum"].dt.date,
        "booking_number": [_clean_str(v) for v in body["Boeknummer"]],
        "debit": _to_num(body["Debet"]),
        "credit": _to_num(body["Credit"]),
        "vat_amount": _to_num(body.get("BTW")),
        "vat_type": [_clean_str(v) for v in body.get("BTW-srt", [None] * n)],
        "boekingstekst": [_clean_str(v) for v in body.get("Boekingstekst", [None] * n)],
    }, n)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reading the third source — a pre-cleaned roll-up (Winschoten)
# MAGIC
# MAGIC For the third portfolio company, **Winschoten** (Groningen), the client
# MAGIC didn't send us the original system's export. Instead they sent a
# MAGIC pre-cleaned summary file with one sheet per year.
# MAGIC
# MAGIC The structure is even simpler — just date, booking number, journal name,
# MAGIC debit, credit and VAT — but we don't know exactly which account category
# MAGIC each booking is in. That gap is fine: every booking in these sheets comes
# MAGIC from the "Sales" journal, so we know it's revenue. Notebook 05 fills in
# MAGIC the missing label.

# COMMAND ----------

def parse_dataset2_yearly(data: bytes, sheet_name: str, source_file: str) -> pd.DataFrame:
    body = pd.read_excel(io.BytesIO(data), sheet_name=sheet_name, header=0)
    body.columns = [str(c).strip() for c in body.columns]
    body = body.dropna(how="all")

    body["Datum"] = _to_date(body["Datum"])
    body = body[body["Datum"].notna()].reset_index(drop=True)

    n = len(body)
    return to_canonical({
        "source_file": [f"{source_file}#{sheet_name}"] * n,
        "source_schema": ["dataset2_yearly"] * n,
        "portco": ["dataset2_opco_tbc"] * n,
        "administration": [None] * n,
        "gl_account": [None] * n,
        "gl_account_text": [None] * n,
        "journal": [_clean_str(v) for v in body["Dagboek"]],
        "period": body["Datum"].dt.month.astype("Int64"),
        "booking_date": body["Datum"].dt.date,
        "booking_number": [_clean_str(v) for v in body["Bkst.nr."]],
        "debit": _to_num(body["Debet"]),
        "credit": _to_num(body["Credit"]),
        "vat_amount": _to_num(body.get("Btw-bedrag")),
    }, n)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Process every file, one by one
# MAGIC
# MAGIC Now we loop through all the Excel files the client gave us. For each one
# MAGIC we look at which folder it sits in and pick the matching reader from
# MAGIC above.
# MAGIC
# MAGIC If a file fails to read for any reason, we don't crash the whole run —
# MAGIC we just log the failure and keep going. That way one weird file can't
# MAGIC sink everything else.

# COMMAND ----------

blobs = sorted(raw_client.list_blobs(name_starts_with=PREFIX), key=lambda b: b.name)
print(f"found {len(blobs)} blobs")

per_source = {}
warnings = []

for b in blobs:
    rel = b.name[len(PREFIX):]
    try:
        data = raw_client.get_blob_client(b.name).download_blob().readall()
    except Exception as e:
        warnings.append({"file": rel, "stage": "download", "error": str(e)})
        continue

    try:
        if rel.startswith("portfolio company 2 data/"):
            df = parse_yuki(data, rel)
            per_source[rel] = df
        elif rel.startswith("portfolio company data/"):
            df = parse_exact_gb(data, rel)
            per_source[rel] = df
        elif rel == "datasets/Altis dataset 2.xlsx":
            for sheet in ("2023", "2024", "2025", "2026"):
                df = parse_dataset2_yearly(data, sheet, rel)
                per_source[f"{rel}#{sheet}"] = df
        else:
            # Skip dataset 1 (KPI), Company E (invoice-list — different schema), JSON
            continue
    except Exception as e:
        warnings.append({"file": rel, "stage": "parse", "error": f"{type(e).__name__}: {e}"})
        continue

print(f"parsed {len(per_source)} source partitions, {len(warnings)} warnings")
if warnings:
    display(pd.DataFrame(warnings))

# One row per parsed source — see rowcounts at a glance
per_source_summary = pd.DataFrame([
    {"source": s, "rows": len(df), "schema": df["source_schema"].iloc[0] if len(df) else None,
     "portco": df["portco"].iloc[0] if len(df) else None}
    for s, df in per_source.items()
]).sort_values(["portco", "source"]).reset_index(drop=True)
display(per_source_summary)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Write per-source parquet to silver/by_source/

# COMMAND ----------

def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


for source, df in per_source.items():
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    silver_client.upload_blob(
        name=f"by_source/{safe_name(source)}.parquet",
        data=buf.getvalue(),
        overwrite=True,
    )
print(f"wrote {len(per_source)} files to silver/by_source/")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Stack everything into one big table, drop duplicates, save it
# MAGIC
# MAGIC Now we stack all the cleaned files on top of each other to get one big
# MAGIC table of every booking.
# MAGIC
# MAGIC **Heads-up: the Yuki files overlap.** For one account category the
# MAGIC client sent us both a "full year" file AND a "first 5 months" file —
# MAGIC meaning the first 5 months of bookings show up twice if we don't watch
# MAGIC out. We spot these duplicates by looking at the booking number + amount
# MAGIC + date together (same numbers on all three = same booking) and keep just
# MAGIC one copy. The "dropped" count below should be a few thousand rows —
# MAGIC those are the safely-removed duplicates.

# COMMAND ----------

canonical = pd.concat(per_source.values(), ignore_index=True) if per_source else pd.DataFrame(columns=CANONICAL_COLS)

# Derive amount_net (credit positive = revenue, debit positive = reversal/note)
canonical["debit"] = canonical["debit"].fillna(0.0)
canonical["credit"] = canonical["credit"].fillna(0.0)
canonical["amount_net"] = canonical["credit"] - canonical["debit"]
canonical["ingested_at"] = datetime.now(timezone.utc)

before = len(canonical)
dedup_keys = ["administration", "gl_account", "booking_number", "debit", "credit", "booking_date"]
# In Yuki files for GL 8005 there are overlapping period exports. Same booking_number
# repeats with identical debit/credit/date → drop_duplicates keeps the first.
canonical = canonical.drop_duplicates(subset=dedup_keys, keep="first").reset_index(drop=True)
after = len(canonical)
print(f"dedup on {dedup_keys}: {before:,} → {after:,}  ({before - after:,} dropped)")

# Write
buf = io.BytesIO()
canonical.to_parquet(buf, index=False)
buf.seek(0)
silver_client.upload_blob(name="bookings.parquet", data=buf.getvalue(), overwrite=True)
print(f"wrote silver/bookings.parquet  rows={len(canonical):,}  cols={len(canonical.columns)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## A peek at the cleaned table

# COMMAND ----------

sdf = spark.createDataFrame(canonical.astype(object).where(pd.notna(canonical), None))
sdf.printSchema()
print(f"rows: {sdf.count():,}")
display(sdf.limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Sanity check — does the cleaned data look reasonable?
# MAGIC
# MAGIC Two quick tables to make sure nothing weird happened during cleanup:
# MAGIC
# MAGIC 1. **Total revenue per company per year** — should look like a healthy
# MAGIC    roofing business (millions of euros per year, not millions of cents).
# MAGIC 2. **Total revenue per account category** — should show mostly
# MAGIC    sales-type categories. (The client only sent us sales-side data so far;
# MAGIC    no materials, no wages, no subcontractor payments.)

# COMMAND ----------

from pyspark.sql import functions as F

by_portco_year = (
    sdf.withColumn("year", F.year("booking_date"))
    .groupBy("portco", "year")
    .agg(
        F.count("*").alias("rows"),
        F.round(F.sum("amount_net"), 2).alias("net_revenue"),
    )
    .orderBy("portco", "year")
)
by_portco_year.show(40, truncate=False)

by_portco_gl = (
    sdf.groupBy("portco", "gl_account", "gl_account_text")
    .agg(
        F.count("*").alias("rows"),
        F.round(F.sum("amount_net"), 2).alias("net_revenue"),
    )
    .orderBy(F.desc("rows"))
)
by_portco_gl.show(40, truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Save a record of this run
# MAGIC
# MAGIC We drop a small summary file alongside the data with stats from this run
# MAGIC (how many rows, how many duplicates removed, when it ran). Handy if
# MAGIC something looks off later and we need to know what the data looked like
# MAGIC at the time of the demo.

# COMMAND ----------

now = datetime.now(timezone.utc)

source_summary = []
for source, df in per_source.items():
    source_summary.append({
        "source": source,
        "rows": int(len(df)),
        "schema": str(df["source_schema"].iloc[0]) if len(df) else None,
        "portco": str(df["portco"].iloc[0]) if len(df) else None,
        "gl_accounts": sorted({str(x) for x in df["gl_account"].dropna().unique().tolist()}),
        "date_min": str(df["booking_date"].min()) if len(df) else None,
        "date_max": str(df["booking_date"].max()) if len(df) else None,
        "credit_total": float(df["credit"].sum()),
        "debit_total": float(df["debit"].sum()),
    })

snapshot = {
    "captured_at": now.isoformat(timespec="seconds"),
    "rows_before_dedup": int(before),
    "rows_after_dedup": int(after),
    "warnings": warnings,
    "per_source": source_summary,
    "by_portco_year": [r.asDict() for r in by_portco_year.collect()],
    "by_portco_gl": [r.asDict() for r in by_portco_gl.collect()],
}

fname = f"_meta/silver-build-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
gold_client.upload_blob(name=fname, data=json.dumps(snapshot, indent=2, default=str).encode(), overwrite=True)
print(f"wrote gold/{fname}")

dbutils.notebook.exit(json.dumps(snapshot, default=str))
