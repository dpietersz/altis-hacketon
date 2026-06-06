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
print(f"Loaded {len(sheets)} sheet(s): {list(sheets)}")

summary = {"bytes": len(data), "sheets": {}}
for name, frame in sheets.items():
    rows, cols = frame.shape
    summary["sheets"][name] = {
        "rows": rows,
        "cols": cols,
        "columns": list(frame.columns.astype(str)),
        "head": frame.head(3).astype(str).to_dict(orient="records"),
    }
    print(f"--- {name} {frame.shape} ---")
    print(frame.head().to_string())

# Surface structured result to `databricks jobs get-run-output`
dbutils.notebook.exit(json.dumps(summary))
