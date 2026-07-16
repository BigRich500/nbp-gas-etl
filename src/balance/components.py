"""Maps National Gas data items to supply/demand balance components.

Two aggregation styles, chosen per component based on what National Gas
actually publishes:

  - Demand side: National Gas publishes its own official pre-aggregated
    "NTS Volume Offtaken" totals for most demand categories. Used directly
    where they exist — simpler, and matches National Gas's own definitions
    rather than us re-deriving a possibly-wrong split.
  - Supply side: no equivalent official aggregate exists (checked — only
    demand-side "NTS Volume Offtaken, X Total" items are published), so
    supply components are built by summing our own per-site entry/storage
    data, which we already collect.

Residential vs industrial note (verified against 2 days of real data
before adopting this split — see project planning notes): `LDZ Offtake
Total` and `Industrial Offtake Total` are NOT a residential/industrial
split of the same population. `LDZ Offtake Total` (~39 mcm/d observed) is
close to our own NDM+DM per-site sum combined (~40 mcm/d) — i.e. it's ALL
LDZ-embedded demand, residential and smaller industrial/commercial
together. `Industrial Offtake Total` (~1.3-1.5 mcm/d observed) is a much
smaller, entirely separate population — large industrial users connected
directly to the NTS, not the LDZ network. They don't overlap, so using
both together does not double-count.

IUK/BBL/Moffat: National Gas's own catalogue confirms these are published
in mscm (~mcm), not GWh/d — see dim_national_gas_item.native_unit. They
were originally assumed to report signed bidirectional net flow (positive
= import, negative = export), but real data disproves that: in every
observation so far PUBOB2038 (IUK) and PUBOBJ1307 (BBL) are positive-only,
while National Gas's separate official "NTS Volume Offtaken, Interconnector
Exports Total" (PUBOBJ1020) shows real, substantial export activity in the
same window. So IUK/BBL/PUBOB2038/PUBOBJ1307 are treated as import-only
(supply-side), and PUBOBJ1020 is used as the single combined export figure
for all interconnectors (IUK+BBL+Moffat together — National Gas doesn't
appear to publish a per-interconnector export breakdown we've found yet).
This was verified against the official headline figure: with this mapping,
our own total_demand landed within ~0.3 mcm of NTS_Demand_Actual
(PUBOB637) for the same day — a near-exact match, whereas the naive sign-
split assumption undershot it by ~51 mcm/d (all of the missing export
volume).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Component:
    name: str
    side: str  # "supply" or "demand"
    pubob_ids: tuple[str, ...]
    # "sum": add up all pubob_ids' value_mcm.
    # "sign_positive" / "sign_negative": single pubob_id, only counted when
    # its value_mcm has that sign (see IUK/BBL note above).
    mode: str = "sum"


UKCS_ENTRY_IDS = (
    "PUBOB407",   # Easington - Dimlington
    "PUBOB401",   # Easington - West Sole
    "PUBOB377",   # Bacton - Perenco
    "PUBOB383",   # Bacton - Shell
    "PUBOB380",   # Bacton - Tullow
    "PUBOB1826",  # Barrow
    "PUBOB428",   # St Fergus - Mobil
    "PUBOB431",   # St Fergus - Shell
    "PUBOB434",   # St Fergus - NSMP
    "PUBOB437",   # Teesside - CATS
    "PUBOB440",   # Teesside - PX
)

LNG_ENTRY_IDS = ("PUBOB3480", "PUBOB3564", "PUBOB371", "PUBOB3473")  # South Hook, Dragon, Grain NTS1/2 — fallback if the aggregate item is unavailable

STORAGE_OUTFLOW_IDS = (
    "PUBOBJ2413", "PUBOBJ2414", "PUBOBJ2416", "PUBOBJ2417", "PUBOBJ2418",
    "PUBOBJ2419", "PUBOBJ2420", "PUBOBJ2421", "PUBOBJ2422",
)

COMPONENTS: list[Component] = [
    # --- Supply: built from per-site sums (no official aggregate exists) ---
    Component("UKCS production", "supply", UKCS_ENTRY_IDS, mode="sum"),
    Component("Norway (Langeled)", "supply", ("PUBOB452",), mode="sum"),
    Component("LNG", "supply", ("PUBOBJ337",), mode="sum"),  # aggregate; falls back to LNG_ENTRY_IDS if empty — see balance.py
    Component("Storage withdrawal", "supply", STORAGE_OUTFLOW_IDS, mode="sum"),
    Component("IUK import", "supply", ("PUBOB2038",), mode="sign_positive"),
    Component("BBL import", "supply", ("PUBOBJ1307",), mode="sign_positive"),

    # --- Demand: National Gas's own official totals, where they exist ---
    Component("Residential/commercial", "demand", ("PUBOBJ1015",), mode="sum"),  # LDZ Offtake Total
    Component("Industrial (NTS-connected)", "demand", ("PUBOBJ1018",), mode="sum"),  # Industrial Offtake Total — distinct population, see module docstring
    Component("Power generation", "demand", ("PUBOBJ1017",), mode="sum"),  # Powerstations Total — direct measurement
    Component("Storage injection", "demand", ("PUBOBJ1016",), mode="sum"),  # Storage Injection Total
    Component("Interconnector exports", "demand", ("PUBOBJ1020",), mode="sum"),  # NTS Volume Offtaken, Interconnector Exports Total (IUK+BBL+Moffat combined) — see module docstring
]
