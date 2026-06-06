# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Connect to atlishackethon storage & read XLSX (quick & dirty)
# MAGIC
# MAGIC Hackathon mode. Account key pasted into a widget. Do NOT keep this notebook
# MAGIC after the event — rotate the key when we're done.
# MAGIC
# MAGIC Account: `atlishackethon` · plain Blob (HNS disabled) · westeurope.

# COMMAND ----------

# MAGIC %pip install openpyxl
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

dbutils.widgets.text("account_key", "", "Storage account key (key1)")

STORAGE_ACCOUNT = "atlishackethon"
RAW = "raw"
GOLD = "gold"
account_key = dbutils.widgets.get("account_key")

assert account_key, "Paste the storage account key"

# COMMAND ----------

# HNS is disabled → use wasbs:// against the blob endpoint, not abfss://
blob_host = f"{STORAGE_ACCOUNT}.blob.core.windows.net"
spark.conf.set(f"fs.azure.account.key.{blob_host}", account_key)

def wasbs(container: str, path: str = "") -> str:
    return f"wasbs://{container}@{blob_host}/{path}"

print("raw :", wasbs(RAW))
print("gold:", wasbs(GOLD))

# COMMAND ----------

# MAGIC %md
# MAGIC ## List both containers

# COMMAND ----------

for c in (RAW, GOLD):
    print(f"=== {c} ===")
    try:
        for f in dbutils.fs.ls(wasbs(c)):
            print(f"{f.size:>12}  {f.path}")
    except Exception as e:
        print("ERROR:", e)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read raw/database.xlsx

# COMMAND ----------

import pandas as pd

xlsx_path = wasbs(RAW, "database.xlsx")
local = "/tmp/database.xlsx"

dbutils.fs.cp(xlsx_path, f"file:{local}")

sheets = pd.read_excel(local, sheet_name=None)
print(f"Loaded {len(sheets)} sheet(s): {list(sheets)}")
for name, frame in sheets.items():
    print(f"--- {name} {frame.shape} ---")
    display(frame.head())
