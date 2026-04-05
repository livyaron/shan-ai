"""Distribution service — suggests and sends decision distributions via Telegram."""

import json
import logging
import html as _html
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from groq import AsyncGroq

from app.config import settings
from app.models import (
    User, Decision, DecisionDistribution,
    DecisionTypeEnum, DistributionTypeEnum, DistributionStatusEnum, DecisionStatusEnum
)

logger = logging.getLogger(__name__)

DIST_TYPE_HE = {
    "info": "לידיעה",
    "execution": "לביצוע",
    "approval": "לאישור",
}

STATUS_EMOJI = {
    "pending": "⏳",
    "acknowledged": "👁️",
    "approved": "✅",
    "rejected": "❌",
    "done": "⚡",
}

ROLE_HE = {
    "project_manager": "מנהל פרויקט",
    "department_manager": "מנהל מחלקה",
    "deputy_division_manager": "סגן מנהל אגף",
    "division_manager": "מנהל אגף",
}


async def _load_past_distribution_examples(session: AsyncSession, decision_type: str, limit: int = 5) -> str:
    """
    Load past distribution patterns for similar decision types to use as learning examples.
    Returns a formatted string for the AI prompt.
    """
    rows = await session.execute(
        select(Decision, DecisionDistribution, User)
        .join(DecisionDistribution, DecisionDistribution.decision_id == Decision.id)
        .join(User, DecisionDistribution.user_id == User.id)
        .where(Decision.type == DecisionTypeEnum(decision_type))
        .where(DecisionDistribution.status != DistributionStatusEnum.PENDING)
        .order_by(desc(Decision.created_at))
        .limit(limit * 5)
    )
    rows = rows.all()
    if not rows:
        return ""

    # Group by decision
    by_decision: dict[int, dict] = {}
    for dec, dist, user in rows:
        if dec.id not in by_decision:
            by_decision[dec.id] = {"summary": dec.summary, "dists": []}
        by_decision[dec.id]["dists"].append({
            "user": user.username,
            "role": user.role.value if user.role else "",
            "job_title": user.job_title or "",
            "dist_type": dist.distribution_type.value,
            "outcome": dist.status.value,
        })

    lines = ["דוגמאות מהעבר (למד מהן):"]
    for i, (did, data) in enumerate(list(by_decision.items())[:limit]):
        dists_str = ", ".join(
            f"{d['user']} ({d['job_title'] or d['role']}) → {d['dist_type']} [{d['outcome']}]"
            for d in data["dists"]
        )
        lines.append(f"{i+1}. \"{data['summary'][:80]}\" — {dists_str}")

    return "\n".join(lines)


