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
- STRUCTURE: הנתונים קיימים אך לא הוצגו כהלכה — שורה פוצלה, כותרת עמודה חסרה, הקשר אבד, תשובה לא מדויקת

## כלל חובה — answer_instruction
לכל פריט בתוצאה (TERMINOLOGY וגם STRUCTURE), חובה לכלול שדה answer_instruction.
שדה זה מכיל **כלל כללי וישים** שיוזרק לפרומפט של ה-AI בשאלות עתידיות.

### דרישות ל-answer_instruction:
- חייב להיות **כלל כללי** — לא תיאור של מה קרה בשאלה הזו
- חייב להיות בפורמט ציווי: "כאשר X, עשה Y" / "תמיד..." / "אל תעשה..."
- **אסור** לכלול שמות פרויקטים ספציפיים — הכלל צריך לעבוד על כל שאלה דומה
- אם הערת המנהל מתארת בעיה ספציפית (למשל "שכחת לרשום פרויקט X"), חלץ את העיקרון הכללי ("סרוק את כל הנתונים ואל תדלג")
- השדה הזה לא יכול להיות ריק

## פורמט JSON (חובה — ללא הסברים):
{
  "analysis": [
    {
      "log_id": 123,
      "type": "TERMINOLOGY",
      "original": "חיבור לחשמל",
      "alternatives": ["חישמול", "הזנת חשמל"],
      "answer_instruction": "כאשר המשתמש מזכיר 'חיבור לחשמל', חפש גם 'חישמול' ו'הזנת חשמל' בנתונים"
    },
    {
      "log_id": 456,
      "type": "STRUCTURE",
      "reason": "שם הפרויקט לא הועתק לשורות הבאות",
      "fix_suggestion": "עבד מחדש עם מילוי תאים ממוזגים",
      "answer_instruction": "כאשר שם פרויקט לא מופיע בשורה, חפש אותו בשורות הסמוכות. אל תדווח על נתון חסר לפני שסרקת את כל ההקשר."
    }
  ]
}

דגשים:
- answer_instruction הוא שדה חובה בכל פריט — לא יכול להיות ריק או חסר!
- עבור TERMINOLOGY: alternatives חייב להכיל לפחות 2 ערכים שונים.
- אם הכשל אינו ברור, השתמש ב-STRUCTURE כברירת מחדל.
- אם אין כשלים ברורים בכלל, החזר: {"analysis": []}
- אם יש הערת מנהל — חלץ ממנה את העיקרון הכללי ונסח אותו ככלל ישים (אל תעתיק את ההערה כמו שהיא)."""


REORGANIZE_SYNONYMS_PROMPT = """אתה עורך מילון נרדפות לחיפוש בעברית.
קיבלת רשימת כל הרשומות הקיימות: כל רשומה מכילה מונח מקורי (original) ומילים נרדפות שלו.

## המשימה:
1. **אחד רשומות שאותו מושג** — אם "חישמול" ו"חשמול" הם אותו מושג, מזג אותם לרשומה אחת עם canonical ברור.
2. **הסר כפילויות פנימיות** — אם מונח מופיע גם כ-original וגם כ-alternative של אחר, ארגן לרשומה אחת.
3. **נקה מונחים חסרי ערך** — הסר alternatives ריקים, כפולים, או זהים ל-canonical.
4. **canonical = הצורה הנפוצה/רשמית ביותר** — זו שתחפש בנתונים.
5. **alternatives = כל הצורות שמשתמש עשוי לכתוב** — לא לכלול את canonical עצמו.

## פורמט קלט:
[{"id": 1, "original": "חישמול", "synonyms": ["חשמול", "הזנה"]}, ...]

## פורמט פלט (JSON בלבד, ללא הסברים):
{"synonyms": [{"canonical": "חשמול", "alternatives": ["חישמול", "הזנת חשמל", "חיבור לחשמל"]}, ...]}

דגשים:
- אם רשומה תקינה ואין מה למזג — השאר אותה כמות שהיא
- אל תמציא alternatives שלא היו בקלט
- החזר [] אם אין רשומות תקינות לאחר הניקוי"""

REORGANIZE_INSTRUCTIONS_PROMPT = """אתה עורך הוראות מערכת ל-AI שמנתח פרויקטים הנדסיים.
קיבלת את כל ההוראות הקיימות במערכת. ארגן אותן מחדש — לא רק נקה כפילויות, אלא **חשוב מחדש** על כולן.

