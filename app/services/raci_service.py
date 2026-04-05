"""RACI service — AI-powered role assignment (Responsible/Accountable/Consulted/Informed)."""

import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.config import settings
from app.models import User, Decision, DecisionRaciRole, RaciRoleEnum, DecisionStatusEnum

logger = logging.getLogger(__name__)

ROLE_HE = {
    "project_manager": "מנהל פרויקט",
    "department_manager": "מנהל מחלקה",
    "deputy_division_manager": "סגן מנהל אגף",
    "division_manager": "מנהל אגף",
}


async def get_ai_raci_suggestions_from_text(problem_text: str) -> list[dict]:
    """
    Call AI and return RACI suggestions based on free-text problem description.
    Does NOT require a decision to exist yet.
    Returns [] on any failure.
    """
    from app.database import async_session_maker

    logger.info(f"get_ai_raci_suggestions_from_text: starting with {len(problem_text)} chars")

    try:
        async with async_session_maker() as session:
            # Load all users
            all_users_q = await session.execute(select(User))
            all_users: dict[int, User] = {u.id: u for u in all_users_q.scalars().all()}
            if not all_users:
                return []

            users_desc = []
            for u in all_users.values():
                role_he = ROLE_HE.get(u.role.value, u.role.value) if u.role else "—"
                manager = all_users.get(u.manager_id)
                manager_str = f", מנהל: {manager.username}" if manager else ""
                hierarchy = f", רמה {u.hierarchy_level}" if u.hierarchy_level else ""
                resp_str = f", תחום: {u.responsibilities}" if u.responsibilities else ""
                users_desc.append(
                    f"- ID={u.id} | {u.username} | {u.job_title or role_he}{hierarchy}{manager_str}{resp_str}"
                )

            prompt = f"""אתה מומחה לניהול RACI בארגונים.

הגדרות תפקידים:
- R (Responsible) = האחראי לביצוע ההחלטה
- A (Accountable) = בעל הסמכות הסופית — חייב להיות אחד בלבד, ורצוי מנהל בכיר
- C (Consulted) = מייעץ — צריך להישאל לפני ביצוע
- I (Informed) = מקבל עדכון בלבד לאחר הביצוע

בעיה/החלטה:
{problem_text}

משתמשים זמינים:
{chr(10).join(users_desc)}

הנחיות:
1. בחר Accountable מהדרגים הגבוהים (מנהל אגף או סגן מנהל אגף)
2. בחר Responsible מהמבצעים הישירים הקרובים לנושא
3. הגבל Consulted ל-3 לכל היותר
4. לכל משתמש תפקיד אחד בלבד
5. חייב להיות בדיוק A אחד

החזר JSON בלבד:
{{"raci_distribution": [{{"user_id": מספר, "role": "R|A|C|I"}}]}}"""

            from app.services.groq_client import groq_chat
            raw = await groq_chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                json_mode=True,
            )

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end == 0:
                    logger.warning(f"get_ai_raci_suggestions_from_text: no JSON in response")
                    return []
                parsed = json.loads(raw[start:end])

            raci_list = parsed.get("raci_distribution", [])
            logger.info(f"get_ai_raci_suggestions_from_text: AI returned {len(raci_list)} RACI items: {raci_list}")

            valid_roles = {e.value for e in RaciRoleEnum}
            seen_users: set[int] = set()
            valid_items = []
            for item in raci_list:
                try:
                    uid = int(item["user_id"])
                    role = str(item.get("role", "")).strip().upper()
                except (KeyError, TypeError, ValueError):
                    continue
                if uid not in all_users or role not in valid_roles or uid in seen_users:
                    continue
                seen_users.add(uid)
                valid_items.append({"user_id": uid, "role": role})

            # Enforce exactly one A
            a_count = sum(1 for i in valid_items if i["role"] == "A")
            if a_count > 1:
                first_a = False
                valid_items = [i for i in valid_items if i["role"] != "A" or (not first_a and (first_a := True))]
            elif a_count == 0:
                # Fallback: assign A to first manager in list
                for u in all_users.values():
                    if u.role and u.role.value in ["division_manager", "deputy_division_manager"]:
                        valid_items.append({"user_id": u.id, "role": "A"})
                        break
                if a_count == 0:
                    # Still no A? Use first user as fallback
                    first_user = next(iter(all_users.values()))
                    valid_items.append({"user_id": first_user.id, "role": "A"})

            logger.info(f"get_ai_raci_suggestions_from_text: {len(valid_items)} suggestions")
            return valid_items

    except Exception as e:
        logger.error(f"get_ai_raci_suggestions_from_text: failed: {e}", exc_info=True)
        return []