async def suggest_distribution(decision: Decision, submitter: User, session: AsyncSession) -> list[dict]:
    """
    Returns AI-powered distribution suggestions: [{user_id, username, job_title, dist_type}]
    Uses Groq to analyze the decision and learn from past distribution patterns.
    Falls back to rule-based logic if AI fails.
    """
    all_users_q = await session.execute(select(User).where(User.id != submitter.id))
    all_users = {u.id: u for u in all_users_q.scalars().all()}

    if not all_users:
        return []

    # Build user list for AI prompt
    users_desc = []
    for u in all_users.values():
        role_he = ROLE_HE.get(u.role.value, u.role.value) if u.role else "—"
        manager = all_users.get(u.manager_id)
        manager_str = f", מנהל: {manager.username}" if manager else ""
        hierarchy = f", רמה {u.hierarchy_level}" if u.hierarchy_level else ""
        users_desc.append(
            f"- ID={u.id} | {u.username} | {u.job_title or role_he}{hierarchy}{manager_str}"
        )

    # Load past examples for learning
    past_examples = await _load_past_distribution_examples(session, decision.type.value)

    submitter_role_he = ROLE_HE.get(submitter.role.value, submitter.role.value) if submitter.role else "—"
    submitter_manager = all_users.get(submitter.manager_id)
    submitter_manager_str = f", מנהל: {submitter_manager.username}" if submitter_manager else ""

    type_he = {"info": "מידע", "normal": "רגיל", "critical": "קריטי", "uncertain": "לא ודאי"}.get(
        decision.type.value, decision.type.value
    )

    prompt = f"""אתה מומחה להפצת החלטות ארגוניות במערכת Shan-AI.

עקרונות הפצה:
- "info" = לידיעה בלבד (לא נדרשת פעולה)
- "execution" = לביצוע (הנמען מבצע את ההחלטה)
- "approval" = לאישור (הנמען חייב לאשר/לדחות לפני ביצוע)

כללים:
1. החלטה CRITICAL — המנהל הישיר מקבל approval, מנהל המנהל מקבל info
2. החלטה NORMAL — המנהל הישיר מקבל info, כפיפים מקבלים execution
3. החלטה INFO — המנהל הישיר וצוות קרוב מקבלים info
4. החלטה UNCERTAIN — המנהל הישיר מקבל approval
5. אל תכלול את המגיש עצמו ברשימה
6. החלט לפי היררכיה, תפקיד ורלוונטיות לתוכן ההחלטה

מגיש: {submitter.username} | {submitter.job_title or submitter_role_he}{submitter_manager_str}
סוג החלטה: {type_he}
סיכום: {decision.summary}
פעולה מומלצת: {decision.recommended_action or '—'}

משתמשים זמינים:
{chr(10).join(users_desc)}

{past_examples}

החזר JSON בלבד — מערך של אובייקטים: [{{"user_id": מספר, "dist_type": "info|execution|approval"}}]
כלול רק משתמשים רלוונטיים. אל תוסיף טקסט מחוץ ל-JSON."""

    try:
        from app.services.groq_client import groq_chat
        raw = await groq_chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        # Extract JSON array from response
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON array found")
        ai_suggestions = json.loads(raw[start:end])

        result = []
        for item in ai_suggestions:
            uid = int(item["user_id"])
            dist_type = item["dist_type"]
            if uid in all_users and dist_type in ("info", "execution", "approval"):
                u = all_users[uid]
                result.append({
                    "user_id": uid,
                    "username": u.username,
                    "job_title": u.job_title or "",
                    "dist_type": dist_type,
                })

        logger.info(f"AI suggested {len(result)} recipients for decision #{decision.id}")
        return result

    except Exception as e:
        logger.warning(f"AI distribution suggestion failed, falling back to rules: {e}")
        return _rule_based_suggestion(decision, submitter, all_users)


def _rule_based_suggestion(decision: Decision, submitter: User, all_users: dict) -> list[dict]:
    """Fallback rule-based distribution logic."""
    from app.services.decision_service import SUPERIOR_ROLE

    suggestions: dict[int, str] = {}
    priority = ["info", "execution", "approval"]

    def add(uid, dist_type):
        if uid not in suggestions or priority.index(dist_type) > priority.index(suggestions[uid]):
            suggestions[uid] = dist_type

    manager_id = submitter.manager_id
    if not manager_id and submitter.role:
        sup_role = SUPERIOR_ROLE.get(submitter.role)
        if sup_role:
            mgr = next((u for u in all_users.values() if u.role == sup_role), None)
            if mgr:
                manager_id = mgr.id

    manager_manager_id = None
    if manager_id and manager_id in all_users:
        mgr = all_users[manager_id]
        manager_manager_id = mgr.manager_id
        if not manager_manager_id and mgr.role:
            sup_role = SUPERIOR_ROLE.get(mgr.role)
            if sup_role:
                up = next((u for u in all_users.values() if u.role == sup_role and u.id != submitter.id), None)
                if up:
                    manager_manager_id = up.id

    peers = [uid for uid, u in all_users.items()
             if u.manager_id == submitter.manager_id and u.manager_id is not None]
    reports = [uid for uid, u in all_users.items() if u.manager_id == submitter.id]

    dtype = decision.type
    if dtype == DecisionTypeEnum.INFO:
        if manager_id: add(manager_id, "info")
        for p in peers: add(p, "info")
    elif dtype == DecisionTypeEnum.NORMAL:
        if manager_id: add(manager_id, "info")
        for r in reports: add(r, "execution")
        for p in peers: add(p, "info")
    elif dtype == DecisionTypeEnum.CRITICAL:
        if manager_id: add(manager_id, "approval")
        if manager_manager_id: add(manager_manager_id, "info")
        for p in peers: add(p, "info")
    elif dtype == DecisionTypeEnum.UNCERTAIN:
        if manager_id: add(manager_id, "approval")
        if manager_manager_id: add(manager_manager_id, "info")

    return [
        {"user_id": uid, "username": all_users[uid].username,
         "job_title": all_users[uid].job_title or "", "dist_type": dt}
        for uid, dt in suggestions.items() if uid in all_users
    ]


