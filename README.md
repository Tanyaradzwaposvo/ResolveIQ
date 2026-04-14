ResolveIQ — Autonomous Multi-Agent IT Resolution System
ResolveIQ goes beyond traditional helpdesk routing. Instead of just assigning tickets to a human, it runs a 4-agent pipeline that classifies, researches, resolves, and communicates — automatically.

## The 4-Agent Pipeline

```
Ticket Input
     │
     ▼
┌─────────────────┐
│  Triage Agent   │  → Classifies ticket, sets priority/impact/urgency,
│  (Claude AI)    │    identifies missing info, routes to correct team
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Memory Agent   │  → Searches historical tickets for similar cases,
│  (Claude AI)    │    extracts resolution patterns, estimates time
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│  Resolution Agent   │  → Generates step-by-step resolution plan with
│  (Claude AI)        │    exact commands, flags human-required steps
└────────┬────────────┘
         │
         ▼
┌──────────────────────────┐
│  Communication Agent     │  → Drafts professional user-facing email
│  (Claude AI)             │    with correct tone, clear next steps
└──────────────────────────┘
         │
         ▼
   Human Review Gate
   (approve / escalate)
```

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your Anthropic API key
```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-...

# Mac/Linux
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Copy your dataset to the ResolveIQ folder
```
Copy it_helpdesk_agent_demo_dataset.xlsx into this folder
```

### 4. Run the server
```bash
python app.py
```

### 5. Open the app
Go to: http://localhost:5000

---

## Business Case

| Traditional Helpdesk | ResolveIQ |
|---|---|
| Ticket routed to human | 4 agents run in parallel |
| Human reads & researches | Memory agent finds similar cases |
| Human drafts response | Communication agent auto-drafts |
| Resolution in 24-72 hours | Resolution plan in < 10 seconds |
| Scales with headcount | Scales with compute |

**Target Market:** SMBs spending $50K+/year on IT helpdesk staff
**Model:** SaaS at $200-500/month, or per-ticket pricing

---

## API Endpoints

- `POST /api/triage` — Process a ticket through all 4 agents
- `GET /api/tickets` — List all tickets in current session
- `GET /api/stats` — Summary statistics
- `GET /api/health` — Health check