## המשימה:
1. **מזג הוראות דומות** — שתי הוראות שאומרות בעצם אותו דבר → הוראה אחת ברורה ותמציתית.
2. **שכתב הוראות עמומות** — הפוך כל הוראה לכלל ישים וברור בפורמט ציווי.
3. **הסר הוראות מיושנות** — אם הוראה נבלעה בכלל כללי יותר, הסר אותה.
4. **כלל אחד במקום כמה** — אם אפשר לנסח כלל אחד שמכסה מספר הוראות, עשה זאת.
5. **סדר עדיפות** — הכי חשוב/שכיח קודם.

## פורמט הוראה תקין (כלל כללי בפורמט ציווי):
- "כאשר שואלים על פרויקטים בשלב מסוים, סרוק את כל הפרויקטים ואל תדלג על אחד"
- "תמיד ציין את מספר הפרויקטים הכולל שנמצאו"
- "אם שם פרויקט לא מופיע בשורה, חפש אותו בשורות הסמוכות"

## מה להסיר:
- שמות פרויקטים/אנשים ספציפיים
- תיאור של מה קרה בשאלה מסוימת ("ענית נכון על...", "שכחת לרשום...")
- הוראות שהן תיאור בעיה ולא פתרון

החזר JSON בלבד (ללא הסברים): {"instructions": ["הוראה 1", "הוראה 2", ...]}"""

MERGE_SYSTEM_PROMPT = """אתה עורך הוראות מערכת ל-AI שמנתח פרויקטים הנדסיים.
קיבלת רשימה של הוראות קיימות ורשימה של הוראות חדשות שהתגלו בתהליך אופטימיזציה.

## המשימה שלך:
1. **שכתב כל הוראה גולמית לכלל פעולה ברור** — הוראות חדשות עלולות להגיע כפידבק גולמי (למשל "ענית נכון על X אבל שכחת Y"). חובה להפוך אותן להוראות כלליות וישימות שה-AI יכול ליישם בשאלות עתידיות.
2. **אחד הוראות דומות** לאחת ברורה ותמציתית.
3. **הסר** הוראות שהפכו לא-רלוונטיות, מיושנות, או שנבלעו בהוראה כללית יותר.
4. **שמור על סדר עדיפות**: הוראות חדשות יותר בהתחלה.

## פורמט הוראה תקין:
כל הוראה חייבת להיות **כלל כללי וישים** בפורמט ציווי, למשל:
- "כאשר שואלים על פרויקטים בשלב מסוים, סרוק את כל הפרויקטים בנתונים ואל תדלג על אף אחד"
- "תמיד ציין את מספר הפרויקטים הכולל שנמצאו, לא רק דוגמאות"
- "אם שם פרויקט לא מופיע בשורה, חפש אותו בשורות הסמוכות"

## טיפול בפידבק גולמי:
הוראות שמתחילות ב-"[פידבק גולמי]" הן הערות מנהל שלא עברו עיבוד.
חובה לשכתב אותן לכללים כלליים וישימים. אל תכלול את התג "[פידבק גולמי]" בתוצאה.

## מה לא לכלול:
- שמות פרויקטים ספציפיים (למשל "דימונה", "רוקח") — הכלל צריך להיות כללי
- תיאור של מה קרה בשאלה מסוימת ("ענית נכון על...", "שכחת לרשום...")
- הוראות שהן בעצם תיאור בעיה ולא פתרון

החזר JSON בלבד (ללא הסברים): {"instructions": ["הוראה 1", "הוראה 2", ...]}
"""


async def _merge_and_store_instructions(session: AsyncSession, new_instructions: list[str]) -> None:
    """Merge new instructions with existing ones, deduplicate, and store in __global_instructions__."""
    from app.services.llm_router import llm_chat

    # Fetch existing global instructions
    existing_row = await session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__global_instructions__")
    )
    existing_instructions = existing_row.synonyms if existing_row else []

    if not existing_instructions and not new_instructions:
        return

    # Call LLM to merge intelligently
    try:
        merge_prompt = f"""Existing instructions:
{json.dumps(existing_instructions, ensure_ascii=False, indent=2)}

