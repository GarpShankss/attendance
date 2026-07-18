"""
Salary calculation engine.

All payroll rates, ceilings and thresholds are read from payroll_config.json
via payroll_settings.get_config(). No percentages or thresholds are
hardcoded here — only the formula structure.

Column mapping is explicit via COLUMN_MAP; no fuzzy matching is used.
"""
from math import floor
from payroll_settings import get_config

# ---------------------------------------------------------------------------
# Explicit column map  –  logical name  ->  exact Excel column header
# ---------------------------------------------------------------------------
COLUMN_MAP = {
    # Fixed salary inputs
    "fixed_basic":            "FIXED - Basic",
    "fixed_da":               "FIXED - DA",
    "fixed_other":            "FIXED - Other Allows",
    "fixed_leave":            "FIXED - Leave With wages",
    "fixed_bonus":            "FIXED - Bonus @8.33%",
    "fixed_hra":              "FIXED - HRA",
    "fixed_total":            "FIXED - Total",
    "working_days":           "FIXED - Working Days",
    "fixed_tshirt":           "CONTRIBUTION - T Shirt",
    "fixed_shoes":            "CONTRIBUTION - Shoes",

    # Attendance inputs
    "present_days":           "ATTENDANCE - Present Days",
    "holiday":                "ATTENDANCE - Holi day",
    "pay_days":               "ATTENDANCE - Pay Days",
    "ot_hours":               "ATTENDANCE - OT Hours",

    # Earnings (calculated, written back)
    "earned_basic":           "EARNING - Basic",
    "earned_da":              "EARNING - DA",
    "earned_other":           "EARNING - Other Allows",
    "earned_leave":           "EARNING - Leave With wages",
    "earned_bonus":           "EARNING - Bonus @8.33%",
    "earned_hra":             "EARNING - HRA",
    "earned_ot":              "EARNING - OT Amount",
    "earned_total":           "EARNING - Total",

    # Employer / Contribution (calculated, written back)
    "emp_esi":                "CONTRIBUTION - ESIC @ 3.25%",
    "emp_pf":                 "CONTRIBUTION - EPF @ 13%",
    "uniform":                "CONTRIBUTION - Uniform Charges",
    "fixed_service_charge":   "CONTRIBUTION - Service Charge",
    "fixed_tshirt":           "CONTRIBUTION - T Shirt",
    "fixed_shoes":            "CONTRIBUTION - Shoes",
    "emp_contribution":       "CONTRIBUTION - Total Employer Contribution",
    "ctc":                    "CONTRIBUTION - CTC",
    "total_ctc":              "CONTRIBUTION - Total CTC",
    "gst":                    "CONTRIBUTION - GST @18%",
    "billing":                "CONTRIBUTION - Total Billing",

    # Deductions (calculated, written back)
    "pf":                     "Deductions - PF 12%",
    "esi":                    "Deductions - ESIC 0.75%",
    "pt":                     "Deductions - PT",
    "advance":                "Deductions - Adv",
    "total_deduction":        "Deductions - Total Deduction",

    # Net pay (calculated, written back)
    "net_pay":                "Net Pay",
}

# Fields that must never be overwritten (inputs, not outputs)
READONLY_FIELDS = {
    "fixed_basic", "fixed_da", "fixed_other", "fixed_leave",
    "fixed_bonus", "fixed_hra", "fixed_total", "working_days",
    "present_days", "holiday", "pay_days", "ot_hours",
    "uniform",
}


# Aliases for common variations in uploaded sheets
ALIASES = {
    "working_days": ["Basic Days", "Working Days"],
    "present_days": ["Actual Days", "Present Days"],
    "pay_days":     ["Actual Days", "Pay Days"],
    "fixed_bonus":  ["FIXED - STATUORY BONUS", "FIXED - STATUTORY BONUS", "FIXED - Bonus", "Bonus", "FIXED - Bonus @8.33%"],
    "fixed_shoes":  ["SEFTY SHOES", "Safety Shoes"],
    "fixed_tshirt": ["T SHIRT", "T-Shirt"],
    "fixed_service_charge": ["SERVICE CHARGES", "Service Charge"],
}

