"""Rebuild daily_balance.csv / component_breakdown.csv from whatever is
currently on disk in national_gas_daily.csv, and write them through the
same dedup-safe csv_store.write_rows() the rest of the pipeline uses.

This is what PowerBI (or anything else) should point at — plain, current
CSVs, no need to run Python to see the latest numbers. Run this after
src/refresh.py has pulled new raw data:

    python -m src.refresh
    python -m src.balance.persist

Both tables are upsert-mode (see schema.py), but that's not quite enough
on its own: upsert only adds/updates rows whose key is in the fresh data —
it never removes a row whose key simply isn't produced anymore (e.g. a
component that was removed from src/balance/components.py). Left alone,
that leaves orphaned rows behind forever, silently drifting out of sync
with daily_balance/the current component set (reproduced and fixed: the
IUK/BBL double-count removal left stale "IUK import"/"BBL import" rows
sitting in component_breakdown.csv, so the dashboard's chart — reading
component_breakdown.csv — kept showing ~51 mcm/d more supply than the
headline cards, which read the correctly-recomputed daily_balance.csv).
Since both tables are fully recomputed from national_gas_daily.csv on
every run (not incrementally accumulated), the correct semantics are
"replace everything for each gas_day being rewritten", not a pure
key-level upsert — _replace_days does that pruning before write_rows()
does its normal dedup/upsert.

Usage:
    python -m src.balance.persist [--data-dir data/csv_tables]
"""
from __future__ import annotations

import argparse

import pandas as pd

from src.csv_store import write_rows, table_path
from src.balance.balance import daily_balance, component_breakdown


def _replace_days(data_dir: str, table_name: str, fresh: pd.DataFrame) -> None:
    """Drop existing rows for any gas_day present in `fresh`, so the
    upcoming write_rows() call fully replaces that day's rows instead of
    merging with (and potentially leaving behind) stale ones."""
    path = table_path(data_dir, table_name)
    try:
        existing = pd.read_csv(path)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return
    if existing.empty or fresh.empty:
        return
    pruned = existing[~existing["gas_day"].astype(str).isin(fresh["gas_day"].astype(str))]
    pruned.to_csv(path, index=False)


def main():
    parser = argparse.ArgumentParser(description="Persist daily_balance / component_breakdown as CSV tables")
    parser.add_argument("--data-dir", default="data/csv_tables")
    args = parser.parse_args()

    bal = daily_balance(args.data_dir)
    bal = bal.rename(columns={"total_supply": "total_supply_mcm", "total_demand": "total_demand_mcm"})
    _replace_days(args.data_dir, "daily_balance", bal)
    result = write_rows(args.data_dir, "daily_balance", bal)
    print(f"daily_balance: {result}")

    breakdown = component_breakdown(args.data_dir)
    _replace_days(args.data_dir, "component_breakdown", breakdown)
    result = write_rows(args.data_dir, "component_breakdown", breakdown)
    print(f"component_breakdown: {result}")


if __name__ == "__main__":
    main()
