# NBP Gas ETL

A dedup-safe CSV pipeline that pulls UK gas supply/demand data from Elexon
BMRS and National Gas's public APIs, as groundwork for NBP gas trading
analysis. Every write checks the row's natural key against what's already
on disk before writing — re-running the refresh script never creates
duplicate rows.

This repo is deliberately CSV-based rather than a live database: it's meant
to be a portable snapshot you can clone onto another machine and point at a
real Postgres server later, without needing any credentials or live
connection to reproduce the data collection.

## Data sources

- **Elexon BMRS Insights API** (`data.elexon.co.uk/bmrs/api/v1`) — half-hourly
  electricity generation by fuel type, for all ~20 fuel types (not just
  gas-fired CCGT). No authentication required.
- **National Gas Transmission Data Portal** (`data.nationalgas.com`) — daily
  UK gas flow, demand, storage, weather, and price data. No authentication
  required. `data/csv_tables/dim_national_gas_item.csv` is National Gas's
  **entire official item catalogue** (13,078 items, sourced directly from
  their published "API Data Item List" spreadsheet), not just the items this
  pipeline currently pulls — see the `relevant` and `category` columns to
  see what's actually being fetched vs just registered for future use.

## Repository layout — what each file does

```
src/
  units.py          - unit conversion helpers
  schema.py          - table definitions (single source of truth)
  csv_store.py        - the dedup engine
  create_tables.py    - script: create empty CSVs
  refresh.py           - script: pull latest data, write only new rows
data/csv_tables/
  dim_national_gas_item.csv
  elexon_generation_hh.csv
  national_gas_daily.csv
```

### `src/units.py`

Two small conversion functions plus the constant they're built on:

- `MCM_TO_GWH = 11.16` — the calorific-value-based factor used to convert
  between million cubic metres (mcm) and gigawatt-hours (GWh) for UK gas.
- `kwh_to_mcm(kwh)` — converts kilowatt-hours to mcm. National Gas publishes
  most demand data in kWh; this is what turns it into the mcm/d figures
  used throughout `national_gas_daily.csv`.
- `gwh_to_mcm(gwh)` — converts gigawatt-hours to mcm. Written but not yet
  wired into `refresh.py` — see the Known Issues section below.

**When to use it:** import from here whenever you need to convert a raw
National Gas value into mcm yourself — e.g. if you register a new item
whose native unit isn't one `refresh.py` already handles (currently it only
converts `kWh` and passes through `mcm/d`/`mcm` unchanged; everything else
is left as `NULL` in `value_mcm`).

### `src/schema.py`

The single source of truth for what tables exist, what columns they have,
and — critically — what "duplicate" means for each one. Two modes:

- **`upsert`** (`dim_national_gas_item`, `elexon_generation_hh`): a row
  represents current state. If a row with the same key already exists, it
  gets replaced, not duplicated. Used where the underlying value can be
  *revised* in place (Elexon restates a settlement period's generation
  figure as better data comes in; an item's metadata can change).
- **`append`** (`national_gas_daily`): a row represents one point in a
  revision history. If a row with the same key already exists, the
  incoming row is dropped entirely (it's the exact same fact we already
  have) — never overwritten, never duplicated. Used because National Gas
  genuinely publishes multiple distinct revisions of the same gas-day
  figure over time, and we want to keep all of them, not just the latest.

**When to use it:** this is the file to edit *first* if you want to add a
new table, add/remove a column, or change what counts as a duplicate for an
existing table. `csv_store.py`, `create_tables.py`, and `refresh.py` all
read their behaviour from here — you shouldn't need to touch the dedup
logic itself to make a schema change.

### `src/csv_store.py`

The actual read/write/dedup engine. Three functions:

- `create_table(data_dir, table_name)` — writes an empty CSV with the
  correct header row, if one doesn't already exist there. Idempotent.
- `load_existing_keys(data_dir, table_name)` — reads just the key columns
  (not the whole file) of an existing CSV, as a set of tuples. This is the
  duplicate check itself.
- `write_rows(data_dir, table_name, new_rows)` — the main entry point.
  Stamps `ingested_at`/`updated_at`, filters or merges against
  `load_existing_keys()` depending on the table's mode, and writes to disk.
  Returns a summary dict (`{written, duplicates_skipped}` for append tables;
  `{written, updated}` for upsert tables) so callers can report what
  actually happened.

**When to use it:** you generally don't call this directly — `refresh.py`
and `create_tables.py` both call into it already. You'd only import from it
yourself if you were writing a new script that needed to write to one of
these tables (e.g. a future script that pulls a data source not yet
covered here).

### `src/create_tables.py`

Standalone script. Creates the three CSVs with correct headers if they
don't already exist; does nothing to ones that do.

```
python -m src.create_tables --data-dir data/csv_tables
```

**When to use it:** run once, right after cloning this repo onto a new
machine, before running `refresh.py` for the first time. Safe to re-run at
any point — it will never touch or reset existing data.

### `src/refresh.py`

Standalone script. The actual data puller:

1. Fetches Elexon FUELHH for the last `--days` days (default 3), all fuel
   types, and upserts into `elexon_generation_hh.csv`.
2. Reads `dim_national_gas_item.csv`, filters to rows where `relevant` is
   true **and** `category` is one of `Demand, Supplies, Storage, Linepack,
   Price` (see Known Issues — this filter is narrower than what's actually
   registered as relevant), and for each one fetches the last `--days` days
   of that item's data with `latestFlag=N` (i.e. full revision history, not
   just the latest value) and appends genuinely new rows into
   `national_gas_daily.csv`.
