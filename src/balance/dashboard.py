"""Generate a self-contained HTML dashboard: supply vs demand for one gas
day, bucketed by component, plus a trend strip for context.

Reads the already-persisted daily_balance.csv / component_breakdown.csv
(run src/refresh.py then src/balance/persist.py first to make sure these
are current). All data is embedded directly in the HTML file — no server,
no fetch/CORS issues, just open it in a browser. Chart.js is pulled from a
CDN (needs internet the first time the page loads; if that's ever a
problem on an offline machine, say so and this can switch to inline SVG).

Defaults to the most recent gas_day in daily_balance.csv ("today", or the
closest thing to it once National Gas has published anything). If that
day is incomplete (some components haven't published yet — expected for
D+1/D+2 items), the page says so clearly rather than presenting partial
numbers as final.

Usage:
    python -m src.balance.dashboard [--data-dir data/csv_tables] [--gas-day 2026-07-05] [--out dashboard.html]
"""
from __future__ import annotations

import argparse
import json
import webbrowser

import pandas as pd

from src.csv_store import table_path

# Consistent color per component, stable across re-generation.
COMPONENT_COLORS = {
    "UKCS production": "#2563eb",
    "Norway (Langeled)": "#0ea5e9",
    "LNG": "#06b6d4",
    "Storage withdrawal": "#14b8a6",
    "IUK import": "#22c55e",
    "BBL import": "#84cc16",
    "Residential/commercial": "#f97316",
    "Industrial (NTS-connected)": "#f59e0b",
    "Power generation": "#ef4444",
    "Storage injection": "#a855f7",
    "Interconnector exports": "#ec4899",
}


