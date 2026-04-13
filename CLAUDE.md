# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## High-Level Architecture

**Shan-AI** is a decision intelligence platform for electrical infrastructure projects. It uses a Telegram bot as the frontend, FastAPI as the backend, and PostgreSQL with pgvector embeddings for RAG/knowledge management.

### Core Data Flow

1. **Telegram Bot** → User submits a decision/question via message
2. **Message Handler** → `app/services/telegram_polling.py` captures the message
3. **AI Analysis** → `app/services/groq_client.py` (Groq API: llama-3.3-70b-versatile) analyzes the decision
4. **Decision Routing** → `app/services/decision_service.py` classifies as INFO/NORMAL/CRITICAL/UNCERTAIN
5. **Approval Flow** → For CRITICAL/UNCERTAIN, sends inline approval buttons to superior in hierarchy
6. **Execution** → INFO/NORMAL logged; CRITICAL/UNCERTAIN executed after approval
7. **Feedback** → 48-hour feedback loop via `app/services/feedback_service.py` to improve future decisions

### Service Architecture

```
app/routers/          # FastAPI endpoints
  ├── auth.py         # API authentication & registration
  ├── telegram.py     # Telegram webhook (unused; bot uses polling)
  ├── login.py        # User login & session management
  ├── dashboard.py    # Protected dashboard UI
  ├── ask.py          # Chat-like decision submission
  ├── files.py        # File upload for knowledge base
  └── logs.py         # Decision history logs

app/services/         # Business logic
  ├── groq_client.py  # Groq API wrapper (primary AI provider)
  ├── decision_service.py  # Decision type classification & routing
  ├── telegram_service.py  # Low-level Telegram API calls
  ├── telegram_polling.py  # Bot message handler & callback routing
  ├── embedding_service.py  # FastEmbed for vector embeddings
  ├── knowledge_service.py  # RAG knowledge retrieval
  ├── feedback_service.py  # 48-hour feedback collection scheduler
  ├── distribution_service.py  # RACI matrix & task distribution
  ├── optimization_service.py  # Decision optimization logic
  ├── raci_service.py  # RACI role assignment (AI-powered)
  ├── lessons_service.py  # Lessons learned extraction & storage
  └── claude_service.py  # Legacy (Anthropic API wrapper, unused)

app/utils/            # Utilities
  ├── auth.py         # bcrypt password hashing
  ├── session.py      # JWT token creation/verification
  └── migrations.py   # Auto-migrate user passwords on startup
```

### Database Schema (Key Tables)

- **users**: telegram_id (BIGINT), username, role (RoleEnum), password_hash, responsibilities, hierarchy_level, manager_id
- **messages**: user_id, content, telegram_message_id, created_at
- **decisions**: submitter_id, type (INFO/NORMAL/CRITICAL/UNCERTAIN), status, summary, feedback_score, feedback_requested_at
- **lessons_learned**: decision_id, lesson_text, embedding (pgvector), decision_type, tags
- **knowledge_summaries**: decision_type, summary_text, lesson_count (aggregated lessons per type)
- **decision_raci_roles**: decision_id, user_id, role (R/A/C/I), assigned_by_ai
- **knowledge_files**: Uploaded files for RAG knowledge base

## Common Commands

### Local Development

```bash
# Start all services (PostgreSQL + FastAPI)
docker-compose up -d

# View logs
docker-compose logs -f fastapi
docker-compose logs -f postgres

# Stop services
docker-compose stop

# Restart after code changes (REQUIRED)
docker-compose restart fastapi

# Connect to PostgreSQL (if needed)
docker exec -it shan-ai-postgres psql -U shan_user -d shan_ai
```

### Database Migrations (Critical)

After a fresh `docker-compose down -v` → `docker-compose up -d`, telegram_id column may be INTEGER instead of BIGINT. **Always run this after rebuild:**

```bash
docker exec shan-ai-postgres psql -U shan_user -d shan_ai \
  -c "ALTER TABLE users ALTER COLUMN telegram_id TYPE BIGINT;"
```

### FastAPI Local Development (Without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (requires PostgreSQL running separately)
DATABASE_URL="postgresql+asyncpg://shan_user:shan_secure_pass_2025@localhost:5432/shan_ai" \
TELEGRAM_BOT_TOKEN="your_token" \
GROQ_API_KEY="your_key" \
uvicorn app.main:app --reload

# Access docs
# http://localhost:8000/docs
```

### Testing & Debugging

```bash
# Check health
curl http://localhost:8000/health

# Check API status
curl http://localhost:8000/api/v1/status

# Telegram polling logs
docker-compose logs -f fastapi | grep "Telegram"
```

## Key Technical Decisions

### AI Provider: Groq (Not Anthropic)

The project originally planned Anthropic Claude, then attempted Google Gemini (failed: free tier quota = 0). **Current provider: Groq API** (`llama-3.3-70b-versatile`). If replacing this, update:
- `app/services/groq_client.py` — Main AI inference
- `app/config.py` — `GROQ_API_KEY` env var
- `requirements.txt` — If removing `groq` dependency

### Hebrew Language Support

All bot messages are in Hebrew with RTL (right-to-left) support:
- Prefix all bot messages with `\u200F` (RIGHT-TO-LEFT MARK) to force RTL rendering
- Replace straight quotes `"` with Hebrew gershayim `״` before sending to Groq (fixes JSON breakage in Hebrew abbreviations like תחמ"ש)

