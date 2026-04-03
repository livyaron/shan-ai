# SYSTEM SPECIFICATION: Shan-AI Decision Intelligence Platform

## 1. CORE DIRECTIVE
You are an expert backend engineer building the "Shan-AI" platform. This is an organizational decision intelligence system for a large-scale electrical infrastructure and transformers projects division. 
Read this entire document before writing any code. Do not attempt to build the entire system at once. Follow the Phased Execution plan strictly.

## 2. TECH STACK (STRICT ENFORCEMENT)
- Backend: FastAPI (Python 3.11+)
- Database: PostgreSQL with the `pgvector` extension.
- ORM: SQLAlchemy (async).
- Interface: `python-telegram-bot` (v20+).
- AI Engine: Anthropic Claude API (using structured JSON output).
- Deployment: Docker & Docker Compose (must spin up the DB, pgvector, and FastAPI app together).

## 3. DOMAIN LOGIC & HIERARCHY
The system manages technical decisions for transformer and substation construction projects.
Roles: `Project_Manager` -> `Department_Manager` -> `Deputy_Division_Manager` -> `Division_Manager`.

Decision Routing Rules:
- INFO: Log in DB. No action.
- NORMAL: Log, notify relevant parties, execute.
- CRITICAL: Halt. Send an interactive Telegram message (Inline Keyboard) to the immediate superior. 
  - If Approved: Execute.
  - If Rejected: Status becomes "REJECTED". Return to the submitter with the manager's notes. Do NOT escalate upward automatically.
- UNCERTAIN: Halt. Ping immediate superior to manually classify.

## 4. THE LEARNING ENGINE (RAG + FEEDBACK)
- Every completed NORMAL or approved CRITICAL decision triggers a scheduled task.
- 48 hours post-decision, the Telegram bot asks the submitter to rate the outcome (1-5) and provide a text post-mortem.
- This feedback, along with the original decision, is vectorized using an embedding model and stored in `pgvector`.
- Before Claude makes future decisions, it queries `pgvector` for similar past scenarios to avoid repeating mistakes (Lessons Learned / תחקירים).

## 5. REQUIRED CLAUDE API OUTPUT (JSON MODE)
Whenever the system queries Claude for a decision, Claude MUST return this exact JSON schema:
{
  "type": "INFO|NORMAL|CRITICAL|UNCERTAIN",
  "summary": "Brief description of the issue",
  "recommended_action": "Specific engineering or management action",
  "confidence": 0.0 to 1.0,
  "requires_approval": boolean,
  "self_critique": {
    "assumptions": ["List of assumptions made"],
    "risks": ["Potential electrical/safety/schedule risks"]
  },
  "measurability": "MEASURABLE|PARTIAL|NOT_MEASURABLE"
}

## 6. PHASED EXECUTION PLAN
You will be instructed to execute these phases one by one via terminal commands.
- PHASE 1 (Infrastructure): Create `docker-compose.yml` (Postgres + pgvector), `requirements.txt`, basic FastAPI scaffolding, and SQLAlchemy models (Users, Messages, Decisions).
- PHASE 2 (Bot & Auth): Implement the Telegram bot webhook/polling in FastAPI and user role registration.
- PHASE 3 (Decision Engine): Integrate Claude API with the strict JSON schema and implement the Approval routing logic.
- PHASE 4 (She-Na-L RAG): Implement the pgvector embedding logic, the 48-hour feedback loop, and context injection.
- PHASE 5 (Dashboards): Create simple FastAPI HTML/Jinja2 endpoints to visualize bottleneck metrics and decision confidence scores.