async def send_distribution(
    decision: Decision,
    submitter: User,
    recipients: list[dict],  # [{user_id, dist_type}]
    session: AsyncSession,
    bot,
    override_type: str = None,
) -> int:
    """
    Saves distributions to DB and sends Telegram messages.
    Returns number of messages sent.
    """
    if override_type:
        try:
            decision.type = DecisionTypeEnum(override_type)
            await session.commit()
        except ValueError:
            pass

    sent = 0
    for rec in recipients:
        user = await session.get(User, rec["user_id"])
        if not user:
            continue

        dist_type = DistributionTypeEnum(rec["dist_type"])

        # Check if distribution already exists
        existing = await session.scalar(
            select(DecisionDistribution)
            .where(DecisionDistribution.decision_id == decision.id)
            .where(DecisionDistribution.user_id == user.id)
        )
        if existing:
            existing.distribution_type = dist_type
            existing.status = DistributionStatusEnum.PENDING
            existing.sent_at = datetime.utcnow()
            dist = existing
        else:
            dist = DecisionDistribution(
                decision_id=decision.id,
                user_id=user.id,
                distribution_type=dist_type,
                status=DistributionStatusEnum.PENDING,
                sent_at=datetime.utcnow(),
            )
            session.add(dist)

        await session.flush()
        await session.refresh(dist)

        if not user.telegram_id:
            continue

        try:
            await _send_telegram_notification(bot, user, decision, submitter, dist)
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send distribution to {user.username}: {e}")

    await session.commit()
    return sent