3. Recomputes the `is_latest` flag across the whole `national_gas_daily`
   table after writing (see column docs below).
4. Prints a per-item summary showing each item's registered publication
   `frequency` next to how many new rows were actually found for it this
   run — useful for sanity-checking that low-frequency items (e.g. "Daily")
   aren't producing suspiciously many "new" rows, and that dedup is working
   as expected.

```
python -m src.refresh --data-dir data/csv_tables --days 3
```

**When to use it:** run periodically (daily, or whenever you want the
latest data). Safe to run repeatedly — the dedup logic in `csv_store.py`
guarantees duplicate data is never written, so there's no harm in running
it more often than the data actually changes.

### `requirements.txt`

Two dependencies: `pandas` (CSV read/write, all the data wrangling) and
`requests` (HTTP calls to both APIs). Nothing else.

## Table documentation — column by column

### `dim_national_gas_item.csv`

Reference table: one row per National Gas data item. This is the *entire*
official catalogue, not just what's being actively pulled.

| Column | Type | Meaning |
|---|---|---|
| `pubob_id` | text, **primary key** | National Gas's own "Publication Object" ID (e.g. `PUBOB637`, `PUBOBJ2401`) — the identifier you pass as the `ids` parameter to their CSV download API. |
| `key_name` | text | A short, readable alias. For the 73 items originally hand-picked for this project (`IUK`, `NDM_EA`, `NTS_Demand_Actual`, etc.) this is a meaningful name. For the remaining ~13,000 items pulled in from the full catalogue, it's an auto-generated slug of the item's description (lowercased, non-alphanumeric characters collapsed to underscores, truncated to 80 chars) — readable but not hand-curated. |
| `category` | text | National Gas's own "Data Dictionary Category" — one of 19 values: `Demand`, `Supplies`, `Storage`, `Weather`, `Linepack`, `Price`, `Balancing`, `Interruption`, `Exit Capacity`, `Entry Capacity`, `Exit Capacity (prior to October 2012)`, `Calorific Value`, `Shrinkage`, `Notices`, `SG-WOBBE Data Flow`, `LNG`, `Entry Capacity Trading Analysis`, `New Site set up`, `Operating Margins`. |
| `native_unit` | text | The unit exactly as National Gas publishes it — e.g. `mscm`, `kWh`, `MJ/scm`, `Deg C`, `p/kWh`, `£`, `%`, `Days`, `N/A`. **Not necessarily mcm** — see `value_type` and the `national_gas_daily.value_mcm` column before assuming units. |
| `description` | text | The full "Data Item" description string from National Gas's catalogue, e.g. `"Demand, Actual NDM, LDZ(EA), D+1"`. This is the authoritative definition of what the series actually measures — read it before using an item you don't already recognise. |
| `relevant` | boolean | Whether this item is currently registered as worth collecting. Currently `true` for the entire catalogue (all 13,078 items) — this flag exists so items can be deprioritised later without deleting them from the reference table. Note this is separate from whether `refresh.py` is *actually* fetching an item — see the category filter caveat above. |
| `value_type` | text | A heuristic classification, inferred from `category`: `flow` (a rate — mcm/d, kWh/d — meaningful to sum or average over time), `stock` (a level/inventory — storage stock, linepack — should *not* be summed across time), `price` (monetary/balancing-market values), `capacity` (contractual capacity booking figures, not actual physical flows). This was assigned programmatically by keyword-matching on category name — spot-check it before relying on it for anything precise. |
| `frequency` | text | How often National Gas actually republishes this item, per their own catalogue — e.g. `Daily`, `ASAP`, `Cyclic every 60 minutes`, `Monthly on day 15`, `Monthly on day 24`. |
| `publication_time` | text | The scheduled time-of-day (or cadence descriptor) the item is published — e.g. `10:00`, `15:56`, `ASAP`. Read together with `frequency`. |
| `ingested_at` | timestamptz | When this row was first written by our pipeline (our own lineage metadata — not from National Gas). |
| `updated_at` | timestamptz | When this row was last modified. Since this table is `upsert` mode, this changes every time the item's metadata is refreshed, even if nothing actually changed. |

