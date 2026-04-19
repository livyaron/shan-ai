"""Decision routing service - stores decisions and routes them per the spec."""

import json
import logging
import html
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from app.models import User, Decision, DecisionTypeEnum, DecisionStatusEnum, RoleEnum
from app.services.claude_service import ClaudeService
from app.services import embedding_service

logger = logging.getLogger(__name__)

# Role hierarchy: each role's immediate superior
SUPERIOR_ROLE = {
    RoleEnum.PROJECT_MANAGER: RoleEnum.DEPARTMENT_MANAGER,
    RoleEnum.DEPARTMENT_MANAGER: RoleEnum.DEPUTY_DIVISION_MANAGER,
    RoleEnum.DEPUTY_DIVISION_MANAGER: RoleEnum.DIVISION_MANAGER,
    RoleEnum.DIVISION_MANAGER: None,
}

TYPE_EMOJI = {
    "INFO": "ℹ️",
    "NORMAL": "✅",
    "CRITICAL": "🚨",
    "UNCERTAIN": "❓",
}


class DecisionService:
    def __init__(self, session: AsyncSession, application: Application):
        self.session = session
        self.application = application
        self.claude = ClaudeService()

    async def process(self, user: User, text: str, force_approval: bool = False) -> str:
        """
        Full pipeline: Claude → store → route.
        force_approval=True: escalate to CRITICAL regardless of AI classification.
        Returns the reply message to send back to the submitter.
        """
        role_str = user.role.value if user.role else "unknown"

        # --- 1. Fetch similar past decisions + lessons for context (RAG) ---
        similar = await embedding_service.get_similar_decisions(self.session, text)
        past_context = embedding_service.format_past_context(similar)
        if past_context:
            logger.info(f"נמצאו {len(similar)} החלטות דומות מהעבר")

        lessons_context = ""
        try:
            from app.services.lessons_service import (
                get_relevant_lessons, format_lessons_context,
                get_risk_patterns, get_calibration_hint,
            )
            lessons = await get_relevant_lessons(text, self.session, limit=3)
            lessons_context = format_lessons_context(lessons)
            if lessons_context:
                logger.info(f"נמצאו {len(lessons)} לקחים רלוונטיים")

            # Infer probable decision type from similar past decisions
            probable_type = None
            if similar:
                from collections import Counter
                type_counts = Counter(d.type.value for d in similar)
                probable_type = type_counts.most_common(1)[0][0]

            risk_context = ""
            calib_context = ""
            if probable_type:
                risk_context  = await get_risk_patterns(probable_type, self.session)
                calib_context = await get_calibration_hint(probable_type, self.session)

        except Exception as e:
            logger.warning(f"Lessons/patterns context fetch failed: {e}")
            risk_context = calib_context = ""

        combined_context = "\n\n".join(filter(None, [
            past_context, lessons_context, risk_context, calib_context
        ]))

        # --- 2. Analyze with Groq (with injected context) ---
        try:
            result = await self.claude.analyze(text, role_str, combined_context)
        except Exception as e:
            logger.error(f"Claude analysis failed: {e}")
            return "\u200F⚠️ מנוע ההחלטות אינו זמין כרגע. אנא נסה שוב."

        # Apply force_approval escalation before storing
        if force_approval and result["type"] in ("INFO", "NORMAL", "UNCERTAIN"):
            result["type"] = "CRITICAL"
            result["requires_approval"] = True

        # --- 2. Store Decision in DB ---
        decision = Decision(
            submitter_id=user.id,
            type=DecisionTypeEnum(result["type"].lower()),
            status=DecisionStatusEnum.PENDING,
            summary=result["summary"],
            problem_description=text,
            recommended_action=result["recommended_action"],
            requires_approval=result["requires_approval"],
            assumptions=json.dumps(result["self_critique"].get("assumptions", [])),
            risks=json.dumps(result["self_critique"].get("risks", [])),
            measurability=result["measurability"],
        )
        self.session.add(decision)
        await self.session.commit()
        await self.session.refresh(decision)

        logger.info(f"Decision #{decision.id} stored: type={result['type']}")

        # --- 4. Generate and store embedding ---
        try:
            embed_text = f"{text} {result['summary']} {result['recommended_action']}"
            decision.embedding = await embedding_service.embed(embed_text)
            await self.session.commit()
        except Exception as e:
            logger.warning(f"Embedding generation failed for decision #{decision.id}: {e}")

        # --- 5. Propose RACI to submitter for approval (background) ---
        if result["type"] != "CRITICAL":
            try:
                import asyncio
                from app.services.raci_service import propose_raci_to_submitter
                asyncio.get_event_loop().create_task(
                    propose_raci_to_submitter(decision.id, user.telegram_id, is_critical=False)
                )
            except Exception as e:
                logger.warning(f"RACI proposal task could not be scheduled for decision #{decision.id}: {e}")

        # --- 3. Route ---
        dtype = result["type"]

        if dtype == "INFO":
            decision.status = DecisionStatusEnum.EXECUTED
            decision.completed_at = datetime.utcnow()
            await self.session.commit()
            return self._format_info_reply(decision, result)

        elif dtype == "NORMAL":
            decision.status = DecisionStatusEnum.EXECUTED
            decision.completed_at = datetime.utcnow()
            await self.session.commit()
            return self._format_normal_reply(decision, result)

        elif dtype == "CRITICAL":
            reply = await self._handle_critical(user, decision, result)
            return reply

        elif dtype == "UNCERTAIN":
            reply = await self._handle_uncertain(user, decision, result)
            return reply

        return "Decision processed."

    # ------------------------------------------------------------------
    # Reply formatters
    # ------------------------------------------------------------------

    @staticmethod
    def _e(text) -> str:
        """Escape text for Telegram HTML."""
        return html.escape(str(text or ""))

    def _format_info_reply(self, decision: Decision, result: dict) -> str:
        e = self._e
        return (
            f"\u200Fℹ️ <b>החלטה #{decision.id} — מידע בלבד</b>\n\n"
            f"📋 <b>סיכום:</b> {e(result['summary'])}\n"
            f"🎯 <b>פעולה מומלצת:</b> {e(result['recommended_action'])}\n"
            f"📏 <b>מדידות:</b> {e(result['measurability'])}\n\n"
            f"<i>נרשם במערכת. אין צורך בפעולה נוספת.</i>"
        )

    def _format_normal_reply(self, decision: Decision, result: dict) -> str:
        e = self._e
        risks = result["self_critique"].get("risks", [])
        risk_text = "\n".join(f"\u200F  • {e(r)}" for r in risks) if risks else "  לא זוהו סיכונים"
        return (
            f"\u200F✅ <b>החלטה #{decision.id} — רגיל</b>\n\n"
            f"📋 <b>סיכום:</b> {e(result['summary'])}\n"
            f"🎯 <b>פעולה מומלצת:</b> {e(result['recommended_action'])}\n"
            f"📏 <b>מדידות:</b> {e(result['measurability'])}\n\n"
            f"⚠️ <b>סיכונים:</b>\n{risk_text}\n\n"
            f"<i>ההחלטה נרשמה ובוצעה.</i>"
        )

    def _format_critical_pending(self, decision: Decision, result: dict) -> str:
        e = self._e
        return (
            f"\u200F🚨 <b>החלטה #{decision.id} — קריטי</b>\n\n"
            f"📋 <b>סיכום:</b> {e(result['summary'])}\n"
            f"🎯 <b>פעולה מומלצת:</b> {e(result['recommended_action'])}\n\n"
            f"⏳ <b>ממתין לאישור מנהל בכיר.</b>\n"
            f"תקבל הודעה ברגע שתתקבל החלטה."
        )

    def _format_uncertain_pending(self, decision: Decision, result: dict) -> str:
        e = self._e
        return (
            f"\u200F❓ <b>החלטה #{decision.id} — לא ודאי</b>\n\n"
            f"📋 <b>סיכום:</b> {e(result['summary'])}\n\n"
            f"הבינה המלאכותית לא הצליחה לסווג את ההחלטה בביטחון מספיק.\n"
            f"המנהל הבכיר קיבל הודעה ויסווג את ההחלטה באופן ידני.\n\n"
            f"⏳ <b>ממתין לסיווג ידני.</b>"
        )

    # ------------------------------------------------------------------
    # Routing handlers
    # ------------------------------------------------------------------

    async def _handle_critical(self, submitter: User, decision: Decision, result: dict) -> str:
        import asyncio
        from app.services.raci_service import propose_raci_to_submitter

        e = self._e

        # If submitter is top of hierarchy, auto-approve and still propose RACI informatively
        superior = await self._get_superior(submitter)
        if not superior:
            decision.status = DecisionStatusEnum.APPROVED
            decision.completed_at = datetime.utcnow()
            await self.session.commit()
            asyncio.get_event_loop().create_task(
                propose_raci_to_submitter(decision.id, submitter.telegram_id, is_critical=False)
            )
            return (
                f"\u200F🚨 <b>החלטה #{decision.id} — קריטי</b>\n\n"
                f"📋 <b>סיכום:</b> {e(result['summary'])}\n"
                f"🎯 <b>פעולה מומלצת:</b> {e(result['recommended_action'])}\n\n"
                f"<i>אתה בראש ההיררכיה. ההחלטה אושרה אוטומטית.</i>"
            )

        # Propose RACI in background; after submitter approves RACI, the accountable gets the approval request
        asyncio.get_event_loop().create_task(
            propose_raci_to_submitter(decision.id, submitter.telegram_id, is_critical=True)
        )
        return (
            f"\u200F🚨 <b>החלטה #{decision.id} — קריטי</b>\n\n"
            f"📋 <b>סיכום:</b> {e(result['summary'])}\n"
            f"🎯 <b>פעולה מומלצת:</b> {e(result['recommended_action'])}\n\n"
            f"⏳ <b>הצעת RACI נשלחה אליך לאישור.</b>\n"
            f"לאחר האישור, ההחלטה תועבר לבעל הסמכות לאישור סופי."
        )

    async def _handle_uncertain(self, submitter: User, decision: Decision, result: dict) -> str:
        superior = await self._get_superior(submitter)
        if not superior:
            return self._format_uncertain_pending(decision, result)

        e = self._e
        superior_msg = (
            f"\u200F❓ <b>החלטה לא ודאית — נדרש סיווג ידני</b>\n\n"
            f"<b>הוגש על ידי:</b> {e(submitter.username)} ({e(submitter.role.value)})\n"
            f"<b>החלטה #{decision.id}</b>\n\n"
            f"📋 <b>סיכום:</b> {e(result['summary'])}\n"
            f"🎯 <b>הצעת הבינה המלאכותית:</b> {e(result['recommended_action'])}\n\n"
            f"<i>הבינה המלאכותית לא הצליחה לסווג החלטה זו. אנא בדוק וסווג באופן ידני.</i>"
        )

        try:
            await self.application.bot.send_message(
                chat_id=superior.telegram_id,
                text=superior_msg,
                parse_mode="HTML",
            )
            logger.info(f"Sent UNCERTAIN notification to {superior.username} for decision #{decision.id}")
        except Exception as e:
            logger.error(f"Failed to notify superior {superior.username}: {e}")

        return self._format_uncertain_pending(decision, result)

    # ------------------------------------------------------------------
    # Approval / rejection (called from callback handler in bot)
    # ------------------------------------------------------------------

    async def approve_decision(self, decision_id: int, approver: User) -> tuple[bool, str]:
        """Approve a CRITICAL decision. Returns (success, message)."""
        decision = await self.session.get(Decision, decision_id)
        if not decision:
            return False, f"Decision #{decision_id} not found."
        if decision.status != DecisionStatusEnum.PENDING:
            return False, f"Decision #{decision_id} is already {decision.status.value}."

        # RACI enforcement: only Accountable can approve (if assigned and reachable)
        from app.services.raci_service import get_accountable_user_id
        accountable_id = await get_accountable_user_id(decision_id, self.session)
        if accountable_id is not None and approver.id != accountable_id:
            accountable_user = await self.session.get(User, accountable_id)
            if accountable_user and accountable_user.telegram_id:
                name = html.escape(accountable_user.username)
                return False, f"⛔ רק {name} (Accountable) יכול לאשר החלטה זו."
            # Accountable has no telegram_id — fall through to existing logic

        decision.status = DecisionStatusEnum.APPROVED
        decision.completed_at = datetime.utcnow()
        await self.session.commit()

        # Notify submitter
        submitter = await self.session.get(User, decision.submitter_id)
        if submitter:
            try:
                await self.application.bot.send_message(
                    chat_id=submitter.telegram_id,
                    text=(
                        f"\u200F✅ <b>החלטה #{decision.id} אושרה</b>\n\n"
                        f"📋 <b>סיכום:</b> {html.escape(decision.summary or '')}\n"
                        f"🎯 <b>פעולה לביצוע:</b> {html.escape(decision.recommended_action or '')}\n\n"
                        f"אושר על ידי: {html.escape(approver.username)}\n"
                        f"<i>אנא המשך לביצוע הפעולה המומלצת.</i>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Failed to notify submitter: {e}")

        logger.info(f"Decision #{decision_id} approved by {approver.username}")
        return True, f"החלטה #{decision_id} אושרה. המגיש קיבל הודעה."

    async def reject_decision(self, decision_id: int, approver: User, notes: str) -> tuple[bool, str]:
        """Reject a CRITICAL decision with notes."""
        decision = await self.session.get(Decision, decision_id)
        if not decision:
            return False, f"Decision #{decision_id} not found."
        if decision.status != DecisionStatusEnum.PENDING:
            return False, f"Decision #{decision_id} is already {decision.status.value}."

        # RACI enforcement: only Accountable can reject (if assigned and reachable)
        from app.services.raci_service import get_accountable_user_id
        accountable_id = await get_accountable_user_id(decision_id, self.session)
        if accountable_id is not None and approver.id != accountable_id:
            accountable_user = await self.session.get(User, accountable_id)
            if accountable_user and accountable_user.telegram_id:
                name = html.escape(accountable_user.username)
                return False, f"⛔ רק {name} (Accountable) יכול לדחות החלטה זו."

        decision.status = DecisionStatusEnum.REJECTED
        decision.feedback_notes = notes
        decision.completed_at = datetime.utcnow()
        await self.session.commit()

        # Notify submitter
        submitter = await self.session.get(User, decision.submitter_id)
        if submitter:
            try:
                await self.application.bot.send_message(
                    chat_id=submitter.telegram_id,
                    text=(
                        f"\u200F❌ <b>החלטה #{decision.id} נדחתה</b>\n\n"
                        f"📋 <b>סיכום:</b> {html.escape(decision.summary or '')}\n\n"
                        f"<b>סיבת הדחייה:</b>\n{html.escape(notes or '')}\n\n"
                        f"נדחה על ידי: {html.escape(approver.username)}\n"
                        f"<i>אנא בדוק ושלח מחדש במידת הצורך.</i>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Failed to notify submitter: {e}")

        logger.info(f"Decision #{decision_id} rejected by {approver.username}: {notes}")
        return True, f"החלטה #{decision_id} נדחתה. המגיש קיבל הודעה."

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_superior(self, user: User) -> User | None:
        """Find the immediate superior user in the DB."""
        if not user.role:
            return None
        superior_role = SUPERIOR_ROLE.get(user.role)
        if not superior_role:
            return None
        stmt = select(User).where(User.role == superior_role)
        return await self.session.scalar(stmt)