async def assign_raci_from_ai(decision_id: int) -> None:
    """
    Fetch decision + all users, ask Groq to assign RACI roles, validate per-item, save to DB.
    Always opens its own DB session. Never raises — all failures are logged and swallowed.
    """
    from app.database import async_session_maker

    try:
        async with async_session_maker() as session:
            # 1. Load decision
            decision = await session.get(Decision, decision_id)
            if not decision:
                logger.warning(f"assign_raci_from_ai: decision {decision_id} not found")
                return

            # 2. Load all users
            all_users_q = await session.execute(select(User))
            all_users: dict[int, User] = {u.id: u for u in all_users_q.scalars().all()}
            if not all_users:
                logger.warning(f"assign_raci_from_ai: no users found, skipping")
                return

            # 3. Build user roster for prompt — includes responsibilities for smarter RACI
            users_desc = []
            for u in all_users.values():
                role_he = ROLE_HE.get(u.role.value, u.role.value) if u.role else "—"
                manager = all_users.get(u.manager_id)
                manager_str = f", מנהל: {manager.username}" if manager else ""
                hierarchy = f", רמה {u.hierarchy_level}" if u.hierarchy_level else ""
                resp_str = f", תחום: {u.responsibilities}" if u.responsibilities else ""
                users_desc.append(
                    f"- ID={u.id} | {u.username} | {u.job_title or role_he}{hierarchy}{manager_str}{resp_str}"
                )

            submitter = all_users.get(decision.submitter_id)
            submitter_str = f"{submitter.username} | {submitter.job_title or ''}" if submitter else "—"

            type_he = {
                "info": "מידע", "normal": "רגיל",
                "critical": "קריטי", "uncertain": "לא ודאי"
            }.get(decision.type.value, decision.type.value)

            # Fetch RACI patterns from successful past decisions
            raci_patterns = ""
            try:
                from app.services.lessons_service import get_raci_patterns
                raci_patterns = await get_raci_patterns(decision.type.value, session)
            except Exception:
                pass

            prompt = f"""אתה מומחה לניהול RACI בארגונים.

הגדרות תפקידים:
- R (Responsible) = האחראי לביצוע ההחלטה
- A (Accountable) = בעל הסמכות הסופית — חייב להיות אחד בלבד, ורצוי מנהל בכיר
- C (Consulted) = מייעץ — צריך להישאל לפני ביצוע
- I (Informed) = מקבל עדכון בלבד לאחר הביצוע

מגיש: {submitter_str}
סוג החלטה: {type_he}
סיכום: {decision.summary or '—'}
פעולה מומלצת: {decision.recommended_action or '—'}

משתמשים זמינים:
{chr(10).join(users_desc)}

{raci_patterns}

הנחיות RACI:
1. בחר Accountable מהדרגים הגבוהים — מנהל אגף או סגן מנהל אגף
2. בחר Responsible מהמבצעים הישירים
3. הגבל Consulted ל-3 לכל היותר
4. הוסף Informed לכל מי שצריך לדעת אבל לא לפעול
5. לכל משתמש תפקיד אחד בלבד
6. אם יש דפוסי RACI מוצלחים מהעבר — בכר אותם

הנחיות responsibility_updates:
- אם הסיבה לבחירת משתמש מצביעה על תחום אחריות שאינו רשום בשדה "תחום" שלו — הוסף אותו.
- כתוב ביטויים קצרים (2-5 מילים), בעברית.
- אל תחזור על מה שכבר רשום בשדה "תחום" של המשתמש.
- אם אין מה להוסיף — השאר רשימה ריקה.

החזר JSON בלבד:
{{
  "raci_distribution": [{{"user_id": מספר, "role": "R|A|C|I", "reason": "סיבה קצרה"}}],
  "responsibility_updates": [{{"user_id": מספר, "learned": "תחום חדש שנלמד"}}]
}}"""

            # 4. Call Groq
            from app.services.groq_client import groq_chat
            raw = await groq_chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                json_mode=True,
            )

            # 5. Parse JSON
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Fallback: find JSON object in response
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start == -1 or end == 0:
                    logger.warning(f"assign_raci_from_ai: no JSON in Groq response for decision {decision_id}")
                    return
                parsed = json.loads(raw[start:end])

            raci_list = parsed.get("raci_distribution", [])
            if not isinstance(raci_list, list):
                logger.warning(f"assign_raci_from_ai: unexpected format for decision {decision_id}")
                return

            # 6. Per-item validation (keep valid, discard invalid — NOT all-or-nothing)
            valid_roles = {e.value for e in RaciRoleEnum}
            seen_users: set[int] = set()
            valid_items = []
            for item in raci_list:
                try:
                    uid = int(item["user_id"])
                    role = str(item.get("role", "")).strip().upper()
                except (KeyError, TypeError, ValueError):
                    continue
                if uid not in all_users:
                    logger.debug(f"assign_raci_from_ai: unknown user_id {uid}, skipping")
                    continue
                if role not in valid_roles:
                    logger.debug(f"assign_raci_from_ai: invalid role '{role}', skipping")
                    continue
                if uid in seen_users:
                    logger.debug(f"assign_raci_from_ai: duplicate user_id {uid}, skipping")
                    continue
                seen_users.add(uid)
                valid_items.append({"user_id": uid, "role": role})

            if not valid_items:
                logger.warning(f"assign_raci_from_ai: no valid RACI items for decision {decision_id}")
                return

            # 7. Enforce exactly one Accountable (A) role
            roles_assigned = [i["role"] for i in valid_items]
            a_count = roles_assigned.count("A")

            if a_count > 1:
                # Multiple A roles: keep only the first Accountable
                logger.warning(f"assign_raci_from_ai: multiple A roles found ({a_count}), keeping only first")
                first_a_seen = False
                filtered = []
                for item in valid_items:
                    if item["role"] == "A":
                        if not first_a_seen:
                            first_a_seen = True
                            filtered.append(item)
                    else:
                        filtered.append(item)
                valid_items = filtered

            elif a_count == 0:
                # No Accountable role: assign to submitter or their manager
                logger.warning(f"assign_raci_from_ai: no A role found, assigning to submitter or their manager")

                submitter = all_users.get(decision.submitter_id)
                a_user_id = decision.submitter_id

                # If submitter is not a manager, try to assign to their manager
                if submitter and submitter.manager_id:
                    if submitter.role and submitter.role.value not in ["division_manager", "deputy_division_manager", "department_manager", "project_manager"]:
                        manager = all_users.get(submitter.manager_id)
                        if manager:
                            a_user_id = manager.id
                            logger.info(f"assign_raci_from_ai: submitter is not a manager, assigning A role to their manager {manager.username} (ID: {manager.id})")
                        else:
                            logger.info(f"assign_raci_from_ai: submitter's manager not found, assigning A role to submitter (ID: {decision.submitter_id})")
                    else:
                        logger.info(f"assign_raci_from_ai: submitter is a manager, assigning A role to submitter (ID: {decision.submitter_id})")
                else:
                    logger.info(f"assign_raci_from_ai: submitter has no manager, assigning A role to submitter (ID: {decision.submitter_id})")

                valid_items.append({"user_id": a_user_id, "role": "A"})

            # 8. Save to DB
            for item in valid_items:
                raci_row = DecisionRaciRole(
                    decision_id=decision_id,
                    user_id=item["user_id"],
                    role=RaciRoleEnum(item["role"]),
                    assigned_by_ai=True,
                )
                session.add(raci_row)

            await session.commit()
            logger.info(
                f"assign_raci_from_ai: saved {len(valid_items)} RACI roles for decision {decision_id}: "
                + ", ".join(f"{i['role']}={i['user_id']}" for i in valid_items)
            )

            # 8b. Create distribution records from RACI assignments
            from app.models import DecisionDistribution, DistributionTypeEnum, DistributionStatusEnum
            from datetime import datetime as _dt
            _raci_to_dist = {"R": "execution", "C": "info", "I": "info"}
            for item in valid_items:
                if item["role"] == "A":
                    # Accountable = submitter → auto-approve, no distribution record needed
                    if item["user_id"] == decision.submitter_id:
                        decision.status = DecisionStatusEnum.APPROVED
                        decision.completed_at = _dt.utcnow()
                        logger.info(f"assign_raci_from_ai: auto-approved decision {decision_id} (accountable = submitter)")
                        continue
                    dist_type = "approval"
                elif item["role"] in _raci_to_dist:
                    dist_type = _raci_to_dist[item["role"]]
                else:
                    continue
                session.add(DecisionDistribution(
                    decision_id=decision_id,
                    user_id=item["user_id"],
                    distribution_type=DistributionTypeEnum(dist_type),
                    status=DistributionStatusEnum.PENDING,
                    sent_at=_dt.utcnow(),
                ))
            await session.commit()

            # 9. Apply AI-learned responsibility updates
            resp_updates = parsed.get("responsibility_updates", [])
            if isinstance(resp_updates, list):
                for upd in resp_updates:
                    try:
                        uid = int(upd.get("user_id", 0))
                        learned = str(upd.get("learned", "")).strip()
                        if not uid or not learned or uid not in all_users:
                            continue
                        user = all_users[uid]
                        existing = (user.responsibilities or "").strip()
                        # Only append if the learned phrase isn't already covered
                        if learned.lower() not in existing.lower():
                            user.responsibilities = f"{existing}, {learned}".lstrip(", ") if existing else learned
                            logger.info(f"assign_raci_from_ai: updated responsibilities for user {user.username}: +'{learned}'")
                    except Exception as upd_err:
                        logger.debug(f"assign_raci_from_ai: skipping responsibility update: {upd_err}")
                await session.commit()

            # 10. Notify all assigned RACI users
            await notify_all_raci_users(decision_id, session)

    except Exception as e:
        logger.error(f"assign_raci_from_ai: failed for decision {decision_id}: {e}", exc_info=True)