### `elexon_generation_hh.csv`

Half-hourly electricity generation by fuel type, upserted (revised in
place as Elexon publishes better data for the same period).

| Column | Type | Meaning |
|---|---|---|
| `settlement_date` | date | The GB electricity settlement day (local UK clock, 00:00–24:00) this reading belongs to. |
| `settlement_period` | integer, 1–50 | Half-hourly period within the settlement day. Normally 1–48; 46 or 50 on clock-change days. |
| `fuel_type` | text | One of Elexon's fuel type codes. Gas-relevant: `CCGT` (combined-cycle gas), `OCGT` (open-cycle gas). Others present: `COAL`, `NUCLEAR`, `WIND`, `BIOMASS`, `NPSHYD` (non-pumped hydro), `PS` (pumped storage), `OIL`, `OTHER`, and the **electricity** interconnectors — `INTFR` (France/IFA), `INTIRL` (Ireland/Moyle), `INTNED` (Netherlands/BritNed), `INTEW` (Ireland/East-West), `INTNEM` (Belgium/Nemo), `INTELEC` (France/ElecLink), `INTIFA2` (France/IFA2), `INTNSL` (Norway/North Sea Link), `INTVKL` (Denmark/Viking Link), `INTGRNL` (Ireland/Greenlink). **Note:** these electricity interconnectors are entirely distinct from the gas interconnectors (`IUK`, `BBL`, `Moffat`) that appear in `national_gas_daily.csv` — don't confuse the two. |
| `generation_mw` | numeric | Average generation/flow in megawatts over that settlement period. Can be negative for interconnector fuel types (net export). |
| `start_time` | timestamptz (UTC) | Exact UTC start of the settlement period. |
| `publish_time` | timestamptz (UTC) | When Elexon published/last revised *this specific reading*. A later revision of the same `settlement_date`+`settlement_period`+`fuel_type` gets a newer `publish_time` and overwrites the row. |
| `gas_day` | date | **Derived by this pipeline**, not published by Elexon. Which National Gas "gas day" (06:00–06:00 Europe/London) this settlement period falls into — computed from `start_time`, correctly accounting for BST/GMT. This is what lets you join this table to `national_gas_daily.csv` without the ~6-hour misalignment between an electricity settlement day and a gas day (a period with `settlement_date` X can have `gas_day` X-1 if it falls before 06:00 local). |
| `ingested_at` / `updated_at` | timestamptz | Our own lineage metadata. |

### `national_gas_daily.csv`

Long/tidy fact table: one row per (gas day × item × revision). Append-only
— nothing is ever overwritten, so the full history of how a figure was
revised over time is preserved.

