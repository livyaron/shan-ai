# Shan-AI Decision Intelligence Platform

A backend system for organizational decision intelligence in electrical infrastructure and transformer projects.

## Tech Stack
- **Backend**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL with pgvector extension
- **ORM**: SQLAlchemy (async)
- **Bot**: python-telegram-bot v20+
- **AI Engine**: Anthropic Claude API

## PHASE 1: Infrastructure Setup (COMPLETED)

### Files Created:
- `docker-compose.yml` - PostgreSQL + pgvector + FastAPI services
- `requirements.txt` - Python dependencies
- `Dockerfile` - FastAPI container configuration
- `app/main.py` - FastAPI application entry point
- `app/config.py` - Configuration management
- `app/database.py` - Database setup and session management
- `app/models.py` - SQLAlchemy models (User, Message, Decision)
- `.env.example` - Environment variables template

### Database Models:
- **User**: Role-based user management (Project_Manager, Department_Manager, etc.)
- **Message**: Telegram message history
- **Decision**: Decision log with type, status, confidence, and feedback tracking

### Quick Start

1. **Clone and setup**:
   ```bash
   cd SHAN-AI
   cp .env.example .env
   ```

2. **Start services** (requires Docker & Docker Compose):
   ```bash
   docker-compose up -d
   ```

3. **Access**:
   - FastAPI Docs: http://localhost:8000/docs
   - Health Check: http://localhost:8000/health
   - Database: localhost:5432 (shan_user / your_db_password_here)

## Project Structure
```
SHAN-AI/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app
│   ├── config.py        # Settings
│   ├── database.py      # DB connection
│   └── models.py        # SQLAlchemy models
├── docker-compose.yml   # Compose config
├── Dockerfile          # FastAPI image
├── requirements.txt     # Python deps
├── .env.example        # Env template
└── .gitignore          # Git ignore rules
```

## Next Phases
- **PHASE 2**: Telegram Bot & User Authentication
- **PHASE 3**: Claude Decision Engine with approval routing
- **PHASE 4**: RAG system with pgvector embeddings
- **PHASE 5**: Dashboards and metrics UI
