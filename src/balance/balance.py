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
from src.balance.components import COMPONENTS, LNG_ENTRY_IDS

SURPLUS_THRESHOLD_MCM = 2.0
DEFICIT_THRESHOLD_MCM = -2.0


def _load_latest(data_dir: str) -> pd.DataFrame:
    path = table_path(data_dir, "national_gas_daily")
    df = pd.read_csv(path, parse_dates=["gas_day"])
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

    # LNG aggregate fallback: if the PUBOBJ337 aggregate has no data yet,
    # sum the individual terminal entry volumes instead.
    components = list(COMPONENTS)
    lng_idx = next(i for i, c in enumerate(components) if c.name == "LNG")
    if latest[latest["pubob_id"] == "PUBOBJ337"].empty:
        components[lng_idx] = type(components[lng_idx])(
            "LNG", "supply", LNG_ENTRY_IDS, mode="sum"
        )

    frames = []
    for component in components:
        series = _component_series(latest, component)
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