def build_html(daily_balance: pd.DataFrame, component_breakdown: pd.DataFrame, gas_day: str, days_behind: int = 0) -> str:
    day_row = daily_balance[daily_balance["gas_day"] == gas_day].iloc[0]
    day_components = component_breakdown[component_breakdown["gas_day"] == gas_day]

    supply = day_components[day_components["side"] == "supply"].sort_values("volume_mcm", ascending=False)
    demand = day_components[day_components["side"] == "demand"].sort_values("volume_mcm", ascending=False)

    bucket_datasets = []
    for _, row in pd.concat([supply, demand]).iterrows():
        bucket_datasets.append({
            "label": row["component"],
            "data": [row["volume_mcm"] if row["side"] == "supply" else 0,
                     row["volume_mcm"] if row["side"] == "demand" else 0],
            "backgroundColor": COMPONENT_COLORS.get(row["component"], "#94a3b8"),
        })

    trend = daily_balance.sort_values("gas_day")
    trend_labels = trend["gas_day"].tolist()
    trend_balance = trend["balance_mcm"].round(2).tolist()
    trend_complete = trend["complete"].tolist()

    complete = bool(day_row["complete"])
    status = day_row["status"]
    status_color = {"surplus": "#16a34a", "deficit": "#dc2626", "balanced": "#64748b"}[status]

    banner = ""
    if not complete:
        banner = (
            '<div class="banner">This gas day (' + gas_day + ') is not fully published yet — '
            "some D+1/D+2 components are still missing. Figures below are best-available, not final.</div>"
        )
    elif days_behind > 0:
        plural = "s" if days_behind != 1 else ""
        banner = (
            f'<div class="banner">Showing the most recent fully-published gas day — {days_behind} day{plural} '
            "behind today. More recent days exist but some components (entry volumes, D+1/D+2 offtake) "
            "haven't published yet; see the trend strip below for their partial figures.</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NBP Gas Supply vs Demand — {gas_day}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 32px; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  .sub {{ color: #94a3b8; margin-bottom: 24px; }}
  .banner {{ background: #7c2d12; color: #fed7aa; padding: 10px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 0.9rem; }}
  .cards {{ display: flex; gap: 16px; margin-bottom: 28px; }}
  .card {{ background: #1e293b; border-radius: 10px; padding: 18px 24px; flex: 1; }}
  .card .label {{ color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .card .value {{ font-size: 1.8rem; font-weight: 600; margin-top: 4px; }}
  .grid {{ display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; }}
  .panel {{ background: #1e293b; border-radius: 10px; padding: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #334155; }}
  th {{ color: #94a3b8; font-weight: 500; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }}
  canvas {{ max-height: 420px; }}
</style>
</head>
<body>
  <h1>NBP Gas Supply vs Demand</h1>
  <div class="sub">Gas day {gas_day} (06:00–06:00 Europe/London) &middot; generated from nbp-gas-etl</div>
  {banner}
  <div class="cards">
    <div class="card"><div class="label">Total supply</div><div class="value">{day_row['total_supply_mcm']:.1f} mcm</div></div>
    <div class="card"><div class="label">Total demand</div><div class="value">{day_row['total_demand_mcm']:.1f} mcm</div></div>
    <div class="card"><div class="label">Balance</div><div class="value" style="color:{status_color}">{day_row['balance_mcm']:+.1f} mcm &middot; {status}</div></div>
  </div>
  <div class="grid">
    <div class="panel">
      <canvas id="bucketChart"></canvas>
    </div>
    <div class="panel">
      <table>
        <thead><tr><th>Component</th><th class="num">Side</th><th class="num">mcm</th></tr></thead>
        <tbody>
          {"".join(f'<tr><td><span class="swatch" style="background:{COMPONENT_COLORS.get(r["component"],"#94a3b8")}"></span>{r["component"]}</td><td class="num">{r["side"]}</td><td class="num">{r["volume_mcm"]:.2f}</td></tr>' for _, r in pd.concat([supply, demand]).iterrows())}
        </tbody>
      </table>
    </div>
  </div>
  <div class="panel" style="margin-top:24px;">
    <canvas id="trendChart" style="max-height:220px;"></canvas>
  </div>

<script>
new Chart(document.getElementById('bucketChart'), {{
  type: 'bar',
  data: {{
    labels: ['Supply', 'Demand'],
    datasets: {json.dumps(bucket_datasets)}
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#e2e8f0', boxWidth: 12 }} }}, title: {{ display: true, text: 'Bucketed by component', color: '#e2e8f0' }} }},
    scales: {{
      x: {{ stacked: true, ticks: {{ color: '#e2e8f0' }}, grid: {{ color: '#334155' }} }},
      y: {{ stacked: true, ticks: {{ color: '#e2e8f0' }}, grid: {{ color: '#334155' }}, title: {{ display: true, text: 'mcm', color: '#94a3b8' }} }}
    }}
  }}
}});

new Chart(document.getElementById('trendChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(trend_labels)},
    datasets: [{{
      label: 'Balance (mcm)',
      data: {json.dumps(trend_balance)},
      backgroundColor: {json.dumps(trend_complete)}.map(c => c ? '#16a34a' : '#475569'),
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }}, title: {{ display: true, text: 'Balance trend (dim = incomplete day)', color: '#e2e8f0' }} }},
    scales: {{
      x: {{ ticks: {{ color: '#e2e8f0' }}, grid: {{ display: false }} }},
      y: {{ ticks: {{ color: '#e2e8f0' }}, grid: {{ color: '#334155' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate the supply/demand HTML dashboard")
    parser.add_argument("--data-dir", default="data/csv_tables")
    parser.add_argument("--gas-day", default=None, help="defaults to the most recent gas_day available")
    parser.add_argument("--out", default="dashboard.html")
    parser.add_argument("--no-open", action="store_true", help="don't auto-open the file in a browser")
    args = parser.parse_args()

    bal = pd.read_csv(table_path(args.data_dir, "daily_balance"))
    breakdown = pd.read_csv(table_path(args.data_dir, "component_breakdown"))

    if args.gas_day:
        gas_day = args.gas_day
    else:
        # Prefer the most recent COMPLETE day — the latest gas_day overall
        # is often mid-publication (D+1/D+2 items missing), which can leave
        # a whole side of the balance at 0 and make for a misleading chart.
        complete_days = bal[bal["complete"] == True]  # noqa: E712
        gas_day = complete_days["gas_day"].max() if not complete_days.empty else bal["gas_day"].max()

    if gas_day not in bal["gas_day"].values:
        raise SystemExit(f"No daily_balance row for gas_day={gas_day}. Available: {sorted(bal['gas_day'].unique())}")

    latest_gas_day = bal["gas_day"].max()
    days_behind = (pd.Timestamp(latest_gas_day) - pd.Timestamp(gas_day)).days

    html = build_html(bal, breakdown, gas_day, days_behind=days_behind)
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Wrote {args.out} for gas_day={gas_day} ({days_behind} day(s) behind the most recent pulled gas_day {latest_gas_day})")

    if not args.no_open:
        webbrowser.open(f"file://{__import__('os').path.abspath(args.out)}")


if __name__ == "__main__":
    main()
