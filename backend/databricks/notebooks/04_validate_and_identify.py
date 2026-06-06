# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Validate silver + identify portcos
# MAGIC
# MAGIC Read `silver/bookings.parquet`, compare against:
# MAGIC - the aggregated JSON (`Altis Groep — Portfolio P&L Data`)
# MAGIC - `Altis dataset 1.xlsx` monthly KPI roll-up
# MAGIC
# MAGIC Goals:
# MAGIC 1. Resolve the placeholder portco names (`exact_opco_tbc`, `dataset2_opco_tbc`)
# MAGIC    by matching monthly revenue against the JSON's per-portco P&L.
# MAGIC 2. Confirm parsing didn't lose material money vs. the KPI roll-up.
# MAGIC 3. Emit a `gold/_meta/portco_mapping.json` for notebook 05 to consume.

# COMMAND ----------

# MAGIC %pip install --quiet azure-storage-blob pyarrow openpyxl
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import io, json
from datetime import datetime, timezone

import pandas as pd
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT = "atlishackethon"
ACCOUNT_KEY = dbutils.secrets.get("altis", "storage_key")
RAW = "raw"
SILVER = "silver"
GOLD = "gold"

svc = BlobServiceClient(
    account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
    credential=ACCOUNT_KEY,
)
raw_c = svc.get_container_client(RAW)
silver_c = svc.get_container_client(SILVER)
gold_c = svc.get_container_client(GOLD)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Load the aggregated JSON

# COMMAND ----------

agg_bytes = raw_c.get_blob_client("received_original_data/Altis Groep — Portfolio P&L Data (Aggregated).json").download_blob().readall()
agg = json.loads(agg_bytes)
print("top keys:", list(agg.keys()))
print()
print("=== metadata ===")
print(json.dumps(agg.get("metadata", {}), indent=2, default=str)[:1500])

