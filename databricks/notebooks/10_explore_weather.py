# Databricks notebook source
# MAGIC %md
# MAGIC # 10 — Discover the KNMI weather files (Tier 2 prep)
# MAGIC
# MAGIC The client uploaded three `.txt` files from **KNMI** (the Dutch weather
# MAGIC institute) into `raw/weather_data/`. These are daily climate exports —
# MAGIC one file per weather station.
# MAGIC
# MAGIC KNMI files are standardised: a long commented header (every metadata line
# MAGIC starts with `#`) followed by comma-separated daily rows. The header
# MAGIC explains every column code (`RH` = daily rainfall, `TG` = mean temp,
# MAGIC etc.) and lists the station name + coordinates.
# MAGIC
# MAGIC This notebook just LOOKS — it doesn't write anything to silver/gold yet.
# MAGIC Goal: understand each file's structure so we can write the Tier 2 ETL
# MAGIC plan in `PLAN_TIER2.md`.

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io, re, json
from datetime import datetime, timezone

import pandas as pd
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
RAW = "raw"; GOLD = "gold"
PREFIX = "weather_data/"

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
raw_c = svc.get_container_client(RAW)
gold_c = svc.get_container_client(GOLD)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. List the files

# COMMAND ----------

blobs = sorted(raw_c.list_blobs(name_starts_with=PREFIX), key=lambda b: b.name)
print(f"found {len(blobs)} files under raw/{PREFIX}")
for b in blobs:
    print(f"  {b.size:>10,} B  {b.last_modified.strftime('%Y-%m-%d %H:%M')}  {b.name}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Peek at the header of each file
# MAGIC
# MAGIC KNMI headers are usually 50–100 lines of `#`-prefixed metadata. We dump
# MAGIC the first 100 lines of each file so we can see the station info and
# MAGIC column dictionary.

# COMMAND ----------

contents = {}
for b in blobs:
    raw_bytes = raw_c.get_blob_client(b.name).download_blob().readall()
    # KNMI uses Latin-1 by default; fall back if needed
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1")
    contents[b.name] = text

# COMMAND ----------

for name, text in contents.items():
    print("=" * 100)
    print(f"FILE: {name}    size: {len(text):,} chars")
    print("=" * 100)
    for line in text.splitlines()[:100]:
        print(line)
    print("...\n")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Parse structure: header end + columns + data range
# MAGIC
# MAGIC KNMI convention: the **last `#`-line** before the data is the column
# MAGIC header (column names), and everything after that is data. Detect that
# MAGIC boundary per file.

# COMMAND ----------

def parse_structure(text: str) -> dict:
    lines = text.splitlines()
    last_hash = -1
    for i, line in enumerate(lines):
        if line.startswith("#"):
            last_hash = i
    header_line = lines[last_hash] if last_hash >= 0 else ""
    # Cleanup: drop leading "#" and split
    cols = [c.strip() for c in header_line.lstrip("#").split(",")]

    # Data starts on the first non-blank line after last_hash
    data_start = last_hash + 1
    while data_start < len(lines) and not lines[data_start].strip():
        data_start += 1

    data_rows = [l for l in lines[data_start:] if l.strip()]
    first_data = data_rows[0] if data_rows else None
    last_data = data_rows[-1] if data_rows else None

    # Try to find station info from the early metadata
    station_line = next((l for l in lines[:last_hash]
                        if "STN" in l.upper() and (("," in l) or ("NAME" in l.upper()))), "")

    return {
        "header_lines_count": last_hash + 1,
        "data_rows": len(data_rows),
        "column_header_line": header_line,
        "columns": cols,
        "first_data_row": first_data,
        "last_data_row": last_data,
        "station_meta_hint": station_line.strip(),
    }

structure = {name: parse_structure(text) for name, text in contents.items()}
for name, s in structure.items():
    print(f"\n=== {name} ===")
    print(f"  header lines: {s['header_lines_count']}")
    print(f"  data rows:    {s['data_rows']:,}")
    print(f"  columns ({len(s['columns'])}): {s['columns']}")
    print(f"  first data:   {s['first_data_row']}")
    print(f"  last data:    {s['last_data_row']}")
    print(f"  station hint: {s['station_meta_hint']}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Load the data section as DataFrames

# COMMAND ----------

def load_data(text: str, s: dict) -> pd.DataFrame:
    lines = text.splitlines()
    data_text = "\n".join(lines[s["header_lines_count"]:])
    return pd.read_csv(
        io.StringIO(data_text),
        names=s["columns"],
        skip_blank_lines=True,
        sep=",",
        skipinitialspace=True,
        dtype=str,  # keep raw, cast later
    )

frames = {name: load_data(contents[name], s) for name, s in structure.items()}

for name, df in frames.items():
    print(f"\n=== {name} ===")
    print(f"  shape: {df.shape}")
    print(f"  columns: {list(df.columns)}")
    display(df.head(5))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Date range + quick numeric overview
# MAGIC
# MAGIC KNMI usually exposes:
# MAGIC - `YYYYMMDD` — date
# MAGIC - `RH` — daily rainfall in 0.1 mm (so a value of 12 = 1.2 mm)
# MAGIC - `TG` — mean temperature in 0.1 °C
# MAGIC - `TN` / `TX` — daily min / max temp
# MAGIC - `FG` — average wind speed in 0.1 m/s
# MAGIC - `SQ` — sunshine duration in 0.1 hours
# MAGIC
# MAGIC (Exact set depends on what was requested from KNMI.)

# COMMAND ----------

OVERVIEW_COLS = ["YYYYMMDD", "RH", "TG", "TN", "TX", "FG", "SQ", "FHX", "RHX", "DDVEC"]

for name, df in frames.items():
    print(f"\n=== {name} ===")
    cols_present = [c for c in OVERVIEW_COLS if c in df.columns]
    print(f"  columns of interest present: {cols_present}")
    if "YYYYMMDD" in df.columns:
        d = pd.to_datetime(df["YYYYMMDD"].str.strip(), format="%Y%m%d", errors="coerce")
        d = d.dropna()
        print(f"  date range: {d.min().date()} → {d.max().date()}   ({len(d):,} days)")
    # Numeric quick stats for a couple of cols
    for col in ("RH", "TG", "FG", "SQ"):
        if col in df.columns:
            s = pd.to_numeric(df[col].str.strip().replace("", pd.NA), errors="coerce")
            print(f"  {col:>5}  non-null={s.notna().sum():>6,}  min={s.min()}  max={s.max()}  mean={s.mean():.1f}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Save discovery snapshot

# COMMAND ----------

now = datetime.now(timezone.utc)
snap = {
    "captured_at": now.isoformat(timespec="seconds"),
    "files": [
        {
            "name": name,
            "size_chars": len(contents[name]),
            "header_lines": s["header_lines_count"],
            "data_rows": s["data_rows"],
            "columns": s["columns"],
            "first_data_row": s["first_data_row"],
            "last_data_row": s["last_data_row"],
            "station_meta_hint": s["station_meta_hint"],
        }
        for name, s in structure.items()
    ],
}
fname = f"_meta/weather-inventory-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
gold_c.upload_blob(name=fname, data=json.dumps(snap, indent=2, default=str).encode(), overwrite=True)
print(f"wrote gold/{fname}")

dbutils.notebook.exit(json.dumps({**snap, "raw_headers": {
    name: "\n".join(text.splitlines()[:100]) for name, text in contents.items()
}}, default=str))