async def _send_raci_telegram(bot, decision_id: int, decision, user, role: str) -> None:
    """Send a single RACI notification to one user."""
    import html as _html
    if not user.telegram_id:
        logger.debug(f"_send_raci_telegram: user {user.id} has no telegram_id, skipping")
        return

    logger.info(f"_send_raci_telegram: starting for user {user.id} ({user.username}), telegram_id={user.telegram_id}, role={role}")

    role_intro = {
        "R": "👤 אתה מוגדר כ-<b>אחראי ביצוע</b>",
        "A": "🧠 אתה מוגדר כ-<b>בעל סמכות</b> (Accountable)",
        "C": "💬 אתה מוגדר כ-<b>יועץ</b> (Consulted)",
        "I": "📢 הנך <b>מעודכן</b> על ההחלטה (Informed)",
    }
    role_note = {
        "R": "נדרשת ממך ביצוע הפעולה המומלצת.",
        "A": "נדרש אישורך לפני ביצוע ההחלטה.",
        "C": "ייתכן שתיפגש לייעוץ לפני הביצוע.",
        "I": "אין צורך בפעולה מצדך.",
    }
    summary = _html.escape(decision.summary or "—")
    action  = _html.escape(decision.recommended_action or "—")
    msg = (
        f"\u200F{role_intro[role]} להחלטה <b>#{decision_id}</b>\n\n"
        f"📋 <b>סיכום:</b> {summary}\n"
        f"🎯 <b>פעולה מומלצת:</b> {action}\n\n"
        f"<i>{role_note[role]}</i>"
    )

    logger.info(f"_send_raci_telegram: message prepared, length={len(msg)}, calling bot.send_message...")

    try:
        result = await bot.send_message(chat_id=user.telegram_id, text=msg, parse_mode="HTML")
        logger.info(f"_send_raci_telegram: sent notification to user {user.id} ({user.username}) for decision {decision_id} role {role}, message_id={result.message_id}")
    except Exception as e:
        logger.warning(f"_send_raci_telegram: failed to notify user {user.id} ({user.username}): {e}", exc_info=True)


