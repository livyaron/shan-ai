"""Telegram bot polling handler - runs as background task."""

import asyncio
import html as _html
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from app.config import settings
from app.database import async_session_maker
from app.services.telegram_service import TelegramService
from app.services.decision_service import DecisionService
from app.services import feedback_service
from app.models import User, RoleEnum
from sqlalchemy import select

logger = logging.getLogger(__name__)

# In-memory state: tracks superiors waiting to provide rejection notes
# { telegram_id (int): decision_id (int) }
_awaiting_rejection_note: dict[int, int] = {}

# { telegram_id (int): distribution_id (int) }  — waiting for rejection reason on a distribution
_awaiting_dist_rejection: dict[int, int] = {}


class TelegramPollingBot:
    """Telegram bot that polls for updates."""

    def __init__(self):
        self.application = None

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Log all handler errors to stdout."""
        print(f"[BOT ERROR] Exception while handling update: {context.error}", flush=True)
        import traceback
        traceback.print_exception(type(context.error), context.error, context.error.__traceback__)

    async def initialize(self):
        """Initialize the Telegram bot application."""
        self.application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()

        self.application.add_handler(CommandHandler("start", self.handle_start))
        self.application.add_handler(CommandHandler("register", self.handle_register))
        self.application.add_handler(CommandHandler("status", self.handle_status))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        self.application.add_error_handler(self.error_handler)

        logger.info("Telegram bot handlers registered")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        async with async_session_maker() as session:
            service = TelegramService(session)
            user = await service._get_or_create_user(
                update.effective_user.id, update.effective_user.to_dict()
            )
            await update.message.reply_text(
                f"👋 ברוך הבא ל-<b>Shan-AI</b>, {_html.escape(user.username)}!\n\n"
                f"אני מנתח החלטות טכניות בפרויקטי תשתיות חשמל, טרנספורמטורים ותחנות משנה.\n\n"
                f"<b>פקודות זמינות:</b>\n"
                f"/register — הרשמה למערכת\n"
                f"/status — בדיקת סטטוס ותפקיד\n\n"
                f"לאחר קבלת תפקיד, שלח לי תיאור של הבעיה או ההחלטה ואנתח אותה בעזרת AI.",
                parse_mode="HTML",
            )

    async def handle_register(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /register command. Usage: /register CODE"""
        telegram_id = update.effective_user.id
        code = context.args[0].strip().upper() if context.args else None

        async with async_session_maker() as session:
            # If no code provided, show current status
            if not code:
                result = await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
                existing = result.scalar_one_or_none()
                if existing and existing.role:
                    ROLE_LABELS = {
                        "project_manager": "מנהל פרויקט",
                        "department_manager": "מנהל מחלקה",
                        "deputy_division_manager": "סגן מנהל אגף",
                        "division_manager": "מנהל אגף",
                    }
                    role_label = ROLE_LABELS.get(existing.role.value, existing.role.value)
                    await update.message.reply_text(
                        f"\u200F✅ אתה כבר רשום במערכת.\n<b>שם:</b> {_html.escape(existing.username)}\n<b>תפקיד:</b> {_html.escape(role_label)}",
                        parse_mode="HTML",
                    )
                else:
                    await update.message.reply_text(
                        "\u200Fכדי להירשם, שלח את הקוד שקיבלת מהמנהל:\n<code>/register קוד</code>",
                        parse_mode="HTML",
                    )
                return

            # Look up user by registration code
            result = await session.execute(
                select(User).where(User.registration_code == code)
            )
            user = result.scalar_one_or_none()

            if not user:
                await update.message.reply_text(
                    "\u200F❌ קוד הרשמה לא נמצא. בדוק שהקוד נכון ונסה שוב.",
                )
                return

            if user.telegram_id is not None:
                await update.message.reply_text(
                    "\u200F⚠️ קוד זה כבר נוצמד לחשבון אחר. פנה למנהל לקוד חדש.",
                )
                return

            # Link telegram account and clear the registration code
            user.telegram_id = telegram_id
            user.registration_code = None
            await session.commit()

            ROLE_LABELS = {
                "project_manager": "מנהל פרויקט",
                "department_manager": "מנהל מחלקה",
                "deputy_division_manager": "סגן מנהל אגף",
                "division_manager": "מנהל אגף",
            }
            role_label = ROLE_LABELS.get(user.role.value, user.role.value) if user.role else "—"
            profile_link = f"http://localhost:8000/profile/{user.profile_token}" if user.profile_token else None
            profile_line = f'\n\n🔗 <a href="{profile_link}">עדכן את הפרופיל שלך</a>' if profile_link else ""
            await update.message.reply_text(
                f"\u200F✅ <b>ההרשמה הצליחה!</b>\n\n"
                f"ברוך הבא, {_html.escape(user.username)}!\n"
                f"תפקיד: {_html.escape(role_label)}\n\n"
                f"כעת תוכל לשלוח החלטות לניתוח."
                f"{profile_line}",
                parse_mode="HTML",
            )

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command."""
        async with async_session_maker() as session:
            service = TelegramService(session)
            user = await service._get_or_create_user(
                update.effective_user.id, update.effective_user.to_dict()
            )
            role_text = user.role.value if user.role else "⏳ ממתין לאישור"
            await update.message.reply_text(
                f"\u200F👤 <b>הסטטוס שלך</b>\n\n"
                f"שם: {_html.escape(user.username)}\n"
                f"תפקיד: {_html.escape(role_text)}\n"
                f"מזהה: {user.id}",
                parse_mode="HTML",
            )

    # ------------------------------------------------------------------
    # Message handler — routes through Claude if user has a role
    # ------------------------------------------------------------------

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle regular text messages — pass through decision engine if role assigned."""
        telegram_id = update.effective_user.id
        text = update.message.text
        print(f"[BOT] Received message from {telegram_id}: {text}", flush=True)

        async with async_session_maker() as session:
            service = TelegramService(session)
            user = await service._get_or_create_user(
                telegram_id, update.effective_user.to_dict()
            )

            # Check if user is providing feedback text (post-mortem) after a score
            awaiting_fb = feedback_service.get_awaiting_feedback()
            if telegram_id in awaiting_fb:
                decision_id = awaiting_fb.pop(telegram_id)
                async with async_session_maker() as fb_session:
                    await feedback_service.save_feedback_text(fb_session, decision_id, text)
                await update.message.reply_text(
                    f"\u200F✅ תודה! הפידבק נשמר ויסייע לשיפור ההחלטות הבאות.",
                    parse_mode="HTML",
                )
                return

            # Check if user is providing a numeric rating (1-5) for feedback
            if text.strip() in ("1", "2", "3", "4", "5"):
                async with async_session_maker() as fb_session:
                    stmt = select(User).where(User.telegram_id == telegram_id)
                    fb_user = await fb_session.scalar(stmt)
                    if fb_user:
                        from sqlalchemy import select as sa_select
                        from app.models import Decision, DecisionStatusEnum
                        pending_stmt = (
                            sa_select(Decision)
                            .where(Decision.submitter_id == fb_user.id)
                            .where(Decision.feedback_requested_at.isnot(None))
                            .where(Decision.feedback_score.is_(None))
                            .order_by(Decision.feedback_requested_at.desc())
                            .limit(1)
                        )
                        result = await fb_session.execute(pending_stmt)
                        pending_decision = result.scalar_one_or_none()
                        if pending_decision:
                            await feedback_service.save_feedback_score(
                                fb_session, pending_decision.id, int(text.strip()), telegram_id
                            )
                            await update.message.reply_text(
                                f"\u200F תודה על הדירוג {text.strip()}/5!\n"
                                f"כעת שלח תיאור קצר של מה שקרה בפועל (post-mortem).",
                            )
                            return

            # Check if user is providing a rejection reason for a distribution
            if telegram_id in _awaiting_dist_rejection:
                dist_id = _awaiting_dist_rejection.pop(telegram_id)
                from app.services.distribution_service import handle_dist_response
                reply = await handle_dist_response(dist_id, "reject", user, text, session, self.application.bot)
                await update.message.reply_text(f"\u200F{reply}")
                return

            # Check if this user is waiting to provide a rejection note
            if telegram_id in _awaiting_rejection_note:
                decision_id = _awaiting_rejection_note.pop(telegram_id)
                decision_svc = DecisionService(session, self.application)
                success, msg = await decision_svc.reject_decision(decision_id, user, text)
                await update.message.reply_text(
                    f"{'✅' if success else '❌'} {msg}",
                    parse_mode="HTML",
                )
                return

            # Store the raw message
            await service._store_message(user, text, update.message.message_id)

            # If no role assigned yet, redirect to register
            if not user.role:
                await update.message.reply_text(
                    "⏳ חשבונך ממתין לאישור תפקיד.\n"
                    "השתמש ב-/register לבדיקת הסטטוס."
                )
                return

            # Show typing indicator
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action="typing"
            )

            # Process through decision engine
            decision_svc = DecisionService(session, self.application)
            reply = await decision_svc.process(user, text)

        await update.message.reply_text(reply, parse_mode="HTML")

    # ------------------------------------------------------------------
    # Callback handler — approve / reject inline keyboard buttons
    # ------------------------------------------------------------------

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks for approval/rejection."""
        query = update.callback_query
        await query.answer()

        data = query.data  # "approve:{id}" or "reject:{id}"
        telegram_id = update.effective_user.id

        try:
            action, decision_id_str = data.split(":")
            decision_id = int(decision_id_str)
        except (ValueError, AttributeError):
            await query.edit_message_text("❌ Invalid action.")
            return

        async with async_session_maker() as session:
            # Get the acting user
            stmt = select(User).where(User.telegram_id == telegram_id)
            approver = await session.scalar(stmt)
            if not approver:
                await query.edit_message_text("❌ You are not registered in the system.")
                return

            # --- Distribution responses ---
            if action in ("dist_ack", "dist_done", "dist_approve", "dist_reject"):
                from app.services.distribution_service import handle_dist_response
                dist_id = decision_id  # reusing variable — it's actually dist_id here

                if action == "dist_reject":
                    _awaiting_dist_rejection[telegram_id] = dist_id
                    await query.edit_message_text(
                        f"\u200F❌ *דחייה — החלטה*\n\nאנא שלח את סיבת הדחייה בהודעה הבאה.",
                        parse_mode="HTML",
                    )
                    return

                dist_action = action.replace("dist_", "")
                reply = await handle_dist_response(dist_id, dist_action, approver, None, session, self.application.bot)
                await query.edit_message_text(f"\u200F{reply}", parse_mode="HTML")
                return

            # --- Legacy approve/reject (CRITICAL decision flow) ---
            decision_svc = DecisionService(session, self.application)

            if action == "approve":
                success, msg = await decision_svc.approve_decision(decision_id, approver)
                await query.edit_message_text(
                    f"{'✅' if success else '❌'} {msg}",
                    parse_mode="HTML",
                )

            elif action == "reject":
                _awaiting_rejection_note[telegram_id] = decision_id
                await query.edit_message_text(
                    f"❌ *דחיית החלטה #{decision_id}*\n\n"
                    f"אנא שלח את סיבת הדחייה בהודעה הבאה.",
                    parse_mode="HTML",
                )

    # ------------------------------------------------------------------
    # Polling lifecycle
    # ------------------------------------------------------------------

    async def start_polling(self):
        """Start polling for updates."""
        if not self.application:
            await self.initialize()

        logger.info("Starting Telegram bot polling...")
        async with self.application:
            await self.application.start()
            await self.application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            logger.info("Bot polling started successfully")
            # Keep the context alive so polling continues until task is cancelled
            await asyncio.Event().wait()

    async def stop_polling(self):
        """Stop polling."""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            logger.info("Bot polling stopped")


# Global bot instance
telegram_bot = TelegramPollingBot()
