"""
ResolveIQ — Autonomous Multi-Agent IT Resolution System
Core agent pipeline: Orchestrator → Triage → Memory → Resolution → Communication
"""

import json
import os
import time
import re
from datetime import datetime
from typing import Optional
import anthropic
import pandas as pd
from dotenv import load_dotenv
# Always load .env from the same folder as this script
_here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_here, ".env"), override=True)

# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        
        if not api_key:
            raise ValueError("API Key missing! Check your .env file.")
            
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def call_claude(system: str, user: str, model: str = "claude-haiku-4-5-20251001") -> str:
    """Single Claude call, returns text."""
    response = get_client().messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": user}],
        system=system,
    )
    return response.content[0].text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Base (loaded from Excel dataset)
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeBase:
    """Loads historical ticket data from the Excel file for memory retrieval."""

    def __init__(self, xlsx_path: str = None):
        # Default: look for the dataset next to this script file
        if xlsx_path is None:
            here = os.path.dirname(os.path.abspath(__file__))
            xlsx_path = os.path.join(here, "it_helpdesk_agent_demo_dataset.xlsx")
        self.tickets: list[dict] = []
        self._load(xlsx_path)

    def _load(self, path: str):
        if not os.path.exists(path):
            print(f"[KnowledgeBase] File not found: {path}. Starting with empty KB.")
            return
        try:
            df = pd.read_excel(path)
            df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
            self.tickets = df.where(pd.notnull(df), None).to_dict(orient="records")
            print(f"[KnowledgeBase] Loaded {len(self.tickets)} historical tickets.")
        except Exception as e:
            print(f"[KnowledgeBase] Error loading: {e}")

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """Simple keyword-based similarity search over historical tickets."""
        if not self.tickets:
            return []
        query_words = set(re.sub(r"[^\w\s]", "", query.lower()).split())
        scored = []
        for t in self.tickets:
            text = " ".join(str(v) for v in t.values() if v is not None).lower()
            overlap = len(query_words & set(text.split()))
            if overlap > 0:
                scored.append((overlap, t))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[:top_k]]

    def add_resolved_ticket(self, ticket: dict):
        """Append a newly resolved ticket to the in-memory knowledge base."""
        self.tickets.append(ticket)


# Singleton KB loaded once
_kb: Optional[KnowledgeBase] = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1 — Triage Agent
# ─────────────────────────────────────────────────────────────────────────────

TRIAGE_SYSTEM = """You are an IT Help Desk Triage Agent for a mid-sized company.
Analyze incoming support tickets and return ONLY valid JSON — no markdown, no extra text.

Rules:
- Be practical and conservative. Do not invent facts not supported by the ticket.
- If security risk, phishing, malware, or suspicious login → Security team.
- If password reset, MFA, SSO, lockout → Identity/Access team.
- If VPN, Wi-Fi, DNS, DHCP, internet → Network team.
- If email, Outlook, Teams, SharePoint, OneDrive → Messaging/Collaboration team.
- If laptop, printer, monitor, docking station, peripherals → Desktop Support team.
- If ERP, CRM, business app → Application Support team.

Return this exact JSON schema:
{
  "summary": "one sentence summary",
  "category": "one of: Security | Identity & Access | Network | Email & Collaboration | Hardware & Workstation | Business Applications",
  "subcategory": "specific subcategory",
  "impact": "Low | Medium | High",
  "urgency": "Low | Medium | High | Critical",
  "priority": "Low | Medium | High | Critical",
  "missing_info": true or false,
  "follow_up_question": "question string or null",
  "assigned_team": "team name",
  "confidence": "Low | Medium | High",
  "confidence_score": 0.0 to 1.0
}"""


def triage_agent(ticket_text: str, submitter: str = "", department: str = "") -> dict:
    """Agent 1: Classify, prioritize, and route the ticket."""
    context = f"Submitter: {submitter}\nDepartment: {department}\n\n" if submitter or department else ""
    user_msg = f"{context}Ticket:\n{ticket_text}"
    raw = call_claude(TRIAGE_SYSTEM, user_msg)
    try:
        # Strip any accidental markdown code fences
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "summary": ticket_text[:100],
            "category": "General IT",
            "subcategory": "Unknown",
            "impact": "Medium",
            "urgency": "Medium",
            "priority": "Medium",
            "missing_info": False,
            "follow_up_question": None,
            "assigned_team": "General Support",
            "confidence": "Low",
            "confidence_score": 0.2,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Agent 2 — Memory Agent
