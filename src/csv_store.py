"""Dedup-aware CSV read/write helpers.

Every write goes through here so the "don't create duplicate data" rule is
enforced in one place, not re-implemented per table. Two modes, matching
schema.TABLES:

  - "append": rows are a revision log. A row whose key already exists in the
    CSV is a genuine duplicate (we've seen this exact revision before) and is
    dropped before writing — this is the trigger that stops duplicate uploads.
  - "upsert": rows represent current state of an entity. A row whose key
    already exists replaces the old one (the entity's data changed).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd

from src.schema import TABLES


def table_path(data_dir: str, table_name: str) -> str:
    return os.path.join(data_dir, f"{table_name}.csv")


def create_table(data_dir: str, table_name: str) -> str:
    """Create an empty CSV with the correct header row, if it doesn't exist."""
    spec = TABLES[table_name]
    path = table_path(data_dir, table_name)
    os.makedirs(data_dir, exist_ok=True)
    if not os.path.exists(path):
        pd.DataFrame(columns=spec["columns"]).to_csv(path, index=False)
    return path


def load_existing_keys(data_dir: str, table_name: str) -> set[tuple]:
    """Read just the key columns of an existing CSV, as a set of tuples."""
    spec = TABLES[table_name]
    path = table_path(data_dir, table_name)
    if not os.path.exists(path):
        return set()
    try:
        existing = pd.read_csv(path, usecols=spec["key"], dtype=str)
    except (pd.errors.EmptyDataError, ValueError):
        return set()
    if existing.empty:
        return set()
    return set(existing[spec["key"]].astype(str).apply(tuple, axis=1))


def write_rows(data_dir: str, table_name: str, new_rows: pd.DataFrame) -> dict:
    """Dedup `new_rows` against what's already on disk, then write.

    Returns a summary dict: {written, duplicates_skipped, mode}.
    """
    spec = TABLES[table_name]
    path = create_table(data_dir, table_name)
    now = datetime.now(timezone.utc).isoformat()

    new_rows = new_rows.copy()
    for col in spec["columns"]:
        if col not in new_rows.columns:
            new_rows[col] = None
    new_rows = new_rows[spec["columns"]]

    key_cols = spec["key"]
    new_rows["_key"] = new_rows[key_cols].astype(str).apply(tuple, axis=1)

    if spec["mode"] == "append":
        existing_keys = load_existing_keys(data_dir, table_name)
        to_write = new_rows[~new_rows["_key"].isin(existing_keys)].drop(columns="_key")
        duplicates = len(new_rows) - len(to_write)
        if not to_write.empty:
            to_write = to_write.copy()
            to_write["ingested_at"] = now
            to_write["updated_at"] = now
            to_write.to_csv(path, mode="a", header=False, index=False)
        return {"written": len(to_write), "duplicates_skipped": duplicates, "mode": "append"}

    else:  # upsert
        existing = pd.read_csv(path, dtype=str) if os.path.getsize(path) > 0 else pd.DataFrame(columns=spec["columns"])
        if not existing.empty:
            existing["_key"] = existing[key_cols].astype(str).apply(tuple, axis=1)
        else:
            existing["_key"] = pd.Series(dtype=str)

        new_rows = new_rows.copy()
        new_rows["updated_at"] = now
        # only stamp ingested_at for genuinely new keys; keep the original for updates
        existing_keys = set(existing["_key"]) if not existing.empty else set()
        is_new = ~new_rows["_key"].isin(existing_keys)
        new_rows.loc[is_new, "ingested_at"] = now

        # rows that already exist keep their original ingested_at
        if not existing.empty:
            ingested_lookup = dict(zip(existing["_key"], existing.get("ingested_at", pd.Series(dtype=str))))
            mask_existing = ~is_new
            new_rows.loc[mask_existing, "ingested_at"] = new_rows.loc[mask_existing, "_key"].map(ingested_lookup)

        combined = pd.concat([existing, new_rows], ignore_index=True)
        combined = combined.drop_duplicates(subset="_key", keep="last").drop(columns="_key")
        combined = combined[spec["columns"]]
        combined.to_csv(path, index=False)

        updated = (~is_new).sum()
        added = is_new.sum()
        return {"written": int(added), "updated": int(updated), "mode": "upsert"}
