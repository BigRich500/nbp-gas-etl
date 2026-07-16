"""Generate a self-contained, interactive HTML dashboard: supply vs demand
bucketed by component, for any gas day in daily_balance.csv, with a day
selector and per-bucket drill-down into the underlying sites/regions.

Reads the already-persisted daily_balance.csv / component_breakdown.csv
(run src/refresh.py then src/balance/persist.py first to make sure these
are current), plus national_gas_daily.csv directly for the drill-down
detail (src/balance/drilldown.py) and the UKCS/Norway split (the balance
calculation itself still uses the official Subterminal aggregate as the
authoritative total — this only re-splits it for display).

All data for every gas_day is embedded directly in the HTML as JSON — no
server, no fetch/CORS issues, just open it in a browser, and the day
selector switches entirely client-side. Chart.js is pulled from a CDN
(needs internet on first load).

Usage:
    python -m src.balance.dashboard [--data-dir data/csv_tables] [--out dashboard.html]
"""
from __future__ import annotations

import argparse
import json
import webbrowser

import pandas as pd

from src.csv_store import table_path
from src.balance.components import UKCS_ENTRY_IDS
from src.balance.drilldown import compute_drilldown

SUBTERMINAL_NAME = "Subterminal entry (UKCS + Norway)"

# Consistent color per component, stable across re-generation.
COMPONENT_COLORS = {
    "UKCS production": "#2563eb",
    "Norway (Langeled)": "#0ea5e9",
    "Other entry sites": "#38bdf8",
    "LNG": "#06b6d4",
    "Storage withdrawal": "#14b8a6",
    "Residential/commercial": "#f97316",
    "Industrial (NTS-connected)": "#f59e0b",
    "Power generation": "#ef4444",
    "Storage injection": "#a855f7",
    "Interconnector exports": "#ec4899",
    "Shrinkage": "#78716c",
}


def _split_subterminal(breakdown_day: pd.DataFrame, latest_day: pd.DataFrame) -> pd.DataFrame:
    """Replace the single 'Subterminal entry (UKCS + Norway)' row (if
    present) with three: UKCS production, Norway (Langeled), Other entry
    sites (a residual = official total - UKCS - Norway, covering minor
    sites not individually tracked). Keeps the same grand total."""
    sub_row = breakdown_day[breakdown_day["component"] == SUBTERMINAL_NAME]
    if sub_row.empty:
        return breakdown_day

    official_total = sub_row["volume_mcm"].iloc[0]
    ukcs_sum = latest_day[latest_day["pubob_id"].isin(UKCS_ENTRY_IDS)]["value_mcm"].sum()
    norway = latest_day[latest_day["pubob_id"] == "PUBOB452"]["value_mcm"].sum()
    other = max(official_total - ukcs_sum - norway, 0.0)

    replacement = pd.DataFrame([
        {"component": "UKCS production", "side": "supply", "volume_mcm": ukcs_sum},
        {"component": "Norway (Langeled)", "side": "supply", "volume_mcm": norway},
        {"component": "Other entry sites", "side": "supply", "volume_mcm": other},
    ])
    return pd.concat([breakdown_day[breakdown_day["component"] != SUBTERMINAL_NAME], replacement], ignore_index=True)


def _load_data(data_dir: str):
    bal = pd.read_csv(table_path(data_dir, "daily_balance"))
    breakdown = pd.read_csv(table_path(data_dir, "component_breakdown"))
    dim = pd.read_csv(table_path(data_dir, "dim_national_gas_item"))
    daily = pd.read_csv(table_path(data_dir, "national_gas_daily"), parse_dates=["gas_day"])
    daily["gas_day"] = daily["gas_day"].dt.date.astype(str)
    latest = daily[daily["is_latest"] == True]  # noqa: E712
    return bal, breakdown, dim, latest


