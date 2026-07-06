import sys
from pymongo import MongoClient

def run_migration():
    client = MongoClient('mongodb://localhost:27017')
    db = client['payroll_db']
    
    print("Starting database migration...")
    
    # 1. Migrate employee_master
    master_count = 0
    for doc in db['employee_master'].find():
        identity = doc.get('identity', {})
        salary = doc.get('salary', {})
        
        updated = False
        
        # Move keys starting with CONTRIBUTION - or Deductions - from identity to salary
        to_move = [k for k in identity if k.startswith('CONTRIBUTION -') or k.startswith('Deductions -')]
        for k in to_move:
            salary[k] = identity.pop(k)
            updated = True
            
        # Rename Net Pay_2 or Net Pay(2) or net_pay(2) to Phone Number in identity
        for old_key in ('Net Pay_2', 'Net Pay(2)', 'net_pay(2)'):
            if old_key in identity:
                identity['Phone Number'] = identity.pop(old_key)
                updated = True
                
        if updated:
            db['employee_master'].update_one(
                {'_id': doc['_id']},
                {'$set': {'identity': identity, 'salary': salary}}
            )
            master_count += 1
            
    print(f"Updated {master_count} records in employee_master.")
    
    # 2. Migrate payroll_records
    payroll_count = 0
    for doc in db['payroll_records'].find():
        identity = doc.get('identity', {})
        salary = doc.get('salary', {})
        deductions = doc.get('deductions', {})
        contributions = doc.get('contributions', {})
        
        updated = False
        
        # Move keys starting with CONTRIBUTION - or Deductions - from identity to correct subdocuments
        to_move = [k for k in list(identity.keys()) if k.startswith('CONTRIBUTION -') or k.startswith('Deductions -')]
        for k in to_move:
            val = identity.pop(k)
            if k in ("CONTRIBUTION - Service Charge", "CONTRIBUTION - T Shirt", "CONTRIBUTION - Shoes", "CONTRIBUTION - Uniform Charges"):
                salary[k] = val
            elif k.startswith('CONTRIBUTION -'):
                contributions[k] = val
            elif k.startswith('Deductions -'):
                deductions[k] = val
            updated = True
            
        # Rename Net Pay_2 or Net Pay(2) or net_pay(2) to Phone Number in identity
        for old_key in ('Net Pay_2', 'Net Pay(2)', 'net_pay(2)'):
            if old_key in identity:
                identity['Phone Number'] = identity.pop(old_key)
                updated = True
                
        if updated:
            db['payroll_records'].update_one(
                {'_id': doc['_id']},
                {'$set': {
                    'identity': identity, 
                    'salary': salary,
                    'deductions': deductions,
                    'contributions': contributions
                }}
            )
            payroll_count += 1
            
    print(f"Updated {payroll_count} records in payroll_records.")
    
    # 3. Migrate other collections (rename Net Pay_2 at top level)
    other_count = 0
    for coll_name in db.list_collection_names():
        if coll_name in ('employee_master', 'payroll_records', 'system.indexes', '_attendance'):
            continue
            
        for old_key in ('Net Pay_2', 'Net Pay(2)', 'net_pay(2)'):
            res = db[coll_name].update_many(
                {old_key: {'$exists': True}},
                {'$rename': {old_key: 'Phone Number'}}
            )
            if res.modified_count > 0:
                other_count += res.modified_count
                
    print(f"Updated {other_count} records across other collections.")
    print("Migration completed successfully!")

if __name__ == '__main__':
    run_migration()