New instructions discovered:
{json.dumps(new_instructions, ensure_ascii=False, indent=2)}"""

        merged_json = await llm_chat(
            "optimization",
            messages=[
                {"role": "system", "content": MERGE_SYSTEM_PROMPT},
                {"role": "user", "content": merge_prompt},
            ],
            json_mode=True,
            max_tokens=2000,
            temperature=0.1,
        )
        merged_data = json.loads(merged_json) if isinstance(merged_json, str) else merged_json
        merged_instructions = merged_data.get("instructions", new_instructions)

        if existing_row:
            existing_row.synonyms = merged_instructions
            logger.info(f"  ✓ Merged instructions: {len(merged_instructions)} total (was {len(existing_instructions)})")
        else:
            session.add(QuerySynonym(
                original="__global_instructions__",
                synonyms=merged_instructions,
                source="instruction",
            ))
            logger.info(f"  ✓ Created __global_instructions__ with {len(merged_instructions)} entries")
    except Exception as e:
        logger.warning(f"Instruction merge LLM call failed, falling back to simple append: {e}")
        # Fallback: just append new instructions
        merged = list(set(existing_instructions + new_instructions))
        if existing_row:
            existing_row.synonyms = merged
        else:
            session.add(QuerySynonym(
                original="__global_instructions__",
                synonyms=merged,
                source="instruction",
            ))


async def run_optimization(session: AsyncSession) -> dict:
    """Analyze failing logs with dual-classification and update DB accordingly."""
    from app.services.llm_router import llm_chat

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

    # 1b. Pre-collect admin notes from ALL fetched logs BEFORE the LLM call
    # These serve as a safety net: if the LLM skips a log, we still capture the admin's feedback.
    # Stored as (log_id, note) tuples so we can check which were already processed by the LLM.
    pre_collected_admin_notes: list[tuple[int, str]] = []
    admin_note_log_ids: set[int] = set()
    for log in logs:
        if log.admin_note and log.admin_note.strip():
            pre_collected_admin_notes.append((log.id, log.admin_note.strip()))
            admin_note_log_ids.add(log.id)
            logger.info(f"  ✓ Pre-collected admin note from log {log.id}: {log.admin_note[:80]}…")

    # 2. Build examples for the LLM (include log_id so LLM can reference each one)
    examples = []
    for log in logs:
        entry = f"log_id: {log.id}\nשאלה: {log.question}\nתשובת AI: {log.ai_response[:400]}"
        if log.admin_note:
            entry += f"\nהערת מנהל: {log.admin_note}"
        entry += f"\nפידבק משתמש: {'שלילי' if log.user_feedback == -1 else 'ניטרלי'}"
        examples.append(entry)

    # 3. Call LLM for dual-classification — in batches of 10 to avoid token truncation
    BATCH_SIZE = 10
    all_analysis_items = []
    try:
        for batch_start in range(0, len(examples), BATCH_SIZE):
            batch = examples[batch_start:batch_start + BATCH_SIZE]
            logger.info(f"Optimization batch {batch_start // BATCH_SIZE + 1}: processing {len(batch)} logs")
            raw = await llm_chat(
                "optimization",
                messages=[
                    {"role": "system", "content": OPTIMIZATION_SYSTEM_PROMPT},
                    {"role": "user", "content": "\n\n---\n\n".join(batch)},
                ],
                json_mode=True,
                max_tokens=4000,
                temperature=0.2,
            )
            # Parse JSON — handle empty or malformed responses gracefully
            if not raw or not raw.strip():
                logger.warning(f"  Batch {batch_start // BATCH_SIZE + 1}: LLM returned empty response, skipping")
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Try to extract JSON object from surrounding text
                start, end = raw.find("{"), raw.rfind("}") + 1
                if start != -1 and end > start:
                    data = json.loads(raw[start:end])
                else:
                    logger.warning(f"  Batch {batch_start // BATCH_SIZE + 1}: could not parse response as JSON, skipping. Preview: {raw[:200]}")
                    continue
            batch_items = data.get("analysis", [])
            logger.info(f"  Batch returned {len(batch_items)} analysis items")
            all_analysis_items.extend(batch_items)
    except Exception as e:
        logger.error(f"Optimization LLM call failed: {e}", exc_info=True)
        return {"status": "llm_error", "error": str(e), "logs_processed": len(logs),
                "synonyms_added": 0, "structural_issues": 0}

    logger.info(f"Optimization total: {len(all_analysis_items)} analysis items from {len(logs)} logs")

    # 4. Process each classified item
    added = 0
    updated = 0
    structural_count = 0
    structural_log_ids = []
    new_instructions = []  # Collect all instructions for later merge
    processed_log_ids = set(admin_note_log_ids)  # Admin notes are always marked analyzed
    llm_instruction_log_ids: set[int] = set()  # Logs where LLM produced a proper instruction

    for item in all_analysis_items:
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
                processed_log_ids.add(log.id)
            # Collect answer_instruction from TERMINOLOGY items too
            term_instruction = item.get("answer_instruction", "").strip()
            if term_instruction:
                new_instructions.append(term_instruction)
                if log_id:
                    llm_instruction_log_ids.add(log_id)
                logger.info(f"  + TERMINOLOGY instruction for log {log_id}: {term_instruction[:80]}...")

        elif item_type == "STRUCTURE":
            structural_count += 1
            if log:
                log.failure_type = "STRUCTURE"
                log.fix_suggestion = item.get("fix_suggestion", "") or item.get("reason", "")
                log.analyzed = True
                structural_log_ids.append(log_id)
                processed_log_ids.add(log.id)

                # PRIORITY: Prefer LLM's answer_instruction (already formatted as a rule),
                # fall back to admin_note only if LLM didn't generate one
                llm_instruction = item.get("answer_instruction", "").strip()
                instruction_to_use = llm_instruction or (log.admin_note.strip() if log.admin_note else "")
                if instruction_to_use:
                    new_instructions.append(instruction_to_use)
                    if log_id:
                        llm_instruction_log_ids.add(log_id)
                    source = "LLM analysis" if llm_instruction else "admin note (raw)"
                    logger.info(f"  ✓ Collected instruction from {source} for log {log_id}: {instruction_to_use[:80]}…")
            else:
                # No log found but STRUCTURE was flagged — use LLM instruction as fallback
                answer_instruction = item.get("answer_instruction", "").strip()
                if answer_instruction:
                    new_instructions.append(answer_instruction)
                    logger.info(f"  ✓ Collected LLM instruction for log {log_id}: {answer_instruction[:80]}…")

    # 5. Merge instructions into __global_instructions__ (consolidate + deduplicate)
    # Only include pre-collected admin notes for logs where the LLM didn't produce an instruction.
    # Tag them as raw feedback so the merge LLM transforms them into proper rules.
    unprocessed_admin_notes = [
        f"[פידבק גולמי] {note}" for log_id, note in pre_collected_admin_notes
        if log_id not in llm_instruction_log_ids
    ]
    all_instructions = new_instructions + unprocessed_admin_notes
    if all_instructions:
        await _merge_and_store_instructions(session, all_instructions)

    # Mark only processed logs as analyzed — unprocessed ones stay for next run
    for log in logs:
        if not log.analyzed:
            if log.id in processed_log_ids:
                # Admin note was pre-collected; safe to mark analyzed
                log.analyzed = True
                logger.info(f"  + Log {log.id} marked analyzed (admin note pre-collected)")
            else:
                logger.warning(f"  ! Log {log.id} was NOT processed by LLM — leaving for next optimization run")

    await session.commit()
    logger.info(
        f"Optimization: {len(logs)} logs → {added} synonyms added, "
        f"{updated} updated, {structural_count} structural issues, "
        f"{len(all_instructions)} instructions collected"
    )

    # Fetch final stored instructions for reference (what's now in the system)
    final_row = await session.scalar(
        select(QuerySynonym).where(QuerySynonym.original == "__global_instructions__")
    )
    final_instructions = final_row.synonyms if final_row else []
    logger.info(f"Current global instructions ({len(final_instructions)} total):")
    for i, inst in enumerate(final_instructions):
        logger.info(f"  [{i + 1}] {inst[:120]}")

    logger.info(f"Newly collected instructions in this run ({len(all_instructions)} items):")
    for i, inst in enumerate(all_instructions):
        logger.info(f"  NEW [{i + 1}] {inst[:120]}")

    # Auto-reorganize: clean up synonyms + instructions when new content was added
    reorg_result = {}
    if added + updated > 0 or all_instructions:
        logger.info("Triggering auto-reorganize after optimization changes...")
        try:
            reorg_result = await reorganize_knowledge(session)
            logger.info(f"Auto-reorganize: synonyms {reorg_result.get('synonyms_before')}→{reorg_result.get('synonyms_after')}, "
                        f"instructions {reorg_result.get('instructions_before')}→{reorg_result.get('instructions_after')}")
        except Exception as e:
            logger.warning(f"Auto-reorganize failed (non-critical): {e}")

    return {
        "status": "ok",
        "logs_processed": len(logs),
        "synonyms_added": added,
        "synonyms_updated": updated,
        "structural_issues": structural_count,
        "structural_log_ids": structural_log_ids,
        "instructions_collected": len(all_instructions),
        "new_instructions": all_instructions,  # ← Only NEW ones from this run
        "current_instructions": final_instructions,  # ← All stored (for reference)
        "reorganize": reorg_result,
    }


async def reorganize_knowledge(session: AsyncSession) -> dict:
    """LLM-powered full reorganization of synonyms and global instructions.

    - Merges semantically similar synonym entries into one canonical form.
    - Rewrites and consolidates global instructions into clear, actionable rules.
    Called automatically after each optimization run that produced changes,
    and exposed manually via POST /api/knowledge/reorganize.
    """
    from app.models import QuerySynonym
    from app.services.llm_router import llm_chat

    rows = (await session.execute(select(QuerySynonym))).scalars().all()
    synonym_rows = [r for r in rows if r.original != "__global_instructions__" and r.source != "instruction"]
    global_instr_row = next((r for r in rows if r.original == "__global_instructions__"), None)
    instructions = global_instr_row.synonyms if global_instr_row else []

    before_synonyms = len(synonym_rows)
    before_instructions = len(instructions)
    after_synonyms = before_synonyms
    after_instructions = before_instructions

    # ── Reorganize synonyms ──────────────────────────────────────────────────
    if synonym_rows:
        synonym_input = [
            {"id": r.id, "original": r.original, "synonyms": r.synonyms}
            for r in synonym_rows
        ]
        try:
            raw = await llm_chat(
                "optimization",
                messages=[
                    {"role": "system", "content": REORGANIZE_SYNONYMS_PROMPT},
                    {"role": "user", "content": json.dumps(synonym_input, ensure_ascii=False)},
                ],
                json_mode=True,
                max_tokens=3000,
                temperature=0.1,
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            new_synonyms = data.get("synonyms", [])

            # Replace all synonym rows with the reorganized set
            for row in synonym_rows:
                await session.delete(row)
            await session.flush()

            for entry in new_synonyms:
                canonical = entry.get("canonical", "").strip()
                alternatives = [a.strip() for a in entry.get("alternatives", []) if a.strip()]
                if canonical and alternatives:
                    session.add(QuerySynonym(original=canonical, synonyms=alternatives, source="ai"))

            after_synonyms = len(new_synonyms)
            logger.info(f"Reorganize synonyms: {before_synonyms} → {after_synonyms} entries")
        except Exception as e:
            logger.error(f"Synonym reorganization failed: {e}", exc_info=True)

    # ── Reorganize instructions ──────────────────────────────────────────────
    if instructions:
        try:
            raw = await llm_chat(
                "optimization",
                messages=[
                    {"role": "system", "content": REORGANIZE_INSTRUCTIONS_PROMPT},
                    {"role": "user", "content": json.dumps(instructions, ensure_ascii=False)},
                ],
                json_mode=True,
                max_tokens=2000,
                temperature=0.1,
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            new_instructions = data.get("instructions", instructions)  # fallback: keep original

            if global_instr_row:
                global_instr_row.synonyms = new_instructions
            after_instructions = len(new_instructions)
            logger.info(f"Reorganize instructions: {before_instructions} → {after_instructions} entries")
        except Exception as e:
            logger.error(f"Instructions reorganization failed: {e}", exc_info=True)

    await session.commit()

    return {
        "status": "ok",
        "synonyms_before": before_synonyms,
        "synonyms_after": after_synonyms,
        "instructions_before": before_instructions,
        "instructions_after": after_instructions,
    }