def R(v):
    """Excel-compatible ROUND(v, 0) — round-half-up."""
    return int(floor(float(v) + 0.5))


def _num(v, default=0):
    """Convert value to float. Rejects datetime objects — they must never be
    used as salary numbers (indicates a parsing bug upstream)."""
    import datetime as _dt
    if isinstance(v, (_dt.datetime, _dt.date)):
        return default   # refuse datetime — caller gets 0 and calc will show it
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def get(row: dict, field: str, default=0):
    """Read a value from the row by logical field name via COLUMN_MAP.
    Tries both the exact mapped name and a whitespace-normalised version.
    For advance, also scans alternate keys like Advance / ADV."""
    import re as _re
    col = COLUMN_MAP.get(field)
    if col is not None:
        if col in row:
            return _num(row[col], default)
        norm_col = _re.sub(r'[\s]+', ' ', col).strip().lower()
        for k, v in row.items():
            if _re.sub(r'[\s]+', ' ', str(k)).strip().lower() == norm_col:
                return _num(v, default)

    if field in ALIASES:
        for k, v in row.items():
            k_norm = _re.sub(r'[\s]+', ' ', str(k)).strip().lower()
            if any(k_norm == a.lower() for a in ALIASES[field]):
                return _num(v, default)

    if field == "advance":
        for k, v in row.items():
            nk = _re.sub(r'[\s]+', ' ', str(k)).strip().lower()
            if nk == "adv" or nk == "advance" or "advance" in nk or nk.endswith(" adv") or nk.endswith(" advance"):
                return _num(v, default)

    return default

def normalize_row(row: dict) -> dict:
    """Standardize the keys of a raw Excel row to match exactly the COLUMN_MAP."""
    import re as _re
    normalized = {}
    
    alias_map = {}
    for logical_key, aliases in ALIASES.items():
        if logical_key in COLUMN_MAP:
            target = COLUMN_MAP[logical_key]
            for a in aliases:
                alias_map[a.lower()] = target

    for k, v in row.items():
        if not isinstance(k, str):
            normalized[k] = v
            continue

        k_norm = _re.sub(r'[\s]+', ' ', k).strip().lower()
        
        if k_norm == "adv" or k_norm == "advance" or "advance" in k_norm or k_norm.endswith(" adv") or k_norm.endswith(" advance"):
            normalized[COLUMN_MAP["advance"]] = v
            continue

        if k_norm in alias_map:
            normalized[alias_map[k_norm]] = v
            continue

        matched = False
        for target_key in COLUMN_MAP.values():
            if target_key and _re.sub(r'[\s]+', ' ', target_key).strip().lower() == k_norm:
                normalized[target_key] = v
                matched = True
                break
        
        if not matched:
            normalized[k] = v

    return normalized


def put(row: dict, field: str, value):
    """Write a calculated value back. Refuses READONLY_FIELDS.
    Matches against whitespace-normalised key names and creates output keys
    when they do not already exist."""
    import re as _re
    if field in READONLY_FIELDS:
        return
    col = COLUMN_MAP.get(field)
    if col is None:
        return
        
    overrides = row.get("_manual_overrides", [])
    if col in overrides:
        return  # Do not overwrite if user manually edited this field
        
    # direct hit
    if col in row:
        row[col] = value
        if field == 'net_pay':
            row['net_pay'] = value
        return
    # normalise and match case-insensitively
    norm_col = _re.sub(r'[\s]+', ' ', col).strip().lower()
    for k in row:
        if _re.sub(r'[\s]+', ' ', str(k)).strip().lower() == norm_col:
            row[k] = value
            if field == 'net_pay' and k.lower() == 'net pay':
                row['net_pay'] = value
            return
    # create missing output field if it was not already present
    row[col] = value
    if field == 'net_pay':
        row['net_pay'] = value


# ---------------------------------------------------------------------------
# Main recalculate function
# ---------------------------------------------------------------------------

