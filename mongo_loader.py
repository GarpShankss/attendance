"""
Shared logic: parse a workbook fresh and load a sheet into MongoDB, tagged
with the location/warehouse the user chose at upload time. Used by app.py.
Nothing about column names is hardcoded here; whatever parse_excel.py finds
in the file is what gets stored.
"""
import re
from datetime import datetime
from parse_excel import parse_workbook, drop_empty_columns
from employee_master import upsert_employees


def slugify(name):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name.strip()).strip("_").lower()
    return s or "sheet"


def guess_warehouse(sheet_name, warehouse_list):
    """Best-effort default for the assignment UI - user can always override."""
    norm_sheet = re.sub(r"[^A-Z0-9]", "", sheet_name.upper())
    candidates = sorted(warehouse_list, key=len, reverse=True)  # longest name first
    for wh in candidates:
        norm_wh = re.sub(r"[^A-Z0-9]", "", wh.upper())
        if norm_wh and norm_wh in norm_sheet:
            return wh
    return None


from salary_calc import normalize_row

def load_sheet_into_collection(sheet_name, sheet_data, db, location, warehouse, source_file):
    """Load ONE already-parsed sheet into its own collection (cleared first)."""
    columns, rows = drop_empty_columns(sheet_data["columns"], sheet_data["rows"])
    coll_name = slugify(sheet_name)
    coll = db[coll_name]
    coll.delete_many({})
    docs = []
    for i, row in enumerate(rows, start=1):
        doc = normalize_row(dict(row))
        doc["_row_id"] = i
        doc["_source_file"] = source_file
        doc["_sheet"] = sheet_name
        doc["_location"] = location
        doc["_warehouse"] = warehouse or None
        doc["_upload_month"] = datetime.utcnow().month
        doc["_upload_year"] = datetime.utcnow().year
        docs.append(doc)
    if docs:
        coll.insert_many(docs)
        coll.create_index("_row_id")

    # Upsert into permanent employee master
    upsert_employees(
        rows=[{k: v for k, v in doc.items() if not k.startswith("_")} for doc in docs],
        location=location,
        warehouse=warehouse or "",
        source_file=source_file,
        month=datetime.utcnow().month,
        year=datetime.utcnow().year,
        db=db,
    )

    return {"collection": coll_name, "sheet": sheet_name, "rows": len(docs),
            "columns": columns, "location": location, "warehouse": warehouse}


def load_workbook_to_mongo(path, db, source_file=None):
    """
    CLI convenience path (no location/warehouse tagging) - kept for quick
    bulk loads via load_to_mongo.py. The UI upload flow uses
    load_sheet_into_collection() above instead, so it can tag each sheet.
    """
    parsed = parse_workbook(path)
    source_file = source_file or path.rsplit("/", 1)[-1]
    summary = []
    for sheet_name, d in parsed.items():
        result = load_sheet_into_collection(sheet_name, d, db, location=None,
                                             warehouse=None, source_file=source_file)
        if result["rows"]:
            summary.append(result)
    return summary
