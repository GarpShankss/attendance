from datetime import datetime
import os
from pymongo import MongoClient
from app import _flatten_doc
from salary_calc import recalculate

MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017')
DB_NAME = os.environ.get('DB_NAME', 'payroll_db')

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

updated = 0
skipped = 0
for doc in db['payroll_records'].find({}):
    row = _flatten_doc(doc)
    calculated = recalculate(row)
    net_pay = calculated.get('net_pay')
    if net_pay is None:
        skipped += 1
        continue

    earnings = {k: v for k, v in calculated.items() if isinstance(v, (int, float, str)) and k.startswith('EARNING -')}
    deductions = {k: v for k, v in calculated.items() if isinstance(v, (int, float, str)) and k.startswith('Deductions -')}
    contributions = {k: v for k, v in calculated.items() if isinstance(v, (int, float, str)) and k.startswith('CONTRIBUTION -')}

    attendance = dict(doc.get('attendance') or {})
    for field in ('ATTENDANCE - Present Days', 'ATTENDANCE - Pay Days'):
        if field in calculated:
            attendance[field] = calculated[field]

    salary = dict(doc.get('salary') or {})
    for field in ('CONTRIBUTION - Service Charge', 'CONTRIBUTION - T Shirt', 'CONTRIBUTION - Shoes', 'CONTRIBUTION - Uniform Charges'):
        if field in row:
            salary[field] = row[field]

    db['payroll_records'].update_one(
        {'_id': doc['_id']},
        {'$set': {
            'earnings': earnings,
            'deductions': deductions,
            'contributions': contributions,
            'salary': salary,
            'attendance': attendance,
            'net_pay': net_pay,
            'updated_at': datetime.utcnow(),
        }}
    )
    updated += 1

print('updated', updated, 'skipped', skipped)