def build_payload(data_dir: str) -> dict:
    bal, breakdown, dim, latest = _load_data(data_dir)
    drilldown_by_day = compute_drilldown(latest, dim)

    days = sorted(bal["gas_day"].astype(str).unique())
    latest_gas_day = days[-1]
    complete_days = bal[bal["complete"] == True]["gas_day"].astype(str)  # noqa: E712
    default_day = complete_days.max() if not complete_days.empty else latest_gas_day

    balance_by_day = {}
    breakdown_by_day = {}
    for day in days:
        row = bal[bal["gas_day"].astype(str) == day].iloc[0]
        days_behind = (pd.Timestamp(latest_gas_day) - pd.Timestamp(day)).days
        balance_by_day[day] = {
            "total_supply_mcm": round(float(row["total_supply_mcm"]), 3),
            "total_demand_mcm": round(float(row["total_demand_mcm"]), 3),
            "balance_mcm": round(float(row["balance_mcm"]), 3),
            "status": row["status"],
            "complete": bool(row["complete"]),
            "days_behind": int(days_behind),
        }

        day_breakdown = breakdown[breakdown["gas_day"].astype(str) == day]
        day_latest = latest[latest["gas_day"] == day]
        day_breakdown = _split_subterminal(day_breakdown, day_latest)
        breakdown_by_day[day] = [
            {"component": r["component"], "side": r["side"], "volume_mcm": round(float(r["volume_mcm"]), 3)}
            for _, r in day_breakdown.sort_values("volume_mcm", ascending=False).iterrows()
        ]

    trend = bal.sort_values("gas_day")
    trend_payload = {
        "labels": trend["gas_day"].astype(str).tolist(),
        "balance": trend["balance_mcm"].round(2).tolist(),
        "complete": trend["complete"].tolist(),
    }

    return {
        "days": days,
        "defaultDay": default_day,
        "latestGasDay": latest_gas_day,
        "balance": balance_by_day,
        "breakdown": breakdown_by_day,
        "drilldown": drilldown_by_day,
        "trend": trend_payload,
        "colors": COMPONENT_COLORS,
    }


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NBP Gas Supply vs Demand</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 32px; }
  h1 { font-size: 1.4rem; margin-bottom: 4px; display: flex; align-items: center; gap: 16px; }
  .sub { color: #94a3b8; margin-bottom: 24px; }
  select { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; padding: 6px 10px; font-size: 0.95rem; }
  .banner { background: #7c2d12; color: #fed7aa; padding: 10px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 0.9rem; }
  .cards { display: flex; gap: 16px; margin-bottom: 28px; }
  .card { background: #1e293b; border-radius: 10px; padding: 18px 24px; flex: 1; }
  .card .label { color: #94a3b8; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 1.8rem; font-weight: 600; margin-top: 4px; }
  .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; }
  .panel { background: #1e293b; border-radius: 10px; padding: 20px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #334155; }
  th { color: #94a3b8; font-weight: 500; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
  canvas { max-height: 420px; }
  details { border-bottom: 1px solid #334155; }
  details:last-child { border-bottom: none; }
  summary { cursor: pointer; padding: 6px 8px; display: flex; justify-content: space-between; align-items: center; list-style: none; }
  summary::-webkit-details-marker { display: none; }
  summary .arrow { color: #64748b; font-size: 0.75rem; margin-left: 6px; }
  .detail-list { padding: 2px 8px 8px 26px; max-height: 220px; overflow-y: auto; }
  .detail-list div { display: flex; justify-content: space-between; padding: 3px 0; font-size: 0.85rem; color: #cbd5e1; }
  .no-detail { color: #64748b; font-size: 0.8rem; padding: 4px 8px 8px 26px; }
</style>
</head>
<body>
  <h1>NBP Gas Supply vs Demand <select id="daySelect"></select></h1>
  <div class="sub" id="subHeader"></div>
  <div id="banner"></div>
  <div class="cards">
    <div class="card"><div class="label">Total supply</div><div class="value" id="cardSupply"></div></div>
    <div class="card"><div class="label">Total demand</div><div class="value" id="cardDemand"></div></div>
    <div class="card"><div class="label">Balance</div><div class="value" id="cardBalance"></div></div>
  </div>
  <div class="grid">
    <div class="panel">
      <canvas id="bucketChart"></canvas>
    </div>
    <div class="panel">
      <div id="detailTable"></div>
    </div>
  </div>
  <div class="panel" style="margin-top:24px;">
    <canvas id="trendChart" style="max-height:220px;"></canvas>
  </div>

<script>
const DATA = __DATA_JSON__;
let bucketChart = null;

function fmt(n) { return n.toLocaleString(undefined, {maximumFractionDigits: 1}); }

function renderDay(day) {
  const bal = DATA.balance[day];
  const breakdown = DATA.breakdown[day];
  const drilldown = DATA.drilldown[day] || {};

  document.getElementById('subHeader').textContent =
    `Gas day ${day} (06:00–06:00 Europe/London) · generated from nbp-gas-etl`;

  let banner = '';
  if (!bal.complete) {
    banner = `<div class="banner">This gas day (${day}) is not fully published yet — some D+1/D+2 components are still missing. Figures below are best-available, not final.</div>`;
  } else if (bal.days_behind > 0) {
    const plural = bal.days_behind !== 1 ? 's' : '';
    banner = `<div class="banner">${bal.days_behind} day${plural} behind the most recent pulled gas_day (${DATA.latestGasDay}) — more recent days exist but some components haven't published yet.</div>`;
  }
  document.getElementById('banner').innerHTML = banner;

  document.getElementById('cardSupply').textContent = fmt(bal.total_supply_mcm) + ' mcm';
  document.getElementById('cardDemand').textContent = fmt(bal.total_demand_mcm) + ' mcm';
  const statusColor = {surplus: '#16a34a', deficit: '#dc2626', balanced: '#64748b'}[bal.status];
  const balEl = document.getElementById('cardBalance');
  balEl.textContent = (bal.balance_mcm >= 0 ? '+' : '') + fmt(bal.balance_mcm) + ' mcm · ' + bal.status;
  balEl.style.color = statusColor;

  const supply = breakdown.filter(r => r.side === 'supply');
  const demand = breakdown.filter(r => r.side === 'demand');
  const ordered = supply.concat(demand);

  const datasets = ordered.map(r => ({
    label: r.component,
    data: [r.side === 'supply' ? r.volume_mcm : 0, r.side === 'demand' ? r.volume_mcm : 0],
    backgroundColor: DATA.colors[r.component] || '#94a3b8',
  }));

  if (bucketChart) bucketChart.destroy();
  bucketChart = new Chart(document.getElementById('bucketChart'), {
    type: 'bar',
    data: { labels: ['Supply', 'Demand'], datasets },
    options: {
      responsive: true,
      plugins: { legend: { position: 'bottom', labels: { color: '#e2e8f0', boxWidth: 12 } }, title: { display: true, text: 'Bucketed by component', color: '#e2e8f0' } },
      scales: {
        x: { stacked: true, ticks: { color: '#e2e8f0' }, grid: { color: '#334155' } },
        y: { stacked: true, ticks: { color: '#e2e8f0' }, grid: { color: '#334155' }, title: { display: true, text: 'mcm', color: '#94a3b8' } }
      }
    }
  });

  const rowsHtml = ordered.map(r => {
    const items = drilldown[r.component];
    const swatch = `<span class="swatch" style="background:${DATA.colors[r.component] || '#94a3b8'}"></span>`;
    if (!items || !items.length) {
      return `<details><summary>${swatch}<span>${r.component} <span style="color:#94a3b8">(${r.side})</span></span><span>${fmt(r.volume_mcm)} mcm</span></summary><div class="no-detail">No per-site breakdown available for this bucket.</div></details>`;
    }
    const detailRows = items.map(it => `<div><span>${it.label}</span><span>${fmt(it.volume_mcm)} mcm</span></div>`).join('');
    return `<details><summary>${swatch}<span>${r.component} <span style="color:#94a3b8">(${r.side})</span> <span class="arrow">▾</span></span><span>${fmt(r.volume_mcm)} mcm</span></summary><div class="detail-list">${detailRows}</div></details>`;
  }).join('');
  document.getElementById('detailTable').innerHTML = rowsHtml;
}

const daySelect = document.getElementById('daySelect');
DATA.days.forEach(d => {
  const opt = document.createElement('option');
  opt.value = d;
  opt.textContent = d + (DATA.balance[d].complete ? '' : ' (partial)');
  daySelect.appendChild(opt);
});
daySelect.value = DATA.defaultDay;
daySelect.addEventListener('change', () => renderDay(daySelect.value));
renderDay(DATA.defaultDay);

new Chart(document.getElementById('trendChart'), {
  type: 'bar',
  data: {
    labels: DATA.trend.labels,
    datasets: [{
      label: 'Balance (mcm)',
      data: DATA.trend.balance,
      backgroundColor: DATA.trend.complete.map(c => c ? '#16a34a' : '#475569'),
    }]
  },
  options: {
    responsive: true,
    onClick: (evt, elements) => { if (elements.length) daySelect.value = DATA.trend.labels[elements[0].index], renderDay(daySelect.value); },
    plugins: { legend: { display: false }, title: { display: true, text: 'Balance trend (click a bar to view that day; dim = incomplete)', color: '#e2e8f0' } },
    scales: {
      x: { ticks: { color: '#e2e8f0' }, grid: { display: false } },
      y: { ticks: { color: '#e2e8f0' }, grid: { color: '#334155' } }
    }
  }
});
</script>
</body>
</html>
"""


def build_html(data_dir: str) -> str:
    payload = build_payload(data_dir)
    return PAGE_TEMPLATE.replace("__DATA_JSON__", json.dumps(payload))


def main():
    parser = argparse.ArgumentParser(description="Generate the interactive supply/demand HTML dashboard")
    parser.add_argument("--data-dir", default="data/csv_tables")
    parser.add_argument("--out", default="dashboard.html")
    parser.add_argument("--no-open", action="store_true", help="don't auto-open the file in a browser")
    args = parser.parse_args()

    html = build_html(args.data_dir)
    with open(args.out, "w") as f:
        f.write(html)
    print(f"Wrote {args.out}")

    if not args.no_open:
        webbrowser.open(f"file://{__import__('os').path.abspath(args.out)}")


if __name__ == "__main__":
    main()