async def notify_all_raci_users(decision_id: int, session: AsyncSession) -> None:
    """
    Send a tailored Telegram message to every user assigned a RACI role for this decision.
    Used on first assignment (e.g. AI auto-assign). For manual edits use notify_changed_raci_users.
    """
    try:
        from app.services.telegram_polling import telegram_bot
        bot = telegram_bot.application.bot

        decision = await session.get(Decision, decision_id)
        if not decision:
            return

        rows = (await session.execute(
            select(DecisionRaciRole, User)
            .join(User, DecisionRaciRole.user_id == User.id)
            .where(DecisionRaciRole.decision_id == decision_id)
        )).all()

        for raci_row, user in rows:
            await _send_raci_telegram(bot, decision_id, decision, user, raci_row.role.value)

    except Exception as e:
        logger.warning(f"notify_all_raci_users: error for decision {decision_id}: {e}")


async def notify_changed_raci_users(
    decision_id: int,
    old_assignments: dict[int, str],
    new_assignments: dict[int, str],
    session: AsyncSession,
) -> None:
    """
    Notify only users whose RACI role was newly added or changed.
    old_assignments / new_assignments: {user_id: role_value}
    Users removed from RACI are silently skipped (no notification).
    """
    logger.info(f"notify_changed_raci_users: called for decision {decision_id}")
    logger.info(f"  old_assignments: {old_assignments}")
    logger.info(f"  new_assignments: {new_assignments}")

    changed_user_ids = {
        uid for uid, role in new_assignments.items()
        if old_assignments.get(uid) != role
    }

    logger.info(f"  changed_user_ids: {changed_user_ids}")

    if not changed_user_ids:
        logger.debug(f"notify_changed_raci_users: no changes for decision {decision_id}, skipping")
        return

    logger.info(f"notify_changed_raci_users: starting notification for decision {decision_id}, changed users: {changed_user_ids}")

    try:
        from app.services.telegram_polling import telegram_bot

        if not telegram_bot:
            logger.warning(f"notify_changed_raci_users: telegram_bot is None for decision {decision_id}")
            return

        if not telegram_bot.application:
            logger.warning(f"notify_changed_raci_users: telegram_bot.application is None for decision {decision_id}")
            return

        bot = telegram_bot.application.bot

        if not bot:
            logger.warning(f"notify_changed_raci_users: bot is None for decision {decision_id}")
            return

        decision = await session.get(Decision, decision_id)
        if not decision:
            logger.warning(f"notify_changed_raci_users: decision {decision_id} not found")
            return

        rows = (await session.execute(
            select(DecisionRaciRole, User)
            .join(User, DecisionRaciRole.user_id == User.id)
            .where(DecisionRaciRole.decision_id == decision_id)
            .where(DecisionRaciRole.user_id.in_(changed_user_ids))
        )).all()

        logger.info(f"notify_changed_raci_users: found {len(rows)} users to notify for decision {decision_id}")

        for raci_row, user in rows:
            await _send_raci_telegram(bot, decision_id, decision, user, raci_row.role.value)

        logger.info(
            f"notify_changed_raci_users: completed notification for decision {decision_id}: "
            + ", ".join(str(uid) for uid in changed_user_ids)
        )
    except Exception as e:
        logger.warning(f"notify_changed_raci_users: error for decision {decision_id}: {e}", exc_info=True)


