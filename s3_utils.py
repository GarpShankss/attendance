import os
import boto3

def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=os.environ.get("AWS_ENDPOINT"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        region_name="auto" # Cloudflare R2 allows "auto" or anything
    )

def build_pdf_filename(emp_name: str, unit_name: str) -> str:
    emp_clean = "".join([c if c.isalnum() else "_" for c in emp_name])
    unit_clean = "".join([c if c.isalnum() else "_" for c in unit_name])
    return f"{unit_clean}_{emp_clean}.pdf"

def upload_with_cleanup(file_path: str, emp_name: str, unit_name: str, month_name: str, year_str: str) -> str:
    s3 = get_s3_client()
    bucket = os.environ.get("S3_BUCKET")
    
    filename = build_pdf_filename(emp_name, unit_name)
    s3_key = f"payslips/{year_str}/{month_name}/{filename}"
    
    # Upload to S3/R2
    s3.upload_file(file_path, bucket, s3_key)
    return s3_key

# -------------------------------
# GENERATE SIGNED URL
# -------------------------------

def generate_presigned_url(s3_key: str, expiry: int = 3600) -> str:
    """Generate a secure, temporary download URL for a given S3 key."""
    s3 = get_s3_client()
    return s3.generate_presigned_url(
        'get_object',
        Params={
            'Bucket': os.environ.get("S3_BUCKET"),
            'Key': s3_key
        },
        ExpiresIn=expiry
    )
