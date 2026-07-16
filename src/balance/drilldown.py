"""Per-component drill-down detail for the dashboard: which individual
sites/regions/interconnectors make up each bucket in the chart.

This is presentation-only — it doesn't feed into the balance calculation
in balance.py/components.py, which stays anchored to the official
aggregates. Drill-down numbers are shown for context and may not always
sum exactly to their parent bucket's total (documented per component
below where that's the case).
"""
from __future__ import annotations

import re

import pandas as pd

from src.balance.components import (
    UKCS_ENTRY_IDS, LNG_ENTRY_IDS, STORAGE_OUTFLOW_IDS,
)

# Minor "System Entry Volume" sites found in the Supplies category that
# aren't in UKCS_ENTRY_IDS but contribute to the gap between our UKCS+Norway
# sum and the official Subterminal aggregate (PUBOBJ627) — see
# components.py's docstring for how that gap was found. Amethyst (PUBOB1828)
# and Avonmouth (PUBOB374) are also part of that same catalogue search but
# have no data currently (checked: zero rows), so they're left out here.
OTHER_SUBTERMINAL_IDS = (
    ("PUBOB19003", "Murrow"),
    ("PUBOB389", "Bacton Seal"),
    ("PUBOB395", "Burton Point"),
    ("PUBOB443", "Theddlethorpe"),
    ("PUBOBJ2252", "Saltfleetby"),
    ("PUBOBJ2856", "Glentham Biomethane"),
)

INTERCONNECTOR_EXPORT_IDS = (
    ("PUBOB2038", "IUK"),
    ("PUBOBJ1307", "BBL"),
    ("PUBOB2039", "Moffat"),
)

STORAGE_INJECTION_IDS = (
    ("PUBOBJ2401", "INF_HumblyGrove"),
    ("PUBOBJ2402", "INF_Hornsea"),
    ("PUBOBJ2404", "INF_Rough"),
    ("PUBOBJ2405", "INF_HatfieldMoor"),
    ("PUBOBJ2406", "INF_HolehouseFarm"),
    ("PUBOBJ2407", "INF_Aldbrough"),
    ("PUBOBJ2408", "INF_Holford"),
    ("PUBOBJ2409", "INF_HillTop"),
    ("PUBOBJ2410", "INF_Stublach"),
)

UKCS_LABELS = {
    "PUBOB407": "Easington - Dimlington", "PUBOB401": "Easington - West Sole",
    "PUBOB377": "Bacton - Perenco", "PUBOB383": "Bacton - Shell",
    "PUBOB380": "Bacton - Tullow", "PUBOB1826": "Barrow",
    "PUBOB428": "St Fergus - Mobil", "PUBOB431": "St Fergus - Shell",
    "PUBOB434": "St Fergus - NSMP", "PUBOB437": "Teesside - CATS",
    "PUBOB440": "Teesside - PX",
}
LNG_LABELS = {
    "PUBOB3480": "South Hook", "PUBOB3564": "Dragon",
    "PUBOB371": "Grain NTS1", "PUBOB3473": "Grain NTS2",
}


def _prettify(key_name: str, strip_prefix: str = "", strip_suffix: str = "") -> str:
    s = key_name
    if strip_prefix and s.startswith(strip_prefix):
        s = s[len(strip_prefix):]
    if strip_suffix and s.endswith(strip_suffix):
        s = s[:-len(strip_suffix)]
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)  # camelCase -> spaced
    s = s.replace("_", " ").strip()
    return s.title()


_STORAGE_LABEL_OVERRIDES = {"PUBOBJ2415": "Isle of Grain (LNG)"}


def _storage_labels(pubob_ids: tuple[str, ...], dim: pd.DataFrame) -> dict:
    names = dim.set_index("pubob_id")["key_name"].to_dict()
    return {
        pid: _STORAGE_LABEL_OVERRIDES.get(pid, _prettify(names.get(pid, pid), strip_prefix="OUT_"))
        for pid in pubob_ids
    }


def _power_station_ids(dim: pd.DataFrame, top_n: int = 15) -> list[tuple[str, str]]:
    mask = dim["key_name"].str.contains(
        r"nts_energy_offtaken.*power_station", case=False, na=False, regex=True
    ) & ~dim["key_name"].str.contains("meter", case=False, na=False)
    rows = dim[mask]
    return [
        (pid, _prettify(name, strip_prefix="nts_energy_offtaken_", strip_suffix="_nts_power_station"))
        for pid, name in zip(rows["pubob_id"], rows["key_name"])
    ]


def _ldz_demand_ids(dim: pd.DataFrame) -> list[tuple[str, str]]:
    mask = dim["key_name"].str.match(r"^(DM|NDM)_[A-Z]{2}$", na=False)
    rows = dim[mask]
    return [(pid, name.replace("_", " ")) for pid, name in zip(rows["pubob_id"], rows["key_name"])]


def build_drilldown_map(dim: pd.DataFrame) -> dict:
    """component name -> list of (pubob_id, label), ordered as defined."""
    return {
        "UKCS production": [(pid, UKCS_LABELS[pid]) for pid in UKCS_ENTRY_IDS],
        "Other entry sites": list(OTHER_SUBTERMINAL_IDS),
        "LNG": [(pid, LNG_LABELS[pid]) for pid in LNG_ENTRY_IDS],
        "Storage withdrawal": list(_storage_labels(STORAGE_OUTFLOW_IDS, dim).items()),
        "Storage injection": list(STORAGE_INJECTION_IDS),
        "Interconnector exports": list(INTERCONNECTOR_EXPORT_IDS),
        "Power generation": _power_station_ids(dim),
        "Residential/commercial": _ldz_demand_ids(dim),
    }


def compute_drilldown(latest: pd.DataFrame, dim: pd.DataFrame) -> dict:
    """gas_day (str) -> component name -> list of {label, volume_mcm},
    sorted by volume descending, zero/missing rows omitted."""
    drilldown_map = build_drilldown_map(dim)
    result: dict = {}
    for component, id_label_pairs in drilldown_map.items():
        ids = [pid for pid, _ in id_label_pairs]
        labels = dict(id_label_pairs)
        rows = latest[latest["pubob_id"].isin(ids)].dropna(subset=["value_mcm"])
        for gas_day, group in rows.groupby("gas_day"):
            day_key = str(gas_day)
            items = [
                {"label": labels[pid], "volume_mcm": round(float(val), 4)}
                for pid, val in zip(group["pubob_id"], group["value_mcm"])
                if val != 0
            ]
            items.sort(key=lambda x: -x["volume_mcm"])
            if items:
                result.setdefault(day_key, {})[component] = items
    return result