async def get_accountable_user_id(decision_id: int, session: AsyncSession) -> int | None:
    """Return the user_id of the Accountable (A) for a decision, or None if not assigned."""
    return await session.scalar(
        select(DecisionRaciRole.user_id)
        .where(DecisionRaciRole.decision_id == decision_id)
        .where(DecisionRaciRole.role == RaciRoleEnum.ACCOUNTABLE)
        .limit(1)
    )


async def check_and_auto_approve(decision_id: int, session: AsyncSession) -> bool:
    """
    If decision's RACI A is the submitter, auto-approve it.
    Returns True if auto-approved, False otherwise.
    """
    from datetime import datetime as _dt

    decision = await session.get(Decision, decision_id)
    if not decision or decision.status != DecisionStatusEnum.PENDING:
        return False

    accountable_id = await session.scalar(
        select(DecisionRaciRole.user_id)
        .where(DecisionRaciRole.decision_id == decision_id)
        .where(DecisionRaciRole.role == RaciRoleEnum.ACCOUNTABLE)
    )

    if accountable_id and accountable_id == decision.submitter_id:
        decision.status = DecisionStatusEnum.APPROVED
        decision.completed_at = _dt.utcnow()
        await session.commit()
        logger.info(f"check_and_auto_approve: auto-approved decision {decision_id} (accountable = submitter)")
        return True
    return False


