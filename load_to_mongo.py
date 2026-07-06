"""
CLI: load file(s) into MongoDB directly, no web UI needed.

Usage:
    python3 load_to_mongo.py RSM_ABB_JUNE_26_WAGES.xlsx FLYJAC_JUNE-26_SALRY_WORKING.xls
    python3 load_to_mongo.py file1.xlsx --db payroll_db --uri mongodb://localhost:27017
"""
import argparse
from pymongo import MongoClient
from mongo_loader import load_workbook_to_mongo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--uri", default="mongodb://localhost:27017")
    ap.add_argument("--db", default="payroll_db")
    args = ap.parse_args()

    db = MongoClient(args.uri)[args.db]
    for path in args.files:
        for s in load_workbook_to_mongo(path, db):
            print(f"  [{args.db}.{s['collection']}] <- {s['sheet']} : "
                  f"{s['rows']} rows, {len(s['columns'])} cols")
    print("Done.")


if __name__ == "__main__":
    main()
