"""
Payroll configuration loader/saver.
All rates, ceilings, and thresholds are stored in payroll_config.json
next to this file. The salary engine reads from get_config() at runtime,
so any saved change takes effect immediately on the next calculation.
"""
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "payroll_config.json")

DEFAULTS = {
    "employee_pf_percent":       12,
    "employer_pf_percent":       13,
    "employee_esi_percent":      0.75,
    "employer_esi_percent":      3.25,
    "pf_wage_ceiling":           15000,
    "esi_eligibility_ceiling":   20999,
    "professional_tax_amount":   200,
    "professional_tax_threshold": 25000,
    "working_days":              0,
    "attendance_policy": {
        "P":  { "name": "Present",         "present": 1,   "paid": True,  "lop": 0 },
        "HP": { "name": "Holiday Present", "present": 1,   "paid": True,  "lop": 0 },
        "SL": { "name": "Sick Leave",      "present": 1,   "paid": True,  "lop": 0 },
        "PL": { "name": "Paid Leave",      "present": 1,   "paid": True,  "lop": 0 },
        "HL": { "name": "Half Leave",      "present": 0.5, "paid": True,  "lop": 0.5 },
        "A":  { "name": "Absent",          "present": 0,   "paid": False, "lop": 1 },
        "WO": { "name": "Week Off",        "present": 0,   "paid": True,  "lop": 0 },
        "CO": { "name": "Comp Off",        "present": 1,   "paid": True,  "lop": 0 }
    }
}


def get_config() -> dict:
    """Load and return current payroll config. Falls back to defaults."""
    if not os.path.exists(_CONFIG_PATH):
        return dict(DEFAULTS)
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # merge with defaults so new keys added to DEFAULTS are always present
    return {**DEFAULTS, **data}


def save_config(updates: dict) -> dict:
    """
    Merge updates into the current config, validate types, persist to disk.
    Returns the saved config.
    """
    current = get_config()
    for key, value in updates.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown config key: {key!r}")
        try:
            current[key] = type(DEFAULTS[key])(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid value for {key!r}: {value!r}")
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    return current
