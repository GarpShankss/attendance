"""
FastAPI backend.

  GET    /warehouses                   {location: [warehouse, ...]} - the filter/assignment options
  POST   /upload/preview               upload a file -> parses it, returns sheet names/row counts + a token
  POST   /upload/confirm               {token, location, mapping:{sheet: warehouse|""}} -> loads into Mongo
  GET    /collections                  list collections, each with its location/warehouse/sheet tag
  GET    /collections/{name}           {columns, rows} - columns read live from the data, nothing hardcoded
  PATCH  /collections/{name}/{row_id}  edit one cell: body {"column":, "value":}
  GET    /data?location=&warehouse=    combined rows across every collection matching that tag

Run:
    uvicorn app:app --reload
Then open http://localhost:8000
"""
import os, shutil, tempfile, uuid
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient

from mongo_loader import load_sheet_into_collection, guess_warehouse
from parse_excel import parse_workbook, drop_empty_columns
from warehouses_config import LOCATIONS
from salary_calc import recalculate
from payroll_settings import get_config, save_config
from payroll_records import generate_monthly_payroll, get_payroll

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "payroll_db")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

app = FastAPI()

# internal/meta fields - never shown as a data column, never editable
HIDDEN_FIELDS = {"_id", "_row_id", "_source_file", "_sheet", "_location", "_warehouse",
                 "_upload_month", "_upload_year", "created_at", "updated_at", "status", "identity",
                 "emp_name", "month", "year", "location", "warehouse"}

# token -> temp file path, held between /upload/preview and /upload/confirm
PENDING_UPLOADS = {}


@app.get("/debug/trace/{name}/{row_id}")
def debug_trace(name: str, row_id: int):
    """
    Full data-flow trace for a single row.
    Shows the exact values at every stage: MongoDB raw -> API response -> recalculate inputs.
    """
    import re as _re
    from salary_calc import COLUMN_MAP, READONLY_FIELDS

    TRACE_COLS = [
        "FIXED - Working Days",
        "FIXED - Basic",
        "FIXED - DA",
        "FIXED - Other Allows",
        "FIXED - Leave With wages",
        "FIXED - Bonus @8.33%",
        "FIXED - Total",
        "ATTENDANCE - Pay Days",
        "ATTENDANCE - Present Days",
    ]

    if name not in db.list_collection_names():
        raise HTTPException(404, "Unknown collection")
    doc = db[name].find_one({"_row_id": row_id})
    if not doc:
        raise HTTPException(404, "Row not found")

    def _norm(k):
        return _re.sub(r'[\s]+', ' ', str(k)).strip()

    # Stage 1: raw MongoDB document
    stage1 = {}
    for col in TRACE_COLS:
        # try exact, then normalised
        val = doc.get(col)
        if val is None:
            for k, v in doc.items():
                if _norm(k) == _norm(col):
                    val = v
                    break
        stage1[col] = {"value": val, "type": type(val).__name__}

    # Stage 2: what the API /data endpoint would send (string conversion happens in JSON)
    # Values go through no transformation — same as stage 1 for numeric fields

    # Stage 3: what recalculate() receives after save_row preprocessing
    def _clean_key(k):
        return _re.sub(r'[\s]+', ' ', str(k)).strip()
    simulated_payload = {_clean_key(k): v for k, v in doc.items()
                         if k not in {"_id", "_row_id", "_source_file", "_sheet",
                                      "_location", "_warehouse", "_upload_month", "_upload_year"}}
    stage3 = {}
    for col in TRACE_COLS:
        val = simulated_payload.get(_clean_key(col))
        stage3[col] = {"value": val, "type": type(val).__name__}

    # Stage 4: what salary_calc.get() reads via COLUMN_MAP
    from salary_calc import get as sc_get, _num
    stage4 = {}
    for logical, col in COLUMN_MAP.items():
        if col in TRACE_COLS:
            stage4[col] = {
                "logical_name": logical,
                "is_readonly": logical in READONLY_FIELDS,
                "value_via_get": sc_get(simulated_payload, logical),
            }

    # Check: are any FIXED columns overwritten by recalculate?
    from salary_calc import recalculate
    result = recalculate(dict(simulated_payload))
    result.pop("__calc_log__", None)
    stage5_changes = {}
    for col in TRACE_COLS:
        before = simulated_payload.get(_clean_key(col))
        after  = result.get(_clean_key(col))
        if before != after:
            stage5_changes[col] = {"before": before, "after": after}

    # All stored keys that look like FIXED columns
    all_fixed_keys = {k: v for k, v in doc.items() if "FIXED" in str(k).upper()}

    return {
        "row_id": row_id,
        "collection": name,
        "stage1_mongodb_raw": stage1,
        "stage3_after_key_clean": stage3,
        "stage4_salary_calc_reads": stage4,
        "stage5_fixed_cols_changed_by_recalculate": stage5_changes,
        "all_fixed_keys_in_db": {str(k): str(v) for k, v in all_fixed_keys.items()},
        "note": "If stage5 shows any changes, recalculate() is overwriting FIXED columns"
    }


