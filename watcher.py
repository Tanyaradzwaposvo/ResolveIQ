"""
ResolveIQ — Gmail Watcher
Polls the helpdesk Gmail label every N seconds, runs new emails through
the 4-agent pipeline, then either:
  - Auto-resolves: sends full resolution reply, labels IT-Helpdesk/Triaged
  - Escalates:     sends escalation notice with ticket number, labels IT-Helpdesk/Escalated
"""

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

from agents import run_pipeline
from gmail_integration import (
    authenticate,
    get_unread_helpdesk_emails,
    mark_as_triaged,
    send_reply,
    _get_or_create_label,
)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

POLL_INTERVAL_SECONDS = 60
LOG_FILE = "watcher_log.jsonl"
ESCALATED_LABEL = "IT-Helpdesk/Escalated"


# ─────────────────────────────────────────────────────────────────────────────
# Escalation logic
# ─────────────────────────────────────────────────────────────────────────────

def needs_escalation(result: dict) -> bool:
    """
    Returns True if the ticket needs human intervention. Escalation triggers:
    1. Priority is Critical
    2. Priority is High AND memory agent says escalation is likely
    3. Resolution cannot be automated AND any step requires a human
    4. Any step requires elevated permissions (admin action needed)
    5. Security category (phishing, malware, ransomware, data breach)
    6. Missing key info AND priority is High or Critical
    """
    triage     = result.get("triage", {})
    resolution = result.get("resolution", {})
    memory     = result.get("memory", {})
    steps      = resolution.get("resolution_steps", [])

    # 1. Always escalate Critical
    if triage.get("priority") == "Critical":
        return True

    # 2. High priority + memory agent says escalation is likely
    if triage.get("priority") == "High" and memory.get("escalation_likely"):
        return True

    # 3. Cannot be automated and at least one human step
    if not resolution.get("can_be_automated", True):
        if any(s.get("requires_human") for s in steps):
            return True

    # 4. Any step needs admin / elevated permissions
    if any(s.get("requires_elevated_permissions") for s in steps):
        return True

    # 5. Security issues always need human review
    security_categories = {"security", "security incident", "data breach"}
    if triage.get("category", "").lower() in security_categories:
        return True
    security_keywords = ("ransomware", "malware", "phishing", "breach", "hacked",
                         "compromised", "virus", "trojan", "suspicious login")
    ticket_text = result.get("input", {}).get("ticket_text", "").lower()
    if any(kw in ticket_text for kw in security_keywords):
        return True

    # 6. Missing info on a High/Critical ticket
    if triage.get("missing_info") and triage.get("priority") in ("Critical", "High"):
        return True

    return False


def build_escalation_email(ticket_id: str, result: dict, sender_name: str) -> str:
    """Build the escalation auto-reply email body."""
    triage = result.get("triage", {})
    name   = sender_name.split("<")[0].strip() if sender_name else "there"
    team   = triage.get("assigned_team", "the relevant team")
    prio   = triage.get("priority", "High")

    return f"""Hi {name},

Thank you for reaching out to the IT Help Desk.

We have received your request and our system has automatically escalated your issue to {team} for immediate attention.

Your Ticket Details:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ticket Number : {ticket_id}
Priority      : {prio}
Assigned To   : {team}
Status        : Escalated — Awaiting Human Review
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Please keep this ticket number ({ticket_id}) for your records. A member of {team} will be in touch with you shortly to resolve your issue.

If your situation becomes more urgent, please reply to this email quoting your ticket number.

We apologise for any inconvenience and appreciate your patience.

Best regards,
ResolveIQ — IT Help Desk
Powered by AI · Escalated to Human Agent"""


def build_auto_resolve_email(ticket_id: str, result: dict) -> str:
    """Build the auto-resolution email body from the Communication Agent output."""
    comm = result.get("communication", {})
    return "\n\n".join(filter(None, [
        comm.get("greeting", "Hello,"),
        f"Your Ticket Number: {ticket_id}\n\n" + comm.get("body", "Thank you for your request. We are looking into it."),
        comm.get("closing", "Best regards,\nIT Help Desk"),
    ]))


# ─────────────────────────────────────────────────────────────────────────────
# Watcher state
# ─────────────────────────────────────────────────────────────────────────────

class WatcherState:
    def __init__(self):
        self.running          = False
        self.thread: Optional[threading.Thread] = None
        self.processed_count  = 0
        self.escalated_count  = 0
        self.last_check: Optional[str] = None
        self.last_error: Optional[str] = None
        self.recent_log: list[dict] = []

    def to_dict(self) -> dict:
        return {
            "running":          self.running,
            "processed_count":  self.processed_count,
            "escalated_count":  self.escalated_count,
            "last_check":       self.last_check,
            "last_error":       self.last_error,
            "recent_log":       self.recent_log[-20:],
        }


_state = WatcherState()


def get_state() -> WatcherState:
    return _state


# ─────────────────────────────────────────────────────────────────────────────
# Core processing logic
# ─────────────────────────────────────────────────────────────────────────────