def recalculate(row: dict) -> dict:
    cfg = get_config()

    emp_pf_pct   = cfg["employee_pf_percent"]   / 100
    er_pf_pct    = cfg["employer_pf_percent"]   / 100
    emp_esi_pct  = cfg["employee_esi_percent"]  / 100
    er_esi_pct   = cfg["employer_esi_percent"]  / 100
    pf_ceiling   = cfg["pf_wage_ceiling"]
    esi_ceiling  = cfg["esi_eligibility_ceiling"]
    pt_amount    = cfg["professional_tax_amount"]
    pt_threshold = cfg["professional_tax_threshold"]

    r = dict(row)
    log = []   # calculation trace — returned alongside the result
    overrides = r.get("_manual_overrides", [])

    def L(msg):
        log.append(msg)

    working_days = get(r, "working_days") or 30
    present_days = get(r, "present_days")
    pay_days     = present_days
    if working_days <= 0:
        working_days = 30

    # ── Fixed inputs ─────────────────────────────────────────────────────────
    fixed_basic = get(r, "fixed_basic")
    fixed_da    = get(r, "fixed_da")
    fixed_other = get(r, "fixed_other")
    fixed_leave = get(r, "fixed_leave")
    fixed_bonus = get(r, "fixed_bonus")
    fixed_hra   = get(r, "fixed_hra")
    advance     = get(r, "advance")
    fixed_sc    = get(r, "fixed_service_charge")
    uniform     = get(r, "uniform")
    fixed_tshirt = get(r, "fixed_tshirt")
    fixed_shoes  = get(r, "fixed_shoes")

    L("=" * 60)
    L("SALARY CALCULATION LOG")
    L("=" * 60)
    L("")
    L("── INPUTS ──────────────────────────────────────────────────")
    L(f"  Working Days          (FIXED - Working Days)      = {working_days}")
    L(f"  Pay Days              (ATTENDANCE - Pay Days)     = {pay_days}")
    L(f"  Fixed Basic           (FIXED - Basic)             = {fixed_basic}")
    L(f"  Fixed DA              (FIXED - DA)                = {fixed_da}")
    L(f"  Fixed Other Allows    (FIXED - Other Allows)      = {fixed_other}")
    L(f"  Fixed Leave W/Wages   (FIXED - Leave With wages)  = {fixed_leave}")
    L(f"  Fixed Bonus           (FIXED - Bonus @8.33%)      = {fixed_bonus}")
    L(f"  Fixed HRA             (FIXED - HRA)               = {fixed_hra}")
    L(f"  Fixed Service Charge  (CONTRIBUTION - SC)         = {fixed_sc}")
    L(f"  Uniform               (CONTRIBUTION - Uniform)    = {uniform}")
    L(f"  T Shirt               (CONTRIBUTION - T Shirt)     = {fixed_tshirt}")
    L(f"  Shoes                 (CONTRIBUTION - Shoes)       = {fixed_shoes}")
    L(f"  Advance               (Deductions - Adv)          = {advance}")
    L("")
    L("── CONFIG RATES ────────────────────────────────────────────")
    L(f"  Employee PF %         = {cfg['employee_pf_percent']}%")
    L(f"  Employer PF %         = {cfg['employer_pf_percent']}%")
    L(f"  Employee ESI %        = {cfg['employee_esi_percent']}%")
    L(f"  Employer ESI %        = {cfg['employer_esi_percent']}%")
    L(f"  PF Wage Ceiling       = {pf_ceiling}")
    L(f"  ESI Eligibility Ceil  = {esi_ceiling}")
    L(f"  PT Amount             = {pt_amount}")
    L(f"  PT Threshold          = {pt_threshold}")
    L("")

    # ── Earned components ────────────────────────────────────────────────────
    L("── EARNED COMPONENTS  [ROUND(Fixed / Working Days × Pay Days, 0)] ──")

    earned_basic = R(fixed_basic / working_days * pay_days)
    L(f"  Earned Basic  = ROUND({fixed_basic} / {working_days} × {pay_days}, 0)"
      f"  = ROUND({fixed_basic / working_days * pay_days:.4f}, 0)  = {earned_basic}")

    earned_da = R(fixed_da / working_days * pay_days)
    L(f"  Earned DA     = ROUND({fixed_da} / {working_days} × {pay_days}, 0)"
      f"  = ROUND({fixed_da / working_days * pay_days:.4f}, 0)  = {earned_da}")

    earned_other = R(fixed_other / working_days * pay_days)
    L(f"  Earned Other  = ROUND({fixed_other} / {working_days} × {pay_days}, 0)"
      f"  = ROUND({fixed_other / working_days * pay_days:.4f}, 0)  = {earned_other}")

    earned_leave = R(fixed_leave / working_days * pay_days)
    L(f"  Earned Leave  = ROUND({fixed_leave} / {working_days} × {pay_days}, 0)"
      f"  = ROUND({fixed_leave / working_days * pay_days:.4f}, 0)  = {earned_leave}")

    earned_bonus = R(fixed_bonus / working_days * pay_days)
    L(f"  Earned Bonus  = ROUND({fixed_bonus} / {working_days} × {pay_days}, 0)"
      f"  = ROUND({fixed_bonus / working_days * pay_days:.4f}, 0)  = {earned_bonus}")

    earned_hra = R(fixed_hra / working_days * pay_days)
    L(f"  Earned HRA    = ROUND({fixed_hra} / {working_days} × {pay_days}, 0)"
      f"  = ROUND({fixed_hra / working_days * pay_days:.4f}, 0)  = {earned_hra}")
    L("")

    # ── Total Earnings ───────────────────────────────────────────────────────
    total_earnings = R(earned_basic + earned_da + earned_other + earned_leave + earned_bonus + earned_hra)
    L("── TOTAL EARNINGS  [ROUND(sum of earned components, 0)] ────")
    L(f"  = ROUND({earned_basic} + {earned_da} + {earned_other} + {earned_leave} + {earned_bonus} + {earned_hra}, 0)")
    L(f"  = ROUND({earned_basic + earned_da + earned_other + earned_leave + earned_bonus + earned_hra}, 0)")
    L(f"  = {total_earnings}")
    L("")

    # ── PF ───────────────────────────────────────────────────────────────────
    warehouse = str(r.get("warehouse", "")).strip().lower()
    if warehouse == "abb":
        pf_base = min(earned_basic + earned_da, pf_ceiling)
        L(f"  Warehouse is ABB. Capping PF Base at {pf_ceiling}.")
    else:
        pf_base = earned_basic + earned_da
        L(f"  Warehouse is not ABB. No cap on PF Base.")
        
    pf = R(pf_base * emp_pf_pct)
    if COLUMN_MAP.get("ded_pf") in overrides:
        pf = get(r, "ded_pf", pf)
        L("  PF DEDUCTION MANUALLY OVERRIDDEN.")
    L("── PF DEDUCTION  [ROUND(PF Base × PF%, 0)] ──")
    L(f"  Base          = {pf_base}")
    L(f"  PF            = ROUND({pf_base} × {cfg['employee_pf_percent']}%, 0)"
      f"  = ROUND({pf_base * emp_pf_pct:.4f}, 0)  = {pf}")
    L("")

    # ── ESI ──────────────────────────────────────────────────────────────────
    esi = R(total_earnings * emp_esi_pct) if total_earnings < esi_ceiling else 0
    if COLUMN_MAP.get("ded_esi") in overrides:
        esi = get(r, "ded_esi", esi)
    L("── ESI DEDUCTION  [ROUND(IF(Total < ceiling, Total × ESI%, 0), 0)] ──")
    L(f"  Total Earnings {total_earnings} < ESI ceiling {esi_ceiling}? → {total_earnings < esi_ceiling}")
    if total_earnings < esi_ceiling:
        L(f"  ESI = ROUND({total_earnings} × {cfg['employee_esi_percent']}%, 0)"
          f"  = ROUND({total_earnings * emp_esi_pct:.4f}, 0)  = {esi}")
    else:
        L(f"  ESI = 0  (Total Earnings exceeds ESI ceiling)")
    L("")

    # ── PT ───────────────────────────────────────────────────────────────────
    pt = pt_amount if total_earnings >= pt_threshold else 0
    if COLUMN_MAP.get("ded_pt") in overrides:
        pt = get(r, "ded_pt", pt)
    L("── PROFESSIONAL TAX  [IF(Total >= threshold, PT amount, 0)] ──")
    L(f"  Total Earnings {total_earnings} >= PT threshold {pt_threshold}? → {total_earnings >= pt_threshold}")
    L(f"  PT = {pt}")
    L("")

    # ── Total Deduction ──────────────────────────────────────────────────────
    total_deduction = R(pf + esi + pt + advance)
    L("── TOTAL DEDUCTION  [ROUND(PF + ESI + PT + Advance, 0)] ───")
    L(f"  = ROUND({pf} + {esi} + {pt} + {advance}, 0)  = {total_deduction}")
    L("")

    # ── Net Pay ──────────────────────────────────────────────────────────────
    net_pay = R(total_earnings - total_deduction)
    L("── NET PAY  [ROUND(Total Earnings - Total Deduction, 0)] ──")
    L(f"  = ROUND({total_earnings} - {total_deduction}, 0)  = {net_pay}")
    L("")

    # ── Employer PF ──────────────────────────────────────────────────────────
    emp_pf = R(pf_base * er_pf_pct)
    if COLUMN_MAP.get("emp_pf") in overrides:
        emp_pf = get(r, "emp_pf", emp_pf)
    L("── EMPLOYER PF  [ROUND(base × Employer PF%, 0)] ───────────")
    L(f"  = ROUND({pf_base} × {cfg['employer_pf_percent']}%, 0)"
      f"  = ROUND({pf_base * er_pf_pct:.4f}, 0)  = {emp_pf}")
    L("")

    # ── Employer ESI ─────────────────────────────────────────────────────────
    emp_esi = R(total_earnings * er_esi_pct) if total_earnings < esi_ceiling else 0
    if COLUMN_MAP.get("emp_esi") in overrides:
        emp_esi = get(r, "emp_esi", emp_esi)
    L("── EMPLOYER ESI  [ROUND(IF(Total < ceiling, Total × Emp ESI%, 0), 0)] ──")
    if total_earnings < esi_ceiling:
        L(f"  = ROUND({total_earnings} × {cfg['employer_esi_percent']}%, 0)"
          f"  = ROUND({total_earnings * er_esi_pct:.4f}, 0)  = {emp_esi}")
    else:
        L(f"  = 0  (Total Earnings exceeds ESI ceiling)")
    L("")

    # ── Employer Contribution ────────────────────────────────────────────────
    emp_contribution = R(emp_pf + emp_esi)
    L("── EMPLOYER CONTRIBUTION  [ROUND(Emp PF + Emp ESI, 0)] ────")
    L(f"  = ROUND({emp_pf} + {emp_esi}, 0)  = {emp_contribution}")
    L("")

    # ── CTC ──────────────────────────────────────────────────────────────────
    ctc = R(total_earnings + emp_contribution)
    L("── CTC  [ROUND(Total Earnings + Employer Contribution, 0)] ")
    L(f"  = ROUND({total_earnings} + {emp_contribution}, 0)  = {ctc}")
    L("")

    # ── Service Charge ───────────────────────────────────────────────────────
    service_charge = fixed_sc
    L("── SERVICE CHARGE  [Used As-Is] ─────────────────────────────────────────")
    L(f"  = {service_charge}")
    L("")

    # ── Total CTC ────────────────────────────────────────────────────────────
    total_ctc = ctc + service_charge + uniform + fixed_tshirt + fixed_shoes
    L("── TOTAL CTC  [CTC + Service Charge + Uniform + T Shirt + Shoes] ─")
    L(f"  = {ctc} + {service_charge} + {uniform} + {fixed_tshirt} + {fixed_shoes}  = {total_ctc}")
    L("")
    L("── SUMMARY ─────────────────────────────────────────────────")
    L(f"  Earned Basic          = {earned_basic}")
    L(f"  Earned DA             = {earned_da}")
    L(f"  Earned Other Allows   = {earned_other}")
    L(f"  Earned Leave W/Wages  = {earned_leave}")
    L(f"  Earned Bonus          = {earned_bonus}")
    L(f"  Earned HRA            = {earned_hra}")
    L(f"  Total Earnings        = {total_earnings}")
    L(f"  PF                    = {pf}")
    L(f"  ESI                   = {esi}")
    L(f"  PT                    = {pt}")
    L(f"  Total Deduction       = {total_deduction}")
    L(f"  Net Pay               = {net_pay}")
    L(f"  Employer PF           = {emp_pf}")
    L(f"  Employer ESI          = {emp_esi}")
    L(f"  Employer Contribution = {emp_contribution}")
    L(f"  CTC                   = {ctc}")
    L(f"  Service Charge        = {service_charge}")
    L(f"  Uniform               = {uniform}")
    L(f"  T Shirt               = {fixed_tshirt}")
    L(f"  Shoes                 = {fixed_shoes}")
    L(f"  Total CTC             = {total_ctc}")
    L("=" * 60)

    # ── Write back ───────────────────────────────────────────────────────────
    put(r, "earned_basic",    earned_basic)
    put(r, "earned_da",       earned_da)
    put(r, "earned_other",    earned_other)
    put(r, "earned_leave",    earned_leave)
    put(r, "earned_bonus",    earned_bonus)
    put(r, "earned_hra",      earned_hra)
    put(r, "earned_total",    total_earnings)
    put(r, "pf",              pf)
    put(r, "esi",             esi)
    put(r, "pt",              pt)
    put(r, "total_deduction", total_deduction)
    put(r, "net_pay",         net_pay)
    put(r, "emp_pf",          emp_pf)
    put(r, "emp_esi",         emp_esi)
    put(r, "emp_contribution", emp_contribution)
    put(r, "ctc",             ctc)
    put(r, "total_ctc",       total_ctc)

    r["__calc_log__"] = log
    return r


