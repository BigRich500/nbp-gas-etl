"""Create the CSV "tables" this ETL uses, with correct headers.

Idempotent: running this again does not touch CSVs that already exist.

Usage:
    python -m src.csv_etl.create_tables [--data-dir data/csv_tables]
"""
from __future__ import annotations

import argparse
import os

from src.schema import TABLES
from src.csv_store import create_table, table_path


def main():
    parser = argparse.ArgumentParser(description="Create empty CSV tables")
    parser.add_argument("--data-dir", default="data/csv_tables")
    args = parser.parse_args()

    for table_name in TABLES:
        path = table_path(args.data_dir, table_name)
        existed = os.path.exists(path)
        create_table(args.data_dir, table_name)
        status = "already exists" if existed else "created"
        print(f"{table_name}: {status} -> {path}")


if __name__ == "__main__":
    main()
