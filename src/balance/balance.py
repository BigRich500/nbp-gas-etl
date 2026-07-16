"""Daily supply/demand balance, computed from national_gas_daily.csv.

Same output shape as the original NBP-Gas-SD-Stack dashboard's balance
engine (date, total_supply, total_demand, balance_mcm, status), written
fresh for this project's schema and component set (src/balance/components.py).

Only uses is_latest=true rows — the most current known revision of each
gas day's figures. Reads from the local CSVs (data/csv_tables by default),
not Supabase, which has diverged and is now a stale, smaller subset.
"""
from __future__ import annotations

import argparse

import pandas as pd

from src.csv_store import table_path
from src.balance.components import COMPONENTS, LNG_ENTRY_IDS, SUBTERMINAL_ENTRY_IDS

# Aggregate-item components that fall back to a per-site sum on days the
# aggregate has no row — see components.py docstring for why each needs one.
_AGGREGATE_FALLBACKS = {
    "LNG": LNG_ENTRY_IDS,
    "Subterminal entry (UKCS + Norway)": SUBTERMINAL_ENTRY_IDS,
}

SURPLUS_THRESHOLD_MCM = 2.0
DEFICIT_THRESHOLD_MCM = -2.0


def _load_latest(data_dir: str) -> pd.DataFrame:
    path = table_path(data_dir, "national_gas_daily")
    df = pd.read_csv(path, parse_dates=["gas_day"])
    # Keep gas_day as a plain date, matching national_gas_daily.csv's own
    # on-disk convention (written from Python date objects, not Timestamps).
    # Carrying datetime64 through to daily_balance/component_breakdown made
    # their gas_day round-trip through CSV as "2026-07-05 00:00:00" while
    # astype(str) on the in-memory value produced "2026-07-05" — the dedup
    # key in csv_store.write_rows() never matched, so upserts silently
    # became inserts (verified: reproduced duplicate rows in daily_balance.csv).
    df["gas_day"] = df["gas_day"].dt.date
    return df[df["is_latest"] == True]  # noqa: E712


def _component_series(latest: pd.DataFrame, component) -> pd.Series:
    """Daily value_mcm for one component, per its aggregation mode."""
    rows = latest[latest["pubob_id"].isin(component.pubob_ids)]

    if component.mode == "sum":
        return rows.groupby("gas_day")["value_mcm"].sum()

    if component.mode == "sign_positive":
        return rows[rows["value_mcm"] > 0].groupby("gas_day")["value_mcm"].sum()

    if component.mode == "sign_negative":
        # export volume is reported as a positive demand figure, so negate
        exports = rows[rows["value_mcm"] < 0]
        return (-exports["value_mcm"]).groupby(exports["gas_day"]).sum()

    raise ValueError(f"unknown component mode: {component.mode}")


def component_breakdown(data_dir: str = "data/csv_tables") -> pd.DataFrame:
    """Long/tidy: gas_day, component, side, volume_mcm — one row per
    component per day, for drill-down into what's driving the balance."""
    latest = _load_latest(data_dir)

    frames = []
    for component in COMPONENTS:
        series = _component_series(latest, component)

        # Aggregate-item fallback: some aggregate items (e.g. PUBOBJ337 for
        # LNG, PUBOBJ627 for subterminal entry) don't publish for every
        # gas_day. Fill only the missing days from the per-site sum —
        # combine_first keeps a real 0.0 from the aggregate as-is, and only
        # fills gas_days absent from it. Must be done per-day, not by
        # checking if the aggregate is empty across the whole table: once it
        # has a row for *any* day, a table-wide empty-check stops falling
        # back and silently drops supply on every day it itself has no row
        # for (this exact bug was found and fixed for LNG — see git history).
        fallback_ids = _AGGREGATE_FALLBACKS.get(component.name)
        if fallback_ids:
            fallback_series = _component_series(
                latest, type(component)(component.name, component.side, fallback_ids, mode="sum")
            )
            series = series.combine_first(fallback_series)

        if series.empty:
            continue
        frames.append(pd.DataFrame({
            "gas_day": series.index,
            "component": component.name,
            "side": component.side,
            "volume_mcm": series.values,
        }))

    if not frames:
        return pd.DataFrame(columns=["gas_day", "component", "side", "volume_mcm"])
    return pd.concat(frames, ignore_index=True).sort_values(["gas_day", "side", "component"])


def daily_balance(data_dir: str = "data/csv_tables") -> pd.DataFrame:
    """gas_day, total_supply, total_demand, balance_mcm, status, complete.

    balance_mcm = total_supply - total_demand (positive = surplus).

    ``complete`` is False on any day where at least one component that has
    data *somewhere* in this window is missing on that specific day — e.g.
    entry-volume items publish D+2, so the most recent 1-2 days will
    usually show incomplete supply-side data. Treat balance_mcm as
    unreliable (likely showing a fake "deficit" from missing supply, not a
    real one) on any row where complete is False.
    """
    breakdown = component_breakdown(data_dir)
    if breakdown.empty:
        return pd.DataFrame(columns=["gas_day", "total_supply", "total_demand", "balance_mcm", "status", "complete"])

    expected_components = set(breakdown["component"].unique())

    totals = breakdown.groupby(["gas_day", "side"])["volume_mcm"].sum().unstack(fill_value=0)
    totals = totals.rename(columns={"supply": "total_supply", "demand": "total_demand"})
    for col in ("total_supply", "total_demand"):
        if col not in totals.columns:
            totals[col] = 0.0

    present_per_day = breakdown.groupby("gas_day")["component"].apply(set)
    totals["complete"] = present_per_day.apply(lambda present: present == expected_components)

    totals["balance_mcm"] = totals["total_supply"] - totals["total_demand"]
    totals["status"] = totals["balance_mcm"].apply(
        lambda b: "surplus" if b > SURPLUS_THRESHOLD_MCM
        else ("deficit" if b < DEFICIT_THRESHOLD_MCM else "balanced")
    )
    return totals.reset_index()[["gas_day", "total_supply", "total_demand", "balance_mcm", "status", "complete"]]


def main():
    parser = argparse.ArgumentParser(description="Print the daily supply/demand balance")
    parser.add_argument("--data-dir", default="data/csv_tables")
    args = parser.parse_args()

    pd.set_option("display.width", 120)
    bal = daily_balance(args.data_dir)
    print(bal.to_string(index=False))


if __name__ == "__main__":
    main()
