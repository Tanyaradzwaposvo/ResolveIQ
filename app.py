"""
ResolveIQ — Flask API Backend
Run: python app.py
Then open http://localhost:5000 in your browser.
"""

import os
from dotenv import load_dotenv

# Always load .env from the same folder as this script
_here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_here, ".env"), override=True)

# Clear any stale key from the shell environment
print("[ResolveIQ] API key loaded:", bool(os.environ.get("ANTHROPIC_API_KEY")))

import uuid
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from agents import run_pipeline, get_kb

app = Flask(__name__, static_folder=".", static_url_path="")

# In-memory store of all processed tickets for this session
ticket_store: list[dict] = []

# ─────────────────────────────────────────────────────────────────────────────
# Core Triage Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/api/triage", methods=["POST"])
def api_triage():
    """Process a ticket through the full 4-agent pipeline."""
    data = request.get_json(force=True)
    ticket_text = (data.get("ticket_text") or "").strip()
    submitter   = (data.get("submitter") or "").strip()
    department  = (data.get("department") or "").strip()

    if not ticket_text:
        return jsonify({"error": "ticket_text is required"}), 400

    ticket_id = f"TKT-{str(uuid.uuid4())[:8].upper()}"

    try:
        result = run_pipeline(
            ticket_text=ticket_text,
            submitter=submitter,
            department=department,
            ticket_id=ticket_id,
        )
        ticket_store.append(result)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tickets", methods=["GET"])
def api_tickets():
    return jsonify(ticket_store)


@app.route("/api/stats", methods=["GET"])
def api_stats():
    if not ticket_store:
        return jsonify({"total": 0, "by_priority": {}, "by_team": {}, "avg_duration_ms": 0,
                        "auto_resolvable_pct": 0, "kb_size": len(get_kb().tickets),
                        "escalated_count": 0, "auto_resolved_count": 0})

    priority_counts: dict[str, int] = {}
    team_counts: dict[str, int] = {}
    total_ms = 0
    auto_count = 0
    escalated_count = 0

    for t in ticket_store:
        p    = t["triage"].get("priority", "Unknown")
        team = t["triage"].get("assigned_team", "Unknown")
        priority_counts[p]   = priority_counts.get(p, 0) + 1
        team_counts[team]    = team_counts.get(team, 0) + 1
        total_ms            += t.get("total_duration_ms", 0)
        if t["resolution"].get("can_be_automated"):
            auto_count += 1
        if t.get("escalated"):
            escalated_count += 1

    return jsonify({
        "total": len(ticket_store),
        "by_priority": priority_counts,
        "by_team": team_counts,
        "avg_duration_ms": round(total_ms / len(ticket_store)),
        "auto_resolvable_pct": round(auto_count / len(ticket_store) * 100),
        "kb_size": len(get_kb().tickets),
        "escalated_count": escalated_count,
        "auto_resolved_count": len(ticket_store) - escalated_count,
    })


