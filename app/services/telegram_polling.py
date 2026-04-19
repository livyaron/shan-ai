"""Telegram bot polling handler - runs as background task."""

import html as _html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from app.config import settings
from app.database import async_session_maker
from app.services.telegram_service import TelegramService
from app.services.decision_service import DecisionService
from app.services import feedback_service
from app.models import User
from app.services.telegram_state import (
    _awaiting_rejection_note, _awaiting_dist_rejection,
    _awaiting_clarification, _awaiting_master_confirm,
    _awaiting_decision_confirm, _awaiting_mgr_approval_confirm,
    _raci_edit_state,
)
from app.services.telegram_routing import (
    _is_data_question, _is_project_query, _maybe_summarize,
    _ai_route_message, _TG_MAX, _DECISION_HISTORY_KEYWORDS,
)
from sqlalchemy import select

logger = logging.getLogger(__name__)

def _mgr_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ כן, נדרש אישור", callback_data="mgr_yes:0"),
        InlineKeyboardButton("❌ לא, בצע ישירות", callback_data="mgr_no:0"),
    ]])


def _user_has_manager(user) -> bool:
    """Return True if this user has an immediate superior in the hierarchy."""
    from app.services.decision_service import SUPERIOR_ROLE
    return bool(user.role and SUPERIOR_ROLE.get(user.role))


def _feedback_keyboard(log_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard with 👍/👎 buttons tied to a query log entry."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 מועיל", callback_data=f"lfb_up:{log_id}"),
        InlineKeyboardButton("👎 לא מועיל", callback_data=f"lfb_dn:{log_id}"),
    ]])


