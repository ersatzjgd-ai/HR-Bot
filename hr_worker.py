import imaplib
import email
from email.header import decode_header
import email.utils
import smtplib
from email.message import EmailMessage
import datetime
import time
import schedule
import requests
import os
import io
from PyPDF2 import PdfReader
from openai import OpenAI

# ================= CONFIGURATION (From Environment Variables) =================
ZOHO_IMAP_SERVER = os.getenv("ZOHO_IMAP_SERVER", "imap.zoho.in")
ZOHO_SMTP_SERVER = os.getenv("ZOHO_SMTP_SERVER", "smtp.zoho.in")
ZOHO_EMAIL = os.getenv("ZOHO_EMAIL")
ZOHO_APP_PASSWORD = os.getenv("ZOHO_APP_PASSWORD")

WEBHOOK_URL = os.getenv("WEBHOOK_URL") 
SUMMARY_RECIPIENT = os.getenv("SUMMARY_RECIPIENT") 
GROK_API_KEY = os.getenv("GROK_API_KEY") # Add this back to Railway!

TARGET_KEYWORDS = ["jai gurudev", "bhaiya", "didi", "art of living"]
# ==============================================================================

# Initialize Grok
llm_client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")

# In-memory list to track today's candidates
daily_candidates = []

def extract_name_from_pdf(pdf_bytes):
    """Reads the PDF and uses Grok to extract the candidate's name."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        # Only read the first page to save API tokens and time
        if len(reader.pages) > 0:
            text += reader.pages[0].extract_text() + "\n"
            
        if not text.strip():
            return None

        # Ask Grok to find the name
        response = llm_client.chat.completions.create(
            model="grok-2-latest",
            messages=[
                {"role": "system", "content": "You are a data extraction bot. Your only job is to look at the provided resume text and extract the candidate's full legal name. Reply ONLY with the candidate's name. Do not include any other words."},
                {"role": "user", "content": text[:3000]} # Send up to 3000 characters
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Failed to extract name from PDF via Grok: {e}")
        return None

def check_inbox():
    global daily_candidates
    try:
        print(f"[{datetime.datetime.now()}] Checking for new emails...")
        mail = imaplib.IMAP4_SSL(ZOHO_IMAP_SERVER)
        mail.login(ZOHO_EMAIL, ZOHO_APP_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, "UNSEEN")
        email_ids = messages[0].split()

        if not email_ids:
            mail.logout()
            return

        for e_id in email_ids:
            res, msg_data = mail.fetch(e_id, "(RFC822)") 
            
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg.get("Subject", ""))[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8", errors="ignore")
                    
                    from_header = msg.get("From", "")
                    email_name, candidate_email = email.utils.parseaddr(from_header)
                    
                    body = ""
                    pdf_bytes = None

                    # Extract body and look for PDF attachment
                    if msg.is_multipart():
                        for part in msg.walk():
                            content_type = part.get_content_type()
                            content_disposition = str(part.get("Content-Disposition"))

                            if content_type == "text/plain" and "attachment" not in content_disposition:
                                try:
                                    body = part.get_payload(decode=True).decode(errors="ignore")
                                except:
                                    pass
                            elif "attachment" in content_disposition and part.get_filename() and part.get_filename().endswith(".pdf"):
                                pdf_bytes = part.get_payload(decode=True)
                    else:
                        try:
                            body = msg.get_payload(decode=True).decode(errors="ignore")
                        except:
                            pass

                    combined_text = f"{subject} {body}".lower()
                    
                    if any(keyword in combined_text for keyword in TARGET_KEYWORDS):
                        # WE HAVE A MATCH. Now try to get the real name from the resume.
                        candidate_name = email_name # Default to the email header name
                        
                        if pdf_bytes:
                            extracted_name = extract_name_from_pdf(pdf_bytes)
                            if extracted_name:
                                candidate_name = extracted_name
                        
                        print(f"New match found! {candidate_name}")
                        
                        daily_candidates.append(f"{candidate_name} ({candidate_email})")
                        
                        data = {
                            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                            "name": candidate_name,
                            "email": candidate_email,
                            "subject": subject
                        }
                        try:
                            requests.post(WEBHOOK_URL, json=data)
                        except Exception as sheet_err:
                            print(f"Failed to push to sheet: {sheet_err}")

        mail.logout()
    except Exception as e:
        print(f"Error checking inbox: {e}")

def send_daily_summary():
    global daily_candidates
    print("Preparing daily summary email...")
    
    msg = EmailMessage()
    msg['Subject'] = f"Daily Candidate Summary - {datetime.datetime.now().strftime('%Y-%m-%d')}"
    msg['From'] = ZOHO_EMAIL
    msg['To'] = SUMMARY_RECIPIENT

    if not daily_candidates:
        email_content = "Hello,\n\nNo candidates matching the criteria were found today.\n\nBest,\nHR Bot"
    else:
        names_list = "\n".join([f"- {name}" for name in daily_candidates])
        email_content = f"Hello,\n\nThe automated candidate filtering has run for today.\n\nWe identified {len(daily_candidates)} devotee(s). Here are their names:\n\n{names_list}\n\nThey have been automatically added to your Google Sheet.\n\nBest,\nHR Bot"

    msg.set_content(email_content)

    try:
        server = smtplib.SMTP_SSL(ZOHO_SMTP_SERVER, 465)
        server.login(ZOHO_EMAIL, ZOHO_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("Daily summary email sent successfully.")
        
        daily_candidates = []
        
    except Exception as e:
        print(f"Failed to send daily summary: {e}")

# ================= SCHEDULING =================
schedule.every(5).minutes.do(check_inbox)
schedule.every().day.at("18:00").do(send_daily_summary)

if __name__ == "__main__":
    print("Worker script started. Listening for incoming emails...")
    while True:
        schedule.run_pending()
        time.sleep(1)