@app.get("/debug/row/{name}/{row_id}")
def debug_row(name: str, row_id: int):
    """Show raw stored values + what salary_calc reads for the key input fields."""
    from salary_calc import COLUMN_MAP, get as sc_get, _num
    if name not in db.list_collection_names():
        raise HTTPException(404, "Unknown collection")
    doc = db[name].find_one({"_row_id": row_id})
    if not doc:
        raise HTTPException(404, "Row not found")

    # Show every field that salary_calc cares about
    input_fields = [
        "working_days", "pay_days",
        "fixed_basic", "fixed_da", "fixed_other", "fixed_leave", "fixed_bonus",
        "fixed_service_charge", "uniform", "advance",
    ]
    inputs = {}
    for field in input_fields:
        col = COLUMN_MAP.get(field)
        raw = doc.get(col) if col else None
        inputs[field] = {
            "column": col,
            "raw_value": str(raw),
            "raw_type": type(raw).__name__,
            "parsed_num": _num(raw),
        }

    # All keys in the doc (excluding internal)
    all_keys = {k: str(v) for k, v in doc.items() if not k.startswith("_") or k == "_row_id"}
    return {"row_id": row_id, "collection": name, "inputs": inputs, "all_fields": all_keys}


@app.post("/admin/sanitize-keys")
def sanitize_keys():
    """One-time: rename any keys containing newlines/tabs in all collections."""
    import re as _re
    def _clean(k):
        return _re.sub(r'[\s]+', ' ', str(k)).strip()
    total_docs = 0
    for name in db.list_collection_names():
        if name.startswith("system."):
            continue
        for doc in db[name].find({}):
            dirty = {k: v for k, v in doc.items()
                     if k != "_id" and _clean(k) != k}
            if not dirty:
                continue
            # unset old keys, set cleaned keys
            unset = {k: "" for k in dirty}
            setval = {_clean(k): v for k, v in dirty.items()}
            db[name].update_one(
                {"_id": doc["_id"]},
                {"$unset": unset, "$set": setval}
            )
            total_docs += 1
    return {"sanitized_docs": total_docs}


@app.get("/admin/payroll-settings")
def get_payroll_settings():
    return get_config()