class TelegramPollingBot:
    """Telegram bot that processes updates via webhook."""

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
        self.application.add_handler(CommandHandler("ask", self.handle_ask))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))
        self.application.add_handler(
            MessageHandler(filters.Document.ALL, self.handle_document)
        )
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
        )
        self.application.add_error_handler(self.error_handler)

        logger.info("Telegram bot handlers registered")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command. If called with a registration code (QR deep link), auto-register."""
        telegram_id = update.effective_user.id
        code = context.args[0].strip().upper() if context.args else None

        # Deep-link registration: /start CODE (from QR scan)
        if code:
            await self._do_register(update, telegram_id, code)
            return

        async with async_session_maker() as session:
            service = TelegramService(session)
            user = await service._get_or_create_user(
                telegram_id, update.effective_user.to_dict()
            )
            await update.message.reply_text(
                f"\u200F👋 ברוך הבא ל-<b>Shan-AI</b>, {_html.escape(user.username)}!\n\n"
                f"אני מנתח החלטות טכניות בפרויקטי תשתיות חשמל, טרנספורמטורים ותחנות משנה.\n\n"
                f"<b>פקודות זמינות:</b>\n"
                f"/register קוד — הרשמה למערכת\n"
                f"/status — בדיקת סטטוס ותפקיד\n\n"
                f"לאחר קבלת תפקיד, שלח לי תיאור של הבעיה או ההחלטה ואנתח אותה בעזרת AI.",
                parse_mode="HTML",
            )

    async def _do_register(self, update: Update, telegram_id: int, code: str) -> None:
        """Core registration logic — shared by /start CODE (QR deep link) and /register CODE."""
        from sqlalchemy import update as _sa_update
        from app.models import Message as _Message

        async with async_session_maker() as session:
            # Look up pre-created user by registration code
            user = await session.scalar(
                select(User).where(User.registration_code == code)
            )

            if not user:
                await update.message.reply_text(
                    "\u200F❌ קוד הרשמה לא נמצא. בדוק שהקוד נכון ונסה שוב.",
                )
                return

            if user.telegram_id is not None and user.telegram_id != telegram_id:
                await update.message.reply_text(
                    "\u200F⚠️ קוד זה כבר נוצמד לחשבון אחר. פנה למנהל לקוד חדש.",
                )
                return

            if user.telegram_id == telegram_id:
                # Already linked — just confirm
                ROLE_LABELS = {
                    "project_manager": "מנהל פרויקט", "department_manager": "מנהל מחלקה",
                    "deputy_division_manager": "סגן מנהל אגף", "division_manager": "מנהל אגף",
                }
                role_label = ROLE_LABELS.get(user.role.value, user.role.value) if user.role else "—"
                await update.message.reply_text(
                    f"\u200F✅ אתה כבר רשום במערכת.\n<b>שם:</b> {_html.escape(user.username)}\n<b>תפקיד:</b> {_html.escape(role_label)}",
                    parse_mode="HTML",
                )
                return

            # Remove any roleless placeholder auto-created by /start before registration
            placeholder = await session.scalar(
                select(User).where(
                    User.telegram_id == telegram_id,
                    User.role.is_(None),
                    User.id != user.id,
                )
            )
            if placeholder:
                await session.execute(
                    _sa_update(_Message)
                    .where(_Message.user_id == placeholder.id)
                    .values(user_id=user.id)
                )
                await session.flush()
                await session.delete(placeholder)
                await session.flush()

            user.telegram_id = telegram_id
            user.registration_code = None
            await session.commit()
            await session.refresh(user)

            ROLE_LABELS = {
                "project_manager": "מנהל פרויקט", "department_manager": "מנהל מחלקה",
                "deputy_division_manager": "סגן מנהל אגף", "division_manager": "מנהל אגף",
            }
            role_label = ROLE_LABELS.get(user.role.value, user.role.value) if user.role else "—"
            from app.config import settings as _settings
            profile_link = f"{_settings.BASE_URL}/profile/{user.profile_token}" if user.profile_token else None
            profile_line = f'\n\n🔗 <a href="{profile_link}">עדכן את הפרופיל שלך</a>' if profile_link else ""
            await update.message.reply_text(
                f"\u200F✅ <b>ברוך הבא, {_html.escape(user.username)}!</b>\n\n"
                f"ההרשמה הצליחה!\n"
                f"תפקיד: <b>{_html.escape(role_label)}</b>\n\n"
                f"כעת תוכל לשלוח החלטות לניתוח."
                f"{profile_line}",
                parse_mode="HTML",
            )

    async def handle_register(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /register command. Usage: /register CODE"""
        telegram_id = update.effective_user.id
        code = context.args[0].strip().upper() if context.args else None

        if not code:
            async with async_session_maker() as session:
                existing = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                if existing and existing.role:
                    ROLE_LABELS = {
                        "project_manager": "מנהל פרויקט", "department_manager": "מנהל מחלקה",
                        "deputy_division_manager": "סגן מנהל אגף", "division_manager": "מנהל אגף",
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

        await self._do_register(update, telegram_id, code)

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
    # /ask command — knowledge base Q&A
    # ------------------------------------------------------------------

    async def handle_ask(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/ask <question> — search knowledge base and answer with AI."""
        telegram_id = update.effective_user.id
        question = " ".join(context.args).strip() if context.args else ""

        if not question:
            await update.message.reply_text(
                "\u200Fשימוש: /ask <שאלה>\n\nדוגמה: /ask מה הנהלים להחלפת טרנספורמטור?",
                parse_mode="HTML",
            )
            return

        async with async_session_maker() as session:
            # Verify user is registered
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("\u200F⏳ יש להירשם תחילה. השתמש ב-/register")
                return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

        async with async_session_maker() as session:
            from app.services.knowledge_service import answer_with_full_context
            qa = await answer_with_full_context(question, session, user.id)

        if not qa.get("has_files") and not qa.get("has_decisions"):
            await update.message.reply_text(
                "\u200F📂 לא נמצא מידע רלוונטי בבסיס הידע.\n\nנסה להעלות קבצים רלוונטיים דרך לוח הניהול.",
            )
            return

        reply = f"\u200F🤖 <b>תשובה מבסיס הידע:</b>\n\n{_html.escape(qa['answer'])}"
        if qa.get("sources_text"):
            reply += f"\n\n<i>{_html.escape(qa['sources_text'])}</i>"

        reply = await _maybe_summarize(reply)
        log_id = qa.get("log_id")
        await update.message.reply_text(
            reply,
            parse_mode="HTML",
            reply_markup=_feedback_keyboard(log_id) if log_id else None,
        )

    # ------------------------------------------------------------------
    # Document handler — file upload to knowledge base
    # ------------------------------------------------------------------

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle file uploads — add to knowledge base."""
        telegram_id = update.effective_user.id
        document = update.message.document

        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text(
                    "\u200F⏳ יש להירשם תחילה כדי להעלות קבצים."
                )
                return

        # Check file type
        filename = document.file_name or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("pdf", "docx", "xlsx"):
            await update.message.reply_text(
                "\u200F❌ סוג קובץ לא נתמך.\nמותרים: PDF, DOCX, XLSX בלבד."
            )
            return

        try:
            from pathlib import Path
            import uuid as _uuid
            UPLOAD_DIR = Path("uploads")
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = f"{_uuid.uuid4().hex}_{filename}"
            file_path = UPLOAD_DIR / safe_name

            tg_file = await context.bot.get_file(document.file_id)
            await tg_file.download_to_drive(str(file_path))

            from app.models import KnowledgeFile
            from app.database import async_session_maker as _sm
            async with _sm() as session:
                kf = KnowledgeFile(
                    original_name=filename,
                    file_path=str(file_path),
                    file_type=ext,
                    file_size=document.file_size or 0,
                    uploader_id=user.id,
                    status="processing",
                )
                session.add(kf)
                await session.commit()
                await session.refresh(kf)
                file_id = kf.id

            # For XLSX files — ask if this is the master project file before processing
            if ext == "xlsx":
                _awaiting_master_confirm[telegram_id] = file_id
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("⭐ כן, קובץ מאסטר", callback_data=f"master_yes:{file_id}"),
                    InlineKeyboardButton("📄 לא, קובץ רגיל", callback_data=f"master_no:{file_id}"),
                ]])
                await update.message.reply_text(
                    f"\u200F📁 הקובץ <b>{_html.escape(filename)}</b> התקבל.\n\n"
                    f"⭐ <b>האם זהו קובץ המאסטר של הפרויקטים?</b>\n"
                    f"<i>(קובץ מאסטר עובר עיבוד מיוחד לחילוץ נתוני פרויקטים)</i>",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            else:
                # PDF / DOCX — process immediately without asking
                await update.message.reply_text(
                    f"\u200F📁 הקובץ <b>{_html.escape(filename)}</b> התקבל ומעובד...\nתקבל אישור בסיום.",
                    parse_mode="HTML",
                )
                context.application.create_task(_process_and_notify(
                    file_id, telegram_id, filename, context.bot
                ))

        except Exception as e:
            logger.error(f"Document upload error: {e}", exc_info=True)
            await update.message.reply_text(
                f"\u200F❌ שגיאה בהעלאת הקובץ: {str(e)[:80]}"
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
                if text.strip().lower() != "/skip":
                    async with async_session_maker() as fb_session:
                        await feedback_service.save_feedback_text(fb_session, decision_id, text)
                await update.message.reply_text(
                    "\u200F✅ תודה! הפידבק נשמר ויסייע לשיפור ההחלטות הבאות.",
                    parse_mode="HTML",
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

            # Hard bypass: single-word or clearly non-work messages skip LLM routing entirely
            _NON_WORK_WORDS = {"בדיחה", "בדיחות", "שלום", "היי", "הי", "תודה", "להתראות", "ביי", "בוקר טוב", "ערב טוב", "לילה טוב"}
            if text.strip() in _NON_WORK_WORDS:
                routing = {"route": None, "intent": None, "param": None}
            else:
                routing = await _ai_route_message(text)
            ai_route = routing["route"]
            ai_intent = routing["intent"]
            ai_param = routing["param"]

            # Decision history query — answer from decisions DB
            if ai_route != "decision" and any(kw in text for kw in _DECISION_HISTORY_KEYWORDS):
                kb = None
                try:
                    from app.services.knowledge_service import get_decisions_context, answer_decisions_question
                    decisions_ctx = await get_decisions_context(session, user.id)
                    if decisions_ctx:
                        dec_answer = await answer_decisions_question(text, decisions_ctx)
                    else:
                        dec_answer = "לא נמצאו החלטות עבורך במסד הנתונים."
                    reply = f"\u200F{dec_answer}"
                except Exception as e:
                    logger.warning(f"decisions query failed: {e}")
                    reply = "\u200Fלא הצלחתי לשלוף את ההחלטות."
                await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                return

            if ai_route == "project":
                kb = None
                try:
                    from app.services.project_tools import answer_project_query
                    reply_text, proj_log_id = await answer_project_query(
                        text, session, context.user_data, user_id=user.id,
                        precomputed_intent=ai_intent, precomputed_param=ai_param,
                    )
                    reply = f"\u200F{reply_text}"
                    if proj_log_id:
                        kb = _feedback_keyboard(proj_log_id)
                except Exception as e:
                    logger.warning(f"project_tools failed: {e}")
                    reply = "\u200Fלא הצלחתי למצוא נתוני פרויקט. ודא שהקובץ הראשי הועלה."
                reply = await _maybe_summarize(reply)
                await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                return

            if ai_route == "knowledge":
                kb = None
                try:
                    from app.services.knowledge_service import answer_with_full_context
                    qa_result = await answer_with_full_context(text, session, user.id)
                    reply = f"\u200F🤖 <b>תשובה:</b>\n\n{_html.escape(qa_result['answer'])}"
                    if qa_result.get("sources_text"):
                        reply += f"\n\n<i>{_html.escape(qa_result['sources_text'])}</i>"
                    if qa_result.get("log_id"):
                        kb = _feedback_keyboard(qa_result["log_id"])
                except Exception as e:
                    logger.warning(f"answer_with_full_context failed: {e}")
                    reply = "\u200Fלא הצלחתי למצוא תשובה. נסה לנסח אחרת."
                reply = await _maybe_summarize(reply)
                await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                return

            if ai_route == "decision":
                pass  # falls through to ClaudeService().classify() below

            # AI routing failed — keyword fallback
            if ai_route is None:
                _NON_WORK = {"בדיחה", "בדיחות", "שלום", "היי", "הי", "תודה", "להתראות", "ביי", "בוקר טוב", "ערב טוב", "לילה טוב"}
                if text.strip() not in _NON_WORK and _is_project_query(text):
                    kb = None
                    try:
                        from app.services.project_tools import answer_project_query
                        reply_text, proj_log_id = await answer_project_query(text, session, context.user_data, user_id=user.id)
                        reply = f"\u200F{reply_text}"
                        if proj_log_id:
                            kb = _feedback_keyboard(proj_log_id)
                    except Exception as e:
                        logger.warning(f"project_tools failed (keyword fallback): {e}")
                        reply = "\u200Fלא הצלחתי למצוא נתוני פרויקט. ודא שהקובץ הראשי הועלה."
                    reply = await _maybe_summarize(reply)
                    await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                    return
                if _is_data_question(text):
                    kb = None
                    try:
                        from app.services.knowledge_service import answer_with_full_context
                        qa_result = await answer_with_full_context(text, session, user.id)
                        reply = f"\u200F🤖 <b>תשובה:</b>\n\n{_html.escape(qa_result['answer'])}"
                        if qa_result.get("sources_text"):
                            reply += f"\n\n<i>{_html.escape(qa_result['sources_text'])}</i>"
                        if qa_result.get("log_id"):
                            kb = _feedback_keyboard(qa_result["log_id"])
                    except Exception as e:
                        logger.warning(f"answer_with_full_context failed (keyword fallback): {e}")
                        reply = "\u200Fלא הצלחתי למצוא תשובה. נסה לנסח אחרת."
                    reply = await _maybe_summarize(reply)
                    await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                    return

            # --- Check if this is a clarification response ---
            if telegram_id in _awaiting_clarification:
                original_text = _awaiting_clarification.pop(telegram_id)
                combined_text = f"{original_text}\n\nפרטים נוספים: {text}"
                # If original was a question, answer it; otherwise process as decision
                if _is_data_question(original_text):
                    from app.services.knowledge_service import answer_with_full_context
                    kb = None
                    try:
                        qa = await answer_with_full_context(combined_text, session, user.id)
                        reply = f"\u200F🤖 <b>תשובה:</b>\n\n{_html.escape(qa['answer'])}"
                        if qa.get("sources_text"):
                            reply += f"\n\n<i>{_html.escape(qa['sources_text'])}</i>"
                        if qa.get("log_id"):
                            kb = _feedback_keyboard(qa["log_id"])
                    except Exception:
                        reply = "\u200Fלא הצלחתי למצוא תשובה. נסה לנסח אחרת."
                    reply = await _maybe_summarize(reply)
                    await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                else:
                    if _user_has_manager(user):
                        _awaiting_mgr_approval_confirm[telegram_id] = combined_text
                        await update.message.reply_text(
                            "\u200F👔 <b>האם החלטה זו דורשת אישור מנהל?</b>",
                            parse_mode="HTML",
                            reply_markup=_mgr_approval_keyboard(),
                        )
                    else:
                        decision_svc = DecisionService(session, self.application)
                        reply = await decision_svc.process(user, combined_text)
                        await update.message.reply_text(reply, parse_mode="HTML")
                return

            # --- LLM classify for everything else ---
            from app.services.claude_service import ClaudeService as _CS
            try:
                classify_result = await _CS().classify(text)
                verdict = classify_result.get("verdict", "DECISION")
                logger.info(f"Classification verdict for user {telegram_id}: {verdict}")
            except Exception as e:
                logger.error(f"Classification failed: {e}", exc_info=True)
                await update.message.reply_text(
                    "\u200F⚠️ שגיאה בניתוח הטקסט. נסה שוב.",
                    parse_mode="HTML",
                )
                return

            _dec_confirm_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ כן, זו החלטה", callback_data="dec_conf_y:0"),
                InlineKeyboardButton("❌ לא", callback_data="dec_conf_n:0"),
            ]])

            if verdict == "NOT_DECISION":
                ai_reply = classify_result.get("reply", "")
                if ai_reply:
                    # Send the AI's reply, then still offer to process as a decision
                    await update.message.reply_text(f"\u200F{_html.escape(ai_reply)}", parse_mode="HTML")
                else:
                    # Answer from knowledge base
                    kb = None
                    try:
                        from app.services.knowledge_service import answer_with_full_context
                        qa_result = await answer_with_full_context(text, session, user.id)
                        reply = f"\u200F🤖 <b>תשובה:</b>\n\n{_html.escape(qa_result['answer'])}"
                        if qa_result.get("sources_text"):
                            reply += f"\n\n<i>{_html.escape(qa_result['sources_text'])}</i>"
                        if qa_result.get("log_id"):
                            kb = _feedback_keyboard(qa_result["log_id"])
                    except Exception as e:
                        logger.warning(f"answer_with_full_context failed: {e}")
                        reply = "\u200Fשאל שאלות עבודה או שלח החלטה לניתוח."
                    reply = await _maybe_summarize(reply)
                    await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                # Always ask — user can correct misclassification
                _awaiting_decision_confirm[telegram_id] = text
                await update.message.reply_text(
                    "\u200F❓ <b>האם זוהי החלטה לתיעוד?</b>",
                    parse_mode="HTML",
                    reply_markup=_dec_confirm_kb,
                )
                return

            if verdict == "UNCLEAR":
                _awaiting_decision_confirm[telegram_id] = text
                question = classify_result.get("clarifying_question", "")
                msg = "\u200F❓ <b>לא הצלחתי לסווג את ההודעה. האם זוהי החלטה חדשה?</b>"
                if question:
                    msg += f"\n\n<i>{_html.escape(question)}</i>"
                await update.message.reply_text(msg, parse_mode="HTML", reply_markup=_dec_confirm_kb)
                return

            # DECISION — ask manager approval question first (if user has a manager)
            if _user_has_manager(user):
                _awaiting_mgr_approval_confirm[telegram_id] = text
                await update.message.reply_text(
                    "\u200F👔 <b>האם החלטה זו דורשת אישור מנהל?</b>",
                    parse_mode="HTML",
                    reply_markup=_mgr_approval_keyboard(),
                )
                return
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
            parts = data.split(":")
            action = parts[0]
            # dfb_score:{score}:{decision_id} — 3 parts
            if action == "dfb_score":
                dfb_score_val = int(parts[1])
                decision_id = int(parts[2])
            else:
                dfb_score_val = 0
                decision_id = int(parts[1]) if len(parts) > 1 else 0
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

            # --- Master file confirmation ---
            if action in ("master_yes", "master_no"):
                file_id = decision_id  # reusing variable — it's the knowledge file id
                _awaiting_master_confirm.pop(telegram_id, None)
                from app.models import KnowledgeFile as _KF
                from sqlalchemy import update as _sa_update

                if action == "master_yes":
                    async with async_session_maker() as db:
                        # Unset any existing master
                        await db.execute(_sa_update(_KF).values(is_master=False))
                        kf = await db.get(_KF, file_id)
                        if kf:
                            kf.is_master = True
                            await db.commit()
                            filename = kf.original_name
                        else:
                            filename = "קובץ"
                    await query.edit_message_text(
                        f"\u200F⭐ <b>קובץ מאסטר!</b>\n"
                        f"מעבד את <b>{_html.escape(filename)}</b> בעיבוד מיוחד...\n"
                        f"תקבל אישור בסיום.",
                        parse_mode="HTML",
                    )
                    context.application.create_task(
                        _process_master_and_notify(file_id, telegram_id, filename, context.bot)
                    )
                else:  # master_no
                    async with async_session_maker() as db:
                        kf = await db.get(_KF, file_id)
                        filename = kf.original_name if kf else "קובץ"
                    await query.edit_message_text(
                        f"\u200F📄 מעבד את <b>{_html.escape(filename)}</b>...\nתקבל אישור בסיום.",
                        parse_mode="HTML",
                    )
                    context.application.create_task(
                        _process_and_notify(file_id, telegram_id, filename, context.bot)
                    )
                return

            # --- Query log feedback (👍/👎 on RAG answers) ---
            if action in ("lfb_up", "lfb_dn"):
                from app.models import QueryLog as _QL
                log = await session.get(_QL, decision_id)
                if log:
                    log.user_feedback = 1 if action == "lfb_up" else -1
                    await session.commit()
                emoji = "👍" if action == "lfb_up" else "👎"
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"\u200F{emoji} תודה! הפידבק נשמר.",
                )
                return

            # --- Decision feedback score (1-5 inline buttons) ---
            if action == "dfb_score":
                saved = await feedback_service.save_feedback_score(
                    session, decision_id, dfb_score_val, telegram_id
                )
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                if saved:
                    score_labels = {1: "כישלון מוחלט", 2: "לא טוב", 3: "בסדר", 4: "טוב", 5: "מצוין"}
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=(
                            f"\u200F✅ ציון {dfb_score_val} — <b>{score_labels.get(dfb_score_val, '')}</b> נשמר.\n\n"
                            f"רוצה להוסיף הערה? שלח טקסט, או /skip לדילוג."
                        ),
                        parse_mode="HTML",
                    )
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="\u200F❌ לא נמצאה ההחלטה. ייתכן שהפידבק כבר נשמר.",
                    )
                return

            # --- Distribution responses ---
            if action in ("dist_ack", "dist_done", "dist_approve", "dist_reject"):
                from app.services.distribution_service import handle_dist_response
                dist_id = decision_id  # reusing variable — it's actually dist_id here

                if action == "dist_reject":
                    _awaiting_dist_rejection[telegram_id] = dist_id
                    await query.edit_message_text(
                        "\u200F❌ *דחייה — החלטה*\n\nאנא שלח את סיבת הדחייה בהודעה הבאה.",
                        parse_mode="HTML",
                    )
                    return

                dist_action = action.replace("dist_", "")
                reply = await handle_dist_response(dist_id, dist_action, approver, None, session, self.application.bot)
                await query.edit_message_text(f"\u200F{reply}", parse_mode="HTML")
                return

            # --- RACI proposal: approve / edit ---
            if action in ("raci_approve", "raci_edit"):
                from app.services.raci_service import _pending_raci_suggestions, save_pregenerated_raci

                if action == "raci_edit":
                    pending = _pending_raci_suggestions.get(decision_id)
                    if not pending:
                        await query.edit_message_text("\u200F⚠️ פג תוקף ההצעה. שלח את ההחלטה מחדש.")
                        return
                    from app.models import User as _User
                    async with async_session_maker() as _sess:
                        _all = (await _sess.scalars(
                            select(_User).where(_User.role.isnot(None))
                        )).all()
                        all_users = [{"id": u.id, "name": u.username or str(u.telegram_id)} for u in _all]
                    _raci_edit_state[telegram_id] = {
                        "decision_id": decision_id,
                        "items": [
                            {
                                "user_id": i["user_id"],
                                "role": i["role"],
                                "name": pending["user_names"].get(i["user_id"], str(i["user_id"])),
                            }
                            for i in pending["valid_items"]
                        ],
                        "all_users": all_users,
                        "is_critical": pending.get("is_critical", False),
                        "parsed": pending.get("parsed", {}),
                    }
                    from app.services.raci_service import build_raci_list_message as _blm
                    text, kbd = _blm(decision_id, _raci_edit_state[telegram_id]["items"])
                    await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                    return

                # raci_approve
                pending = _pending_raci_suggestions.pop(decision_id, None)
                if not pending:
                    await query.edit_message_text("\u200F⚠️ לא נמצאה הצעת RACI פעילה לאישור.")
                    return

                await save_pregenerated_raci(decision_id, pending["valid_items"], pending["parsed"])

                # Mark suggestion as accepted for learning
                from app.services.raci_service import mark_raci_accepted as _mark_accepted
                context.application.create_task(_mark_accepted(decision_id))

                # For CRITICAL decisions: after RACI is saved, send approval request to accountable
                if pending.get("is_critical"):
                    from app.services.raci_service import get_accountable_user_id as _get_acc
                    from app.database import async_session_maker as _sm
                    import html as _html2
                    async with _sm() as _sess:
                        accountable_id = await _get_acc(decision_id, _sess)
                        if accountable_id:
                            acc_user = await _sess.get(User, accountable_id)
                            dec = await _sess.get(Decision, decision_id)
                            if acc_user and acc_user.telegram_id and dec:
                                crit_keyboard = InlineKeyboardMarkup([[
                                    InlineKeyboardButton("✅ אישור", callback_data=f"approve:{decision_id}"),
                                    InlineKeyboardButton("❌ דחייה", callback_data=f"reject:{decision_id}"),
                                ]])
                                try:
                                    await context.bot.send_message(
                                        chat_id=acc_user.telegram_id,
                                        text=(
                                            f"\u200F🚨 <b>החלטה קריטית — נדרש אישור</b>\n\n"
                                            f"<b>החלטה #{decision_id}</b>\n\n"
                                            f"📋 <b>סיכום:</b> {_html2.escape(dec.summary or '')}\n"
                                            f"🎯 <b>פעולה מומלצת:</b> {_html2.escape(dec.recommended_action or '')}\n\n"
                                            f"<b>אנא אשר או דחה החלטה זו:</b>"
                                        ),
                                        parse_mode="HTML",
                                        reply_markup=crit_keyboard,
                                    )
                                except Exception as _e:
                                    logger.warning(f"RACI approve: failed to notify accountable {acc_user.username}: {_e}")

                await query.edit_message_text(
                    "\u200F✅ <b>RACI אושר ונשמר.</b>\nהצוות יקבל הודעות בהתאם לתפקידיהם.",
                    parse_mode="HTML",
                )
                return

            # --- Decision classification confirmation (yes/no buttons) ---
            if action in ("dec_conf_y", "dec_conf_n"):
                original_text = _awaiting_decision_confirm.pop(telegram_id, None)
                if action == "dec_conf_n" or not original_text:
                    await query.edit_message_text("\u200Fבסדר, ממשיכים.")
                    return
                # Ask manager approval question before processing (if user has a manager)
                if _user_has_manager(approver):
                    _awaiting_mgr_approval_confirm[telegram_id] = original_text
                    await query.edit_message_text(
                        "\u200F👔 <b>האם החלטה זו דורשת אישור מנהל?</b>",
                        parse_mode="HTML",
                        reply_markup=_mgr_approval_keyboard(),
                    )
                    return
                # No manager — process directly
                await query.edit_message_text("\u200F⏳ <b>מעבד את ההחלטה...</b>", parse_mode="HTML")
                decision_svc = DecisionService(session, self.application)
                reply = await decision_svc.process(approver, original_text)
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=reply,
                    parse_mode="HTML",
                )
                return

            # --- Manager approval answer ---
            if action in ("mgr_yes", "mgr_no"):
                original_text = _awaiting_mgr_approval_confirm.pop(telegram_id, None)
                if not original_text:
                    await query.edit_message_text("\u200F⚠️ פג תוקף הבקשה. שלח את ההחלטה מחדש.")
                    return
                await query.edit_message_text("\u200F⏳ <b>מעבד את ההחלטה...</b>", parse_mode="HTML")
                decision_svc = DecisionService(session, self.application)
                reply = await decision_svc.process(
                    approver, original_text, force_approval=(action == "mgr_yes")
                )
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=reply,
                    parse_mode="HTML",
                )
                return

            # --- Inline RACI editor callbacks ---
            if action == "raci_ev":
                state = _raci_edit_state.get(telegram_id)
                if not state or state["decision_id"] != decision_id:
                    await query.edit_message_text("\u200F⚠️ סשן עריכה לא פעיל. לחץ ✏️ ערוך שוב.")
                    return
                from app.services.raci_service import build_raci_list_message as _blm
                text, kbd = _blm(decision_id, state["items"])
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                return

            if action == "raci_eu":
                # data = "raci_eu:{did}:{uid}"
                uid = int(data.split(":")[2])
                state = _raci_edit_state.get(telegram_id)
                if not state:
                    await query.answer("סשן עריכה לא פעיל.", show_alert=True)
                    return
                item = next((i for i in state["items"] if i["user_id"] == uid), None)
                uname = item["name"] if item else str(uid)
                from app.services.raci_service import build_role_picker as _brp
                text, kbd = _brp(decision_id, uid, uname)
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                return

            if action == "raci_sr":
                # data = "raci_sr:{did}:{uid}:{role}"
                _dp = data.split(":")
                uid, role = int(_dp[2]), _dp[3]
                state = _raci_edit_state.get(telegram_id)
                if not state:
                    await query.answer("סשן עריכה לא פעיל.", show_alert=True)
                    return
                for item in state["items"]:
                    if item["user_id"] == uid:
                        item["role"] = role
                        break
                from app.services.raci_service import build_raci_list_message as _blm
                text, kbd = _blm(decision_id, state["items"])
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                return

            if action == "raci_rm":
                # data = "raci_rm:{did}:{uid}"
                uid = int(data.split(":")[2])
                state = _raci_edit_state.get(telegram_id)
                if not state:
                    await query.answer("סשן עריכה לא פעיל.", show_alert=True)
                    return
                state["items"] = [i for i in state["items"] if i["user_id"] != uid]
                from app.services.raci_service import build_raci_list_message as _blm
                text, kbd = _blm(decision_id, state["items"])
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                return

            if action == "raci_au":
                # data = "raci_au:{did}:{page}"
                _dp = data.split(":")
                page = int(_dp[2]) if len(_dp) > 2 else 0
                state = _raci_edit_state.get(telegram_id)
                if not state:
                    await query.answer("סשן עריכה לא פעיל.", show_alert=True)
                    return
                existing_ids = {i["user_id"] for i in state["items"]}
                from app.services.raci_service import build_user_picker as _bup
                text, kbd = _bup(decision_id, state["all_users"], existing_ids, page)
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                return

            if action == "raci_ap":
                # data = "raci_ap:{did}:{uid}"
                uid = int(data.split(":")[2])
                state = _raci_edit_state.get(telegram_id)
                if not state:
                    await query.answer("סשן עריכה לא פעיל.", show_alert=True)
                    return
                uname = next((u["name"] for u in state["all_users"] if u["id"] == uid), str(uid))
                from app.services.raci_service import build_new_user_role_picker as _bnrp
                text, kbd = _bnrp(decision_id, uid, uname)
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                return

            if action == "raci_ar":
                # data = "raci_ar:{did}:{uid}:{role}"
                _dp = data.split(":")
                uid, role = int(_dp[2]), _dp[3]
                state = _raci_edit_state.get(telegram_id)
                if not state:
                    await query.answer("סשן עריכה לא פעיל.", show_alert=True)
                    return
                uname = next((u["name"] for u in state["all_users"] if u["id"] == uid), str(uid))
                state["items"].append({"user_id": uid, "role": role, "name": uname})
                from app.services.raci_service import build_raci_list_message as _blm
                text, kbd = _blm(decision_id, state["items"])
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
                return

            if action == "raci_confirm":
                state = _raci_edit_state.get(telegram_id)
                if not state or state["decision_id"] != decision_id:
                    await query.edit_message_text("\u200F⚠️ סשן עריכה לא פעיל.")
                    return
                if not state["items"]:
                    await query.answer("לא ניתן לשמור RACI ריק.", show_alert=True)
                    return
                accountables = [i for i in state["items"] if i["role"] == "A"]
                if len(accountables) != 1:
                    from app.services.raci_service import build_raci_list_message as _blm
                    text, kbd = _blm(decision_id, state["items"])
                    err = f"\u200F⚠️ חייב להיות בדיוק מוסמך אחד (A). כרגע: {len(accountables)}.\n\n" + text
                    await query.edit_message_text(err, parse_mode="HTML", reply_markup=kbd)
                    return

                from app.services.raci_service import (
                    save_pregenerated_raci as _spr,
                    _pending_raci_suggestions as _prs,
                )
                pending = _prs.pop(decision_id, None)
                parsed = state.get("parsed") or (pending.get("parsed") if pending else {})
                valid_items = [{"user_id": i["user_id"], "role": i["role"]} for i in state["items"]]
                _raci_edit_state.pop(telegram_id, None)

                await _spr(decision_id, valid_items, parsed)

                # Mark suggestion as edited for learning
                from app.services.raci_service import mark_raci_edited as _mark_edited
                context.application.create_task(_mark_edited(decision_id, valid_items))

                if state.get("is_critical"):
                    from app.services.raci_service import get_accountable_user_id as _get_acc
                    from app.models import Decision as _Dec
                    import html as _html2
                    async with async_session_maker() as _sess:
                        accountable_id = await _get_acc(decision_id, _sess)
                        if accountable_id:
                            acc_user = await _sess.get(User, accountable_id)
                            dec = await _sess.get(_Dec, decision_id)
                            if acc_user and acc_user.telegram_id and dec:
                                crit_keyboard = InlineKeyboardMarkup([[
                                    InlineKeyboardButton("✅ אישור", callback_data=f"approve:{decision_id}"),
                                    InlineKeyboardButton("❌ דחייה", callback_data=f"reject:{decision_id}"),
                                ]])
                                try:
                                    await context.bot.send_message(
                                        chat_id=acc_user.telegram_id,
                                        text=(
                                            f"\u200F🚨 <b>החלטה קריטית — נדרש אישור</b>\n\n"
                                            f"<b>החלטה #{decision_id}</b>\n\n"
                                            f"📋 <b>סיכום:</b> {_html2.escape(dec.summary or '')}\n"
                                            f"🎯 <b>פעולה מומלצת:</b> {_html2.escape(dec.recommended_action or '')}\n\n"
                                            f"<b>אנא אשר או דחה החלטה זו:</b>"
                                        ),
                                        parse_mode="HTML",
                                        reply_markup=crit_keyboard,
                                    )
                                except Exception as _e:
                                    logger.warning(f"RACI confirm: failed to notify accountable: {_e}")

                await query.edit_message_text(
                    "\u200F✅ <b>RACI עודכן ונשמר.</b>\nהצוות יקבל הודעות בהתאם לתפקידיהם.",
                    parse_mode="HTML",
                )
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
    # Webhook lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Initialize and start the bot application for webhook mode."""
        try:
            await self.application.initialize()
            await self.application.start()
            logger.info("Telegram bot application started (webhook mode)")
        except Exception as e:
            logger.error(f"Failed to start bot application: {e}")
            raise

    async def start_polling(self):
        """Start bot in polling mode (local dev — no public URL needed)."""
        try:
            await self.application.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started in polling mode")
        except Exception as e:
            logger.error(f"Failed to start polling: {e}")
            raise

    async def stop(self):
        """Stop the bot application gracefully."""
        try:
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram bot application stopped")
        except Exception as e:
            logger.error(f"Error stopping bot application: {e}")

    async def set_webhook(self):
        """Register the webhook URL with Telegram."""
        webhook_url = (
            settings.TELEGRAM_WEBHOOK_URL
            or f"{settings.BASE_URL}/telegram/webhook"
        )
        try:
            await self.application.bot.set_webhook(
                url=webhook_url,
                secret_token=settings.WEBHOOK_SECRET_TOKEN or None,
                allowed_updates=["message", "callback_query", "edited_message"],
            )
            logger.info(f"Telegram webhook set to: {webhook_url}")
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
            raise

    async def delete_webhook(self):
        """Remove the webhook registration from Telegram."""
        try:
            await self.application.bot.delete_webhook()
            logger.info("Telegram webhook deleted")
        except Exception as e:
            logger.error(f"Failed to delete webhook: {e}")