def mark_as_escalated(service, message_id: str):
    """Apply IT-Helpdesk/Escalated label and mark as read."""
    escalated_id = _get_or_create_label(service, ESCALATED_LABEL)
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "removeLabelIds": ["UNREAD"],
            "addLabelIds":    [escalated_id],
        },
    ).execute()
    print(f"[Watcher] Message {message_id} marked as escalated.")


def process_email(service, email: dict) -> dict:
    """
    Run one email through the full agent pipeline.
    - If escalation needed: send escalation notice, label Escalated
    - Otherwise: send resolution reply, label Triaged
    Returns the full pipeline result with escalation metadata.
    """
    ticket_text = f"Subject: {email['subject']}\n\n{email['body']}"
    submitter   = email.get("sender", "")
    ticket_id   = f"EMAIL-{email['id'][:8].upper()}"

    print(f"\n[Watcher] Processing: '{email['subject']}' from {email['sender_email']}")

    # Run all 4 agents
    result = run_pipeline(
        ticket_text=ticket_text,
        submitter=submitter,
        department="",
        ticket_id=ticket_id,
    )

    escalated = needs_escalation(result)

    if escalated:
        # ── Escalation path ──────────────────────────────────────────────────
        print(f"[Watcher] ⚠️  Escalating {ticket_id} to {result['triage'].get('assigned_team')}")
        email_body = build_escalation_email(ticket_id, result, email.get("sender", ""))
        sent = send_reply(
            service=service,
            original=email,
            subject=f"[{ticket_id}] Your IT Request Has Been Escalated — {email['subject']}",
            body_text=email_body,
        )
        mark_as_escalated(service, email["id"])
    else:
        # ── Auto-resolve path ─────────────────────────────────────────────────
        print(f"[Watcher] ✅ Auto-resolving {ticket_id}")
        email_body = build_auto_resolve_email(ticket_id, result)
        comm = result.get("communication", {})
        sent = send_reply(
            service=service,
            original=email,
            subject=f"[{ticket_id}] {comm.get('subject', 'Re: ' + email['subject'])}",
            body_text=email_body,
        )
        mark_as_triaged(service, email["id"])

    result["gmail_reply_sent"]  = sent
    result["escalated"]         = escalated
    result["original_email"]    = {
        "id":           email["id"],
        "subject":      email["subject"],
        "sender":       email["sender"],
        "sender_email": email["sender_email"],
    }
    result["reply_body"] = email_body

    return result


def _append_log(entry: dict):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Polling loop
# ─────────────────────────────────────────────────────────────────────────────

def _poll_loop(interval: int = POLL_INTERVAL_SECONDS):
    print(f"[Watcher] Starting — polling every {interval}s")
    service = None

    while _state.running:
        try:
            if service is None:
                service = authenticate()

            _state.last_check = datetime.now().isoformat()
            emails = get_unread_helpdesk_emails(service)

            for email in emails:
                if not _state.running:
                    break
                try:
                    result = process_email(service, email)
                    _state.processed_count += 1
                    if result.get("escalated"):
                        _state.escalated_count += 1
                    _state.last_error = None

                    log_entry = {
                        "timestamp":  datetime.now().isoformat(),
                        "ticket_id":  result["ticket_id"],
                        "subject":    email["subject"],
                        "sender":     email["sender_email"],
                        "priority":   result["triage"].get("priority"),
                        "team":       result["triage"].get("assigned_team"),
                        "escalated":  result.get("escalated"),
                        "reply_sent": result.get("gmail_reply_sent"),
                        "duration_ms":result.get("total_duration_ms"),
                    }
                    _state.recent_log.append(log_entry)
                    _append_log(log_entry)

                    status = "⚠️  ESCALATED" if result.get("escalated") else "✅ AUTO-RESOLVED"
                    print(f"[Watcher] {status}: {result['ticket_id']} | {result['triage'].get('priority')} | {result['triage'].get('assigned_team')}")

                except Exception as e:
                    err = f"Error processing email {email['id']}: {e}"
                    print(f"[Watcher] ❌ {err}")
                    _state.last_error = err

        except Exception as e:
            err = f"Poll error: {e}"
            print(f"[Watcher] ❌ {err}")
            _state.last_error = err
            service = None

        for _ in range(interval):
            if not _state.running:
                break
            time.sleep(1)

    print("[Watcher] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
# Start / Stop
# ─────────────────────────────────────────────────────────────────────────────

def start_watcher(interval: int = POLL_INTERVAL_SECONDS) -> bool:
    if _state.running:
        return False
    _state.running = True
    _state.thread  = threading.Thread(target=_poll_loop, args=(interval,), daemon=True)
    _state.thread.start()
    return True


def stop_watcher() -> bool:
    if not _state.running:
        return False
    _state.running = False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import signal, sys

    def _handle_signal(sig, frame):
        print("\n[Watcher] Shutting down...")
        stop_watcher()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    start_watcher()
    while _state.running:
        time.sleep(1)
