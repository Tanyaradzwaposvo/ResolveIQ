"""
ResolveIQ — Gmail Integration
Reads unread helpdesk emails, runs them through the agent pipeline,
sends the drafted response back, and labels the thread as Triaged.

Prerequisites:
  1. pip install google-auth google-auth-oauthlib google-api-python-client
  2. Run setup_gmail.py once to authenticate and save token.json
"""

import base64
import json
import os
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Gmail API scopes required
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
]

CREDENTIALS_FILE = "credentials.json"   # Downloaded from Google Cloud Console
TOKEN_FILE = "token.json"               # Auto-generated after first auth

GMAIL_ACCOUNT  = "teamzootopia3@gmail.com"  # The Gmail account to connect
HELPDESK_LABEL = "IT-Helpdesk"             # Gmail label to watch for incoming tickets
TRIAGED_LABEL  = "IT-Helpdesk/Triaged"     # Applied after pipeline runs


# ─────────────────────────────────────────────────────────────────────────────
# Authentication
# ─────────────────────────────────────────────────────────────────────────────

def authenticate() -> object:
    """
    OAuth2 authentication. On first run, opens a browser for consent.
    Subsequent runs use the saved token.json.
    Returns an authorized Gmail service object.
    """
    creds: Optional[Credentials] = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"'{CREDENTIALS_FILE}' not found.\n"
                    "Download it from Google Cloud Console:\n"
                    "  APIs & Services → Credentials → Create OAuth 2.0 Client ID → Desktop app\n"
                    "Then save it as credentials.json in the ResolveIQ folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    print("[Gmail] Authenticated successfully.")
    return service


