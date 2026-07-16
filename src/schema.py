"""Single source of truth for the CSV "tables" this ETL maintains.

Each entry defines: the CSV filename, column order, and the natural key
used to detect duplicates before writing. This mirrors the Postgres schema
already validated in Supabase (see the Supabase project for this session),
but is intentionally backend-agnostic — these CSVs are meant to become
Postgres tables later on a different machine, using this same column set.
"""
from __future__ import annotations

TABLES = {
    "dim_national_gas_item": {
        "columns": [
            "pubob_id", "key_name", "category", "native_unit", "description",
            "relevant", "value_type", "frequency", "publication_time",
            "ingested_at", "updated_at",
        ],
        # reference data: a pubob_id's metadata can change (e.g. relevant flag
        # flipped) — upsert on the id, don't accumulate history.
        "key": ["pubob_id"],
        "mode": "upsert",
    },
    "elexon_generation_hh": {
        "columns": [
            "settlement_date", "settlement_period", "fuel_type", "generation_mw",
            "start_time", "publish_time", "gas_day", "ingested_at", "updated_at",
        ],
        # Elexon revises a settlement period's generation figure as later,
        # more complete data comes in (publish_time changes) — upsert on the
        # physical identity of the reading, not a new row per revision.
        "key": ["settlement_date", "settlement_period", "fuel_type"],
        "mode": "upsert",
    },
    "national_gas_daily": {
        "columns": [
            "gas_day", "pubob_id", "value_raw", "unit_raw", "value_mcm",
            "applicable_at", "generated_time", "quality_indicator", "is_latest",
            "gas_day_start_utc", "gas_day_end_utc", "ingested_at", "updated_at",
        ],
        # National Gas publishes distinct revisions of the same gas_day+item
        # (different applicable_at/generated_time) — this table is an append-
        # only revision log. A "duplicate" here means we've already stored
        # this exact revision; skip it rather than re-append.
        "key": ["gas_day", "pubob_id", "applicable_at", "generated_time"],
        "mode": "append",
    },
    "daily_balance": {
        "columns": [
            "gas_day", "total_supply_mcm", "total_demand_mcm", "balance_mcm",
            "status", "complete", "ingested_at", "updated_at",
        ],
        # Derived, current-state table (recomputed from national_gas_daily
        # each time src/balance/persist.py runs) — upsert on gas_day, not a
        # revision log.
        "key": ["gas_day"],
        "mode": "upsert",
    },
    "component_breakdown": {
        "columns": [
            "gas_day", "component", "side", "volume_mcm", "ingested_at", "updated_at",
        ],
        "key": ["gas_day", "component"],
        "mode": "upsert",
    },
}