@app.put("/admin/payroll-settings")
def update_payroll_settings(payload: dict = Body(...)):
    try:
        saved = save_config(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return saved


@app.post("/admin/backfill-months")
def backfill_months():
    """One-time: stamp _upload_month/_upload_year on all existing docs that lack it."""
    now = datetime.utcnow()
    total = 0
    for name in db.list_collection_names():
        if name.startswith("system."):
            continue
        result = db[name].update_many(
            {"_upload_month": {"$exists": False}},
            {"$set": {"_upload_month": now.month, "_upload_year": now.year}}
        )
        total += result.modified_count
    return {"patched": total}


@app.get("/warehouses")
def get_warehouses():
    return LOCATIONS


@app.post("/upload/preview")
async def upload_preview(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1] or ".xlsx"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(tmp_path, "wb") as out:
        shutil.copyfileobj(file.file, out)

    parsed = parse_workbook(tmp_path)
    all_warehouses = sorted({wh for whs in LOCATIONS.values() for wh in whs})
    sheets = []
    for sheet_name, d in parsed.items():
        columns, rows = drop_empty_columns(d["columns"], d["rows"])
        if not rows:
            continue
        sheets.append({
            "sheet_name": sheet_name, "rows": len(rows), "columns": len(columns),
            "guess": guess_warehouse(sheet_name, all_warehouses),
        })

    if not sheets:
        os.remove(tmp_path)
        raise HTTPException(400, "No readable sheets found in that file.")

    token = uuid.uuid4().hex
    PENDING_UPLOADS[token] = {"path": tmp_path, "filename": file.filename}
    return {"token": token, "sheets": sheets}


@app.post("/upload/confirm")
def upload_confirm(payload: dict = Body(...)):
    token = payload.get("token")
    location = payload.get("location")
    mapping = payload.get("mapping", {})  # {sheet_name: warehouse_name_or_empty}
    pending = PENDING_UPLOADS.pop(token, None)
    if not pending:
        raise HTTPException(400, "Unknown or expired upload token - re-upload the file.")

    try:
        parsed = parse_workbook(pending["path"])
        summary = []
        for sheet_name, d in parsed.items():
            warehouse = mapping.get(sheet_name) or None
            result = load_sheet_into_collection(
                sheet_name, d, db, location=location,
                warehouse=warehouse, source_file=pending["filename"],
            )
            if result["rows"]:
                summary.append(result)
    finally:
        os.remove(pending["path"])

    return {"loaded": summary}


@app.get("/collections")
def list_collections():
    names = [n for n in db.list_collection_names() if not n.startswith("system.")]
    out = []
    for name in sorted(names):
        sample = db[name].find_one()
        out.append({
            "name": name,
            "sheet": sample.get("_sheet") if sample else None,
            "location": sample.get("_location") if sample else None,
            "warehouse": sample.get("_warehouse") if sample else None,
            "row_count": db[name].count_documents({}),
        })
    return {"collections": out}


def _flatten_nested_keys(value, parent_key=None):
    result = {}
    if not isinstance(value, dict):
        return result
    for k, v in value.items():
        new_key = f"{parent_key}.{k}" if parent_key else str(k)
        if isinstance(v, dict):
            result.update(_flatten_nested_keys(v, new_key))
        else:
            result[new_key] = v
    return result


def _normalize_subdoc(value):
    if not isinstance(value, dict):
        return value
    normalized = {}
    for k, v in value.items():
        if isinstance(v, dict):
            normalized.update(_flatten_nested_keys(v, str(k)))
        else:
            normalized[str(k)] = v
    return normalized


def _flatten_doc(doc: dict):
    flat = {}
    for container in ("identity", "salary", "attendance", "earnings", "deductions", "contributions"):
        value = doc.get(container)
        if isinstance(value, dict):
            flat.update(_normalize_subdoc(value))
    for k, v in doc.items():
        if k in {"_id", "identity", "salary", "attendance", "earnings", "deductions", "contributions"}:
            continue
        if k not in flat:
            flat[k] = v

    # Recursively flatten any nested dictionaries that might have been created by MongoDB's dot-notation
    fully_flat = _flatten_nested_keys(flat)

    if "net_pay" in fully_flat and "Net Pay" not in fully_flat:
        fully_flat["Net Pay"] = fully_flat["net_pay"]
    if "Net Pay" in fully_flat and "net_pay" in fully_flat:
        del fully_flat["net_pay"]
    return fully_flat


def _row_filter(name: str, row_id: str):
    if name == "payroll_records":
        if row_id.isdigit():
            return {"_row_id": int(row_id)}
        return {"emp_id": row_id}
    try:
        return {"_row_id": int(row_id)}
    except ValueError:
        return {"_row_id": row_id}


def _map_payroll_updates(name: str, to_write: dict, stored: dict = None):
    if name != "payroll_records":
        return to_write

    mapped = {}
    earnings_updates = {}
    deductions_updates = {}
    contributions_updates = {}
    salary_updates = {}
    attendance_updates = {}
    identity_updates = {}

    for key, value in to_write.items():
        if key.startswith("EARNING -"):
            earnings_updates[key] = value
        elif key.startswith("Deductions -"):
            deductions_updates[key] = value
        elif key == "Net Pay":
            mapped["net_pay"] = value
        elif key in ("ATTENDANCE - Present Days", "ATTENDANCE - Pay Days"):
            attendance_updates[key] = value
        elif key in ("CONTRIBUTION - Service Charge",
                     "CONTRIBUTION - T Shirt",
                     "CONTRIBUTION - Shoes",
                     "CONTRIBUTION - Uniform Charges"):
            salary_updates[key] = value
        elif key.startswith("CONTRIBUTION -"):
            contributions_updates[key] = value
        elif key in ("emp_id", "emp_name", "location", "warehouse", "month", "year", "status"):
            mapped[key] = value
        else:
            identity_updates[key] = value

    def _merge(subdoc_name, updates):
        if not updates:
            return
        existing = {}
        if stored and isinstance(stored.get(subdoc_name), dict):
            existing.update(stored[subdoc_name])
        existing.update(updates)
        mapped[subdoc_name] = existing

    _merge("earnings", earnings_updates)
    _merge("deductions", deductions_updates)
    _merge("contributions", contributions_updates)
    _merge("salary", salary_updates)
    _merge("attendance", attendance_updates)
    _merge("identity", identity_updates)
    return mapped


@app.get("/collections/{name}")
def get_collection(name: str):
    if name not in db.list_collection_names():
        raise HTTPException(404, "Unknown collection")
    sort_field = "_row_id" if name != "payroll_records" else "emp_id"
    docs = list(db[name].find().sort(sort_field, 1))
    if not docs:
        return {"columns": [], "rows": []}

    flat_docs = [_flatten_doc(d) for d in docs]
    columns, seen = [], set()
    for d in flat_docs:
        for k in d.keys():
            if k not in HIDDEN_FIELDS and k not in seen:
                seen.add(k)
                columns.append(k)

    rows = [{"_row_id": d.get("_row_id", d.get("emp_id")), **{c: d.get(c) for c in columns}} for d in flat_docs]
    return {"columns": columns, "rows": rows}


@app.get("/data/months")
def get_available_months(location: str = None, warehouse: str = None):
    """Return distinct (month, year) pairs from payroll records only."""
    query = {}
    if location:  query["location"]  = location
    if warehouse: query["warehouse"] = warehouse

    pairs = set()
    for doc in db["payroll_records"].find(query, {"month": 1, "year": 1}):
        if doc.get("month") and doc.get("year"):
            pairs.add((int(doc["month"]), int(doc["year"])))

    result = sorted(pairs, key=lambda x: (x[1], x[0]))
    return {"months": [{"month": m, "year": y} for m, y in result]}


@app.get("/data")
def get_filtered_data(location: str = None, warehouse: str = None,
                      month: int = None, year: int = None):
    """Combined rows across every collection matching location/warehouse/month/year."""
    columns, seen = [], set()
    rows = []

    if month and year:
        matching = ["payroll_records"]
    else:
        matching = []
        names = [n for n in db.list_collection_names() if not n.startswith("system.")]
        for name in names:
            sample = db[name].find_one()
            if not sample:
                continue
            if name == "payroll_records":
                if location and sample.get("location") != location:
                    continue
                if warehouse and sample.get("warehouse") != warehouse:
                    continue
            else:
                if location and sample.get("_location") != location:
                    continue
                if warehouse and sample.get("_warehouse") != warehouse:
                    continue
            matching.append(name)

    for name in matching:
        if name == "payroll_records":
            query = {}
            if month and year:
                query["month"] = month
                query["year"] = year
            if location:
                query["location"] = location
            if warehouse:
                query["warehouse"] = warehouse
        else:
            query = {}
            if month:
                query["_upload_month"] = month
            if year:
                query["_upload_year"] = year

        sort_field = "_row_id" if name != "payroll_records" else "emp_id"
        for d in db[name].find(query).sort(sort_field, 1):
            flat = _flatten_doc(d)
            for k in flat.keys():
                if k not in HIDDEN_FIELDS and k not in seen:
                    seen.add(k)
                    columns.append(k)
            rows.append({
                "_collection": name,
                "_row_id": d.get("_row_id", d.get("emp_id")),
                "_sheet": d.get("_sheet"), "_warehouse": d.get("_warehouse"),
                "_upload_month": d.get("_upload_month"), "_upload_year": d.get("_upload_year"),
                **{c: flat.get(c) for c in columns},
            })
    for r in rows:
        for c in columns:
            r.setdefault(c, None)
    return {"collections": matching, "columns": columns, "rows": rows}


@app.post("/collections/{name}/rows")
def add_row(name: str, payload: dict = Body(...)):
    if name not in db.list_collection_names():
        raise HTTPException(404, "Unknown collection")
    data = {k: v for k, v in payload.items() if k not in HIDDEN_FIELDS}
    if not data:
        raise HTTPException(400, "No data provided")
    sample = db[name].find_one()
    last_id = db[name].find_one(sort=[("_row_id", -1)])
    next_id = (last_id["_row_id"] + 1) if last_id else 1
    now = datetime.utcnow()
    calculated = recalculate(data)
    doc = {**calculated, "_row_id": next_id,
           "_location": sample.get("_location") if sample else None,
           "_warehouse": sample.get("_warehouse") if sample else None,
           "_sheet": sample.get("_sheet") if sample else None,
           "_source_file": "manual",
           "_upload_month": now.month, "_upload_year": now.year}
    db[name].insert_one(doc)
    return {"ok": True, "_row_id": next_id}


@app.put("/collections/{name}/{row_id}")
def save_row(name: str, row_id: str, payload: dict = Body(...)):
    """Recalculate and save. Reads FIXED inputs from MongoDB (never from payload)
    so stale/string frontend values can never corrupt salary inputs."""
    if name not in db.list_collection_names():
        raise HTTPException(404, "Unknown collection")
    import re as _re
    from salary_calc import COLUMN_MAP, READONLY_FIELDS

    def _clean_key(k):
        return _re.sub(r'[\s]+', ' ', str(k)).strip()

    # Fetch the authoritative row from MongoDB
    stored = db[name].find_one(_row_filter(name, row_id))
    if not stored:
        raise HTTPException(404, "row not found")

    flattened = _flatten_doc(stored)
    # Build base from stored document (clean keys)
    base = {_clean_key(k): v for k, v in flattened.items() if k != "_id"}

    # Apply only non-readonly edits from payload on top of stored values
    readonly_cols = {_clean_key(COLUMN_MAP[f]) for f in READONLY_FIELDS if f in COLUMN_MAP}
    for k, v in payload.items():
        ck = _clean_key(k)
        if ck not in readonly_cols and ck not in {_clean_key(h) for h in HIDDEN_FIELDS}:
            base[ck] = v

    # Run salary calculation
    calculated = recalculate(base)
    calc_log = calculated.pop("__calc_log__", [])

    # Only write output (non-readonly) columns back to MongoDB
    output_col_names = {
        _clean_key(COLUMN_MAP[f])
        for f in COLUMN_MAP
        if f not in READONLY_FIELDS and COLUMN_MAP[f]
    }
    to_write = {_clean_key(k): v for k, v in calculated.items()
                if _clean_key(k) in output_col_names}

    # Persist editable input fields as well (advance/service/shoes/tshirt/uniform)
    editable_inputs = {}
    for k, v in payload.items():
        ck = _clean_key(k)
        if ck not in readonly_cols and ck not in {_clean_key(h) for h in HIDDEN_FIELDS}:
            editable_inputs[ck] = v

    updates = {}
    if to_write:
        updates.update(_map_payroll_updates(name, to_write, stored=stored))
    if editable_inputs:
        updates.update(_map_payroll_updates(name, editable_inputs, stored=stored))

    if not updates:
        raise HTTPException(400, "No columns to update")

    db[name].update_one(_row_filter(name, row_id), {"$set": updates})
    updated = db[name].find_one(_row_filter(name, row_id))
    clean = _flatten_doc(updated)
    clean.pop("_id", None)
    return {"ok": True, "row": clean, "calc_log": calc_log}


@app.post("/collections/{name}/{row_id}/recalculate")
def recalculate_row(name: str, row_id: str):
    if name not in db.list_collection_names():
        raise HTTPException(404, "Unknown collection")
    return save_row(name, row_id, {})


@app.delete("/employee/{location}/{warehouse}/{emp_id}")
def delete_employee(location: str, warehouse: str, emp_id: str):
    att_res = db[ATTENDANCE_COLL].delete_many({
        "location": location,
        "warehouse": warehouse,
        "emp_id": emp_id,
    })
    pay_res = db["payroll_records"].delete_many({
        "location": location,
        "warehouse": warehouse,
        "emp_id": emp_id,
    })
    return {
        "ok": True,
        "attendance_deleted": att_res.deleted_count,
        "payroll_deleted": pay_res.deleted_count,
    }


@app.patch("/collections/{name}/{row_id}")
def update_cell(name: str, row_id: str, payload: dict):
    column, value = payload.get("column"), payload.get("value")
    if column is None:
        raise HTTPException(400, "payload needs {column, value}")
    if column in HIDDEN_FIELDS:
        raise HTTPException(400, "that field isn't editable")
    result = db[name].update_one(_row_filter(name, row_id), {"$set": {column: value}})
    if result.matched_count == 0:
        raise HTTPException(404, "row not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Excel Download endpoints
# ---------------------------------------------------------------------------
from fastapi.responses import StreamingResponse
import io
import openpyxl
from openpyxl.utils import get_column_letter

@app.get("/payroll/download")
def download_payroll(month: int, year: int, location: str = None, warehouse: str = None):
    query = {"month": month, "year": year}
    if location:  query["location"]  = location
    if warehouse: query["warehouse"] = warehouse

    docs = list(db["payroll_records"].find(query).sort("emp_id", 1))
    if not docs:
        raise HTTPException(404, "No payroll records found for the selected criteria.")

    flat_docs = [_flatten_doc(d) for d in docs]
    
    # Get columns (excluding hidden fields)
    columns, seen = [], set()
    for d in flat_docs:
        for k in d.keys():
            if k not in HIDDEN_FIELDS and k not in seen:
                seen.add(k)
                columns.append(k)

    # Create Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Payroll_{month}_{year}"

    # Write headers
    ws.append(columns)

    # Write rows
    for d in flat_docs:
        row_data = [d.get(c) for c in columns]
        ws.append(row_data)

    # Auto-fit columns
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 2, 10)

    # Save to stream
    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    filename = f"payroll_{location or 'all'}_{warehouse or 'all'}_{month}_{year}.xlsx"
    return StreamingResponse(
        file_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/attendance/download")
def download_attendance(month: int, year: int, location: str = None, warehouse: str = None):
    from datetime import datetime, timedelta
    
    # Get employees
    emp_query = {"status": "active"}
    if location:  emp_query["location"]  = location
    if warehouse: emp_query["warehouse"] = warehouse
    
    employees = list(db["employee_master"].find(emp_query).sort("emp_id", 1))
    if not employees:
        raise HTTPException(404, "No active employees found for the selected criteria.")
        
    # Generate 26th-to-25th payroll period dates
    if month == 1:
        start_year = year - 1
        start_month = 12
    else:
        start_year = year
        start_month = month - 1
        
    start_date = datetime(start_year, start_month, 26)
    end_date = datetime(year, month, 25)
    
    dates = []
    curr = start_date
    while curr <= end_date:
        dates.append(curr)
        curr += timedelta(days=1)
        
    # Define columns
    columns = ["Emp ID", "Employee Name", "Location", "Warehouse"] + [d.strftime("%Y-%m-%d") for d in dates] + ["Present Days", "Absent Days", "Pay Days"]
    
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Attendance_{month}_{year}"
    ws.append(columns)
    
    for emp in employees:
        emp_id = emp["emp_id"]
        # Find attendance doc
        att_doc = db["_attendance"].find_one({
            "emp_id": emp_id, "month": month, "year": year,
            "location": emp["location"], "warehouse": emp["warehouse"]
        })
        
        days_data = att_doc.get("days", {}) if att_doc else {}
        row_data = [
            emp_id,
            emp.get("emp_name", ""),
            emp.get("location", ""),
            emp.get("warehouse", "")
        ]
        # Add daily attendance
        for d in dates:
            key = d.strftime("%Y-%m-%d")
            fallback = str(d.day)
            val = days_data.get(key)
            if val is None:
                val = days_data.get(fallback, "")
            row_data.append(str(val or "").upper())
            
        # Add totals
        row_data.extend([
            att_doc.get("present_days", 0) if att_doc else 0,
            att_doc.get("absent_days", 0) if att_doc else 0,
            att_doc.get("pay_days", 0) if att_doc else 0
        ])
        ws.append(row_data)
        
    # Auto-fit columns
    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 2, 6)
        
    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)
    
    filename = f"attendance_{location or 'all'}_{warehouse or 'all'}_{month}_{year}.xlsx"
    return StreamingResponse(
        file_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ---------------------------------------------------------------------------
# Payroll Records endpoints
# ---------------------------------------------------------------------------

@app.post("/payroll/generate")
def generate_payroll(payload: dict = Body(...)):
    """
    Generate monthly payroll records from the employee master.
    Body: {month, year, location?, warehouse?}
    Existing records for that month are never overwritten.
    """
    month    = int(payload.get("month"))
    year     = int(payload.get("year"))
    location = payload.get("location", "")
    warehouse = payload.get("warehouse", "")

    if not month or not year:
        raise HTTPException(400, "month and year are required")

    result = generate_monthly_payroll(month, year, location, warehouse, db)
    return result


@app.get("/payroll")
def list_payroll(month: int, year: int,
                location: str = None, warehouse: str = None):
    """
    Return all payroll records for a given month/year.
    Optionally filter by location and warehouse.
    """
    records = get_payroll(month, year, location, warehouse, db)
    return {"records": records, "count": len(records)}


# ---------------------------------------------------------------------------
# Employee Master endpoints
# ---------------------------------------------------------------------------

@app.get("/employees")
def list_employees(location: str = None, warehouse: str = None, status: str = None):
    """List all employees from the permanent master."""
    query = {}
    if location:  query["location"]  = location
    if warehouse: query["warehouse"] = warehouse
    if status:    query["status"]    = status
    docs = list(db["employee_master"].find(query, {"_id": 0}).sort("emp_id", 1))
    return {"employees": docs, "count": len(docs)}


@app.post("/employees")
def create_employee(payload: dict = Body(...)):
    """
    Manually add a new employee to the master.
    Body: {
        emp_id, emp_name, location, warehouse,
        joined_month, joined_year,
        salary: { "FIXED - Basic": ..., ... },
        identity: { "Department": ..., ... }
    }
    Returns error if emp_id already exists for that location+warehouse.
    """
    required = ["emp_id", "emp_name", "location", "warehouse",
                "joined_month", "joined_year"]
    for f in required:
        if not payload.get(f):
            raise HTTPException(400, f"Missing required field: {f}")

    emp_id    = str(payload["emp_id"]).strip()
    location  = str(payload["location"]).strip()
    warehouse = str(payload["warehouse"]).strip()

    existing = db["employee_master"].find_one(
        {"emp_id": emp_id, "location": location, "warehouse": warehouse}
    )
    if existing:
        raise HTTPException(409, f"Employee {emp_id!r} already exists for "
                                 f"{location} / {warehouse}")

    doc = {
        "emp_id":       emp_id,
        "emp_name":     str(payload["emp_name"]).strip(),
        "location":     location,
        "warehouse":    warehouse,
        "status":       "active",
        "joined_month": int(payload["joined_month"]),
        "joined_year":  int(payload["joined_year"]),
        "salary":       payload.get("salary", {}),
        "identity":     payload.get("identity", {}),
        "last_updated": datetime.utcnow(),
        "last_source":  "manual",
    }
    db["employee_master"].insert_one(doc)
    return {"ok": True, "emp_id": emp_id}


@app.get("/employees/{emp_id}")
def get_employee(emp_id: str, location: str = None, warehouse: str = None):
    """Get a single employee from the master."""
    query = {"emp_id": emp_id}
    if location:  query["location"]  = location
    if warehouse: query["warehouse"] = warehouse
    doc = db["employee_master"].find_one(query, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Employee {emp_id!r} not found")
    return doc


# ---------------------------------------------------------------------------
# Attendance endpoints
# ---------------------------------------------------------------------------

ATTENDANCE_COLL = "_attendance"

PAY_STATUSES = {"p", "sl", "pl", "hp"}   # counts as present/paid
ABSENT_STATUS = "a"


def _att_key(emp_id: str, month: int, year: int, collection: str):
    return {"emp_id": emp_id, "month": month, "year": year, "collection": collection}


def _parse_attendance_date(key: str):
    if not isinstance(key, str):
        return None
    key = key.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(key, fmt).date()
        except ValueError:
            continue
    return None


def _calc_attendance(days: dict):
    """Given {"2026-05-26": "P", "2026-05-27": "A", ...} return present_days, absent_days, pay_days."""
    present = 0
    absent = 0
    for key, value in days.items():
        norm = str(value or "").strip().lower()
        if norm in PAY_STATUSES:
            present += 1
        elif norm == ABSENT_STATUS:
            absent += 1
        else:
            dt = _parse_attendance_date(str(key))
            if dt and dt.weekday() == 6:  # Sunday is weekoff and paid if left blank
                present += 1
    return present, absent, present   # pay_days == present_days


def _flatten_salary_doc(doc: dict):
    """Flatten payroll record fields for salary recalculation."""
    row = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if k in {"salary", "attendance"} and isinstance(v, dict):
            row.update(v)
        else:
            row[k] = v
    return row


@app.get("/attendance")
def get_attendance(collection: str, month: int, year: int):
    """Return all attendance records for a collection+month+year."""
    records = list(db[ATTENDANCE_COLL].find(
        {"month": month, "year": year, "collection": collection},
        {"_id": 0}
    ))
    return {"records": records}


def _ensure_payroll_record(emp_id: str, location: str, warehouse: str,
                           month: int, year: int):
    payroll_key = {"emp_id": emp_id, "location": location,
                   "warehouse": warehouse, "month": month, "year": year}
    payroll_doc = db["payroll_records"].find_one(payroll_key)
    if payroll_doc:
        return payroll_doc

    result = generate_monthly_payroll(month, year, location, warehouse, db)
    return db["payroll_records"].find_one(payroll_key)


@app.put("/attendance/{location}/{warehouse}/{emp_id}")
def save_attendance(location: str, warehouse: str, emp_id: str,
                    payload: dict = Body(...)):
    """
    Upsert attendance for one employee.
    payload: {month, year, days: {"1":"P", "2":"A", ...}}
    Stores against location+warehouse+emp_id (not salary collection).
    Salary recalc is handled in Step 5.
    """
    month = int(payload["month"])
    year  = int(payload["year"])
    days  = {str(k): str(v).strip().upper() for k, v in payload.get("days", {}).items()}

    present_days, absent_days, pay_days = _calc_attendance(days)

    key = {"emp_id": emp_id, "location": location,
           "warehouse": warehouse, "month": month, "year": year}
    db[ATTENDANCE_COLL].update_one(
        key,
        {"$set": {**key, "days": days,
                  "present_days": present_days,
                  "absent_days":  absent_days,
                  "pay_days":     pay_days,
                  "updated_at":   datetime.utcnow()}},
        upsert=True
    )

    payroll_doc = _ensure_payroll_record(emp_id, location, warehouse, month, year)
    if payroll_doc:
        row = _flatten_salary_doc(payroll_doc)
        row["ATTENDANCE - Present Days"] = present_days
        row["ATTENDANCE - Pay Days"] = pay_days

        calculated = recalculate(row)

        update_fields = {
            "attendance.ATTENDANCE - Present Days": present_days,
            "attendance.ATTENDANCE - Pay Days":     pay_days,
            "ATTENDANCE - Present Days":           present_days,
            "ATTENDANCE - Pay Days":               pay_days,
            "updated_at": datetime.utcnow(),
        }
        for k, v in calculated.items():
            if k not in {"__calc_log__", "_id"} and not isinstance(v, dict):
                if k.startswith("EARNING -") or k.startswith("Deductions -") \
                   or k.startswith("CONTRIBUTION -") or k == "Net Pay":
                    update_fields[k] = v
        if update_fields:
            db["payroll_records"].update_one({"_id": payroll_doc["_id"]}, {"$set": update_fields})

    # Ensure index
    db[ATTENDANCE_COLL].create_index(
        [("emp_id",1),("location",1),("warehouse",1),("month",1),("year",1)],
        unique=True, background=True
    )
    return {"ok": True, "present_days": present_days,
            "absent_days": absent_days, "pay_days": pay_days}


@app.get("/attendance/employees")
def get_attendance_employees(location: str, warehouse: str, month: int, year: int):
    """
    Return employee list for attendance entry from employee_master,
    merged with any existing attendance records for that month/year.
    Driven entirely by employee_master — no salary collection needed.
    """
    query = {"status": "active", "location": location, "warehouse": warehouse}
    masters = list(db["employee_master"].find(query, {"_id": 0}).sort("emp_id", 1))

    employees = []
    for emp in masters:
        emp_id = emp["emp_id"]
        att = db[ATTENDANCE_COLL].find_one(
            {"emp_id": emp_id, "month": month, "year": year,
             "location": location, "warehouse": warehouse},
            {"_id": 0}
        )
        employees.append({
            "emp_id":       emp_id,
            "emp_name":     emp.get("emp_name", ""),
            "days":         att["days"]         if att else {},
            "present_days": att["present_days"] if att else 0,
            "absent_days":  att["absent_days"]  if att else 0,
            "pay_days":     att["pay_days"]     if att else 0,
        })
    return {"employees": employees}


# ---------------------------------------------------------------------------
# Static files — keep LAST
# ---------------------------------------------------------------------------
# Resolve static files path relative to this file's directory
import os
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

