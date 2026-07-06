"""
Employee Master

Maintains a permanent `employee_master` collection in MongoDB.
One document per employee — keyed by emp_id + location + warehouse.

Called from mongo_loader after every upload. Existing records are updated
(upsert), never deleted. This means employee data survives month rollovers.

Schema of each employee_master document:
{
    "emp_id":       "RSMAB0006",          # unique employee identifier
    "emp_name":     "John Doe",
    "location":     "Hyderabad",
    "warehouse":    "Havells",
    "status":       "active",
    "joined_month": 5,
    "joined_year":  2026,
    "salary": {                           # FIXED salary components
        "FIXED - Basic":           13354,
        "FIXED - DA":              4549,
        ...
        "FIXED - Working Days":    30,
        "CONTRIBUTION - Service Charge": 700,
        "CONTRIBUTION - Uniform Charges": 150,
    },
    "identity": {                         # non-salary columns (name, dept, etc.)
        "Department": "...",
        "Designation": "...",
        ...
    },
    "last_updated": <datetime>,
    "last_source":  "RSM_MAY_2026.xlsx",
}
"""
import re
from datetime import datetime

# Columns that belong in salary.  Everything else goes into identity.
SALARY_PREFIX = ("FIXED -", "CONTRIBUTION -", "Deductions -")

# Columns used to identify an employee (checked in order, first match wins)
EMP_ID_CANDIDATES  = ["ID Number", "Employee ID", "Emp ID", "EmpID", "Sl No", "S.No"]
EMP_NAME_CANDIDATES = ["Employee Name", "Name", "Emp Name", "EMP NAME"]

MASTER_COLLECTION = "employee_master"


def _find_col(row: dict, candidates: list):
    """Return (column_name, value) for the first candidate key found in row."""
    for c in candidates:
        if c in row and row[c] is not None:
            return c, row[c]
    # case-insensitive fallback
    lower = {k.lower(): (k, v) for k, v in row.items()}
    for c in candidates:
        match = lower.get(c.lower())
        if match and match[1] is not None:
            return match
    return None, None


def upsert_employees(rows: list, location: str, warehouse: str,
                     source_file: str, month: int, year: int, db):
    """
    Upsert every row from a parsed sheet into employee_master.
    Returns count of upserted documents.
    """
    coll = db[MASTER_COLLECTION]
    # Ensure index on emp_id + location + warehouse
    coll.create_index(
        [("emp_id", 1), ("location", 1), ("warehouse", 1)],
        unique=True, background=True
    )

    upserted = 0
    for row in rows:
        id_col, emp_id = _find_col(row, EMP_ID_CANDIDATES)
        if not emp_id:
            continue  # skip rows with no identifiable employee ID

        emp_id = str(emp_id).strip()
        _, emp_name = _find_col(row, EMP_NAME_CANDIDATES)

        salary  = {}
        identity = {}
        for k, v in row.items():
            if v is None:
                continue
            if any(k.startswith(p) for p in SALARY_PREFIX):
                salary[k] = v
            elif k not in (id_col,):
                identity[k] = v

        filter_q = {"emp_id": emp_id, "location": location, "warehouse": warehouse}
        update = {
            "$set": {
                "emp_id":       emp_id,
                "emp_name":     str(emp_name) if emp_name else "",
                "location":     location,
                "warehouse":    warehouse,
                "salary":       salary,
                "identity":     identity,
                "last_updated": datetime.utcnow(),
                "last_source":  source_file,
            },
            "$setOnInsert": {
                "status":       "active",
                "joined_month": month,
                "joined_year":  year,
            }
        }
        coll.update_one(filter_q, update, upsert=True)
        upserted += 1

    return upserted