### pgvector for RAG

- Embeddings stored as `Vector(384)` in `lessons_learned` table (FastEmbed default size)
- `embedding_service.py` handles embedding generation
- `knowledge_service.py` handles similarity search & RAG retrieval
- Docker image: `pgvector/pgvector:0.7.0-pg16` (includes pgvector extension pre-installed)

### Decision Type Classification

The AI classifies decisions into 4 types:
- **INFO** — Log only, no action required
- **NORMAL** — Log, notify, execute immediately
- **CRITICAL** — Halt, send to superior for approval via Telegram inline button
- **UNCERTAIN** — Halt, request manual classification from superior

Decision JSON schema (from Groq) includes: type, summary, recommended_action, confidence, requires_approval, self_critique (assumptions/risks), measurability.

### Telegram Bot Architecture

- **Polling-based** (not webhooks) — `app/services/telegram_polling.py` continuously polls for updates
- **Single polling instance** — `_polling_task` global in `app/main.py` prevents multiple concurrent pollers (causes conflicts)
- **Inline buttons** for approval — `CallbackQueryHandler` in `telegram_polling.py` handles approve/reject actions
- **Right-to-left messages** — All responses prefixed with `\u200F`

**Critical gotcha**: If local Docker + Railway both run simultaneously, Telegram polling fails on both (conflict over bot token). Always `docker-compose stop` before Railway is active.

### Authentication & Sessions

- **Login**: `/login` endpoint with bcrypt password hashing
- **Default password**: `1234` (hashed in `User.password_hash` default)
- **Session tokens**: JWT tokens (7-day expiry) stored in `access_token` cookie
- **Protected dashboard**: All `/dashboard/*` endpoints require valid JWT cookie
- **Auto-migration**: `app/utils/migrations.py` automatically hashes default password for users without `password_hash` on app startup

## Deployment

### Local (Development)

- `docker-compose.yml` defines PostgreSQL + FastAPI services
- FastAPI runs on `http://localhost:8000`
- Database: `postgresql://shan_user:shan_secure_pass_2025@postgres:5432/shan_ai`

### Railway (Production)

- **Status**: Paused during development; will be used post-dev
- **App URL**: `https://shan-ai-production.up.railway.app`
- **PostgreSQL Proxy**: `interchange.proxy.rlwy.net:15720` (external TCP, not internal DNS)
- **Port env var**: Must set `PORT=8000` on FastAPI service (Railway routes traffic to this)
- **pgvector extension**: Manually created in Railway's PostgreSQL; also auto-created via `CREATE EXTENSION IF NOT EXISTS vector` in `app/main.py` startup

See `MEMORY.md` → "Railway Deployment (LIVE)" for detailed Railway IDs and redeploy commands.

## Critical Gotchas & Safety Rules

1. **Docker Destruction** — NEVER run `docker-compose down -v` without explicit user approval. This deletes all PostgreSQL data. Always confirm before destructive operations.

2. **Database Migration Required** — After `docker-compose rebuild`, the `users.telegram_id` column may revert to INTEGER. Always run the ALTER TABLE command above.

3. **Telegram Polling Conflict** — Only ONE instance of the bot can poll at a time. Keep local Docker **stopped** when Railway is active.

4. **Code → Docker Restart** — After code changes, always `docker-compose restart fastapi` to reflect changes in the running container.

5. **Input Sanitization** — Hebrew quotes (`"`) in user input break JSON parsing. Replace with `״` before sending to Groq.

6. **Session Token Expiry** — JWT tokens expire after 7 days. The app auto-refreshes on `/dashboard` requests, but long-idle sessions require re-login.

## Project Status

**PHASE 1–3: COMPLETED** (Infrastructure, Telegram bot, AI decision engine)
**PHASE 4: IN PROGRESS** (RAG with pgvector, lessons learned, knowledge summaries)
**PHASE 5: COMPLETED** (Dashboard login & HTML UI)

See `MEMORY.md` → "5-Phase Architecture" for detailed phase breakdown.

## Environment Variables (.env)

```
TELEGRAM_BOT_TOKEN=your_bot_token
GROQ_API_KEY=your_groq_api_key
ANTHROPIC_API_KEY=your_anthropic_key (optional, unused)
GEMINI_API_KEY=your_gemini_key (optional, unused)
DATABASE_URL=postgresql+asyncpg://... (auto-set in Docker)
BASE_URL=http://localhost:8000 (or production URL)
PYTHONUNBUFFERED=1
```

## Performance & Scalability Notes

- **Async/await throughout**: FastAPI + SQLAlchemy async, non-blocking I/O
- **pgvector indexes**: Indexes on `lessons_learned.decision_id` and `decision_raci_roles` for fast queries
- **FastEmbed**: Local embeddings (no API calls), fast on CPU
- **Groq API rate limits**: Free tier; monitor token usage for batch operations
- **Connection pooling**: SQLAlchemy manages async connection pool automatically
