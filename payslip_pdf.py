import os
import datetime
import tempfile
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
from s3_utils import upload_with_cleanup, build_pdf_filename

def number_to_words(n):
    if n == 0 or n is None:
        return "Zero Rupees Only"
    return f"{n} Rupees Only"

def generate_payslip_pdf(payroll_record: dict, db=None) -> bytes:
    """
    Renders the Jinja2 HTML template and converts it to PDF using Playwright.
    """
    identity = payroll_record.get('identity', {})
    
    # Fallback to employee_master if identity is missing or incomplete
    if db is not None:
        master = db["employee_master"].find_one({"emp_id": payroll_record.get('emp_id')})
        if master:
            master_identity = master.get("identity", {})
            for k, v in master_identity.items():
                if k not in identity or not identity[k]:
                    identity[k] = v
                    
    # Employee info extraction
    emp = {
        'month': payroll_record.get('month'),
        'year': payroll_record.get('year'),
        'emp_id': payroll_record.get('emp_id', identity.get('Sl No', '')),
        'designation': identity.get('Designation', ''),
        'emp_name': payroll_record.get('emp_name', identity.get('Employee Name', '')),
        'warehouse': payroll_record.get('warehouse', identity.get('Department', '')),
        'uan_number': identity.get('UAN', ''),
        'bank_account_number': identity.get('ACCOUNT', ''),
        'esi_number': identity.get('ESIC', ''),
        'doj': identity.get('DOJ', ''),
        'working_days': payroll_record.get('FIXED - Working Days', 30),
        'pay_days': payroll_record.get('ATTENDANCE - Pay Days', identity.get('ATTENDANCE - Pay Days', 0)),
        
        # Fixed
        'fixed_basic': float(payroll_record.get('FIXED - Basic', 0)),
        'fixed_da': float(payroll_record.get('FIXED - DA', 0)),
        'fixed_other': float(payroll_record.get('FIXED - Other Allows', 0)),
        'fixed_leave': float(payroll_record.get('FIXED - Leave With wages', 0)),
        'fixed_bonus': float(payroll_record.get('FIXED - Bonus @8.33%', 0)),
        
        # Earned
        'earned_basic': float(payroll_record.get('EARNING - Basic', 0)),
        'earned_da': float(payroll_record.get('EARNING - DA', 0)),
        'earned_other': float(payroll_record.get('EARNING - Other Allows', 0)),
        'earned_leave': float(payroll_record.get('EARNING - Leave With wages', 0)),
        'earned_bonus': float(payroll_record.get('EARNING - Bonus @8.33%', 0)),
        
        # Deductions
        'pf': float(payroll_record.get('Deductions - PF 12%', 0)),
        'esi': float(payroll_record.get('Deductions - ESIC 0.75%', 0)),
        'pt': float(payroll_record.get('Deductions - PT', 0)),
        'advance': float(payroll_record.get('Deductions - Adv', 0)),
    }

    gross_earnings = float(payroll_record.get('EARNING - Total', 0))
    gross_deductions = float(payroll_record.get('Deductions - Total Deduction', 0))
    net_pay = float(payroll_record.get('Net Pay', 0))
    
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("payslip_template.html")
    
    import base64
    logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logo.png") if not os.path.exists(os.path.join(os.path.dirname(__file__), "logo.png")) else os.path.join(os.path.dirname(__file__), "logo.png")
    logo_b64 = ""
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode('utf-8')
            
    html_out = template.render(
        emp=emp,
        gross_earnings=gross_earnings,
        gross_deductions=gross_deductions,
        net_pay=net_pay,
        net_pay_words=number_to_words(net_pay),
        generated_date=datetime.date.today().strftime('%d-%m-%Y'),
        company={"name": "RS-MANTECH", "address": "Electronic City, Bangalore - 560100"},
        logo_base64=logo_b64
    )
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_out, wait_until="networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"}
        )
        browser.close()
        
    return pdf_bytes

def generate_and_upload_payslip(payroll_record: dict, db):
    """
    Generates the payslip PDF for a payroll record, uploads it to R2,
    and stores the S3 key in the database record.
    """
    pdf_bytes = generate_payslip_pdf(payroll_record, db=db)
    
    emp_name = payroll_record.get('emp_name', 'Unknown')
    unit_name = payroll_record.get('warehouse', 'Unknown')
    year_val = payroll_record.get('year', 2026)
    month_val = payroll_record.get('month', 1)
    
    month_name = datetime.date(year_val, month_val, 1).strftime('%B')
    year_str = str(year_val)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
        tmp_file.write(pdf_bytes)
        tmp_path = tmp_file.name

    try:
        s3_key = upload_with_cleanup(tmp_path, emp_name, unit_name, month_name, year_str)
        filename = build_pdf_filename(emp_name, unit_name)
    finally:
        os.remove(tmp_path)

    payslip_data = {
        "file_name": filename,
        "s3_key": s3_key,
        "generated_at": datetime.datetime.utcnow()
    }

    db["payroll_records"].update_one(
        {"_id": payroll_record["_id"]},
        {"$set": {"payslip": payslip_data}}
    )
    
    return payslip_data