for portco in ("winschoten", "andijk", "peter_ummels", "heeze"):
    block = agg.get(portco, {})
    print(f"\n=== {portco} top keys ===")
    print(list(block.keys()) if isinstance(block, dict) else type(block).__name__)
    if isinstance(block, dict):
        # show one level deeper for first 5 keys
        for k in list(block.keys())[:5]:
            v = block[k]
            if isinstance(v, dict):
                print(f"  {k}: dict keys = {list(v.keys())[:10]}")
            elif isinstance(v, list):
                print(f"  {k}: list[{len(v)}] sample0 = {v[0] if v else None}")
            else:
                print(f"  {k}: {type(v).__name__} = {str(v)[:80]}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Pull per-portco yearly revenue from the JSON (for matching)

# COMMAND ----------

# Walk recursively; collect everything that looks like a year → revenue number
def find_yearly_revenue(d, prefix=""):
    """Return a list of (path, year, value) candidates."""
    out = []
    if isinstance(d, dict):
        for k, v in d.items():
            new_prefix = f"{prefix}/{k}" if prefix else k
            ks = str(k)
            if ks.isdigit() and 2020 <= int(ks) <= 2030 and isinstance(v, (int, float)):
                out.append((prefix, int(ks), float(v)))
            elif isinstance(v, (dict, list)):
                out.extend(find_yearly_revenue(v, new_prefix))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.extend(find_yearly_revenue(v, f"{prefix}[{i}]"))
    return out


for portco in ("winschoten", "andijk", "peter_ummels", "heeze"):
    found = find_yearly_revenue(agg.get(portco, {}), portco)
    if found:
        print(f"=== {portco} ({len(found)} year-numbers found) ===")
        # show first 30
        for path, yr, val in found[:30]:
            print(f"  {path:40} {yr}  {val:>15,.2f}")
    else:
        print(f"=== {portco}: no year:value pairs found ===")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Load silver and compute totals

# COMMAND ----------

silver_bytes = silver_c.get_blob_client("bookings.parquet").download_blob().readall()
silver = pd.read_parquet(io.BytesIO(silver_bytes))
silver["booking_date"] = pd.to_datetime(silver["booking_date"])
silver["year"] = silver["booking_date"].dt.year
silver["month"] = silver["booking_date"].dt.month

print(f"silver rows: {len(silver):,}")
print(silver["portco"].value_counts().to_string())

# COMMAND ----------

# Per-portco per-year net revenue (credit - debit, positive = revenue)
silver_yearly = (
    silver.groupby(["portco", "year"], as_index=False)["amount_net"].sum().round(2)
)
display(silver_yearly)

# COMMAND ----------

# Per-portco per-month net revenue
silver_monthly = (
    silver.groupby(["portco", "year", "month"], as_index=False)["amount_net"].sum().round(2)
)
print(silver_monthly.head(40).to_string())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Pull dataset 1 monthly KPI for cross-check

# COMMAND ----------

ds1_bytes = raw_c.get_blob_client("received_original_data/datasets/Altis dataset 1.xlsx").download_blob().readall()
ds1_sheets = pd.read_excel(io.BytesIO(ds1_bytes), sheet_name=None, header=None)

month_keys = {"Jan": 1, "Feb": 2, "Mrt": 3, "Apr": 4, "Mei": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Okt": 10, "Nov": 11, "Dec": 12}

ds1_rows = []
for sheet_name, raw_pdf in ds1_sheets.items():
    year_str = sheet_name.replace("YTD", "")
    if not year_str.isdigit():
        continue
    year = int(year_str)
    header = raw_pdf.iloc[0].tolist()
    for ri in range(1, len(raw_pdf)):
        row = raw_pdf.iloc[ri].tolist()
        label = str(row[0]).strip() if row[0] is not None else ""
        if not label or label.lower() == "totaal":
            continue
        for ci, m in enumerate(header):
            mkey = str(m).strip() if m is not None else ""
            month = month_keys.get(mkey)
            if month is None:
                continue
            val = row[ci]
            try:
                if val is None or pd.isna(val):
                    continue
                ds1_rows.append({"year": year, "month": month, "label": label, "amount": float(val)})
            except Exception:
                continue

ds1 = pd.DataFrame(ds1_rows)
print("dataset 1 monthly rows:", len(ds1))
# Yearly totals per label
ds1_yearly = ds1.groupby(["year", "label"], as_index=False)["amount"].sum().round(2)
ds1_yearly_total = ds1[ds1["label"] == "Netto-omzet"].groupby("year", as_index=False)["amount"].sum().round(2)
print()
print("Netto-omzet by year (dataset 1):")
print(ds1_yearly_total.to_string(index=False))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Match silver portcos to JSON portcos

# COMMAND ----------

# Try matching by yearly revenue closeness
matches = []
for sp in silver_yearly["portco"].unique():
    sp_rev = silver_yearly[silver_yearly["portco"] == sp].set_index("year")["amount_net"].to_dict()
    # for each candidate JSON portco, sum yearly revenue from the candidate's found years
    candidate_scores = {}
    for jp in ("winschoten", "andijk", "peter_ummels", "heeze"):
        jp_years = find_yearly_revenue(agg.get(jp, {}), jp)
        # collapse to year → max value per year (largest match usually = total revenue)
        jp_max = {}
        for path, yr, val in jp_years:
            if yr in sp_rev:
                jp_max[yr] = max(jp_max.get(yr, 0), val)
        if not jp_max:
            continue
        # score = 1 - mean relative error across overlapping years
        errs = []
        for yr in jp_max:
            sv = sp_rev.get(yr)
            if sv is None or sv == 0:
                continue
            errs.append(abs(jp_max[yr] - sv) / max(abs(sv), 1))
        if errs:
            candidate_scores[jp] = (sum(errs) / len(errs), jp_max)
    if candidate_scores:
        # best = lowest error
        best = sorted(candidate_scores.items(), key=lambda x: x[1][0])
        matches.append({
            "silver_portco": sp,
            "best_match": best[0][0],
            "best_score_mean_rel_err": round(best[0][1][0], 4),
            "best_year_values": {int(k): float(v) for k, v in best[0][1][1].items()},
            "silver_year_values": {int(k): float(v) for k, v in sp_rev.items()},
            "runner_up": best[1][0] if len(best) > 1 else None,
            "runner_up_score": round(best[1][1][0], 4) if len(best) > 1 else None,
        })

print(json.dumps(matches, indent=2, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Emit portco mapping snapshot

# COMMAND ----------

now = datetime.now(timezone.utc)
mapping = {
    "captured_at": now.isoformat(timespec="seconds"),
    "matches": matches,
    "silver_yearly": silver_yearly.to_dict(orient="records"),
    "dataset1_yearly_total": ds1_yearly_total.to_dict(orient="records"),
    "json_portcos_seen": list(agg.keys()),
    "raw_json_for_inspection": agg,  # entire 8KB JSON for offline structure analysis
}

gold_c.upload_blob(
    name=f"_meta/portco-mapping-{now.strftime('%Y%m%dT%H%M%SZ')}.json",
    data=json.dumps(mapping, indent=2, default=str).encode(),
    overwrite=True,
)
# Stable alias for notebook 05 to consume
gold_c.upload_blob(
    name="_meta/portco-mapping-latest.json",
    data=json.dumps(mapping, indent=2, default=str).encode(),
    overwrite=True,
)
print(f"wrote portco mapping snapshot")

dbutils.notebook.exit(json.dumps(mapping, default=str))
