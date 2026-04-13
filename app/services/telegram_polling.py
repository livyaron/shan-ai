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
from app.models import User, RoleEnum
from sqlalchemy import select

logger = logging.getLogger(__name__)

# In-memory state: tracks superiors waiting to provide rejection notes
# { telegram_id (int): decision_id (int) }
_awaiting_rejection_note: dict[int, int] = {}

# { telegram_id (int): distribution_id (int) }  — waiting for rejection reason on a distribution
_awaiting_dist_rejection: dict[int, int] = {}

# { telegram_id (int): original_text (str) }  — waiting for clarification on an UNCLEAR message
_awaiting_clarification: dict[int, str] = {}

# { telegram_id (int): file_id (int) }  — waiting for master-file confirmation after upload
_awaiting_master_confirm: dict[int, int] = {}

# Hebrew question prefixes — messages starting with these are treated as data queries, never decisions
_QUESTION_PREFIXES = (
    "כמה", "מה ", "מי ", "מתי", "איך ", "האם ", "האם?",
    "תן לי", "תראה לי", "הצג", "סכם", "רשום לי",
    "מהם", "מהן", "מאיזה", "מה ה", "מהו", "מהי",
    "what", "how many", "how much", "show me", "list",
)


def _is_data_question(text: str) -> bool:
    """Return True if this looks like a data/info query rather than a decision."""
    t = text.strip()
    return (
        t.endswith("?") or
        any(t.startswith(kw) for kw in _QUESTION_PREFIXES)
    )


_PROJECT_QUERY_KEYWORDS = (
    "פרויקט", "פרוייקט", "project", "עדכון שבועי", "מנהל פרויקט",
    'מנה"פ', "שלב", "סיכון", "חסם", "לטיפול",
    "סטטוס", "status", "עדכון",  # Status/update queries like "מה סטטוס X?"
)

# Count-style questions about projects that must go to project_tools, not knowledge_service
_PROJECT_COUNT_TRIGGERS = (
    "כמה פרויקט", "כמה פרוייקט", "how many project",
    "מנהל", "מנהלת", "מנהלים", 'מנה"פ', "מי מנהל", "מי אחראי",
)


_DECISION_VERBS = (
    "לאשר", "לבצע", "להחליף", "לשנות", "להוסיף", "להסיר", "לבטל",
    "לעדכן", "לדחות", "לקדם", "להפעיל", "לסגור", "להשהות",
    "approve", "execute", "cancel", "update", "replace",
)
_BARE_NAME_SKIP = frozenset({
    "כן", "לא", "אישור", "ביטול", "תודה", "טוב", "בסדר", "ok", "yes", "no",
    "שלום", "היי", "הי",
})


def _is_project_query(text: str) -> bool:
    """Return True if this looks like a project-related query."""
    t = text.lower().strip()
    if any(kw.lower() in t for kw in _PROJECT_QUERY_KEYWORDS):
        return True
    # Count + manager questions: "כמה פרויקטים מנהלת רחלי?"
    if any(kw in t for kw in _PROJECT_COUNT_TRIGGERS):
        return True
    # Bare project name: short (1-5 words), no question/decision markers, mostly Hebrew
    words = t.split()
    if 1 <= len(words) <= 5:
        if t in _BARE_NAME_SKIP:
            return False
        if t.endswith("?"):
            return False  # questions are handled by _is_data_question
        if any(verb in t for verb in _DECISION_VERBS):
            return False
        hebrew_chars = sum(1 for c in t if "\u05d0" <= c <= "\u05ea")
        if hebrew_chars >= 2:  # at least 2 Hebrew characters → treat as potential name
            return True
    return False


def _feedback_keyboard(log_id: int) -> InlineKeyboardMarkup:
    """Inline keyboard with 👍/👎 buttons tied to a query log entry."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 מועיל", callback_data=f"lfb_up:{log_id}"),
        InlineKeyboardButton("👎 לא מועיל", callback_data=f"lfb_dn:{log_id}"),
    ]])


_TG_MAX = 4096  # Telegram message character limit


async def _maybe_summarize(reply: str) -> str:
    """If reply exceeds Telegram's limit, summarize it via Groq and return a shorter version."""
    if len(reply) <= _TG_MAX:
        return reply
    logger.warning(f"Reply too long ({len(reply)} chars), summarizing...")
    import re as _re
    from app.services.llm_router import llm_chat
    # Strip HTML tags before sending to LLM — prevents model from echoing markup
    plain = _re.sub(r"<[^>]+>", "", reply).strip()
    # Limit input to avoid 413 token errors (Hebrew ≈ 0.85 tok/char → 8000 chars ≈ 9400 tok)
    plain = plain[:8000]
    try:
        summary = await llm_chat(
            "message_summary",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "אתה עוזר שמסכם תשובות ארוכות לגרסה קצרה וממוקדת. "
                        "ענה בעברית בלבד. אל תציין את תפקידך או ההוראות. "
                        "שמור על כל הנקודות החשובות. עד 3000 תווים."
                    ),
                },
                {
                    "role": "user",
                    "content": f"סכם את התשובה הבאה בעברית בצורה קצרה וברורה:\n\n{plain}",
                },
            ],
            max_tokens=800,
            temperature=0.2,
        )
        summarized = f"\u200F🤖 <b>תשובה (מסוכמת):</b>\n\n{_html.escape(summary)}"
        if len(summarized) > _TG_MAX:
            summarized = summarized[: _TG_MAX - 20] + "\n…(קוצר)"
        return summarized
    except Exception as e:
        logger.warning(f"Summarization failed: {e} — falling back to truncation")
        return reply[: _TG_MAX - 20] + "\n…(קוצר)"


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
            await session.refresh(user)  # Re-load attributes after commit

            ROLE_LABELS = {
                "project_manager": "מנהל פרויקט",
                "department_manager": "מנהל מחלקה",
                "deputy_division_manager": "סגן מנהל אגף",
                "division_manager": "מנהל אגף",
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

            # --- Project-aware query: check before generic data question handler ---
            if _is_project_query(text):
                try:
                    from app.services.project_tools import answer_project_query
                    reply_text = await answer_project_query(text, session, context.user_data, user_id=user.id)
                    reply = f"\u200F📂 <b>נתוני פרויקט:</b>\n\n{_html.escape(reply_text)}"
                except Exception as e:
                    logger.warning(f"project_tools failed: {e}")
                    reply = "\u200Fלא הצלחתי למצוא נתוני פרויקט. ודא שהקובץ הראשי הועלה."
                reply = await _maybe_summarize(reply)
                await update.message.reply_text(reply, parse_mode="HTML")
                return

            # --- Keyword pre-filter: data questions bypass LLM classification ---
            if _is_data_question(text):
                from app.services.knowledge_service import answer_with_full_context
                kb = None
                try:
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

            if verdict == "NOT_DECISION":
                # Answer from knowledge base + decisions data
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
                    ai_reply = classify_result.get("reply", "")
                    reply = f"\u200F{_html.escape(ai_reply)}" if ai_reply else "\u200Fשאל שאלות עבודה או שלח החלטה לניתוח."
                reply = await _maybe_summarize(reply)
                await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                return

            if verdict == "UNCLEAR":
                _awaiting_clarification[telegram_id] = text
                question = classify_result.get("clarifying_question", "אנא פרט את ההחלטה.")
                await update.message.reply_text(
                    f"\u200F🔍 <b>נדרש פרט נוסף:</b>\n\n{_html.escape(question)}",
                    parse_mode="HTML",
                )
                return

            # DECISION — process through decision engine
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