async def get_raci_summary(decision_id: int, session: AsyncSession) -> dict[str, list[str]]:
    """Return RACI usernames grouped by role: {"R": [...], "A": [...], "C": [...], "I": [...]}"""
    rows = await session.execute(
        select(DecisionRaciRole, User)
        .join(User, DecisionRaciRole.user_id == User.id)
        .where(DecisionRaciRole.decision_id == decision_id)
    )
    result: dict[str, list[str]] = {"R": [], "A": [], "C": [], "I": []}
    for raci_row, user in rows.all():
        result[raci_row.role.value].append(user.username)
    return result


async def get_raci_counts_for_decisions(decision_ids: list[int], session: AsyncSession) -> dict[int, dict]:
    """
    Bulk-fetch RACI role counts for a list of decision IDs.
    Returns: {decision_id: {"R": n, "A": n, "C": n, "I": n}}
    """
    if not decision_ids:
        return {}
    rows = await session.execute(
        select(DecisionRaciRole.decision_id, DecisionRaciRole.role, func.count().label("cnt"))
        .where(DecisionRaciRole.decision_id.in_(decision_ids))
        .group_by(DecisionRaciRole.decision_id, DecisionRaciRole.role)
    )
    result: dict[int, dict] = {}
    for decision_id, role, cnt in rows.all():
        result.setdefault(decision_id, {"R": 0, "A": 0, "C": 0, "I": 0})
        result[decision_id][role.value] = cnt
    return result


async def save_raci_and_notify_changes(
    decision_id: int,
    new_assignments: dict[int, str],
    session: AsyncSession,
) -> None:
    """
    Save RACI assignments to database and notify only users whose roles changed.

    Args:
        decision_id: The decision to assign RACI for
        new_assignments: Dict of {user_id: role} to assign
        session: Database session
    """
    logger.info(f"save_raci_and_notify_changes: decision_id={decision_id}, new_assignments={new_assignments}")

    try:
        # 1. Get existing assignments (old state)
        old_rows = await session.execute(
            select(DecisionRaciRole)
            .where(DecisionRaciRole.decision_id == decision_id)
        )
        old_assignments: dict[int, str] = {}
        for row in old_rows.scalars():
            old_assignments[row.user_id] = row.role.value
            # Delete old assignment
            await session.delete(row)

        # 2. Insert new assignments
        for user_id, role in new_assignments.items():
            try:
                role_enum = RaciRoleEnum(role.upper())
                new_raci = DecisionRaciRole(
                    decision_id=decision_id,
                    user_id=user_id,
                    role=role_enum,
                )
                session.add(new_raci)
            except (ValueError, KeyError):
                logger.warning(f"save_raci_and_notify_changes: invalid role '{role}' for user {user_id}")
                continue

        await session.commit()
        logger.info(f"save_raci_and_notify_changes: saved {len(new_assignments)} RACI assignments for decision {decision_id}")

        # 3. Notify changed users
        await notify_changed_raci_users(
            decision_id=decision_id,
            old_assignments=old_assignments,
            new_assignments=new_assignments,
            session=session,
        )

    except Exception as e:
        logger.error(f"save_raci_and_notify_changes failed for decision {decision_id}: {e}", exc_info=True)
        await session.rollback()
        raise