# ─────────────────────────────────────────────────────────────────────────────
# Label helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_or_create_label(service, name: str) -> str:
    """Return the label ID for 'name', creating it if it doesn't exist."""
    existing = service.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in existing:
        if lbl["name"].lower() == name.lower():
            return lbl["id"]

    # Create it
    created = service.users().labels().create(
        userId="me",
        body={
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    print(f"[Gmail] Created label: {name} (id={created['id']})")
    return created["id"]


def _get_label_id(service, name: str) -> Optional[str]:
    """Return the label ID for 'name', or None if it doesn't exist."""
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for lbl in labels:
        if lbl["name"].lower() == name.lower():
            return lbl["id"]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Reading emails
# ─────────────────────────────────────────────────────────────────────────────

def _decode_body(part: dict) -> str:
    """Decode base64url email body part to plain text."""
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")


def _extract_text(payload: dict) -> str:
    """Recursively extract plain text from a MIME payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode_body(payload)
    if mime.startswith("multipart"):
        parts_text = []
        for part in payload.get("parts", []):
            text = _extract_text(part)
            if text:
                parts_text.append(text)
        return "\n".join(parts_text)
    return ""


def _clean_text(text: str) -> str:
    """Remove excessive whitespace and quoted reply chains."""
    # Remove common reply chains
    text = re.sub(r"\nOn .+ wrote:\n.*", "", text, flags=re.DOTALL)
    text = re.sub(r"\n>.*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_unread_helpdesk_emails(service, max_results: int = 10) -> list[dict]:
    """
    Fetch emails from the HELPDESK_LABEL inbox that haven't been processed yet
    (i.e. don't carry the Triaged or Escalated sub-labels).
    Read/unread status is ignored so the system works even if you opened the email.
    """
    # Ensure the label exists
    label_id = _get_label_id(service, HELPDESK_LABEL)
    if not label_id:
        print(f"[Gmail] Label '{HELPDESK_LABEL}' not found. Creating it...")
        label_id = _get_or_create_label(service, HELPDESK_LABEL)

    # Pick up everything with IT-Helpdesk label that hasn't been triaged or escalated yet
    query = f"label:{HELPDESK_LABEL} -label:IT-Helpdesk/Triaged -label:IT-Helpdesk/Escalated"

    try:
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=max_results,
        ).execute()
    except HttpError as e:
        print(f"[Gmail] Error fetching messages: {e}")
        return []

    messages = result.get("messages", [])
    if not messages:
        print("[Gmail] No pending helpdesk emails found.")
        return []

    emails = []
    for msg_ref in messages:
        try:
            msg = service.users().messages().get(
                userId="me",
                id=msg_ref["id"],
                format="full",
            ).execute()

            headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            subject = headers.get("subject", "(no subject)")
            sender  = headers.get("from", "")
            sender_email = re.findall(r"<(.+?)>", sender)
            sender_email = sender_email[0] if sender_email else sender

            body = _clean_text(_extract_text(msg["payload"]))

            emails.append({
                "id": msg["id"],
                "thread_id": msg["threadId"],
                "subject": subject,
                "sender": sender,
                "sender_email": sender_email,
                "body": body,
                "snippet": msg.get("snippet", ""),
            })
        except HttpError as e:
            print(f"[Gmail] Error reading message {msg_ref['id']}: {e}")

    print(f"[Gmail] Found {len(emails)} pending helpdesk email(s).")
    return emails


# ─────────────────────────────────────────────────────────────────────────────
# Sending replies
# ─────────────────────────────────────────────────────────────────────────────

def send_reply(service, original: dict, subject: str, body_text: str) -> bool:
    """
    Send a reply to the original email's thread.
    Returns True on success.
    """
    message = MIMEMultipart("alternative")
    message["To"]      = original["sender_email"]
    message["Subject"] = subject if subject.startswith("Re:") else f"Re: {original['subject']}"
    message["In-Reply-To"] = original["id"]
    message["References"]  = original["id"]

    # Plain text part
    message.attach(MIMEText(body_text, "plain"))

    # HTML part — nicely formatted
    html = f"""
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#1e293b;line-height:1.6;">
<div style="max-width:600px;margin:0 auto;padding:20px;">
  <div style="background:#f1f5f9;border-left:4px solid #6366f1;padding:12px 16px;border-radius:4px;margin-bottom:20px;">
    <strong style="color:#6366f1;">⚡ ResolveIQ — Auto-Generated Response</strong>
  </div>
  <div style="white-space:pre-line;">{body_text}</div>
  <hr style="border:none;border-top:1px solid #e2e8f0;margin:24px 0;">
  <p style="color:#94a3b8;font-size:12px;">This response was generated by ResolveIQ, an AI-powered IT Help Desk system. A human agent has been notified and will follow up if needed.</p>
</div>
</body></html>"""
    message.attach(MIMEText(html, "html"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    try:
        service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": original["thread_id"]},
        ).execute()
        print(f"[Gmail] Reply sent to {original['sender_email']}")
        return True
    except HttpError as e:
        print(f"[Gmail] Error sending reply: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Post-processing: mark as read, apply Triaged label
# ─────────────────────────────────────────────────────────────────────────────

def mark_as_triaged(service, message_id: str):
    """Mark email as read and move it to the Triaged label."""
    triaged_id = _get_or_create_label(service, TRIAGED_LABEL)

    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [triaged_id],
        },
    ).execute()
    print(f"[Gmail] Message {message_id} marked as triaged.")


# ─────────────────────────────────────────────────────────────────────────────
# Connection test
# ─────────────────────────────────────────────────────────────────────────────

def test_connection() -> dict:
    """
    Test Gmail API connectivity. Returns status dict.
    Used by the Flask health endpoint.
    """
    try:
        service = authenticate()
        profile = service.users().getProfile(userId="me").execute()
        label_id = _get_label_id(service, HELPDESK_LABEL)
        return {
            "connected": True,
            "email": profile.get("emailAddress"),
            "helpdesk_label_exists": bool(label_id),
            "helpdesk_label": HELPDESK_LABEL,
        }
    except FileNotFoundError as e:
        return {"connected": False, "error": str(e)}
    except Exception as e:
        return {"connected": False, "error": str(e)}
