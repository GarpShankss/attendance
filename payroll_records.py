"""
Monthly Payroll Records

Collection: `payroll_records`
One document per employee per month/year per warehouse/location.

Schema:
{
    "emp_id":       "RSMAB0006",
    "emp_name":     "John Doe",
    "location":     "Hyderabad",
    "warehouse":    "Havells",
    "month":        6,
    "year":         2026,
    "status":       "draft",          # draft | finalised
    "salary":       { ...FIXED cols from employee_master... },
    "attendance": {
        "ATTENDANCE - Present Days": 0,
        "ATTENDANCE - Pay Days":     0,
    },
    "earnings":     {},               # populated by salary_calc
    "deductions":   {},
    "contributions":{},
    "net_pay":      None,
    "created_at":   <datetime>,
    "updated_at":   <datetime>,
}
"""
from datetime import datetime
from salary_calc import recalculate
from attendance_engine import _calc_attendance

PAYROLL_COLLECTION = "payroll_records"
ATTENDANCE_COLLECTION = "_attendance"


def generate_monthly_payroll(month: int, year: int,
                              location: str, warehouse: str, db) -> dict:
    """
    Create payroll records for every active employee matching
    location + warehouse, for the given month/year.

    - Already-existing records are NOT overwritten (idempotent).
    - Returns {"created": N, "skipped": N}
    """
    master_query = {"status": "active"}
    if location:  master_query["location"]  = location
    if warehouse: master_query["warehouse"] = warehouse

    employees = list(db["employee_master"].find(master_query))
    if not employees:
        return {"created": 0, "skipped": 0, "error": "No active employees found"}

    created = skipped = 0
    for emp in employees:
        key = {
            "emp_id":   emp["emp_id"],
            "location": emp["location"],
            "warehouse": emp["warehouse"],
            "month":    month,
            "year":     year,
        }
        # Skip if record already exists for this month
        if db[PAYROLL_COLLECTION].find_one(key):
            skipped += 1
            continue

        attendance_doc = db[ATTENDANCE_COLLECTION].find_one({
            "emp_id": emp["emp_id"], "location": emp["location"], "warehouse": emp["warehouse"]
        })
        full_days = attendance_doc.get("days", {}) if attendance_doc else {}
        
        fixed_wd = emp.get("FIXED - Working Days")
        try:
            fixed_wd = float(fixed_wd)
        except (ValueError, TypeError):
            fixed_wd = None
            
        present, absent, pay, lop, wd = _calc_attendance(emp["emp_id"], emp["location"], emp["warehouse"], month, year, full_days, fixed_wd, db=db)

        attendance = {
            "ATTENDANCE - Present Days": present,
            "ATTENDANCE - Pay Days":     pay,
            "ATTENDANCE - LOP":          lop,
        }

        # Try to find the previous month's payroll record to carry forward inputs
        prev_month = 12 if month == 1 else month - 1
        prev_year = year - 1 if month == 1 else year
        prev_record = db[PAYROLL_COLLECTION].find_one({
            "emp_id": emp["emp_id"],
            "location": emp["location"],
            "warehouse": emp["warehouse"],
            "month": prev_month,
            "year": prev_year
        })

        salary_data = dict(emp.get("salary", {}))
        identity_data = dict(emp.get("identity", {}))
        mobile_number = emp.get("mobile_number", "")
        deductions_data = {}
        
        # If there's a previous record, carry forward its full Employee Info and Fixed Inputs
        if prev_record:
            if isinstance(prev_record.get("salary"), dict):
                salary_data = dict(prev_record["salary"])
            if isinstance(prev_record.get("identity"), dict):
                identity_data = dict(prev_record["identity"])
            if isinstance(prev_record.get("deductions"), dict):
                deductions_data["Deductions - Adv"] = prev_record["deductions"].get("Deductions - Adv", 0)
            if "mobile_number" in prev_record:
                mobile_number = prev_record["mobile_number"]

        salary_data.setdefault("CONTRIBUTION - T Shirt", 0)
        salary_data.setdefault("CONTRIBUTION - Shoes", 0)
        salary_data.setdefault("CONTRIBUTION - Service Charge", salary_data.get("CONTRIBUTION - Service Charge", 0))
        salary_data.setdefault("Deductions - Adv", deductions_data.get("Deductions - Adv", 0))

        doc = {
            **key,
            "emp_name":     emp.get("emp_name", ""),
            "mobile_number": mobile_number,
            "status":       "draft",
            "salary":       salary_data,
            "identity":     identity_data,
            "attendance":   dict(attendance),
            "FIXED - Working Days":      wd,
            "ATTENDANCE - Present Days": present,
            "ATTENDANCE - Pay Days":     pay,
            "ATTENDANCE - LOP":          lop,
            "earnings":      {},
            "deductions":    deductions_data,
            "contributions": {},
            "net_pay":       None,
            "created_at":    datetime.utcnow(),
            "updated_at":    datetime.utcnow(),
        }

        row = dict(doc)
        row.update(doc["salary"])
        row.update(doc["attendance"])
        calculated = recalculate(row)
        for k, v in calculated.items():
            if k in {"__calc_log__", "_id"}:
                continue
            if not isinstance(v, dict):
                doc[k] = v
                if k.startswith("EARNING -"):
                    doc["earnings"][k] = v
                elif k.startswith("Deductions -"):
                    doc["deductions"][k] = v
                elif k.startswith("CONTRIBUTION -"):
                    doc["contributions"][k] = v
                elif k == "Net Pay":
                    doc["net_pay"] = v

        db[PAYROLL_COLLECTION].insert_one(doc)
        created += 1

    # Ensure useful indexes
    db[PAYROLL_COLLECTION].create_index(
        [("emp_id", 1), ("location", 1), ("warehouse", 1),
         ("month", 1), ("year", 1)],
        unique=True, background=True
    )
    return {"created": created, "skipped": skipped}


def get_payroll(month: int, year: int,
                location: str = None, warehouse: str = None, db=None) -> list:
    """Return all payroll records for a month, optionally filtered."""
    query = {"month": month, "year": year}
    if location:  query["location"]  = location
    if warehouse: query["warehouse"] = warehouse
    return list(db[PAYROLL_COLLECTION].find(query, {"_id": 0}).sort("emp_id", 1))
