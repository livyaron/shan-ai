"""RAG Self-Optimization Service.

Dual-classification: each failing log is categorized as either:
  TERMINOLOGY — missing synonym/acronym gap
  STRUCTURE   — row was split, header missing, column context lost

TERMINOLOGY → upsert to query_synonyms
STRUCTURE   → mark log with failure_type + fix_suggestion for manual re-parse
"""

import json
import logging
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QueryLog, QuerySynonym

logger = logging.getLogger(__name__)

OPTIMIZATION_SYSTEM_PROMPT = """אתה מומחה בשיפור מנועי חיפוש RAG לעברית.
קיבלת רשימה של שאלות שנכשלו — כל אחת עם שאלת המשתמש, תשובת ה-AI, ואם יש — הערת מנהל.

המשימה שלך: עבור כל לוג, קבע את סוג הכשל:
- TERMINOLOGY: המשתמש השתמש במונח שאינו מופיע ישירות בנתונים (מילה נרדפת, ראשי תיבות, שם חלופי)
  דוגמה: המשתמש שאל "חיבור לחשמל" אבל הנתונים מכילים "חישמול"
- STRUCTURE: הנתונים קיימים אך לא הוצגו כהלכה — שורה פוצלה, כותרת עמודה חסרה, הקשר אבד, תא ממוזג לא הועתק לכל השורות
  דוגמה: "שם הפרויקט" מופיע רק בשורה הראשונה של קבוצה, שאר השורות ריקות בשדה זה

חובה להחזיר JSON בלבד (ללא הסברים), בפורמט הזה:
{
  "analysis": [
    {"log_id": 123, "type": "TERMINOLOGY", "original": "המונח של המשתמש", "alternatives": ["מונח מקביל 1", "מונח מקביל 2"]},
    {"log_id": 456, "type": "STRUCTURE", "reason": "שם הפרויקט לא הועתק לשורות הבאות (תא ממוזג)", "fix_suggestion": "עבד מחדש עם מילוי תאים ממוזגים"}
  ]
}

אם הכשל אינו ברור, השתמש ב-STRUCTURE כברירת מחדל.
אם אין כשלים ברורים בכלל, החזר: {"analysis": []}"""


async def run_optimization(session: AsyncSession) -> dict:
    """Analyze failing logs with dual-classification and update DB accordingly."""
    from app.services.groq_client import groq_chat

    # 1. Fetch only unanalyzed logs with negative feedback or admin notes
    stmt = (
        select(QueryLog)
        .where(QueryLog.analyzed == False)  # noqa: E712
        .where(or_(QueryLog.user_feedback == -1, QueryLog.admin_note.isnot(None)))
        .order_by(QueryLog.timestamp.desc())
        .limit(50)
    )
    logs = (await session.execute(stmt)).scalars().all()

    if not logs:
        return {"status": "no_data", "synonyms_added": 0, "logs_processed": 0, "structural_issues": 0}

    # Build a quick id→log map for later updates
    log_map = {log.id: log for log in logs}

    # 2. Build examples for the LLM (include log_id so LLM can reference each one)
    examples = []
    for log in logs:
        entry = f"log_id: {log.id}\nשאלה: {log.question}\nתשובת AI: {log.ai_response[:400]}"
        if log.admin_note:
            entry += f"\nהערת מנהל: {log.admin_note}"
        entry += f"\nפידבק משתמש: {'שלילי' if log.user_feedback == -1 else 'ניטרלי'}"
        examples.append(entry)

    # 3. Call LLM for dual-classification
    try:
        raw = await groq_chat(
            messages=[
                {"role": "system", "content": OPTIMIZATION_SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(examples)},
            ],
            json_mode=True,
            max_tokens=1500,
            temperature=0.2,
        )
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        logger.error(f"Optimization LLM call failed: {e}", exc_info=True)
        return {"status": "llm_error", "error": str(e), "logs_processed": len(logs),
                "synonyms_added": 0, "structural_issues": 0}

    # 4. Process each classified item
    added = 0
    updated = 0
    structural_count = 0
    structural_log_ids = []

    for item in data.get("analysis", []):
        item_type = item.get("type", "").upper()
        log_id = item.get("log_id")
        log = log_map.get(log_id)

        if item_type == "TERMINOLOGY":
            original = item.get("original", "").strip()
            alternatives = [a.strip() for a in item.get("alternatives", []) if a.strip()]
            if not original or not alternatives:
                continue
            existing = await session.scalar(
                select(QuerySynonym).where(QuerySynonym.original == original)
            )
            if existing:
                existing.synonyms = list(set(existing.synonyms + alternatives))
                updated += 1
            else:
                session.add(QuerySynonym(original=original, synonyms=alternatives, source="ai"))
                added += 1
            if log:
                log.failure_type = "TERMINOLOGY"
                log.analyzed = True

        elif item_type == "STRUCTURE":
            structural_count += 1
            if log:
                log.failure_type = "STRUCTURE"
                log.fix_suggestion = item.get("fix_suggestion", "") or item.get("reason", "")
                log.analyzed = True
                structural_log_ids.append(log_id)

    # Mark any remaining unprocessed logs as analyzed to avoid re-fetching
    for log in logs:
        if not log.analyzed:
            log.analyzed = True

    await session.commit()
    logger.info(
        f"Optimization: {len(logs)} logs → {added} synonyms added, "
        f"{updated} updated, {structural_count} structural issues"
    )

    return {
        "status": "ok",
        "logs_processed": len(logs),
        "synonyms_added": added,
        "synonyms_updated": updated,
        "structural_issues": structural_count,
        "structural_log_ids": structural_log_ids,
    }