| Column | Type | Meaning |
|---|---|---|
| `gas_day` | date | The National Gas "gas day" this reading applies to (06:00–06:00 Europe/London) — National Gas's own "Applicable For" field. |
| `pubob_id` | text, foreign key | References `dim_national_gas_item.pubob_id` — look up `category`/`description`/`native_unit` there to know what this row actually measures. |
| `value_raw` | numeric | The value exactly as published by National Gas, in `native_unit` (see the dim table) — completely unconverted. |
| `unit_raw` | text | The native unit at the time this row was written, copied alongside `value_raw` so the row is self-describing even if the dim table's unit metadata changes later. |
| `value_mcm` | numeric, **nullable** | `value_raw` converted to mcm, only when the native unit is one this pipeline currently knows how to convert (`kWh`, or already-native `mcm`/`mcm/d`). `NULL` for prices, percentages, day-counts, temperatures, and — see Known Issues — the gas interconnector flow items pending unit verification. |
| `applicable_at` | timestamptz | National Gas's own "Applicable At" field — when this particular revision was generated/valid as of. Part of the key that distinguishes one revision from the next for the same gas day + item. |
| `generated_time` | timestamptz | National Gas's own "Generated Time" field — when this row was actually computed on their side. **Also part of the dedup key** — we found real cases where two distinct revisions share the same `applicable_at` but differ in `generated_time`, so both fields are needed together to uniquely identify a revision. |
| `quality_indicator` | text, nullable | National Gas's own data-quality flag. Observed values so far: `NULL` (unflagged/initial), `L`, `A` — exact meaning of the letter codes not yet confirmed from National Gas's documentation; treat as informational, not authoritative, until verified. |
| `is_latest` | boolean | `true` for exactly one row per `(gas_day, pubob_id)` — the row with the maximum `(applicable_at, generated_time)`, i.e. the most current known value for that gas day. Recomputed by `refresh.py` after every write. **Filter on `is_latest = true` for a "current best estimate" view; drop the filter to see the full revision history.** |
| `gas_day_start_utc` / `gas_day_end_utc` | timestamptz | **Derived**, not published by National Gas. The exact UTC boundary of this `gas_day` (06:00 Europe/London that calendar day to 06:00 the next, correctly adjusted for BST/GMT). Lets you join finer-grained data (e.g. Elexon settlement periods) against the correct gas-day window without recomputing the boundary yourself. |
| `ingested_at` / `updated_at` | timestamptz | Our own lineage metadata. |

## Known issues / open items

1. **Gas interconnector unit ambiguity.** The National Gas items for `IUK`,
   `BBL`, and `Moffat` (the physical gas interconnector flows, category
   `Demand` in the catalogue, key names `IUK`/`BBL`/`Moffat` in
   `dim_national_gas_item.csv`) are documented as GWh/d, but the raw
   magnitudes observed don't unambiguously confirm that versus mcm/d.
   `value_mcm` is left `NULL` for these pending verification against
   National Gas's own Data Item Explorer — don't assume either unit without
   checking first. `src/units.py` already has `gwh_to_mcm()` ready to wire
   in once this is confirmed.
2. **`refresh.py`'s active category filter is narrower than what's
   registered as `relevant`.** It currently only pulls
   `Demand, Supplies, Storage, Linepack, Price`. `Weather` (Composite
   Weather Variable — the main demand driver, arguably the single most
   valuable addition available) and `Calorific Value` (needed to replace
   the fixed unit-conversion assumption with real per-site figures) are
   registered in the dim table with `relevant = true` but are **not yet
   being pulled** by `refresh.py`. This needs a one-line fix to the
   category list before those categories will actually show up in
   `national_gas_daily.csv`.
3. **Exit/Entry Capacity (10,560 of the 13,078 registered items) has no
   data pulled at all yet.** These are contractual capacity bookings, not
   physical flows, and are a different kind of dataset (per-shipper
   booking positions) that may need its own fetch/parsing logic rather
   than reusing the flow-item code path as-is.
4. **No historical backfill.** Everything in `data/csv_tables/` right now
   is a small (2–3 day) investigative window, not full history. Deliberate
   for now — see project notes for backfill planning.

## Status

Pipeline shape, schema, and dedup logic are validated (ran `refresh.py`
twice back-to-back with zero duplicate rows produced on the second run).
Not yet done: wiring in Weather/Calorific Value, resolving the
interconnector unit question, Exit/Entry Capacity data, and any historical
backfill.