# ─────────────────────────────────────────────────────────────────────────────

MEMORY_SYSTEM = """You are an IT Knowledge Retrieval Agent.
You are given:
1. A current support ticket
2. A list of similar historical tickets with their resolutions

Your job is to extract the most relevant past resolution steps and patterns.
Return ONLY valid JSON — no markdown, no extra text.

JSON schema:
{
  "similar_cases_found": true or false,
  "resolution_pattern": "description of common resolution pattern, or null",
  "past_steps": ["step 1", "step 2", ...],
  "estimated_resolution_time_minutes": integer or null,
  "auto_resolvable": true or false,
  "escalation_likely": true or false,
  "notes": "any important patterns or caveats"
}"""


def memory_agent(ticket_text: str, triage: dict) -> dict:
    """Agent 2: Search historical tickets and extract resolution patterns."""
    similar = get_kb().search(f"{ticket_text} {triage.get('category', '')} {triage.get('subcategory', '')}")

    if not similar:
        return {
            "similar_cases_found": False,
            "resolution_pattern": None,
            "past_steps": [],
            "estimated_resolution_time_minutes": None,
            "auto_resolvable": False,
            "escalation_likely": False,
            "notes": "No historical cases found. This may be a novel issue.",
        }

    history_str = json.dumps(similar, indent=2, default=str)
    user_msg = f"""Current ticket:
{ticket_text}

Category: {triage.get('category')} / {triage.get('subcategory')}

Historical similar tickets:
{history_str}"""

    raw = call_claude(MEMORY_SYSTEM, user_msg)
    try:
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "similar_cases_found": bool(similar),
            "resolution_pattern": "Historical data available but could not be parsed.",
            "past_steps": [],
            "estimated_resolution_time_minutes": None,
            "auto_resolvable": False,
            "escalation_likely": False,
            "notes": raw[:300],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Agent 3 — Resolution Agent
# ─────────────────────────────────────────────────────────────────────────────

RESOLUTION_SYSTEM = """You are an expert IT Resolution Agent.
Based on the ticket details, triage classification, and any historical patterns, generate a complete resolution plan.

Guidelines:
- Provide specific, actionable steps (not vague advice).
- Include exact commands, tool names, or UI navigation paths where applicable.
- Flag any steps that require elevated permissions or human intervention.
- Estimate time for each step.
- Identify if this can be auto-resolved without human involvement.

Return ONLY valid JSON — no markdown, no extra text.

JSON schema:
{
  "resolution_steps": [
    {
      "step": 1,
      "action": "specific action description",
      "details": "exact commands, UI path, or technical details",
      "requires_human": true or false,
      "requires_elevated_permissions": true or false,
      "estimated_minutes": integer
    }
  ],
  "total_estimated_minutes": integer,
  "can_be_automated": true or false,
  "automation_notes": "what can be scripted or null",
  "escalation_trigger": "condition under which to escalate or null",
  "prevention_tip": "how to prevent this issue in future or null"
}"""


def resolution_agent(ticket_text: str, triage: dict, memory: dict) -> dict:
    """Agent 3: Generate a step-by-step resolution plan."""
    user_msg = f"""Ticket:
{ticket_text}

Triage Results:
{json.dumps(triage, indent=2)}

Historical Resolution Patterns:
{json.dumps(memory, indent=2)}"""

    raw = call_claude(RESOLUTION_SYSTEM, user_msg)
    try:
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "resolution_steps": [{"step": 1, "action": "Investigate issue", "details": raw[:300],
                                   "requires_human": True, "requires_elevated_permissions": False,
                                   "estimated_minutes": 15}],
            "total_estimated_minutes": 15,
            "can_be_automated": False,
            "automation_notes": None,
            "escalation_trigger": "If issue cannot be reproduced",
            "prevention_tip": None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Agent 4 — Communication Agent
