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

Supply-side official aggregates — CORRECTED (see below, this reverses the
"no equivalent official aggregate exists" claim above for entry volumes).
Searching the "Supplies" category (only pulled in full once we added it
to refresh.py's category filter this session) turned up
"System Entry Volume, Aggregate Physical Volume, Subterminal/Interconnector/
LNG Importation/Storage Withdrawal" (PUBOBJ627/628/629/630, daily D+1) —
official system-wide entry totals, mirroring the demand side's "NTS Volume
Offtaken, X Total" pattern. Checked against real data:
  - PUBOBJ628 (Interconnector) is exactly 0.0 on all 14 days checked —
    an independent, official confirmation that interconnector import is
    genuinely ~0 right now, not just an absence of a good series (see the
    IUK/BBL note below).
  - PUBOBJ629 (LNG) matches our existing LNG figure almost exactly
    (e.g. 4.99801 official vs 4.998 ours on 2026-07-05) — validates LNG.
  - PUBOBJ627 (Subterminal) consistently runs ~5-8 mcm/d (~4-6%) above our
    UKCS_ENTRY_IDS + Langeled per-site sum — consistent with a handful of
    smaller entry points we don't individually track (Easington-Amethyst,
    Murrow, Avonmouth, Bacton Seal, Burton Point, Theddlethorpe,
    Saltfleetby, Glentham Biomethane all showed up as separate "System
    Entry Volume" items in the catalogue that aren't in UKCS_ENTRY_IDS).
    Switched "Subterminal entry" to use PUBOBJ627 directly instead of
    chasing down every missing site ID, with the old per-site sum kept as
    a fallback (same pattern as LNG) if the aggregate is ever unavailable.
  - PUBOBJ630 (Storage Withdrawal) does NOT cleanly reconcile — it's
    exactly 0.0 on several days where our per-site OUT_* sum shows real
    withdrawal (e.g. 2026-07-05: official 0.0 vs our 3.52 mcm), and shows
    real values (14-26 mcm) on other days where our per-site sum has no
    data at all. Left as our per-site sum for now — this is a genuine open
    question, not resolved, unlike the other three.

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

IUK/BBL/Moffat — CORRECTED (previous versions of this module had this
backwards, see below): PUBOB2038 (IUK) and PUBOBJ1307 (BBL) are each
interconnector's EXPORT contribution, not import. Checked across 14 days
of real data: PUBOB2038 + PUBOBJ1307 + PUBOB2039 (Moffat) sum to
PUBOBJ1020 ("NTS Volume Offtaken, Interconnector Exports Total") to
within floating-point rounding (~0.00005 mcm) on every single day. So
PUBOBJ1020 already fully captures all three — summing PUBOB2038/
PUBOBJ1307 again as "IUK import"/"BBL import" on the supply side (an
earlier version of this file did exactly that) double-counts the same
gas, inflating supply by ~50-65 mcm/d. Found via an independent physical
sanity check: computed balance showed a ~46 mcm/d "surplus" on a day
where opening/closing linepack (PUBOB693/694) barely moved (~-1 mcm) —
a real surplus of that size has to show up as linepack build, and it
didn't. Removing the double-count brought balance_mcm to within a few
mcm of the linepack change (consistent with normal shrinkage/measurement
tolerance), instead of off by ~46 mcm.

This also means we currently have no reliable *import*-specific series
for IUK/BBL — PUBOB386 ("Bacton_IUK_entry", System Entry Volume D+2) is
too small (~0.1-0.4 mcm/d) to be it (see below), and PUBOBJ515
("Allocations, Energy, Interconnector Entry Total, D+2") is similarly
tiny (~0.1-1.3 mcm/d over 90 days) — neither looks like the real import
figure. Given GB is a net gas exporter in summer 2026 (LNG imports +
indigenous production comfortably covering demand), near-zero interconnector
import may simply be physically correct right now — but this hasn't been
confirmed against a genuine import-specific source, so treat "IUK/BBL
import = 0" as an assumption of the current model, not a proven fact, and
revisit if a real import series turns up later (e.g. once winter imports
plausibly resume).
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

SUBTERMINAL_ENTRY_IDS = UKCS_ENTRY_IDS + ("PUBOB452",)  # UKCS terminals + Langeled — fallback if PUBOBJ627 is unavailable

STORAGE_OUTFLOW_IDS = (
    "PUBOBJ2413", "PUBOBJ2414", "PUBOBJ2415", "PUBOBJ2416", "PUBOBJ2417",
    "PUBOBJ2418", "PUBOBJ2419", "PUBOBJ2420", "PUBOBJ2421", "PUBOBJ2422",
)

COMPONENTS: list[Component] = [
    # --- Supply ---
    Component("Subterminal entry (UKCS + Norway)", "supply", ("PUBOBJ627",), mode="sum"),  # official aggregate; falls back to SUBTERMINAL_ENTRY_IDS if empty — see balance.py
    Component("LNG", "supply", ("PUBOBJ337",), mode="sum"),  # aggregate; falls back to LNG_ENTRY_IDS if empty — see balance.py
    Component("Storage withdrawal", "supply", STORAGE_OUTFLOW_IDS, mode="sum"),  # per-site sum — official PUBOBJ630 doesn't reconcile cleanly yet, see module docstring
    # No "IUK import"/"BBL import" supply component — PUBOB2038/PUBOBJ1307
    # are export-side figures already fully counted in "Interconnector
    # exports" below (PUBOBJ1020). See module docstring for how this was
    # found and confirmed (an earlier version of this file double-counted
    # them here, inflating supply by ~50-65 mcm/d).

    # --- Demand: National Gas's own official totals, where they exist ---
    Component("Residential/commercial", "demand", ("PUBOBJ1015",), mode="sum"),  # LDZ Offtake Total
    Component("Industrial (NTS-connected)", "demand", ("PUBOBJ1018",), mode="sum"),  # Industrial Offtake Total — distinct population, see module docstring
    Component("Power generation", "demand", ("PUBOBJ1017",), mode="sum"),  # Powerstations Total — direct measurement
    Component("Storage injection", "demand", ("PUBOBJ1016",), mode="sum"),  # Storage Injection Total
    Component("Interconnector exports", "demand", ("PUBOBJ1020",), mode="sum"),  # NTS Volume Offtaken, Interconnector Exports Total (IUK+BBL+Moffat combined) — see module docstring
    Component("Shrinkage", "demand", ("PUBOB289",), mode="sum"),  # NTS Shrinkage Quantity — real system losses (gas used/lost in transport), small (~0.3-0.7 mcm/d) but legitimate
]
