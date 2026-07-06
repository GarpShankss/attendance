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

def _calc_attendance(emp_id: str, location: str, warehouse: str, month: int, year: int, full_days: dict, fixed_working_days: float = None, db=None):
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
    
    doj_date = None
    if db is not None:
        master = db["employee_master"].find_one({"emp_id": emp_id, "location": location, "warehouse": warehouse})
        if master and master.get("DOJ"):
            doj_str = master.get("DOJ").strip()
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
                try:
                    doj_date = datetime.strptime(doj_str, fmt).date()
                    break
                except ValueError:
                    continue
                
    for d_str in period_dates:
        if doj_date:
            try:
                dt = datetime.strptime(d_str, "%Y-%m-%d").date()
                if dt < doj_date:
                    continue
            except ValueError:
                pass
                
        val = str(full_days.get(d_str) or "").strip().upper()
        if not val:
            # check if Sunday
            dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
            if dt_obj.weekday() == 6:  # Sunday
                val = "WO"
            else:
                val = "A"
            
        if val in policy:
            lop += policy[val].get("lop", 0)
            if not policy[val].get("paid", True):
                absent += 1
        else:
            lop += 1
            absent += 1
            
    pay_days = max(0, working_days - lop)
    present = pay_days  # Present Days is padded to match Working Days - LOP
    
    return present, absent, pay_days, lop, working_days