# ─────────────────────────────────────────────────────────────────────────────

COMMUNICATION_SYSTEM = """You are an IT Communication Agent who drafts professional, empathetic responses to end users.

Guidelines:
- Use clear, jargon-free language appropriate for non-technical users.
- Acknowledge the user's issue with empathy.
- Explain what will happen next (or what they should do) in simple steps.
- Set clear expectations about timing.
- Be concise — users are frustrated, not reading essays.
- If you need more info from the user, ask ONE specific question.

Return ONLY valid JSON — no markdown, no extra text.

JSON schema:
{
  "subject": "email subject line",
  "greeting": "Dear [Name] / Hi [Name] / Hello,",
  "body": "the full email body",
  "closing": "closing line",
  "tone": "empathetic | professional | urgent",
  "requires_user_action": true or false,
  "user_action_required": "what the user needs to do, or null"
}"""


def communication_agent(ticket_text: str, triage: dict, resolution: dict, submitter: str = "") -> dict:
    """Agent 4: Draft a professional user-facing response."""
    name_part = submitter if submitter else "there"
    user_msg = f"""Submitter name: {name_part}

Original ticket:
{ticket_text}

Triage result:
- Priority: {triage.get('priority')}
- Team: {triage.get('assigned_team')}
- Follow-up needed: {triage.get('missing_info')}
- Question: {triage.get('follow_up_question')}

Resolution plan:
{json.dumps(resolution.get('resolution_steps', []), indent=2)}
Estimated time: {resolution.get('total_estimated_minutes')} minutes"""

    raw = call_claude(COMMUNICATION_SYSTEM, user_msg)
    try:
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "subject": f"Re: Your IT Support Request - {triage.get('category', 'General')}",
            "greeting": f"Hi {name_part},",
            "body": f"Thank you for reaching out. We've received your request and assigned it to {triage.get('assigned_team', 'our team')}. We'll be in touch shortly.",
            "closing": "Best regards,\nIT Help Desk",
            "tone": "professional",
            "requires_user_action": triage.get("missing_info", False),
            "user_action_required": triage.get("follow_up_question"),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator — Runs all 4 agents in sequence and returns full result
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(
    ticket_text: str,
    submitter: str = "",
    department: str = "",
    ticket_id: str = None,
) -> dict:
    """
    Main entry point: Run the full 4-agent pipeline.
    Returns a dict with all agent outputs, timing, and metadata.
    """
    start_time = time.time()
    if not ticket_id:
        ticket_id = f"TKT-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    pipeline_log = []

    def run_agent(name: str, fn, *args) -> dict:
        t0 = time.time()
        print(f"[{name}] Running...")
        result = fn(*args)
        elapsed = round((time.time() - t0) * 1000)
        print(f"[{name}] Done in {elapsed}ms")
        pipeline_log.append({"agent": name, "duration_ms": elapsed})
        return result

    # Run agents
    triage = run_agent("Triage Agent", triage_agent, ticket_text, submitter, department)
    memory = run_agent("Memory Agent", memory_agent, ticket_text, triage)
    resolution = run_agent("Resolution Agent", resolution_agent, ticket_text, triage, memory)
    communication = run_agent("Communication Agent", communication_agent, ticket_text, triage, resolution, submitter)

    total_ms = round((time.time() - start_time) * 1000)

    # Add to knowledge base for future reference
    get_kb().add_resolved_ticket({
        "ticket_id": ticket_id,
        "ticket_text": ticket_text,
        "submitter": submitter,
        "department": department,
        "category": triage.get("category"),
        "subcategory": triage.get("subcategory"),
        "priority": triage.get("priority"),
        "assigned_team": triage.get("assigned_team"),
        "resolution_steps": json.dumps(resolution.get("resolution_steps", [])),
        "status": "Pending",
    })

    return {
        "ticket_id": ticket_id,
        "submitted_at": datetime.now().isoformat(),
        "input": {
            "ticket_text": ticket_text,
            "submitter": submitter,
            "department": department,
        },
        "triage": triage,
        "memory": memory,
        "resolution": resolution,
        "communication": communication,
        "pipeline_log": pipeline_log,
        "total_duration_ms": total_ms,
    }
