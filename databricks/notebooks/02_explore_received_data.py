# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Explore: raw/received_original_data/
# MAGIC
# MAGIC Discovery pass over the as-received bronze data. We **don't** infer headers
# MAGIC yet — the brief says meta rows live in the first few rows of many files, so
# MAGIC for each sheet we capture the first 15 rows raw (no header) and let the
# MAGIC controller see where the real data starts.
# MAGIC
# MAGIC Output:
# MAGIC - Per-file inventory: size, modified, container path
# MAGIC - Per-sheet structure: shape, first-15 raw rows, heuristic header-row guess
# MAGIC - JSON snapshot to `gold/_meta/received-inventory-<ts>.json`
# MAGIC - Full JSON returned via `dbutils.notebook.exit` for terminal consumption

# COMMAND ----------

# MAGIC %pip install --quiet openpyxl azure-storage-blob
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
RAW = "raw"
GOLD = "gold"
PREFIX = "received_original_data/"

from azure.storage.blob import BlobServiceClient

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
raw_client = svc.get_container_client(RAW)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Inventory — every blob under the prefix

# COMMAND ----------

blobs = sorted(raw_client.list_blobs(name_starts_with=PREFIX), key=lambda b: b.name)
print(f"found {len(blobs)} blobs under {PREFIX}")
for b in blobs:
    mtime = b.last_modified.strftime("%Y-%m-%d %H:%M")
    print(f"  {b.size:>12,} B  {mtime}  {b.name}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Helpers — header-row heuristic
# MAGIC
# MAGIC Quick-and-dirty: walk down the first N rows; the header is the first row
# MAGIC where (a) at least 50% of cells are non-null AND (b) all non-null cells are
# MAGIC strings AND (c) the next row has comparable fill density. Fallback: row 0.

# COMMAND ----------

import io
import pandas as pd

def guess_header_row(raw_pdf: pd.DataFrame, scan: int = 12) -> int:
    n_cols = raw_pdf.shape[1]
    if n_cols == 0:
        return 0
    for i in range(min(scan, len(raw_pdf) - 1)):
        row = raw_pdf.iloc[i]
        next_row = raw_pdf.iloc[i + 1]
        fill = row.notna().sum() / n_cols
        next_fill = next_row.notna().sum() / n_cols
        all_strings = row.dropna().apply(lambda x: isinstance(x, str)).all()
        if fill >= 0.5 and next_fill >= 0.4 and all_strings:
            return i
    return 0


def load_blob(name: str) -> bytes:
    return raw_client.get_blob_client(name).download_blob().readall()


def safe_str(v):
    if pd.isna(v):
        return None
    return str(v)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Walk every file and capture structure

# COMMAND ----------

import json

inventory = {"prefix": PREFIX, "files": []}

for b in blobs:
    name = b.name
    rel = name[len(PREFIX):]
    entry = {
        "path": name,
        "rel": rel,
        "size_bytes": b.size,
        "last_modified": b.last_modified.isoformat(timespec="seconds"),
        "ext": rel.lower().rsplit(".", 1)[-1] if "." in rel else "",
        "sheets": [],
        "error": None,
        "top_raw_rows": None,  # for non-xlsx
    }
    try:
        data = load_blob(name)
        if entry["ext"] in ("xlsx", "xls"):
            book = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None)
            for sheet_name, raw_pdf in book.items():
                hr = guess_header_row(raw_pdf)
                # also do a header-applied load so we can see inferred column names
                try:
                    pdf_h = pd.read_excel(io.BytesIO(data), sheet_name=sheet_name, header=hr)
                    inferred_cols = list(pdf_h.columns.astype(str))
                    data_rows = int(pdf_h.shape[0])
                except Exception as e:
                    inferred_cols = []
                    data_rows = None
                first_rows = [
                    [safe_str(v) for v in raw_pdf.iloc[i].tolist()]
                    for i in range(min(15, len(raw_pdf)))
                ]
                entry["sheets"].append({
                    "sheet": sheet_name,
                    "raw_shape": list(raw_pdf.shape),
                    "guessed_header_row": int(hr),
                    "inferred_columns_after_header": inferred_cols,
                    "data_rows_after_header": data_rows,
                    "first_15_raw_rows": first_rows,
                })
        elif entry["ext"] == "json":
            txt = data.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(txt)
                # high-level shape, not full dump
                if isinstance(parsed, dict):
                    entry["top_raw_rows"] = {
                        "kind": "dict",
                        "top_keys": list(parsed.keys())[:30],
                        "sample": {k: type(v).__name__ for k, v in list(parsed.items())[:30]},
                    }
                elif isinstance(parsed, list):
                    entry["top_raw_rows"] = {
                        "kind": "list",
                        "len": len(parsed),
                        "sample_first": parsed[0] if parsed and not isinstance(parsed[0], (dict, list)) else (
                            {"_type": type(parsed[0]).__name__, "_keys": list(parsed[0].keys())[:30]}
                            if parsed and isinstance(parsed[0], dict) else None
                        ),
                    }
            except Exception as e:
                entry["error"] = f"json parse: {e}"
        else:
            entry["error"] = f"unsupported ext: {entry['ext']}"
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {e}"

    inventory["files"].append(entry)
    sheets_str = f"{len(entry['sheets'])} sheet(s)" if entry["sheets"] else ""
    err = f"  ERROR: {entry['error']}" if entry["error"] else ""
    print(f"  {entry['size_bytes']:>10,} B  {entry['ext']:5}  {sheets_str:14}  {rel}{err}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Per-file quick console preview

# COMMAND ----------

for f in inventory["files"]:
    print("=" * 80)
    print(f"FILE: {f['rel']}  ({f['size_bytes']:,} B)")
    if f["error"]:
        print(f"  ERROR: {f['error']}")
        continue
    if f["top_raw_rows"]:
        print(f"  JSON: {f['top_raw_rows']}")
    for s in f["sheets"]:
        print(f"  -- sheet: {s['sheet']}  shape: {tuple(s['raw_shape'])}  "
              f"guessed_header_row: {s['guessed_header_row']}  "
              f"data_rows: {s['data_rows_after_header']}")
        if s["inferred_columns_after_header"]:
            print(f"     inferred cols: {s['inferred_columns_after_header'][:15]}"
                  + (" ..." if len(s["inferred_columns_after_header"]) > 15 else ""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Snapshot to gold/_meta/

# COMMAND ----------

from datetime import datetime, timezone

now = datetime.now(timezone.utc)
fname = f"_meta/received-inventory-{now.strftime('%Y%m%dT%H%M%SZ')}.json"

svc.get_container_client(GOLD).upload_blob(
    name=fname,
    data=json.dumps(inventory, indent=2, default=str).encode(),
    overwrite=True,
)
print(f"wrote gold/{fname}")

# COMMAND ----------

# A compact summary (no raw 15-row dumps) for the CLI output — full version is in gold/_meta/
compact = {
    "captured_at": now.isoformat(timespec="seconds"),
    "snapshot_blob": f"gold/{fname}",
    "files": [
        {
            "rel": f["rel"],
            "ext": f["ext"],
            "size_bytes": f["size_bytes"],
            "error": f["error"],
            "sheets": [
                {
                    "sheet": s["sheet"],
                    "raw_shape": s["raw_shape"],
                    "guessed_header_row": s["guessed_header_row"],
                    "inferred_columns_after_header": s["inferred_columns_after_header"],
                    "data_rows_after_header": s["data_rows_after_header"],
                }
                for s in f["sheets"]
            ],
            "top_raw_rows": f.get("top_raw_rows"),
        }
        for f in inventory["files"]
    ],
}
dbutils.notebook.exit(json.dumps(compact, default=str))
