"""One-off repair: recompute value_mcm for existing national_gas_daily.csv
rows using the corrected unit-matching logic in refresh.py.

Why this is needed: refresh.py's unit matching previously only recognised
"kWh" and exact "mcm/d"/"mcm" strings. National Gas's catalogue actually
uses inconsistent variants (mscm, GWh, Kwh, "kw/h", leading whitespace,
etc.) for the same units, so most rows pulled before this fix have
value_mcm stuck at NULL even though value_raw and unit_raw were captured
correctly. This does not re-fetch anything — it recomputes value_mcm from
the unit_raw already stored on each row, in place.

Safe to run multiple times (idempotent). Run once after pulling the
refresh.py fix; not needed for rows written after that fix.

Usage:
    python -m src.repair_value_mcm [--data-dir data/csv_tables]
"""
from __future__ import annotations

import argparse

import pandas as pd

from src.csv_store import table_path
from src.refresh import value_to_mcm


def main():
    parser = argparse.ArgumentParser(description="Recompute value_mcm from stored unit_raw")
    parser.add_argument("--data-dir", default="data/csv_tables")
    args = parser.parse_args()

    path = table_path(args.data_dir, "national_gas_daily")
    df = pd.read_csv(path)

    def _recompute(row):
        result = value_to_mcm(pd.Series([row["value_raw"]]), row["unit_raw"])
        return result.iloc[0] if result is not None else None

    before_null = df["value_mcm"].isna().sum()
    df["value_mcm"] = df.apply(_recompute, axis=1)
    after_null = df["value_mcm"].isna().sum()

    df.to_csv(path, index=False)
    print(f"{path}: value_mcm null before={before_null}, after={after_null} (repaired {before_null - after_null} rows)")


if __name__ == "__main__":
    main()