async def _process_master_and_notify(file_id: int, telegram_id: int, filename: str, bot) -> None:
    """Process a master XLSX file with specialized ETL and notify when done."""
    from app.services.knowledge_service import process_master_file
    await process_master_file(file_id)
    from app.database import async_session_maker as _sm
    from app.models import KnowledgeFile as _KF
    async with _sm() as session:
        kf = await session.get(_KF, file_id)
        if kf and kf.status == "ready":
            msg = (
                f"\u200F⭐ <b>קובץ המאסטר עובד בהצלחה!</b>\n"
                f"📄 {_html.escape(filename)}\n"
                f"🔢 {kf.chunk_count} בלוקי פרויקט נוצרו\n\n"
                f"כעת תוכל לשאול: /ask שאלה"
            )
        else:
            summary = kf.summary if kf else ""
            msg = f"\u200F❌ שגיאה בעיבוד קובץ המאסטר <b>{_html.escape(filename)}</b>.\n{_html.escape(summary or '')}"
    try:
        await bot.send_message(chat_id=telegram_id, text=msg, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to notify user {telegram_id}: {e}")


async def _process_and_notify(file_id: int, telegram_id: int, filename: str, bot) -> None:
    """Process a knowledge file and send Telegram notification when done."""
    from app.services.knowledge_service import process_file
    await process_file(file_id)
    # Re-read updated record
    from app.database import async_session_maker as _sm
    from app.models import KnowledgeFile as _KF
    async with _sm() as session:
        kf = await session.get(_KF, file_id)
        if kf and kf.status == "ready":
            msg = (
                f"\u200F✅ <b>הקובץ עובד בהצלחה!</b>\n"
                f"📄 {_html.escape(filename)}\n"
                f"🔢 {kf.chunk_count} קטעי ידע נוצרו\n\n"
                f"כעת תוכל לשאול: /ask שאלה"
            )
        else:
            summary = kf.summary if kf else ""
            msg = f"\u200F❌ שגיאה בעיבוד הקובץ <b>{_html.escape(filename)}</b>.\n{_html.escape(summary or '')}"
    try:
        await bot.send_message(chat_id=telegram_id, text=msg, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to notify user {telegram_id}: {e}")


# Global bot instance
telegram_bot = TelegramPollingBot()
