# NBP Gas ETL

Pulls UK gas supply/demand data from Elexon BMRS and National Gas's public
APIs into dedup-safe CSV tables, as a step toward NBP gas trading analysis.

## Data sources

- **Elexon BMRS Insights API** (`data.elexon.co.uk`) — half-hourly generation
  by fuel type (all ~20 fuel types, not just CCGT), no auth required.
- **National Gas Transmission Data Portal** (`data.nationalgas.com`) — daily
  gas flow/demand/storage/weather/price data. `dim_national_gas_item.csv` is
  the full official item catalogue (13,078 items, from National Gas's own
  "API Data Item List" spreadsheet) — the `relevant` column flags which ones
  this ETL actually pulls data for.

## Tables

| CSV | Grain | Mode |
|---|---|---|
| `dim_national_gas_item.csv` | one row per National Gas data item (reference) | upsert |
| `elexon_generation_hh.csv` | settlement_date + settlement_period + fuel_type | upsert (revised as later data comes in) |
| `national_gas_daily.csv` | gas_day + pubob_id + applicable_at + generated_time | append (full revision history kept) |

Schema and dedup keys are defined once in `src/schema.py`.

## Usage

```
pip install -r requirements.txt
python -m src.create_tables            # create empty CSVs (idempotent)
python -m src.refresh --days 3         # pull latest data, skip duplicates
```

Every write goes through `src/csv_store.py`, which checks each row's natural
key against what's already on disk before writing anything — running
`refresh.py` repeatedly does not create duplicate rows.

## Known open issue

`elexon_generation_hh` / `national_gas_daily`: the National Gas "NTS Physical
Flows" items for the interconnectors (IUK, BBL, Moffat) are documented as
GWh/d but the raw magnitudes don't unambiguously confirm that vs mcm/d —
`value_mcm` is left `NULL` for these pending verification against National
Gas's own Data Item Explorer. See `dim_national_gas_item.csv` (category
`interconnector_flow`... actually not present in this catalogue export;
check `native_unit` per item) before trusting those specific series.

## Status

This is a small investigative pull (last 2-3 days only), not a historical
backfill — the goal so far has been to validate the pipeline shape, schema,
and dedup logic before committing to pulling full history for ~1,500+
registered items. See commit history / project notes for what's been
scoped but not yet pulled (Exit/Entry Capacity data, full weather/CV
history, etc.).
