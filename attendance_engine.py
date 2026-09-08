import calendar
from datetime import datetime
from payroll_settings import get_config

def get_payroll_dates(month: int, year: int):
    if month == 1:
        prev_m = 12
        prev_y = year - 1
    else:
        prev_m = month - 1
        prev_y = year
    prev_days_in_month = calendar.monthrange(prev_y, prev_m)[1]
    
    dates = []
    for d in range(26, prev_days_in_month + 1):
        dates.append(f"{prev_y}-{prev_m:02d}-{d:02d}")
    for d in range(1, 26):
        dates.append(f"{year}-{month:02d}-{d:02d}")
    return dates

def _calc_attendance(emp_id: str, location: str, warehouse: str, month: int, year: int, full_days: dict, fixed_working_days: float = None, doj_str: str = None, leaving_date_str: str = None):
    cfg = get_config()
    policy = cfg.get("attendance_policy", {})
    configured_wd = cfg.get("working_days", 0)
    
    if fixed_working_days is not None and fixed_working_days > 0:
        working_days = fixed_working_days
    elif configured_wd > 0:
        working_days = configured_wd
    else:
        working_days = calendar.monthrange(year, month)[1]

    period_dates = get_payroll_dates(month, year)
    
    present = 0.0
    absent = 0.0
    lop = 0.0
    unemployed_days = 0.0
    
    doj_date = None
    if doj_str:
        doj_str = str(doj_str).strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
            try:
                doj_date = datetime.strptime(doj_str, fmt).date()
                break
            except ValueError:
                continue

    leaving_date = None
    if leaving_date_str:
        leaving_date_str = str(leaving_date_str).strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
            try:
                leaving_date = datetime.strptime(leaving_date_str, fmt).date()
                break
            except ValueError:
                continue
                
    for d_str in period_dates:
        try:
            dt = datetime.strptime(d_str, "%Y-%m-%d").date()
        except ValueError:
            dt = None

        if doj_date and dt and dt < doj_date:
            unemployed_days += 1
            continue

        if leaving_date and dt and dt > leaving_date:
            unemployed_days += 1
            continue
                
        val = str(full_days.get(d_str) or "").strip().upper()
        if val == "L":
            unemployed_days += 1
            continue

        if not val:
            # Default to Present ("P"), except Sunday which is Week Off ("WO")
            if dt and dt.weekday() == 6:  # Sunday
                val = "WO"
            else:
                val = "P"
            
        if val in policy:
            lop += policy[val].get("lop", 0)
            if not policy[val].get("paid", True):
                absent += 1
        else:
            lop += 1
            absent += 1
            
    effective_wd = max(0.0, working_days - unemployed_days)
    pay_days = max(0.0, effective_wd - lop)
    present = pay_days  # Present Days is padded to match Working Days - LOP
    
    return present, absent, pay_days, lop, working_days