async def _send_telegram_notification(bot, recipient: User, decision: Decision, submitter: User, dist: DecisionDistribution):
    """Send the appropriate Telegram message based on distribution type."""
    dtype_he = {
        DecisionTypeEnum.INFO: "מידע",
        DecisionTypeEnum.NORMAL: "רגיל",
        DecisionTypeEnum.CRITICAL: "🚨 קריטי",
        DecisionTypeEnum.UNCERTAIN: "❓ לא ודאי",
    }.get(decision.type, decision.type.value)

    e = _html.escape
    header = {
        DistributionTypeEnum.INFO: f"📢 <b>החלטה #{decision.id} — לידיעתך</b>",
        DistributionTypeEnum.EXECUTION: f"⚡ <b>החלטה #{decision.id} — לביצוע</b>",
        DistributionTypeEnum.APPROVAL: f"🔐 <b>החלטה #{decision.id} — נדרש אישורך</b>",
    }[dist.distribution_type]

    submitter_line = e(submitter.username) + (f" — {e(submitter.job_title)}" if submitter.job_title else "")
    body = (
        f"\u200F{header}\n\n"
        f"<b>סוג:</b> {e(dtype_he)}\n"
        f"<b>מגיש:</b> {submitter_line}\n\n"
        f"📋 <b>סיכום:</b>\n{e(decision.summary or '')}\n\n"
        f"🎯 <b>פעולה מומלצת:</b>\n{e(decision.recommended_action or '—')}"
    )

    if dist.distribution_type == DistributionTypeEnum.INFO:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("👁️ קיבלתי לידיעה", callback_data=f"dist_ack:{dist.id}"),
        ]])
    elif dist.distribution_type == DistributionTypeEnum.EXECUTION:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ בוצע", callback_data=f"dist_done:{dist.id}"),
            InlineKeyboardButton("❌ לא יכול לבצע", callback_data=f"dist_reject:{dist.id}"),
        ]])
    else:  # APPROVAL
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ מאשר", callback_data=f"dist_approve:{dist.id}"),
            InlineKeyboardButton("❌ דוחה", callback_data=f"dist_reject:{dist.id}"),
        ]])

    await bot.send_message(
        chat_id=recipient.telegram_id,
        text=body,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def handle_dist_response(
    dist_id: int,
    action: str,
    responder: User,
    notes: str,
    session: AsyncSession,
    bot,
) -> str:
    """Process a distribution response. Returns reply text for the user."""
    dist = await session.get(DecisionDistribution, dist_id)
    if not dist:
        return "❌ הפצה לא נמצאה."
    if dist.user_id != responder.id:
        return "❌ אין לך הרשאה לענות על הפצה זו."

    decision = await session.get(Decision, dist.decision_id)
    submitter = await session.get(User, decision.submitter_id)

    dist.responded_at = datetime.utcnow()
    dist.notes = notes or None

    if action == "ack":
        dist.status = DistributionStatusEnum.ACKNOWLEDGED
        await session.commit()
        return f"👁️ סומן — החלטה #{decision.id} נלקחה לידיעה."

    elif action == "done":
        dist.status = DistributionStatusEnum.DONE
        await session.commit()
        await _notify_submitter(bot, submitter, decision, responder, "⚡ בוצע", notes)
        return f"✅ מצוין! דווח כבוצע עבור החלטה #{decision.id}."

    elif action == "approve":
        # RACI enforcement: only Accountable can approve (if assigned and reachable)
        from app.services.raci_service import get_accountable_user_id
        accountable_id = await get_accountable_user_id(decision.id, session)
        if accountable_id is not None and responder.id != accountable_id:
            accountable_user = await session.get(User, accountable_id)
            if accountable_user and accountable_user.telegram_id:
                return f"⛔ רק {_html.escape(accountable_user.username)} (Accountable) יכול לאשר החלטה זו."
        dist.status = DistributionStatusEnum.APPROVED
        decision.status = DecisionStatusEnum.APPROVED
        decision.completed_at = datetime.utcnow()
        await session.commit()
        await _notify_submitter(bot, submitter, decision, responder, "✅ אושרה", notes)
        return f"✅ החלטה #{decision.id} אושרה. המגיש קיבל הודעה."

    elif action == "reject":
        # RACI enforcement: only Accountable can reject (if assigned and reachable)
        from app.services.raci_service import get_accountable_user_id
        accountable_id = await get_accountable_user_id(decision.id, session)
        if accountable_id is not None and responder.id != accountable_id:
            accountable_user = await session.get(User, accountable_id)
            if accountable_user and accountable_user.telegram_id:
                return f"⛔ רק {_html.escape(accountable_user.username)} (Accountable) יכול לדחות החלטה זו."
        dist.status = DistributionStatusEnum.REJECTED
        decision.status = DecisionStatusEnum.REJECTED
        decision.completed_at = datetime.utcnow()
        await session.commit()
        await _notify_submitter(bot, submitter, decision, responder, "❌ נדחתה", notes)
        return f"❌ החלטה #{decision.id} נדחתה. המגיש קיבל הודעה."

    return "❓ פעולה לא ידועה."


async def _notify_submitter(bot, submitter: User, decision: Decision, responder: User, action_he: str, notes: str):
    if not submitter or not submitter.telegram_id:
        return
    try:
        e = _html.escape
        responder_line = e(responder.username) + (f" — {e(responder.job_title)}" if responder.job_title else "")
        msg = (
            f"\u200F{action_he} <b>החלטה #{decision.id}</b>\n\n"
            f"📋 {e(decision.summary or '')}\n\n"
            f"על ידי: {responder_line}"
            + (f"\n\nהערה: {e(notes)}" if notes else "")
        )
        await bot.send_message(chat_id=submitter.telegram_id, text=msg, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to notify submitter {submitter.username}: {e}")
