# Databricks notebook source
# MAGIC %md
# MAGIC # 08 — Export gold to Azure SQL
# MAGIC
# MAGIC Copies every gold parquet to a table in the Azure SQL database `altis`
# MAGIC (server `atlis.database.windows.net`). Tables are dropped + recreated
# MAGIC (`mode="overwrite"`) so a re-run is idempotent.
# MAGIC
# MAGIC Credentials live in the `altis` Databricks secret scope:
# MAGIC `dbutils.secrets.get("altis", "sql_password")`.
# MAGIC
# MAGIC ### One-time setup on the SQL server
# MAGIC
# MAGIC Azure SQL blocks Databricks egress by default. In the portal:
# MAGIC **SQL server `atlis`** → *Security* → *Networking* → enable
# MAGIC **"Allow Azure services and resources to access this server"** and save.
# MAGIC That's the quickest hackathon firewall — for production we'd allowlist
# MAGIC the workspace's serverless egress range.

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io
import pandas as pd
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
GOLD = "gold"

SQL_SERVER = "atlis.database.windows.net"
SQL_DB = "altis"
SQL_USER = "sasqladmin"
SQL_PASSWORD = dbutils.secrets.get("altis", "sql_password")

# Serverless Databricks doesn't allow the generic `jdbc` writer; use the
# native `sqlserver` connector which IS on the allowlist (see UNSUPPORTED_DATA_SOURCE_WRITE).
SQLSERVER_OPTS = {
    "host": SQL_SERVER,
    "port": "1433",
    "database": SQL_DB,
    "user": SQL_USER,
    "password": SQL_PASSWORD,
    "encrypt": "true",
    "trustServerCertificate": "false",
}

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
gold_c = svc.get_container_client(GOLD)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Table manifest
# MAGIC
# MAGIC One row per gold parquet → SQL table.

# COMMAND ----------

TABLES = [
    ("bookings_classified.parquet",          "gold_bookings_classified"),
    ("portfolio_kpi_monthly.parquet",        "gold_portfolio_kpi_monthly"),
    ("cashflow_history_weekly.parquet",      "gold_cashflow_history_weekly"),
    ("cashflow_forecast_13w.parquet",        "gold_cashflow_forecast_13w"),
    ("assumptions.parquet",                  "gold_assumptions"),
    ("covenant_headroom.parquet",            "gold_covenant_headroom"),
    ("cashflow_consolidated_13w.parquet",    "gold_cashflow_consolidated_13w"),
    ("cashflow_consolidated_net_13w.parquet", "gold_cashflow_consolidated_net_13w"),
]

display(pd.DataFrame(TABLES, columns=["blob", "sql_table"]))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Copy each parquet → SQL table
# MAGIC
# MAGIC We pull each parquet from blob into pandas, lift to a Spark DataFrame,
# MAGIC then `write.jdbc(mode="overwrite")`. JDBC overwrite drops and recreates
# MAGIC the table, so each run is a clean replace.

# COMMAND ----------

import json

results = []

for blob_name, table_name in TABLES:
    print(f"=== {blob_name} → {table_name} ===")
    try:
        data = gold_c.get_blob_client(blob_name).download_blob().readall()
        pdf = pd.read_parquet(io.BytesIO(data))
        # Stringify date/timestamp columns that Spark JDBC handles best as text
        # to avoid SQL Server dtype edge cases — controller-tunable later.
        for col in pdf.columns:
            if pdf[col].dtype == "object":
                pdf[col] = pdf[col].astype(object).where(pd.notna(pdf[col]), None)
        sdf = spark.createDataFrame(pdf)
        (
            sdf.write
            .format("sqlserver")
            .option("dbtable", table_name)
            .options(**SQLSERVER_OPTS)
            .mode("overwrite")
            .save()
        )
        rows = len(pdf)
        print(f"  ok   {rows:,} rows")
        results.append({"blob": blob_name, "table": table_name, "rows": rows, "status": "ok"})
    except Exception as e:
        print(f"  FAIL {type(e).__name__}: {e}")
        results.append({"blob": blob_name, "table": table_name, "rows": None,
                        "status": "fail", "error": f"{type(e).__name__}: {e}"})

display(pd.DataFrame(results))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Verify — read each table back

# COMMAND ----------

verify_rows = []
for _, table_name in TABLES:
    try:
        sdf = (
            spark.read.format("sqlserver")
            .option("dbtable", table_name)
            .options(**SQLSERVER_OPTS)
            .load()
        )
        cnt = sdf.count()
        verify_rows.append({"table": table_name, "rows_in_sql": cnt, "status": "ok"})
    except Exception as e:
        verify_rows.append({"table": table_name, "rows_in_sql": None,
                            "status": "fail", "error": f"{type(e).__name__}: {e}"})

display(pd.DataFrame(verify_rows))

# COMMAND ----------

dbutils.notebook.exit(json.dumps({
    "writes": results,
    "verify": verify_rows,
    "sql_server": SQL_SERVER,
    "sql_db": SQL_DB,
}, default=str))
