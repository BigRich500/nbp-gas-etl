"""Minimal unit conversions needed by this ETL.

Base unit throughout is mcm/d (million cubic meters per day).
"""
from __future__ import annotations

MCM_TO_GWH = 11.16  # standard UK gas calorific-value-based conversion factor


def kwh_to_mcm(kwh):
    """Convert kWh to mcm. 1 mcm ~= MCM_TO_GWH * 1e6 kWh."""
    return kwh / (MCM_TO_GWH * 1e6)


def gwh_to_mcm(gwh):
    return gwh / MCM_TO_GWH
