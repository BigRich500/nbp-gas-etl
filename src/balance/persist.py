"""Rebuild daily_balance.csv / component_breakdown.csv from whatever is
currently on disk in national_gas_daily.csv, and write them through the
same dedup-safe csv_store.write_rows() the rest of the pipeline uses.

This is what PowerBI (or anything else) should point at — plain, current
CSVs, no need to run Python to see the latest numbers. Run this after
src/refresh.py has pulled new raw data:

    python -m src.refresh
    python -m src.balance.persist

Both tables are upsert-mode (see schema.py): each run recomputes and
overwrites the current best-known balance per gas_day/component, rather
than accumulating a revision history. The underlying raw data
(national_gas_daily) already keeps the full revision history if that's
ever needed.

Usage:
    python -m src.balance.persist [--data-dir data/csv_tables]
"""
from __future__ import annotations

import argparse

from src.csv_store import write_rows
from src.balance.balance import daily_balance, component_breakdown


def main():
    parser = argparse.ArgumentParser(description="Persist daily_balance / component_breakdown as CSV tables")
    parser.add_argument("--data-dir", default="data/csv_tables")
    args = parser.parse_args()

    bal = daily_balance(args.data_dir)
    bal = bal.rename(columns={"total_supply": "total_supply_mcm", "total_demand": "total_demand_mcm"})
    result = write_rows(args.data_dir, "daily_balance", bal)
    print(f"daily_balance: {result}")

    breakdown = component_breakdown(args.data_dir)
    result = write_rows(args.data_dir, "component_breakdown", breakdown)
    print(f"component_breakdown: {result}")


if __name__ == "__main__":
    main()
