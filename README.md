ResolveIQ — Autonomous Multi-Agent IT Helpdesk System
ResolveIQ is a fully autonomous IT helpdesk system powered by four specialised AI agents. It reads support emails from Gmail, analyses them using a multi-agent pipeline built on the Claude API, generates step-by-step resolution plans informed by a 150-ticket knowledge base, and automatically sends professional replies — all within 30 seconds and without human involvement.
How it works: Every ticket runs through four agents in sequence — a Triage Agent that classifies and prioritises, a Memory Agent that searches historical tickets for patterns, a Resolution Agent that generates actionable fix steps, and a Communication Agent that drafts and sends the reply via Gmail. Complex tickets are automatically escalated with a unique ticket number and flagged for human review.
Features

4-agent Claude AI pipeline with structured JSON outputs
Gmail OAuth2 integration — reads labelled emails, sends replies, applies sub-labels automatically
150-ticket knowledge base loaded from Excel, grows with every resolved ticket
Smart escalation system with 6 trigger conditions (security threats, admin permissions, critical priority, etc.)
Real-time web dashboard built with Flask and vanilla JavaScript
Live agent pipeline visualiser, ticket feed, and stats tracking
Secure API key management via .env — zero hardcoded credentials

Tech stack: Python · Flask · Anthropic Claude API (claude-haiku-4-5) · Gmail API · OAuth2 · pandas · HTML/CSS/JavaScript
