# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Explore: finance_database
# MAGIC
# MAGIC Personal exploration notebook. Run top-to-bottom for:
# MAGIC 1. **Inventory** — what's in `raw/` and `gold/` right now
# MAGIC 2. **Load** — pull `raw/database.xlsx` into pandas
# MAGIC 3. **Schema view** — column / dtype / nulls / distinct counts
# MAGIC 4. **Data preview** — first 50 rows
# MAGIC 5. **Numeric stats** — describe() on number columns
# MAGIC 6. **PySpark view** — same data as a Spark DataFrame for ETL prototyping
# MAGIC 7. **Schema snapshot** — written to `gold/_meta/schema-<ts>.json`
# MAGIC
# MAGIC ### How we track changes
# MAGIC - **Schema drift over time** → automatic, read `gold/_meta/schema-*.json`
# MAGIC - **Human-readable changes** → `databricks/CHANGELOG.md` in this repo
# MAGIC - **Code changes** → `git log databricks/notebooks/`
# MAGIC
# MAGIC ETL belongs in other notebooks. This one stays read-only on `raw/` and
# MAGIC append-only on `gold/_meta/`.

# COMMAND ----------

# MAGIC %pip install --quiet openpyxl azure-storage-blob
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# Hackathon: key lives in the `altis` Databricks secret scope. GitHub blocks the
# raw value in source. Rotate the key after the event. To rotate:
#   databricks secrets put-secret altis storage_key --string-value '<new key>'
STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
RAW = "raw"
GOLD = "gold"

from azure.storage.blob import BlobServiceClient

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Inventory

# COMMAND ----------

for c in (RAW, GOLD):
    print(f"=== {c} ===")
    try:
        blobs = sorted(svc.get_container_client(c).list_blobs(), key=lambda b: b.name)
        for b in blobs:
            mtime = b.last_modified.strftime("%Y-%m-%d %H:%M")
            print(f"  {b.size:>12,} B  {mtime}  {b.name}")
        if not blobs:
            print("  (empty)")
    except Exception as e:
        print("  ERROR:", e)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Load raw/database.xlsx

# COMMAND ----------

import io
import pandas as pd

data = svc.get_blob_client(container=RAW, blob="database.xlsx").download_blob().readall()
sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)

print(f"sheets: {list(sheets)}")
pdf = sheets["finance_database"]
print(f"shape: {pdf.shape}  bytes: {len(data):,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Schema view

# COMMAND ----------

schema_pdf = pd.DataFrame({
    "column": pdf.columns,
    "dtype": [str(t) for t in pdf.dtypes],
    "non_null": pdf.notna().sum().values,
    "nulls": pdf.isna().sum().values,
    "null_pct": (pdf.isna().mean() * 100).round(2).values,
    "distinct": [pdf[c].nunique(dropna=True) for c in pdf.columns],
    "sample": [str(pdf[c].dropna().iloc[0]) if pdf[c].notna().any() else "" for c in pdf.columns],
})
display(schema_pdf)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Data preview (first 50 rows)

# COMMAND ----------

display(pdf.head(50))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Numeric stats

# COMMAND ----------

display(pdf.describe(include="number").T)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. PySpark DataFrame
# MAGIC
# MAGIC Use this as a starting point for ETL notebooks. We round-trip via Arrow,
# MAGIC casting NaN→None so Spark Connect doesn't choke.

# COMMAND ----------

sdf = spark.createDataFrame(pdf.where(pdf.notna(), None))
sdf.printSchema()
print(f"row count: {sdf.count():,}")
display(sdf.limit(20))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Write schema snapshot to gold/_meta/
# MAGIC
# MAGIC One JSON per run. Compare two snapshots to see what changed (added column,
# MAGIC dtype shift, row-count jump).

# COMMAND ----------

import json
from datetime import datetime, timezone

now = datetime.now(timezone.utc)
snapshot = {
    "captured_at": now.isoformat(timespec="seconds"),
    "source": "raw/database.xlsx",
    "sheet": "finance_database",
    "rows": int(pdf.shape[0]),
    "cols": int(pdf.shape[1]),
    "columns": [
        {
            "name": str(c),
            "dtype": str(pdf[c].dtype),
            "nulls": int(pdf[c].isna().sum()),
            "distinct": int(pdf[c].nunique(dropna=True)),
        }
        for c in pdf.columns
    ],
}

fname = f"_meta/schema-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
svc.get_container_client(GOLD).upload_blob(
    name=fname,
    data=json.dumps(snapshot, indent=2).encode(),
    overwrite=True,
)
print(f"wrote gold/{fname}")

# COMMAND ----------

# Surface result to the CLI / get-run-output
dbutils.notebook.exit(json.dumps(snapshot, default=str))
