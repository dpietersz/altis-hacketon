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

dbutils.widgets.text("container_a", "", "Container A name")
dbutils.widgets.text("container_b", "", "Container B name")
dbutils.widgets.text("account_key", "", "Storage account key (key1)")

STORAGE_ACCOUNT = "atlishackethon"
container_a = dbutils.widgets.get("container_a")
container_b = dbutils.widgets.get("container_b")
account_key = dbutils.widgets.get("account_key")

assert container_a and container_b and account_key, "Fill all three widgets"

# COMMAND ----------

# HNS is disabled → use wasbs:// against the blob endpoint, not abfss://
blob_host = f"{STORAGE_ACCOUNT}.blob.core.windows.net"

for c in (container_a, container_b):
    spark.conf.set(f"fs.azure.account.key.{blob_host}", account_key)

def wasbs(container: str, path: str = "") -> str:
    return f"wasbs://{container}@{blob_host}/{path}"

print("A:", wasbs(container_a))
print("B:", wasbs(container_b))

# COMMAND ----------

# MAGIC %md
# MAGIC ## List both containers

# COMMAND ----------

for c in (container_a, container_b):
    print(f"=== {c} ===")
    try:
        for f in dbutils.fs.ls(wasbs(c)):
            print(f"{f.size:>12}  {f.path}")
    except Exception as e:
        print("ERROR:", e)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read an XLSX
# MAGIC
# MAGIC Replace the path with one you saw in the listing above, then run.

# COMMAND ----------

import pandas as pd

xlsx_path = wasbs(container_a, "REPLACE_WITH_FILE.xlsx")
local = "/tmp/sample.xlsx"

dbutils.fs.cp(xlsx_path, f"file:{local}")

sheets = pd.read_excel(local, sheet_name=None)
for name, frame in sheets.items():
    print(f"--- {name} {frame.shape} ---")
    display(frame.head())