@app.route("/api/health", methods=["GET"])
def health():
    from gmail_integration import test_connection
    gmail_status = test_connection()
    return jsonify({
        "status": "ok",
        "api_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "kb_tickets": len(get_kb().tickets),
        "session_tickets": len(ticket_store),
        "gmail": gmail_status,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Gmail Integration Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/gmail/status", methods=["GET"])
def gmail_status():
    """Return Gmail connection status and watcher state."""
    from gmail_integration import test_connection
    from watcher import get_state
    conn = test_connection()
    state = get_state().to_dict()
    return jsonify({"connection": conn, "watcher": state})


@app.route("/api/gmail/start", methods=["POST"])
def gmail_start():
    """Start the Gmail watcher in a background thread."""
    from watcher import start_watcher, get_state
    data = request.get_json(force=True) or {}
    interval = int(data.get("interval_seconds", 60))
    started = start_watcher(interval=interval)
    return jsonify({"started": started, "watcher": get_state().to_dict()})


@app.route("/api/gmail/stop", methods=["POST"])
def gmail_stop():
    """Stop the Gmail watcher."""
    from watcher import stop_watcher, get_state
    stopped = stop_watcher()
    return jsonify({"stopped": stopped, "watcher": get_state().to_dict()})


@app.route("/api/gmail/process-now", methods=["POST"])
def gmail_process_now():
    """
    Manually trigger one poll cycle — fetch unread helpdesk emails,
    run them through the pipeline, and send replies right now.
    """
    from gmail_integration import authenticate, get_unread_helpdesk_emails
    from watcher import process_email

    try:
        service = authenticate()
        emails  = get_unread_helpdesk_emails(service, max_results=5)
        results = []

        for email in emails:
            result = process_email(service, email)
            ticket_store.append(result)
            results.append({
                "ticket_id": result["ticket_id"],
                "subject":   email["subject"],
                "sender":    email["sender_email"],
                "priority":  result["triage"].get("priority"),
                "team":      result["triage"].get("assigned_team"),
                "reply_sent": result.get("gmail_reply_sent"),
                "duration_ms": result.get("total_duration_ms"),
            })

        return jsonify({"processed": len(results), "tickets": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/gmail/log", methods=["GET"])
def gmail_log():
    """Return the most recent processed email log entries."""
    from watcher import get_state
    return jsonify(get_state().recent_log[-50:])


@app.route("/api/gmail/escalated", methods=["GET"])
def gmail_escalated():
    """Return only escalated email tickets."""
    escalated_tickets = [
        t for t in ticket_store
        if t.get("ticket_id", "").startswith("EMAIL-") and t.get("escalated")
    ]
    feed = []
    for t in reversed(escalated_tickets):
        orig = t.get("original_email", {})
        feed.append({
            "ticket_id":    t["ticket_id"],
            "submitted_at": t["submitted_at"],
            "email": {
                "subject":      orig.get("subject", ""),
                "sender":       orig.get("sender", ""),
                "sender_email": orig.get("sender_email", ""),
            },
            "triage": {
                "summary":       t["triage"].get("summary", ""),
                "priority":      t["triage"].get("priority", ""),
                "assigned_team": t["triage"].get("assigned_team", ""),
                "category":      t["triage"].get("category", ""),
            },
            "reply_sent": t.get("gmail_reply_sent", False),
            "reply_body": t.get("reply_body", ""),
            "duration_ms": t.get("total_duration_ms", 0),
        })
    return jsonify(feed)


@app.route("/api/gmail/feed", methods=["GET"])
def gmail_feed():
    """Return full ticket details for all email-sourced tickets."""
    email_tickets = [t for t in ticket_store if t.get("ticket_id", "").startswith("EMAIL-")]
    feed = []
    for t in reversed(email_tickets):
        orig = t.get("original_email", {})
        comm = t.get("communication", {})
        feed.append({
            "ticket_id":    t["ticket_id"],
            "submitted_at": t["submitted_at"],
            "email": {
                "subject":      orig.get("subject", ""),
                "sender":       orig.get("sender", ""),
                "sender_email": orig.get("sender_email", ""),
            },
            "triage": {
                "summary":       t["triage"].get("summary", ""),
                "category":      t["triage"].get("category", ""),
                "subcategory":   t["triage"].get("subcategory", ""),
                "priority":      t["triage"].get("priority", ""),
                "impact":        t["triage"].get("impact", ""),
                "urgency":       t["triage"].get("urgency", ""),
                "assigned_team": t["triage"].get("assigned_team", ""),
                "missing_info":  t["triage"].get("missing_info", False),
                "follow_up":     t["triage"].get("follow_up_question", ""),
                "confidence":    t["triage"].get("confidence", ""),
            },
            "resolution": {
                "steps":           t["resolution"].get("resolution_steps", []),
                "total_minutes":   t["resolution"].get("total_estimated_minutes", 0),
                "can_be_automated":t["resolution"].get("can_be_automated", False),
                "prevention_tip":  t["resolution"].get("prevention_tip", ""),
            },
            "reply": {
                "subject": comm.get("subject", ""),
                "body":    "\n\n".join(filter(None, [comm.get("greeting"), comm.get("body"), comm.get("closing")])),
                "sent":    t.get("gmail_reply_sent", False),
            },
            "escalated":   t.get("escalated", False),
            "reply_body":  t.get("reply_body", ""),
            "duration_ms": t.get("total_duration_ms", 0),
        })
    return jsonify(feed)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    api_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"""
╔══════════════════════════════════════════════╗
║           ResolveIQ is starting...           ║
╠══════════════════════════════════════════════╣
║  Open:        http://localhost:{port}           ║
║  API key set: {str(api_ok):<37}║
║  Gmail:       configure via /api/gmail/status║
╚══════════════════════════════════════════════╝
""")
    app.run(host="0.0.0.0", port=port, debug=False)
