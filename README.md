Autonomous Multi-Agent IT Helpdesk System
ResolveIQ is an autonomous IT helpdesk system powered by a 4-agent Claude AI pipeline. It triages incoming requests via Gmail, researches historical solutions, and sends automated, intelligent responses.

The 4-Agent Pipeline
Every ticket runs through four specialized agents in sequence:

Triage Agent: Classifies issues (Security, Network, etc.) and sets priority levels.

Memory Agent: Searches a knowledge base of 150 historical tickets for resolution patterns.

Resolution Agent: Generates actionable step-by-step plans and flags if human intervention is needed.

Communication Agent: Drafts professional, empathetic replies and sends them via the Gmail API.

Smart Escalation
The system identifies 6 escalation triggers, including critical priority, security threats, or the need for administrator permissions. Escalated tickets receive a unique ticket number and notify the human IT team.

Technical Stack
AI/LLM: Anthropic Claude API (claude-haiku-4-5).

Backend: Python 3.x and Flask 3.0.

Email: Gmail API with OAuth2.

Frontend: Vanilla HTML, CSS, and JavaScript dashboard.
