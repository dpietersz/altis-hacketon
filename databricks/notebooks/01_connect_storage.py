# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Connect to Azure Storage & read XLSX
# MAGIC
# MAGIC Goal: prove we can read `.xlsx` files from two ADLS Gen2 containers in the same
# MAGIC resource group as this serverless workspace.
# MAGIC
# MAGIC **Fill in the widgets below before running.** Auth method defaults to
# MAGIC `account_key` for fastest hackathon setup — switch to `service_principal` or
# MAGIC `managed_identity` for anything you want to keep.

# COMMAND ----------

# MAGIC %pip install openpyxl
# dbutils.library.restartPython()  # uncomment on classic clusters; not needed on serverless

# COMMAND ----------

dbutils.widgets.text("storage_account", "", "Storage account name")
dbutils.widgets.text("container_a", "", "Container A name")
dbutils.widgets.text("container_b", "", "Container B name")
dbutils.widgets.dropdown(
    "auth_method",
    "account_key",
    ["account_key", "sas_token", "service_principal", "managed_identity"],
    "Auth method",
)

storage_account = dbutils.widgets.get("storage_account")
container_a = dbutils.widgets.get("container_a")
container_b = dbutils.widgets.get("container_b")
auth_method = dbutils.widgets.get("auth_method")

assert storage_account and container_a and container_b, "Fill the widgets first"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configure Spark for ABFS
# MAGIC
# MAGIC Pulls the secret from a Databricks secret scope named `altis`. Create the scope
# MAGIC and put your key/SAS/client-secret in it — never paste credentials in the
# MAGIC notebook itself.
# MAGIC
# MAGIC ```bash
# MAGIC databricks secrets create-scope altis
# MAGIC databricks secrets put-secret altis storage_key            # for account_key
# MAGIC databricks secrets put-secret altis storage_sas            # for sas_token
# MAGIC databricks secrets put-secret altis sp_client_id           # for service_principal
# MAGIC databricks secrets put-secret altis sp_client_secret       # for service_principal
# MAGIC databricks secrets put-secret altis sp_tenant_id           # for service_principal
# MAGIC ```

# COMMAND ----------

endpoint = f"{storage_account}.dfs.core.windows.net"

if auth_method == "account_key":
    spark.conf.set(
        f"fs.azure.account.key.{endpoint}",
        dbutils.secrets.get("altis", "storage_key"),
    )

elif auth_method == "sas_token":
    spark.conf.set(f"fs.azure.account.auth.type.{endpoint}", "SAS")
    spark.conf.set(
        f"fs.azure.sas.token.provider.type.{endpoint}",
        "org.apache.hadoop.fs.azurebfs.sas.FixedSASTokenProvider",
    )
    spark.conf.set(
        f"fs.azure.sas.fixed.token.{endpoint}",
        dbutils.secrets.get("altis", "storage_sas"),
    )

elif auth_method == "service_principal":
    tenant_id = dbutils.secrets.get("altis", "sp_tenant_id")
    spark.conf.set(f"fs.azure.account.auth.type.{endpoint}", "OAuth")
    spark.conf.set(
        f"fs.azure.account.oauth.provider.type.{endpoint}",
        "org.apache.hadoop.fs.azurebfs.oauth2.ClientCredsTokenProvider",
    )
    spark.conf.set(
        f"fs.azure.account.oauth2.client.id.{endpoint}",
        dbutils.secrets.get("altis", "sp_client_id"),
    )
    spark.conf.set(
        f"fs.azure.account.oauth2.client.secret.{endpoint}",
        dbutils.secrets.get("altis", "sp_client_secret"),
    )
    spark.conf.set(
        f"fs.azure.account.oauth2.client.endpoint.{endpoint}",
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/token",
    )

elif auth_method == "managed_identity":
    # Requires the workspace's managed identity to have Storage Blob Data Reader on
    # the storage account. Serverless compute uses the workspace's system-assigned MI
    # when no other creds are configured.
    spark.conf.set(f"fs.azure.account.auth.type.{endpoint}", "OAuth")
    spark.conf.set(
        f"fs.azure.account.oauth.provider.type.{endpoint}",
        "org.apache.hadoop.fs.azurebfs.oauth2.MsiTokenProvider",
    )

# COMMAND ----------

def abfss(container: str, path: str = "") -> str:
    return f"abfss://{container}@{endpoint}/{path}"

print("Container A root:", abfss(container_a))
print("Container B root:", abfss(container_b))

# COMMAND ----------

# MAGIC %md
# MAGIC ## List contents of both containers

# COMMAND ----------

for c in (container_a, container_b):
    print(f"=== {c} ===")
    try:
        for f in dbutils.fs.ls(abfss(c)):
            print(f.path, f.size)
    except Exception as e:
        print("ERROR:", e)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read an XLSX file
# MAGIC
# MAGIC Serverless doesn't ship `com.crealytics:spark-excel`, so the simplest path is
# MAGIC pandas + openpyxl. Swap in the actual path you saw in the listing above.

# COMMAND ----------

import pandas as pd

xlsx_path = abfss(container_a, "REPLACE_WITH_FILE.xlsx")

# pandas can read directly from abfss:// via fsspec — but on Databricks the simplest
# route is to copy locally first via dbutils.fs.cp, then read with pandas.
local = "/tmp/sample.xlsx"
dbutils.fs.cp(xlsx_path, f"file:{local}")

df = pd.read_excel(local, sheet_name=None)  # dict of {sheet_name: DataFrame}
for sheet, frame in df.items():
    print(sheet, frame.shape)
    display(frame.head())
