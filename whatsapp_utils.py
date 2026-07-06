# whatsapp_utils.py
import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

WA_PHONE_NUMBER_ID = os.getenv("WA_PHONE_NUMBER_ID")
WA_ACCESS_TOKEN = os.getenv("WA_ACCESS_TOKEN")
WA_API_URL = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/messages"

HEADERS = {
    "Authorization": f"Bearer {WA_ACCESS_TOKEN}",
    "Content-Type": "application/json"
}


# -------------------------------
# STEP 1: Upload PDF to WhatsApp Media
# -------------------------------
def upload_pdf_to_whatsapp(pdf_bytes: bytes, filename: str) -> str | None:
    """
    Upload a PDF to WhatsApp's media endpoint.
    Returns the media_id to use in the message.
    """
    upload_url = f"https://graph.facebook.com/v19.0/{WA_PHONE_NUMBER_ID}/media"

    try:
        response = requests.post(
            upload_url,
            headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}"},
            files={
                "file": (filename, pdf_bytes, "application/pdf"),
                "messaging_product": (None, "whatsapp"),
                "type": (None, "application/pdf"),
            }
        )
        response.raise_for_status()
        media_id = response.json().get("id")
        print(f"✓ PDF uploaded to WhatsApp, media_id: {media_id}")
        return media_id

    except Exception as e:
        print(f"✗ WhatsApp media upload failed: {e}")
        print(f"  Response: {response.text if 'response' in dir() else 'N/A'}")
        return None


# -------------------------------
# STEP 2: Send PDF via Template Message
# -------------------------------
def send_payslip_whatsapp(
    phone_number: str,
    emp_name: str,
    month: str,
    pdf_bytes: bytes,
    pdf_filename: str
) -> bool:
    """
    Send payslip PDF to employee via WhatsApp.
    Uses a pre-approved template with document header.
    
    phone_number must be in international format without '+': e.g. '919876543210'
    """

    # Sanitize phone — remove +, spaces, dashes
    phone = phone_number.strip().replace("+", "").replace(" ", "").replace("-", "")

    if not phone.isdigit() or len(phone) < 10:
        print(f"✗ Invalid phone number: {phone_number}")
        return False

    # Upload PDF first to get media_id
    media_id = upload_pdf_to_whatsapp(pdf_bytes, pdf_filename)
    if not media_id:
        return False

    # Send template message with PDF as document header
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": "payslip_notification",   # must match your approved template name
            "language": {"code": "en"},
            "components": [
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": "document",
                            "document": {
                                "id": media_id,
                                "filename": pdf_filename
                            }
                        }
                    ]
                },
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": emp_name},   # {{1}}
                        {"type": "text", "text": month}        # {{2}}
                    ]
                }
            ]
        }
    }

    try:
        response = requests.post(WA_API_URL, headers=HEADERS, json=payload)
        data = response.json()

        print(f"  Send status code: {response.status_code}")
        print(f"  Send response: {data}")
        if response.status_code == 200:
            msg_id = data.get("messages", [{}])[0].get("id")
            print(f"✓ WhatsApp sent to {phone} ({emp_name}) — message ID: {msg_id}")
            return True
        else:
            error = data.get("error", {})
            print(f"✗ WhatsApp failed: code={error.get('code')} message={error.get('message')}")
            return False

    except Exception as e:
        print(f"✗ WhatsApp failed for {phone} ({emp_name}): {e}")
        return False