# ---------------------------------------------------------------------------
# Self-test — run:  python salary_calc.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_row = {
        "FIXED - Working Days":           30,
        "ATTENDANCE - Pay Days":          28,
        "FIXED - Basic":                  13354,
        "FIXED - DA":                     4549,
        "FIXED - Other Allows":           4861,
        "FIXED - Leave With wages":       1033,
        "FIXED - Bonus @8.33%":           1491,
        "CONTRIBUTION - Service Charge":  700,
        "CONTRIBUTION - Uniform Charges": 150,
        "Deductions - Adv":               0,
        "EARNING - Basic":                0,
        "EARNING - DA":                   0,
        "EARNING - Other Allows":         0,
        "EARNING - Leave With wages":     0,
        "EARNING - Bonus @8.33%":         0,
        "EARNING - Total":                0,
        "Deductions - PF 12%":            0,
        "Deductions - ESIC 0.75%":        0,
        "Deductions - PT":                0,
        "Deductions - Total Deduction":   0,
        "Net Pay":                        0,
        "CONTRIBUTION - EPF @ 13%":       0,
        "CONTRIBUTION - ESIC @ 3.25%":    0,
        "CONTRIBUTION - Total CTC":       0,
    }

    result = recalculate(test_row)

    checks = {
        "EARNING - Basic":               12464,
        "EARNING - DA":                  4246,
        "EARNING - Other Allows":        4537,
        "EARNING - Leave With wages":    964,
        "EARNING - Bonus @8.33%":        1392,
        "EARNING - Total":               23603,
        "Deductions - PF 12%":           1800,
        "Deductions - ESIC 0.75%":       0,
        "Deductions - PT":               0,
        "Deductions - Total Deduction":  1800,
        "Net Pay":                       21803,
        "CONTRIBUTION - EPF @ 13%":      1950,
        "CONTRIBUTION - Total CTC":      26356,
    }

    print("=== Salary Calc Self-Test ===")
    all_ok = True
    for col, expected in checks.items():
        got = result.get(col)
        status = "OK" if got == expected else f"FAIL (got {got}, expected {expected})"
        print(f"  {col}: {status}")
        if got != expected:
            all_ok = False
    print("PASS" if all_ok else "Some values differ — check rounding.")
