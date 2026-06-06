# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Silver cleanse
# MAGIC
# MAGIC Read every file from `raw/received_original_data/`, parse per source-system
# MAGIC schema, normalize to the canonical booking schema, dedup on natural key,
# MAGIC and write `silver/bookings.parquet` + `silver/by_source/<file>.parquet`.
# MAGIC
# MAGIC No Spark for blob I/O (Spark Connect denies `fs.azure.account.key.*`).
# MAGIC We use `azure-storage-blob` + pandas, then `spark.createDataFrame` at the
# MAGIC end for display + verification.
# MAGIC
# MAGIC Canonical schema lives in `PLAN.md` § "Canonical silver schema".

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
# MAGIC ## Canonical schema (target)
# MAGIC
# MAGIC We collapse 4 source-system schemas (Yuki, Exact, dataset2-yearly, andijk-KPI)
# MAGIC into one canonical booking row. Same shape coming out of every parser so
# MAGIC the union is a plain `pd.concat`. Fields the source doesn't have stay as
# MAGIC nulls (e.g. Yuki has no `vat_amount`).
# MAGIC
# MAGIC The `source_file` and `source_schema` columns survive into gold so every
# MAGIC downstream number traces back to a specific xlsx + sheet.

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
# MAGIC ## Parser — Schema A (Yuki / Peter Ummels)
# MAGIC
# MAGIC Yuki exports have **12 meta rows** before the real header. Row 0 carries the
# MAGIC administration, row 6 carries the GL account ("Grootboekrekening 8005 - omzet
# MAGIC waarbij de heffing naar u is verlegd"), and row 12 is the actual column header
# MAGIC (`Nr · Per · Datum · Bkst.nr · Dagboek · Debet · Credit`).
# MAGIC
# MAGIC We read the file twice: first with no header to grab the meta, then again
# MAGIC with `header=12` to grab the body. Footer "Eindtotaal" rows are filtered
# MAGIC out by requiring `Datum` to parse as a date.

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
# MAGIC ## Parser — Schema B (Exact / GB files)
# MAGIC
# MAGIC Exact GB exports are tidier — header on row 0, 11 columns. We carry over
# MAGIC `BTW` (VAT amount), `BTW-srt` (VAT type), and `Boekingstekst` (free-text
# MAGIC booking description, often the invoice address).

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
# MAGIC ## Parser — Schema C (Dataset 2 yearly sheets)
# MAGIC
# MAGIC The yearly sheets in `Altis dataset 2.xlsx` are winschoten's transactional
# MAGIC data — 6 columns, one journal (`006 - Verkoop`). No GL column; we leave
# MAGIC `gl_account` null here and let driver classification fall back to the
# MAGIC journal-to-driver map in notebook 05.

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
# MAGIC ## Walk + parse + write per-source
# MAGIC
# MAGIC Iterate every blob under `raw/received_original_data/`, pick a parser by
# MAGIC folder prefix, and accumulate parsed DataFrames in `per_source`. Any file
# MAGIC that throws gets logged in `warnings` so one bad file doesn't kill the
# MAGIC pipeline — the team can read `warnings` after the run to see what didn't
# MAGIC make it in.

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
# MAGIC ## Union → canonical, dedup, write `silver/bookings.parquet`
# MAGIC
# MAGIC The Yuki exports overlap: for GL 8005 each year has BOTH a full-year file
# MAGIC AND a periods 1-5 file. Naively unioning would double-count ~5k rows. We
# MAGIC dedup on `(administration, gl_account, booking_number, debit, credit,
# MAGIC booking_date)` — same booking in two files is identical on all six. The
# MAGIC dropped count below should be ~4-5k for this data set.

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
# MAGIC ## Display via Spark

# COMMAND ----------

sdf = spark.createDataFrame(canonical.astype(object).where(pd.notna(canonical), None))
sdf.printSchema()
print(f"rows: {sdf.count():,}")
display(sdf.limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Quick aggregations to sanity-check
# MAGIC
# MAGIC Net revenue per (portco, year) and per (portco, GL) — first eyeball that
# MAGIC the dedup worked, that GLs in our data are what we expected (all 80xx
# MAGIC Omzet), and that portco labels are consistent.

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
# MAGIC ## Write run snapshot to gold/_meta/

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
