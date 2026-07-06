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
            "emp_id": emp["emp_id"], "location": emp["location"],
            "warehouse": emp["warehouse"], "month": month, "year": year,
        })
        present = attendance_doc.get("present_days", 0) if attendance_doc else 0
        pay = attendance_doc.get("pay_days", present) if attendance_doc else 0

        attendance = {
            "ATTENDANCE - Present Days": present,
            "ATTENDANCE - Pay Days":     pay,
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
        
        # If there's a previous record, carry forward its inputs
        if prev_record:
            if isinstance(prev_record.get("salary"), dict):
                for k, v in prev_record["salary"].items():
                    if k in ("CONTRIBUTION - Service Charge", "CONTRIBUTION - T Shirt", 
                            "CONTRIBUTION - Shoes", "CONTRIBUTION - Uniform Charges"):
                        salary_data[k] = v
            if isinstance(prev_record.get("deductions"), dict):
                salary_data["Deductions - Adv"] = prev_record["deductions"].get("Deductions - Adv", 0)

        salary_data.setdefault("CONTRIBUTION - T Shirt", 0)
        salary_data.setdefault("CONTRIBUTION - Shoes", 0)
        salary_data.setdefault("CONTRIBUTION - Service Charge", salary_data.get("CONTRIBUTION - Service Charge", 0))
        salary_data.setdefault("Deductions - Adv", salary_data.get("Deductions - Adv", 0))

        doc = {
            **key,
            "emp_name":     emp.get("emp_name", ""),
            "status":       "draft",
            "salary":       salary_data,
            "identity":     dict(emp.get("identity", {})),
            "attendance":   dict(attendance),
            "ATTENDANCE - Present Days": present,
            "ATTENDANCE - Pay Days":     pay,
            "earnings":      {},
            "deductions":    {},
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
