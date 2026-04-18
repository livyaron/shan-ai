# Shan-AI Technical Reference

## Core Services Map
- `telegram_polling.py`: Main entry point. Single instance only.
- `groq_client.py`: Primary AI (Llama-3.3-70b-versatile).
- `decision_service.py`: Classification (INFO/NORMAL/CRITICAL/UNCERTAIN).
- `embedding_service.py`: FastEmbed (384 dims).
- `knowledge_service.py`: pgvector RAG retrieval.

## Database Tables
- `users`: Includes hierarchy_level and manager_id for approval flows.
- `decisions`: Stores AI summary, confidence, and self_critique.
- `lessons_learned`: pgvector storage for RAG.

## Technical Nuances
- **Approval Flow:** CRITICAL/UNCERTAIN statuses trigger inline buttons for managers.
- **Feedback Loop:** 48-hour scheduler via `feedback_service.py`.
- **Migrations:** `app/utils/migrations.py` handles auto-hashing of the default "1234" password.