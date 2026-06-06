# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Connect to atlishackethon storage & read XLSX (serverless-safe)
# MAGIC
# MAGIC Serverless Databricks blocks `spark.conf.set("fs.azure.account.key.*")` via
# MAGIC Spark Connect's allowlist. So we skip Spark and use the Azure Blob SDK
# MAGIC directly — pure Python on the driver.
# MAGIC
# MAGIC Account: `atlishackethon` · plain Blob (HNS disabled) · westeurope.

# COMMAND ----------

# MAGIC %pip install --quiet openpyxl azure-storage-blob
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("account_key", "", "Storage account key (key1)")

STORAGE_ACCOUNT = "atlishackethon"
RAW = "raw"
GOLD = "gold"
account_key = dbutils.widgets.get("account_key")

assert account_key, "Paste the storage account key"

# COMMAND ----------

from azure.storage.blob import BlobServiceClient

account_url = f"https://{STORAGE_ACCOUNT}.blob.core.windows.net"
svc = BlobServiceClient(account_url=account_url, credential=account_key)

# COMMAND ----------

# MAGIC %md
# MAGIC ## List both containers

# COMMAND ----------

for c in (RAW, GOLD):
    print(f"=== {c} ===")
    try:
        for blob in svc.get_container_client(c).list_blobs():
            print(f"{blob.size:>12}  {blob.name}")
    except Exception as e:
        print("ERROR:", e)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read raw/database.xlsx

# COMMAND ----------

import pandas as pd
import io, json

blob = svc.get_blob_client(container=RAW, blob="database.xlsx")
data = blob.download_blob().readall()
print(f"Downloaded {len(data):,} bytes")

sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
sheet_name = next(iter(sheets))
pdf = sheets[sheet_name]
print(f"Sheet: {sheet_name} · shape: {pdf.shape}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Pandas preview

# COMMAND ----------

display(pdf.head(20))
print("\n=== dtypes ===")
print(pdf.dtypes.to_string())
print("\n=== describe (numeric) ===")
print(pdf.describe(include="number").to_string())
print("\n=== nulls per column ===")
print(pdf.isna().sum().to_string())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Convert to PySpark DataFrame
# MAGIC
# MAGIC On serverless we round-trip via Arrow. The Spark DataFrame gets the same
# MAGIC rows; type inference comes from pandas.

# COMMAND ----------

# Pandas leaves NaN floats in money columns — Spark Connect Arrow chokes on
# Object columns with mixed types, so cast NaN→None first.
pdf_clean = pdf.where(pdf.notna(), None)

sdf = spark.createDataFrame(pdf_clean)
print("=== schema ===")
sdf.printSchema()
print(f"\nrow count: {sdf.count():,}")
sdf.show(20, truncate=False)
display(sdf)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Quick aggregations to prove Spark is usable

# COMMAND ----------

from pyspark.sql import functions as F

by_company = (
    sdf.groupBy("company")
    .agg(
        F.count("*").alias("rows"),
        F.round(F.sum(F.coalesce(F.col("debit").cast("double"), F.lit(0.0))), 2).alias("debit_total"),
        F.round(F.sum(F.coalesce(F.col("credit").cast("double"), F.lit(0.0))), 2).alias("credit_total"),
    )
    .orderBy(F.desc("rows"))
)
by_company.show(20, truncate=False)

by_ledger = (
    sdf.groupBy("ledger")
    .agg(F.count("*").alias("rows"))
    .orderBy(F.desc("rows"))
)
by_ledger.show(20, truncate=False)

# COMMAND ----------

# Surface structured result to `databricks jobs get-run-output`
summary = {
    "bytes": len(data),
    "sheet": sheet_name,
    "rows": int(pdf.shape[0]),
    "cols": int(pdf.shape[1]),
    "columns": list(pdf.columns.astype(str)),
    "dtypes": {c: str(t) for c, t in pdf.dtypes.items()},
    "nulls": {c: int(n) for c, n in pdf.isna().sum().items()},
    "head": pdf.head(5).astype(str).to_dict(orient="records"),
    "spark_schema": [(f.name, f.dataType.simpleString()) for f in sdf.schema.fields],
    "spark_row_count": sdf.count(),
    "top_companies": [r.asDict() for r in by_company.limit(10).collect()],
    "top_ledgers": [r.asDict() for r in by_ledger.limit(10).collect()],
}
dbutils.notebook.exit(json.dumps(summary, default=str))
