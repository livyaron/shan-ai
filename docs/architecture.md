# Shan-AI Technical Reference

## Core Services Map
- `telegram_polling.py`: Main entry point. Single instance only.
- `groq_client.py`: Primary AI (Llama-3.3-70b-versatile).
- `decision_service.py`: Classification (INFO/NORMAL/CRITICAL/UNCERTAIN).
- `embedding_service.py`: FastEmbed (384 dims).
- `knowledge_service.py`: pgvector RAG retrieval.
- `analysis_cache.py`: short-TTL (15 min) in-process cache for the ◈ ניתוח AI dashboard
  panels. Losing it costs one rebuild — unlike the חדר מבצעים day-cache in
  `missions_report_service.py`, which is a shared once-a-day artifact and is
  persisted to `mission_report_cache` so a Railway redeploy cannot lose it.

## Database Tables
- `users`: Includes hierarchy_level and manager_id for approval flows.
- `decisions`: Stores AI summary, confidence, and self_critique.
- `lessons_learned`: pgvector storage for RAG.
- `mission_report_cache`: today's חדר מבצעים XLSX + AI summary, keyed (day, kind).

## Technical Nuances
- **Approval Flow:** CRITICAL/UNCERTAIN statuses trigger inline buttons for managers.
- **Feedback Loop:** 48-hour scheduler via `feedback_service.py`.
- **AI panels:** every `*/ai-analysis` endpoint counts in SQL (never hydrates the
  whole result set to tally it) and serves repeats from `analysis_cache`.
  `?refresh=1` forces a rebuild; failures are never cached.
- **Migrations:** `app/utils/migrations.py` handles auto-hashing of the default "1234" password.