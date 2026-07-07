"""Pull the latest data from Elexon + National Gas and append only new rows.

Duplicate protection: every write goes through csv_store.write_rows, which
checks each row's natural key (see schema.py) against what's already in the
CSV before writing anything. Re-running this script never creates duplicate
rows — it either adds genuinely new data or writes nothing.

Cadence: each National Gas item's registered publication FREQUENCY (Daily,
ASAP, Cyclic every N minutes, Monthly on day N) is looked up from the
dim_national_gas_item CSV and printed alongside how many new rows were found
for it this run, so you can sanity-check the dedup behaviour against how
often that item is actually supposed to publish.

Usage:
    python -m src.csv_etl.refresh [--data-dir data/csv_tables] [--days 3]
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import date, timedelta

import pandas as pd
import requests

sys.path.insert(0, ".")
from src.units import kwh_to_mcm  # noqa: E402
from src.schema import TABLES  # noqa: E402
from src.csv_store import write_rows, table_path  # noqa: E402

ELEXON_URL = "https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH"
NATGAS_URL = "https://data.nationalgas.com/api/find-gas-data-download"

LONDON_TZ = "Europe/London"


def compute_gas_day(start_time_utc: pd.Timestamp) -> date:
    local = start_time_utc.tz_convert(LONDON_TZ)
    if local.hour < 6:
        return (local - pd.Timedelta(days=1)).date()
    return local.date()


def gas_day_bounds(gas_day: date) -> tuple[str, str]:
    local_start = pd.Timestamp(gas_day, tz=LONDON_TZ) + pd.Timedelta(hours=6)
    local_end = local_start + pd.Timedelta(days=1)
    return local_start.tz_convert("UTC").isoformat(), local_end.tz_convert("UTC").isoformat()


def fetch_elexon(days: int) -> pd.DataFrame:
    today = date.today()
    start = today - timedelta(days=days)
    resp = requests.get(
        ELEXON_URL,
        params={"settlementDateFrom": str(start), "settlementDateTo": str(today), "format": "json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    records = payload.get("data", []) if isinstance(payload, dict) else payload
    df = pd.DataFrame(records)
    if df.empty:
        return df
    df["settlement_date"] = df["settlementDate"]
    df["settlement_period"] = df["settlementPeriod"].astype(int)
    df["fuel_type"] = df["fuelType"]
    df["generation_mw"] = pd.to_numeric(df["generation"], errors="coerce")
    df["start_time"] = df["startTime"]
    df["publish_time"] = df["publishTime"]
    st = pd.to_datetime(df["start_time"], utc=True)
    df["gas_day"] = st.apply(compute_gas_day)
    return df[["settlement_date", "settlement_period", "fuel_type", "generation_mw", "start_time", "publish_time", "gas_day"]]


def fetch_national_gas_item(pubob_id: str, unit: str, days: int) -> pd.DataFrame:
    today = date.today()
    start = today - timedelta(days=days)
    params = {
        "applicableFor": "Y",
        "dateFrom": f"{start}T00:00:00",
        "dateTo": f"{today}T23:59:59",
        "dateType": "GASDAY",
        "latestFlag": "N",
        "ids": pubob_id,
        "type": "CSV",
    }
    resp = requests.get(NATGAS_URL, params=params, timeout=45)
    resp.raise_for_status()
    if not resp.text.strip():
        return pd.DataFrame()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = df.columns.str.strip()
    if df.empty:
        return df

    df["gas_day"] = pd.to_datetime(df["Applicable For"], dayfirst=True).dt.date
    df["value_raw"] = pd.to_numeric(df["Value"], errors="coerce")
    df["applicable_at"] = pd.to_datetime(df["Applicable At"], dayfirst=True)
    df["generated_time"] = pd.to_datetime(df["Generated Time"], dayfirst=True, errors="coerce")
    df["quality_indicator"] = df.get("Quality Indicator")
    df["pubob_id"] = pubob_id
    df["unit_raw"] = unit

    if unit == "kWh":
        df["value_mcm"] = kwh_to_mcm(df["value_raw"])
    elif unit in ("mcm/d", "mcm"):
        df["value_mcm"] = df["value_raw"]
    else:
        df["value_mcm"] = None

    bounds = df["gas_day"].apply(gas_day_bounds)
    df["gas_day_start_utc"] = bounds.apply(lambda b: b[0])
    df["gas_day_end_utc"] = bounds.apply(lambda b: b[1])
    df["is_latest"] = True  # recomputed below across the whole table after write

    return df[["gas_day", "pubob_id", "value_raw", "unit_raw", "value_mcm", "applicable_at",
               "generated_time", "quality_indicator", "is_latest", "gas_day_start_utc", "gas_day_end_utc"]]


def recompute_is_latest(data_dir: str):
    path = table_path(data_dir, "national_gas_daily")
    df = pd.read_csv(path, parse_dates=["applicable_at", "generated_time"])
    if df.empty:
        return
    df = df.sort_values(["applicable_at", "generated_time"], na_position="first")
    latest_idx = df.groupby(["gas_day", "pubob_id"]).tail(1).index
    df["is_latest"] = False
    df.loc[latest_idx, "is_latest"] = True
    df.to_csv(path, index=False)


def main():
    parser = argparse.ArgumentParser(description="Refresh CSV tables with latest data (dedup-safe)")
    parser.add_argument("--data-dir", default="data/csv_tables")
    parser.add_argument("--days", type=int, default=3, help="how many days back to check for new data")
    args = parser.parse_args()

    dim_path = table_path(args.data_dir, "dim_national_gas_item")
    dim = pd.read_csv(dim_path)
    active_items = dim[dim["relevant"] == True]  # noqa: E712
    active_items = active_items[active_items["category"].isin([
        "Demand", "Supplies", "Storage", "Linepack", "Price",
    ])] if "category" in active_items.columns else active_items

    print(f"=== Elexon (last {args.days} days, all fuel types) ===")
    edf = fetch_elexon(args.days)
    result = write_rows(args.data_dir, "elexon_generation_hh", edf)
    print(f"  {result}")

    print(f"\n=== National Gas ({len(active_items)} registered items, last {args.days} days) ===")
    total_written = 0
    total_dupes = 0
    for _, item in active_items.iterrows():
        pubob_id, key_name, unit, freq = item["pubob_id"], item["key_name"], item["native_unit"], item.get("frequency")
        try:
            df = fetch_national_gas_item(pubob_id, unit, args.days)
        except Exception as exc:
            print(f"  [{key_name}] FETCH FAILED: {exc}")
            continue
        if df.empty:
            print(f"  [{key_name}] (freq={freq}): no data returned")
            continue
        result = write_rows(args.data_dir, "national_gas_daily", df)
        total_written += result["written"]
        total_dupes += result["duplicates_skipped"]
        print(f"  [{key_name}] (freq={freq}): +{result['written']} new, {result['duplicates_skipped']} already had")

    recompute_is_latest(args.data_dir)
    print(f"\nTotal: {total_written} new rows written, {total_dupes} duplicates correctly skipped")


if __name__ == "__main__":
    main()
