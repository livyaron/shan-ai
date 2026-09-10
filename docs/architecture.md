# Shan-AI Technical Reference

## Core Services Map
- `telegram_polling.py`: Main entry point. Single instance only.
- `groq_client.py`: Primary AI (Llama-4-scout first, Llama-3.3-70b as backup model).
- `gemma_client.py`: **Fallback provider** (Google AI Studio). `llm_router` switches
  to it on any Groq failure when the usage's `fallback` flag is on (default on),
  and vice-versa. Needs `GOOGLE_AI_API_KEY` — without it there is no backup.
- `llm_health.py`: on-demand provider check. `/dashboard/llm-health` reports config;
  `?probe=1` actually calls each provider, which is the only way to catch an
  exhausted quota before an outage does. Also on the הגדרות מודל AI page.
- `decision_service.py`: Classification (INFO/NORMAL/CRITICAL/UNCERTAIN).
- `embedding_service.py`: FastEmbed (384 dims).
- `knowledge_service.py`: pgvector RAG retrieval.
- `war_room_wall.py`: shaping for the חדר מבצעים wall display (`?style=wall`) —
  colour tone per mission (time-to-target, never the quadrant), worst-first
  ordering, and the pagination the TV screen rotates through. Pure functions over
  loaded ORM objects; the router only queries and passes the result to the template.
- `war_room_kpis.py`: the four KPI cards of חדר מבצעים and the board filter each
  one turns on (`?kpi=`). Owns which KPIs exist, which stat each shows and which
  rows it selects; the counters stay board-wide, only the list narrows.
- `war_room_chain.py`: משימת המשך — walks `missions.parent_id` upwards and returns
  the whole line of missions that led to the live one (oldest first, the live one
  last). A closed parent is never a card on the open board; it is a link in here.
- `analysis_cache.py`: short-TTL (15 min) in-process cache for the ◈ ניתוח AI dashboard
  panels. Losing it costs one rebuild — unlike the חדר מבצעים day-cache in
  `missions_report_service.py`, which is a shared once-a-day artifact and is
  persisted to `mission_report_cache` so a Railway redeploy cannot lose it.

## Database Tables
- `users`: Includes hierarchy_level and manager_id for approval flows.
- `decisions`: Stores AI summary, confidence, and self_critique.
- `lessons_learned`: pgvector storage for RAG.
- `mission_report_cache`: today's חדר מבצעים XLSX + AI summary, keyed (day, kind).
- `mission_due_changes`: one row per postponement of a mission's target date —
  old date, new date, reason, who asked, who changed it, when. Written by
  `oms.update_mission` itself, so no path can move a date without a trace.
- `missions.parent_id`: the mission a follow-up grew out of (SET NULL, never
  CASCADE — losing a parent must not delete open work).

## Technical Nuances
- **Approval Flow:** CRITICAL/UNCERTAIN statuses trigger inline buttons for managers.
- **Feedback Loop:** 48-hour scheduler via `feedback_service.py`.
- **AI panels:** every `*/ai-analysis` endpoint counts in SQL (never hydrates the
  whole result set to tally it) and serves repeats from `analysis_cache`.
  `?refresh=1` forces a rebuild; failures are never cached.
- **Migrations:** `app/utils/migrations.py` handles auto-hashing of the default "1234" password.