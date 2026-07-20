"""Telegram bot polling handler - runs as background task."""

import html as _html
import logging
import random
import re
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from app.config import settings
from app.database import async_session_maker
from app.services.telegram_service import TelegramService
from app.services.decision_service import DecisionService
from app.services import feedback_service
from app.models import User, Project
from app.services.telegram_state import (
    _awaiting_rejection_note, _awaiting_dist_rejection,
    _awaiting_clarification, _awaiting_master_confirm,
    _awaiting_decision_confirm, _awaiting_mgr_approval_confirm,
    _raci_edit_state, _awaiting_decision_preview,
    _awaiting_irrelevant_reason,
    get_context, append_context, clear_context,
)
from app.services.telegram_routing import (
    _is_data_question, _is_project_query, _maybe_summarize,
    _ai_route_message, _TG_MAX, _DECISION_HISTORY_KEYWORDS,
)
from sqlalchemy import select
from app.services.decisions_menu_service import get_menu_shortcut_keyboard

logger = logging.getLogger(__name__)

def _main_reply_keyboard(user=None) -> ReplyKeyboardMarkup:
    from app.models import RoleEnum
    manager_roles = {RoleEnum.DEPARTMENT_MANAGER, RoleEnum.DEPUTY_DIVISION_MANAGER, RoleEnum.DIVISION_MANAGER}
    rows = [["📁 פרוייקטים", "📋 החלטות", "📊 דוח שלי"]]
    if user and user.role in manager_roles:
        rows.append(["🎯 חדר מבצעים", "👥 דוח צוות", "📊 דוח פרויקטים"])
    else:
        rows.append(["🎯 חדר מבצעים", "📊 דוח פרויקטים"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def _viewer_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["📁 פרוייקטים"]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _keyboard_for_user(user) -> ReplyKeyboardMarkup:
    from app.models import RoleEnum
    if user and user.role == RoleEnum.VIEWER:
        return _viewer_reply_keyboard()
    return _main_reply_keyboard(user)

_VIEWER_DECISIONS_BLOCKED = "‏🔒 גישה לתפריט ההחלטות אינה זמינה למשתמשי צפייה."

# War-room entry posters — a random one is sent on each entry to the operations room.
# Telegram file_ids are cached after the first upload so subsequent sends are instant.
_WAR_ROOM_POSTER_DIR = Path("static/war_room")
_WAR_ROOM_POSTERS: list[Path] = sorted(_WAR_ROOM_POSTER_DIR.glob("*.jpg"))
_poster_file_id_cache: dict[str, str] = {}

def _mgr_approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ כן, נדרש אישור", callback_data="mgr_yes:0"),
        InlineKeyboardButton("❌ לא, בצע ישירות", callback_data="mgr_no:0"),
    ]])


def _decision_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ אשר ותעד", callback_data="dec_prev_y:0"),
        InlineKeyboardButton("❌ בטל", callback_data="dec_prev_n:0"),
    ]])


def _build_preview_text(result: dict) -> str:
    type_map = {
        "INFO": "ℹ️ מידע",
        "NORMAL": "✅ רגיל",
        "CRITICAL": "\U0001f6a8 קריטי",
        "UNCERTAIN": "❓ לא ודאי",
    }
    e = _html.escape
    t = (result.get("type") or "").upper()
    type_label = type_map.get(t, t or "—")
    approval = "כן" if result.get("requires_approval") else "לא"
    return (
        f"‏\U0001f50d <b>ניתוח ראשוני — לפני תיעוד</b>\n\n"
        f"<b>סוג:</b> {type_label}\n"
        f"<b>סיכום:</b> {e(result.get('summary') or '—')}\n"
        f"<b>פעולה מומלצת:</b> {e(result.get('recommended_action') or '—')}\n"
        f"<b>דורש אישור:</b> {approval}\n\n"
        f"האם לתעד ולעבד החלטה זו?"
    )


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


CAUSE_MAP = {
    "WRONG_PROJECT": "פרויקט לא נכון",
    "MISSING_DATA": "חסר מידע",
    "HALLUCINATION": "תשובה שגויה",
}


def _cause_keyboard(log_id: int) -> InlineKeyboardMarkup:
    """Follow-up after 👎: one tap classifies the failure cause."""
    rows = [[InlineKeyboardButton(label, callback_data=f"lfc:{log_id}:{code}")]
            for code, label in CAUSE_MAP.items()]
    rows.append([InlineKeyboardButton("דלג", callback_data=f"lfc:{log_id}:SKIP")])
    return InlineKeyboardMarkup(rows)


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
        self.application.add_handler(CommandHandler("decisions", self.handle_decisions))
        self.application.add_handler(CommandHandler("projects", self.handle_projects))
        self.application.add_handler(CommandHandler("missions", self.handle_missions))
        self.application.add_handler(CommandHandler("menu", self.handle_menu))
        self.application.add_handler(CommandHandler("ask", self.handle_ask))
        self.application.add_handler(CommandHandler("report", self.handle_report))
        self.application.add_handler(CommandHandler("gold", self.handle_gold))
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
        clear_context(telegram_id)
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
            kb = _keyboard_for_user(user) if user.role else None
            await update.message.reply_text(
                f"\u200F👋 ברוך הבא ל-<b>Shan-AI</b>, {_html.escape(user.username)}!\n\n"
                f"אני מנתח החלטות טכניות בפרויקטי תשתיות חשמל, טרנספורמטורים ותחנות משנה.\n\n"
                f"<b>פקודות זמינות:</b>\n"
                f"/register קוד — הרשמה למערכת\n"
                f"/status — בדיקת סטטוס ותפקיד\n\n"
                f"לאחר קבלת תפקיד, שלח לי תיאור של הבעיה או ההחלטה ואנתח אותה בעזרת AI.",
                parse_mode="HTML",
                reply_markup=kb,
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
                reply_markup=_keyboard_for_user(user),
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

    async def handle_decisions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/decisions — open the decisions menu."""
        from app.services.decisions_menu_service import get_menu_keyboard, get_menu_text, get_menu_counts
        from app.models import RoleEnum
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("‏⏳ יש להירשם תחילה. השתמש ב-/register")
                return
            if user.role == RoleEnum.VIEWER:
                await update.message.reply_text(
                    _VIEWER_DECISIONS_BLOCKED,
                    reply_markup=_viewer_reply_keyboard(),
                )
                return
            counts = await get_menu_counts(session, user.id)
        await update.message.reply_text(
            get_menu_text(counts),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(counts["feedback"]),
        )

    async def handle_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/projects — open the projects menu."""
        from app.services.projects_menu_service import get_menu_keyboard, get_menu_text, get_total_active
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("‏⏳ יש להירשם תחילה. השתמש ב-/register")
                return
            total = await get_total_active(session)
        await update.message.reply_text(
            get_menu_text(total),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(),
        )

    async def _send_war_room_poster(self, message) -> None:
        """Send a random war-room poster. Never raises — a poster is decoration."""
        try:
            if not _WAR_ROOM_POSTERS:
                return
            poster = random.choice(_WAR_ROOM_POSTERS)
            cached_id = _poster_file_id_cache.get(poster.name)
            if cached_id:
                await message.reply_photo(cached_id)
                return
            with open(poster, "rb") as fh:
                sent = await message.reply_photo(fh)
            if sent and sent.photo:
                _poster_file_id_cache[poster.name] = sent.photo[-1].file_id
        except Exception as e:
            logger.warning(f"war-room poster send failed: {e}")

    async def handle_missions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/missions — open the operations room (חדר מבצעים)."""
        from app.models import RoleEnum
        from app.services.missions_menu_service import get_menu_keyboard, get_menu_text, get_board_counts
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("‏⏳ יש להירשם תחילה. השתמש ב-/register")
                return
            if user.role == RoleEnum.VIEWER:
                await update.message.reply_text(
                    "‏🔒 גישה לחדר המבצעים אינה זמינה למשתמשי צפייה.",
                    reply_markup=_viewer_reply_keyboard(),
                )
                return
            counts, overdue = await get_board_counts(session)
        await self._send_war_room_poster(update.message)
        await update.message.reply_text(
            get_menu_text(counts, overdue),
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(counts),
        )

    async def handle_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/menu — re-send the persistent reply keyboard."""
        telegram_id = update.effective_user.id
        clear_context(telegram_id)
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user or not user.role:
            await update.message.reply_text("‏⏳ יש להירשם תחילה. השתמש ב-/register")
            return
        await update.message.reply_text(
            "‏📋 בחר תפריט:",
            reply_markup=_keyboard_for_user(user),
        )

    # ------------------------------------------------------------------
    # /report command — weekly intelligence report on demand
    # ------------------------------------------------------------------

    async def handle_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/report — generate and send weekly intelligence report for the requesting user."""
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not user or not user.role:
                await update.message.reply_text("‏⏳ יש להירשם תחילה.")
                return
            from app.models import RoleEnum as _RE
            if user.role == _RE.VIEWER:
                await update.message.reply_text("‏🔒 דוח שבועי אינו זמין לצופים.")
                return
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
            from app.services.weekly_report_service import generate_report_for_user, send_report_to_user
            sections = await generate_report_for_user(
                user, session, triggered_by_id=user.id, sent_via="telegram"
            )
        await send_report_to_user(context.bot, update.effective_chat.id, sections)

    async def handle_gold(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/gold — manager-only gold curation of ungolded production questions."""
        from app.services import gold_telegram_service as gtg
        from app.services.gold_truth_service import propose_gold
        telegram_id = update.effective_user.id
        async with async_session_maker() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not gtg.is_manager(user):
                await update.message.reply_text("‏🔒 פקודה זו זמינה למנהלים בלבד.")
                return
            cand = await gtg.next_candidate(session, exclude_questions=set())
            if not cand:
                await update.message.reply_text("‏✅ אין שאלות הממתינות לתשובת זהב.")
                return
            proposal = await propose_gold(session, cand["question"], use_llm=True)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=("‏🥇 <b>בניית תשובת זהב</b>\n\n"
                  f"‏<b>שאלה:</b> {_html.escape(cand['question'])}\n\n"
                  f"‏<b>הצעה:</b> {_html.escape(proposal['answer'])}"),
            parse_mode="HTML",
            reply_markup=gtg.gold_keyboard(cand["id"]),
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

    # ------------------------------------------------------------------
    # Second brain — explicit memory capture & recall
    # ------------------------------------------------------------------

    async def _handle_remember(self, update: Update, session, user, content: str):
        """Save a user-taught fact ("זכור ש...") with high-confidence project linking."""
        from app.services import memory_service
        try:
            project_id, project_label = await memory_service.link_project(content, session)
            note = await memory_service.save_memory(
                session, content=content, user_id=user.id, project_id=project_id,
            )
        except Exception:
            logger.error("remember: save failed", exc_info=True)
            await update.message.reply_text("‏⚠️ לא הצלחתי לשמור את העובדה. נסה שוב.")
            return
        reply = f"‏🧠 <b>נשמר בזיכרון הארגוני:</b>\n{_html.escape(note.content)}"
        if project_label:
            reply += f"\n📁 שויך לפרויקט: {_html.escape(project_label)}"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ שכח את זה", callback_data=f"mem_forget:{note.id}"),
        ]])
        await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
        await self._notify_admins_new_memory(user, note)

    async def _notify_admins_new_memory(self, author, note):
        """Lightweight audit: tell admins a fact was taught. Best-effort."""
        try:
            async with async_session_maker() as s:
                admins = (await s.execute(
                    select(User).where(User.is_admin.is_(True), User.telegram_id.isnot(None))
                )).scalars().all()
            for admin in admins:
                if admin.id == author.id:
                    continue
                try:
                    await self.application.bot.send_message(
                        chat_id=admin.telegram_id,
                        text=(f"‏🧠 עובדה חדשה בזיכרון הארגוני (מאת {author.username}):\n"
                              f"{note.content[:300]}"),
                    )
                except Exception:
                    pass
        except Exception:
            logger.warning("memory admin notify failed", exc_info=True)

    async def _handle_memory_list(self, update: Update, session, user, text: str):
        """Answer "מה אתה זוכר [על X]" with the stored facts + forget buttons."""
        from app.services import memory_service
        topic = memory_service.extract_recall_topic(text)
        notes = await memory_service.list_memories(session, topic=topic)
        if not notes:
            scope = f" על ״{topic}״" if topic else ""
            await update.message.reply_text(
                f"‏🧠 אין עדיין עובדות בזיכרון{scope}.\n"
                "אפשר ללמד אותי: <i>זכור ש...</i>", parse_mode="HTML",
            )
            return
        lines = await memory_service.describe_notes(notes, session)
        header = f"‏🧠 <b>מה שאני זוכר{' על ״' + _html.escape(topic) + '״' if topic else ''}:</b>"
        body = "\n".join(f"{i}. {_html.escape(line)}" for i, line in enumerate(lines, 1))
        buttons = [
            [InlineKeyboardButton(f"🗑 שכח {i}", callback_data=f"mem_forget:{n.id}")]
            for i, n in enumerate(notes[:6], 1)
        ]
        await update.message.reply_text(
            f"{header}\n{body}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )

    async def _handle_dossier_request(self, update: Update, session, user, name: str):
        """Send the living project dossier for "תיק פרויקט X"."""
        from app.services import dossier_service
        from app.services.project_tools import find_projects_by_identifier
        if not name:
            await update.message.reply_text(
                "‏📁 כתוב: <i>תיק פרויקט &lt;שם&gt;</i> — למשל: תיק פרויקט חדרה",
                parse_mode="HTML",
            )
            return
        matches = await find_projects_by_identifier(name, session)
        if not matches:
            await update.message.reply_text(f"‏⚠️ לא נמצא פרויקט בשם ״{name}״.")
            return
        if len(matches) > 1:
            names = "\n".join(f"• {m['name'] or m['project_identifier']}" for m in matches[:6])
            await update.message.reply_text(
                f"‏🔍 נמצאו כמה פרויקטים — דייק את השם:\n{names}")
            return
        project = matches[0]
        content = await dossier_service.get_dossier_text(project["id"], session)
        if content:
            await update.message.reply_text(
                f"‏📁 <b>תיק פרויקט {_html.escape(project['name'] or project['project_identifier'])}:</b>\n\n"
                f"{_html.escape(content)}",
                parse_mode="HTML",
            )
        else:
            await dossier_service.mark_dirty([project["id"]])
            await update.message.reply_text(
                "‏⏳ התיק עדיין לא הוכן — סומן להכנה ויהיה זמין בדקות הקרובות. נסה שוב מאוחר יותר.",
            )

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

            # Check if user is providing notes text after rating from the feedback menu
            from app.services.telegram_state import _awaiting_fb_menu_text
            from app.services.feedback_service import save_telegram_feedback_text
            from app.services.decisions_menu_service import query_pending_feedback, build_feedback_results_keyboard, format_results_message
            if telegram_id in _awaiting_fb_menu_text:
                fb_dec_id, fb_back_pg = _awaiting_fb_menu_text.pop(telegram_id)
                notes_text = "" if text.strip() == "/skip" else text.strip()
                async with async_session_maker() as fb_s:
                    await save_telegram_feedback_text(fb_s, user.id, fb_dec_id, notes_text)
                await update.message.reply_text("‏✅ הפידבק שלך נשמר.", parse_mode="HTML")
                async with async_session_maker() as fb_s:
                    fb_decs, fb_tot = await query_pending_feedback(fb_s, user.id, fb_back_pg)
                await update.message.reply_text(
                    f"‏⭐ <b>ממתין למשוב שלך</b> ({fb_tot})\n<i>בחר החלטה לדירוג:</i>",
                    parse_mode="HTML",
                    reply_markup=build_feedback_results_keyboard(fb_decs, fb_back_pg, fb_tot),
                )
                return

            from app.services.telegram_state import _awaiting_gold_text
            if telegram_id in _awaiting_gold_text:
                gold_state = _awaiting_gold_text.pop(telegram_id)
                from app.services.gold_truth_service import save_gold
                async with async_session_maker() as gs:
                    await save_gold(gs, question=gold_state["question"], gold_answer=text.strip(),
                                    user_id=user.id, source="telegram")
                await update.message.reply_text("‏✅ תשובת הזהב נשמרה. שלח /gold להמשך.")
                return

            # Operations room — mission wizard / edit flows awaiting text
            from app.services.telegram_state import _missions_create_state, _missions_edit_state
            if telegram_id in _missions_create_state or telegram_id in _missions_edit_state:
                await self._handle_missions_text(update, context, user, text)
                return

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

            # Check if user is providing a reason to mark a decision irrelevant
            if telegram_id in _awaiting_irrelevant_reason:
                decision_id = _awaiting_irrelevant_reason.pop(telegram_id)
                reason = "" if text.strip() in ("ללא", "/skip") else text.strip()
                decision_svc = DecisionService(session, self.application)
                success, msg = await decision_svc.set_decision_relevance(decision_id, user, is_relevant=False, reason=reason)
                await update.message.reply_text(f"‏{msg}", parse_mode="HTML")
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

            # Load conversation context (after role check so roleless users don't accumulate context)
            conv_ctx = get_context(telegram_id)
            append_context(telegram_id, "user", text)

            # Show typing indicator for all role-bearing users (including VIEWER)
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action="typing"
            )

            # Viewer: separate read-only pipeline
            from app.models import RoleEnum as _RE
            if user.role == _RE.VIEWER:
                await self._handle_viewer_message(update, context, user, text.strip())
                return

            # Second brain — must run BEFORE keyword shortcuts and LLM routing:
            # a fact like "זכור שההחלטות..." must not be hijacked by the
            # "החלטות" menu shortcut or fall into the decision-confirm flow.
            from app.services import memory_service as _mem_svc
            _mem_content = _mem_svc.extract_remember_content(text)
            if _mem_content is not None:
                await self._handle_remember(update, session, user, _mem_content)
                return
            if _mem_svc.is_recall_query(text):
                await self._handle_memory_list(update, session, user, text)
                return

            # Second brain phase 2 — "תיק פרויקט X" returns the living dossier
            from app.services import dossier_service as _dossier_svc
            _dossier_req = _dossier_svc.extract_dossier_request(text)
            if _dossier_req is not None:
                await self._handle_dossier_request(update, session, user, _dossier_req)
                return

            # Report shortcut — "📊 דוח שלי"
            if "דוח שלי" in text.strip():
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
                from app.services.weekly_report_service import generate_report_for_user, send_report_to_user
                sections = await generate_report_for_user(
                    user, session,
                    triggered_by_id=user.id,
                    sent_via="telegram",
                )
                await send_report_to_user(context.bot, update.effective_chat.id, sections)
                return

            # Project report shortcut — "📊 דוח פרויקטים" — sends last saved report
            if "דוח פרויקטים" in text.strip():
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
                from app.services.project_report_service import _telegram_send_report
                from app.models import ProjectReport as _PR
                from sqlalchemy import select as _sel, desc as _desc
                try:
                    report = await session.scalar(
                        _sel(_PR).order_by(_desc(_PR.generated_at)).limit(1)
                    )
                    if not report:
                        await update.message.reply_text("‏📊 אין דוחות עדיין. העלה קובץ פרויקטים כדי לקבל דוח.")
                    else:
                        await _telegram_send_report(context.bot, user, report.id, report.report_data or {})
                except Exception as _pe:
                    logger.error(f"Telegram project report failed: {_pe}")
                    await update.message.reply_text("‏❌ שגיאה בשליחת הדוח. נסה שוב מאוחר יותר.")
                return

            # Team report shortcut — "👥 דוח צוות" (managers only)
            if "דוח צוות" in text.strip():
                from app.models import RoleEnum as _RE2
                from app.services.telegram_state import _awaiting_team_report
                _MANAGER_ROLES_RPT = {_RE2.DEPARTMENT_MANAGER, _RE2.DEPUTY_DIVISION_MANAGER, _RE2.DIVISION_MANAGER}
                if user.role not in _MANAGER_ROLES_RPT:
                    await update.message.reply_text("‏🔒 דוח צוות זמין למנהלים בלבד.")
                    return
                async with async_session_maker() as _sub_session:
                    sub_rows = (await _sub_session.execute(
                        select(User).where(User.manager_id == user.id, User.role.isnot(None))
                    )).scalars().all()
                if not sub_rows:
                    await update.message.reply_text("‏📭 אין לך כפופים רשומים במערכת.")
                    return
                _awaiting_team_report[telegram_id] = [u.id for u in sub_rows]
                buttons = []
                row_buf = []
                for i, sub in enumerate(sub_rows):
                    label = f"👤 {sub.username or sub.id}"
                    row_buf.append(InlineKeyboardButton(label, callback_data=f"rpt:{i}"))
                    if len(row_buf) == 2:
                        buttons.append(row_buf)
                        row_buf = []
                if row_buf:
                    buttons.append(row_buf)
                buttons.append([
                    InlineKeyboardButton("👥 כולם", callback_data="rpt:all"),
                    InlineKeyboardButton("❌ ביטול", callback_data="rpt:cancel"),
                ])
                await update.message.reply_text(
                    "‏📊 לאיזה חבר צוות לייצר דוח?",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return

            # Decisions menu keyword shortcut
            if "החלטות" in text.strip():
                if user.role:
                    from app.services.decisions_menu_service import get_menu_keyboard, get_menu_text, get_menu_counts
                    async with async_session_maker() as _cnt_s:
                        counts = await get_menu_counts(_cnt_s, user.id)
                    await update.message.reply_text(
                        get_menu_text(counts),
                        parse_mode="HTML",
                        reply_markup=get_menu_keyboard(),
                    )
                return

            if "פרוייקטים" in text.strip() or "פרויקטים" in text.strip():
                if user.role:
                    from app.services.projects_menu_service import (
                        get_menu_keyboard as pm_get_menu_keyboard,
                        get_menu_text as pm_get_menu_text,
                        get_total_active,
                    )
                    async with async_session_maker() as _pm_s:
                        _pm_total = await get_total_active(_pm_s)
                    await update.message.reply_text(
                        pm_get_menu_text(_pm_total),
                        parse_mode="HTML",
                        reply_markup=pm_get_menu_keyboard(),
                    )
                return

            # Operations room keyword shortcut. Bare "משימות" must match by
            # equality — containment would hijack questions like "מה המשימות בפרויקט X?"
            if "חדר מבצעים" in text.strip() or text.strip() == "משימות":
                if user.role:
                    from app.services.missions_menu_service import (
                        get_menu_keyboard as om_get_menu_keyboard,
                        get_menu_text as om_get_menu_text,
                        get_board_counts as om_get_board_counts,
                    )
                    async with async_session_maker() as _om_s:
                        _om_counts, _om_late = await om_get_board_counts(_om_s)
                    await self._send_war_room_poster(update.message)
                    await update.message.reply_text(
                        om_get_menu_text(_om_counts, _om_late),
                        parse_mode="HTML",
                        reply_markup=om_get_menu_keyboard(_om_counts),
                    )
                return

            # Hard bypass: single-word or clearly non-work messages skip LLM routing entirely
            _NON_WORK_WORDS = {"בדיחה", "בדיחות", "שלום", "היי", "הי", "תודה", "להתראות", "ביי", "בוקר טוב", "ערב טוב", "לילה טוב"}
            if text.strip() in _NON_WORK_WORDS:
                routing = {"route": None, "intent": None, "param": None}
            else:
                routing = await _ai_route_message(text, conversation_context=conv_ctx)
            ai_route = routing["route"]

            # All non-decision routes go through the shared ask_router.
            # decision-route falls through to ClaudeService().classify() below.
            if ai_route in ("project", "knowledge", None):
                kb = None
                try:
                    import json as _json
                    from app.services.ask_router import route as _ask_route
                    from app.services.telegram_state import _awaiting_disambiguation
                    result = await _ask_route(text, session, user.id, log_to_db=True,
                                              conversation_context=conv_ctx)

                    if result.path == "disambiguation":
                        candidates = _json.loads(result.answer)
                        # Store list (not original text) — callback resolves by index to avoid 64-byte limit
                        _awaiting_disambiguation[telegram_id] = candidates
                        buttons = []
                        row = []
                        for i, c in enumerate(candidates):
                            label = c.get("name") if isinstance(c, dict) else c
                            row.append(InlineKeyboardButton(f"📁 {label}", callback_data=f"disambig:{i}"))
                            if len(row) == 2:
                                buttons.append(row)
                                row = []
                        if row:
                            buttons.append(row)
                        buttons.append([InlineKeyboardButton("🔙 ביטול", callback_data="disambig:__cancel__")])
                        await update.message.reply_text(
                            "‏🔍 מצאתי מספר פרויקטים תואמים — על איזה מהם התכוונת?",
                            reply_markup=InlineKeyboardMarkup(buttons),
                        )
                        return

                    answer = result.answer
                    if result.path == "decision":
                        # Decision-DB plain reply — matches OLD pre-route handler.
                        reply = f"‏{answer}"
                    elif result.path == "project_tools":
                        # answer already HTML-formatted by project_tools — do NOT escape.
                        reply = f"‏{answer}"
                    else:
                        # rag path — escape answer, prepend robot header.
                        reply = f"\u200F\U0001F916 <b>\u05EA\u05E9\u05D5\u05D1\u05D4:</b>\n\n{_html.escape(answer)}"
                        if result.sources_text:
                            reply += f"\n\n<i>{_html.escape(result.sources_text)}</i>"
                    if result.log_id:
                        kb = _feedback_keyboard(result.log_id)
                except Exception:
                    logger.warning("ask_router.route failed", exc_info=True)
                    reply = "‏לא הצלחתי למצוא תשובה. נסה לנסח אחרת."
                    await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                    return
                reply = await _maybe_summarize(reply)
                await update.message.reply_text(reply, parse_mode="HTML", reply_markup=kb)
                append_context(telegram_id, "assistant", reply[:300])
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
                        await update.message.reply_text(
                            reply, parse_mode="HTML",
                            reply_markup=get_menu_shortcut_keyboard(),
                        )
                return

            # --- LLM classify for everything else ---
            from app.services.claude_service import ClaudeService as _CS
            try:
                classify_result = await _CS().classify(text)
                verdict = classify_result.get("verdict", "DECISION")
                logger.info(f"Classification verdict for user {telegram_id}: {verdict}")
            except Exception as e:
                logger.error(f"Classification failed: {e}", exc_info=True)
                from app.services.llm_router import is_overload_error
                if is_overload_error(e):
                    from app.services.pending_queue_service import enqueue
                    await enqueue(session, user=user, telegram_id=telegram_id,
                                  raw_text=text, conv_ctx=conv_ctx)
                    await update.message.reply_text(
                        "‏⏳ המערכת עמוסה כרגע. ההודעה נשמרה ותנותח "
                        "אוטומטית כשהמערכת תתפנה — אשלח לך את הניתוח לאישור. אין צורך לשלוח שוב.",
                        parse_mode="HTML",
                    )
                    return
                await update.message.reply_text(
                    "\u200F⚠️ שגיאה בניתוח הטקסט. נסה שוב.",
                    parse_mode="HTML",
                )
                return

            _dec_confirm_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ כן, זו החלטה", callback_data="dec_conf_y:0"),
                    InlineKeyboardButton("❌ לא", callback_data="dec_conf_n:0"),
                ],
                [InlineKeyboardButton("🧠 שמור כעובדה", callback_data="dec_conf_m:0")],
            ])

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
                    append_context(telegram_id, "assistant", reply[:300])
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

            # DECISION — show AI preview; user must approve before commit
            decision_svc = DecisionService(session, self.application)
            try:
                pre_result = await decision_svc.analyze_only(user, text, conversation_context=conv_ctx)
            except Exception as _err:
                logger.error(f"analyze_only failed: {_err}", exc_info=True)
                from app.services.llm_router import is_overload_error
                if is_overload_error(_err):
                    from app.services.pending_queue_service import enqueue
                    await enqueue(session, user=user, telegram_id=telegram_id,
                                  raw_text=text, conv_ctx=conv_ctx)
                    await update.message.reply_text(
                        "‏⏳ המערכת עמוסה כרגע. ההחלטה נשמרה ותנותח "
                        "אוטומטית כשהמערכת תתפנה — אשלח לך את הניתוח לאישור. אין צורך לשלוח שוב.",
                        parse_mode="HTML",
                    )
                    return
                await update.message.reply_text(
                    "‏⚠️ שגיאה בניתוח. נסה שוב.", parse_mode="HTML"
                )
                return
            _awaiting_decision_preview[telegram_id] = {
                "text": text,
                "result": pre_result,
                "user_has_manager": _user_has_manager(user),
            }
            preview_text = _build_preview_text(pre_result)
            await update.message.reply_text(
                preview_text,
                parse_mode="HTML",
                reply_markup=_decision_preview_keyboard(),
            )
            append_context(telegram_id, "assistant", preview_text[:300])

    # ------------------------------------------------------------------
    # Callback handler — approve / reject inline keyboard buttons
    # ------------------------------------------------------------------

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks for approval/rejection."""
        query = update.callback_query
        await query.answer()

        data = query.data  # "approve:{id}" or "reject:{id}"
        telegram_id = update.effective_user.id

        # Decisions menu — handle before int-based action parsing
        if data.startswith("dm:") or data.startswith("dm_cf:"):
            async with async_session_maker() as _dm_session:
                _dm_user = await _dm_session.scalar(select(User).where(User.telegram_id == telegram_id))
            if _dm_user:
                await self._handle_decisions_menu(query, context, data, telegram_id, _dm_user)
            return

        # Projects menu
        if data.startswith("pm:") or data.startswith("pm_cf:"):
            async with async_session_maker() as _pm_session:
                _pm_user = await _pm_session.scalar(select(User).where(User.telegram_id == telegram_id))
            if _pm_user:
                await self._handle_projects_menu(query, context, data, telegram_id, _pm_user)
            return

        # Operations room (חדר מבצעים)
        if data.startswith("om:"):
            from app.models import RoleEnum
            async with async_session_maker() as _om_session:
                _om_user = await _om_session.scalar(select(User).where(User.telegram_id == telegram_id))
            if not _om_user or not _om_user.role:
                return
            if _om_user.role == RoleEnum.VIEWER:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text="‏🔒 גישה לחדר המבצעים אינה זמינה למשתמשי צפייה.",
                )
                return
            await self._handle_missions_menu(query, context, data, telegram_id, _om_user)
            return

        # Team report — manager selected a recipient
        if data.startswith("rpt:"):
            from app.services.telegram_state import _awaiting_team_report
            from app.services.weekly_report_service import generate_report_for_user, send_report_to_user
            token = data[len("rpt:"):]

            if token == "cancel":
                _awaiting_team_report.pop(telegram_id, None)
                await query.edit_message_text("‏❌ בוטל.")
                return

            async with async_session_maker() as _rpt_cb_session:
                requester = await _rpt_cb_session.scalar(
                    select(User).where(User.telegram_id == telegram_id)
                )
                if not requester:
                    await query.answer("שגיאה — משתמש לא נמצא")
                    return

                sub_ids = _awaiting_team_report.pop(telegram_id, [])

                if token == "all":
                    target_ids = sub_ids if sub_ids else []
                    if not target_ids:
                        await query.edit_message_text("‏📭 לא נמצאו כפופים.")
                        return
                    await query.edit_message_text("‏⏳ מייצר דוחות לכל הצוות…")
                    requester_id = requester.id
                    errors = []
                    for uid in target_ids:
                        try:
                            async with async_session_maker() as _per_user_session:
                                target = await _per_user_session.scalar(
                                    select(User).where(User.id == uid)
                                )
                                if not target:
                                    continue
                                sections = await generate_report_for_user(
                                    target, _per_user_session,
                                    triggered_by_id=requester_id,
                                    sent_via="telegram",
                                )
                            await send_report_to_user(
                                context.bot,
                                telegram_id,
                                sections,
                                recipient_label=target.username or str(target.id),
                            )
                        except Exception as _e:
                            errors.append(str(uid))
                            logger.error(f"Team report failed for user {uid}: {_e}")
                    summary = "‏✅ כל הדוחות נשלחו."
                    if errors:
                        summary += f" שגיאות עבור: {', '.join(errors)}"
                    await context.bot.send_message(chat_id=telegram_id, text=summary)
                    return

                # Single user by index
                try:
                    target_id = sub_ids[int(token)]
                except (IndexError, ValueError):
                    await query.answer("שגיאה — נסה שוב")
                    return

                target = await _rpt_cb_session.scalar(
                    select(User).where(User.id == target_id)
                )
                if not target:
                    await query.edit_message_text("‏⚠️ משתמש לא נמצא.")
                    return

                await query.edit_message_text(
                    f"‏⏳ מייצר דוח עבור {target.username or target_id}…"
                )
                sections = await generate_report_for_user(
                    target, _rpt_cb_session,
                    triggered_by_id=requester.id,
                    sent_via="telegram",
                )
            await send_report_to_user(
                context.bot,
                telegram_id,
                sections,
                recipient_label=target.username or str(target_id),
            )
            return

        # Disambiguation — user selected a project from the ambiguous-match keyboard
        if data.startswith("disambig:"):
            from app.services.telegram_state import _awaiting_disambiguation
            from app.services import project_tools as _pt
            token = data[len("disambig:"):]
            if token == "__cancel__":
                _awaiting_disambiguation.pop(telegram_id, None)
                await query.edit_message_text("‏❌ הבקשה בוטלה.")
                return
            # Resolve integer index → identifier (avoids 64-byte callback_data limit)
            candidates = _awaiting_disambiguation.pop(telegram_id, [])
            try:
                candidate = candidates[int(token)]
                identifier = candidate.get("id") if isinstance(candidate, dict) else candidate
            except (IndexError, ValueError):
                await query.answer("שגיאה — נסה שוב")
                return
            async with async_session_maker() as _dis_session:
                _dis_user = await _dis_session.scalar(
                    select(User).where(User.telegram_id == telegram_id)
                )
                if not _dis_user or not _dis_user.role:
                    await query.answer("לא מורשה")
                    return
                answer, log_id = await _pt.answer_project_query(
                    identifier, _dis_session, {},
                    user_id=_dis_user.id,
                    precomputed_intent="by_identifier",
                    precomputed_param=identifier,
                )
            await query.edit_message_text(
                f"‏{answer}",
                parse_mode="HTML",
                reply_markup=_feedback_keyboard(log_id) if log_id else None,
            )
            return

        # --- Manager gold curation (/gold) ---
        if data.startswith("gold:"):
            from app.services import gold_telegram_service as gtg
            from app.services.gold_truth_service import save_gold, propose_gold
            from app.services.telegram_state import _awaiting_gold_text
            parts = data.split(":", 2)
            if len(parts) != 3:
                return
            _, g_action, g_id = parts
            telegram_id = update.effective_user.id
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

            if g_action == "stop":
                async with async_session_maker() as _s:
                    _u = await _s.scalar(select(User).where(User.telegram_id == telegram_id))
                if not gtg.is_manager(_u):
                    return
                _awaiting_gold_text.pop(telegram_id, None)
                await context.bot.send_message(chat_id=update.effective_chat.id, text="‏⏹ הסתיים. תודה!")
                return

            async with async_session_maker() as session:
                user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
                if not gtg.is_manager(user):
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="‏🔒 פקודה זו זמינה למנהלים בלבד.")
                    return
                from app.models import QueryLog as _QL_gold
                try:
                    qlog = await session.get(_QL_gold, int(g_id))
                except ValueError:
                    return
                question = qlog.question if qlog else None

                if question is None and g_action in ("approve", "edit", "skip"):
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="‏⚠️ השאלה לא נמצאה. שלח /gold להמשך.")
                    return

                if g_action == "approve" and question:
                    proposal = await propose_gold(session, question, use_llm=True)
                    await save_gold(session, question=question, gold_answer=proposal["answer"],
                                    user_id=user.id, source="telegram",
                                    target_project=proposal.get("target_project"),
                                    target_field=proposal.get("target_field"))
                    await context.bot.send_message(chat_id=update.effective_chat.id, text="‏✅ נשמר כתשובת זהב.")
                elif g_action == "edit" and question:
                    _awaiting_gold_text[telegram_id] = {"question": question}
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="‏✏️ שלח/י את תשובת הזהב הנכונה כהודעה.")
                    return

                cand = await gtg.next_candidate(session, exclude_questions=set())
                if not cand:
                    await context.bot.send_message(chat_id=update.effective_chat.id,
                                                   text="‏✅ אין עוד שאלות. תודה!")
                    return
                nxt = await propose_gold(session, cand["question"], use_llm=True)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=("‏🥇 <b>בניית תשובת זהב</b>\n\n"
                      f"‏<b>שאלה:</b> {_html.escape(cand['question'])}\n\n"
                      f"‏<b>הצעה:</b> {_html.escape(nxt['answer'])}"),
                parse_mode="HTML",
                reply_markup=gtg.gold_keyboard(cand["id"]),
            )
            return

        # --- Query log failure cause (after 👎) ---
        if query.data.startswith("lfc:"):
            from app.models import QueryLog as _QL
            parts = query.data.split(":", 2)
            if len(parts) != 3:
                return
            _, lfc_log_id, lfc_code = parts
            if lfc_code in CAUSE_MAP:
                async with async_session_maker() as session:
                    try:
                        log = await session.get(_QL, int(lfc_log_id))
                        if log:
                            log.failure_type = lfc_code
                            await session.commit()
                    except ValueError:
                        return
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="\u200Fתודה! זה עוזר לשפר את המערכת.",
            )
            return

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

                if action == "master_yes":
                    async with async_session_maker() as db:
                        # Delete any previous master entirely (stale chunks pollute retrieval)
                        from app.services.knowledge_service import delete_old_masters
                        await delete_old_masters(db, exclude_file_id=file_id)
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
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                if action == "lfb_up":
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="\u200F👍 תודה! הפידבק נשמר.",
                    )
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="\u200F👎 נשמר. מה היה לא בסדר?",
                        reply_markup=_cause_keyboard(decision_id),
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

            # --- Decision relevance toggle ---
            if action == "dec_irrel":
                _awaiting_irrelevant_reason[telegram_id] = decision_id
                await query.edit_message_text(
                    "‏⛔ <b>סמן כלא רלוונטי</b>\n\n"
                    f"שלח סיבה קצרה להחלטה #{decision_id}\n"
                    "(או שלח <code>ללא</code> לדילוג)",
                    parse_mode="HTML",
                )
                return

            if action == "dec_rel":
                decision_svc = DecisionService(session, self.application)
                success, msg = await decision_svc.set_decision_relevance(decision_id, approver, is_relevant=True)
                await query.edit_message_text(f"‏{msg}", parse_mode="HTML")
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

            # --- Second brain: save the pending message as a fact (🧠 button) ---
            if action == "dec_conf_m":
                original_text = _awaiting_decision_confirm.pop(telegram_id, None)
                if not original_text:
                    await query.edit_message_text("‏⚠️ פג תוקף הבקשה. שלח את העובדה מחדש עם ״זכור ש...״.")
                    return
                from app.services import memory_service as _mem_svc
                from app.models import RoleEnum as _RE_mem
                if approver.role == _RE_mem.VIEWER:
                    await query.edit_message_text("‏🔒 שמירת עובדות אינה זמינה למשתמשי צפייה.")
                    return
                content = _mem_svc.extract_remember_content(original_text) or original_text
                try:
                    project_id, project_label = await _mem_svc.link_project(content, session)
                    note = await _mem_svc.save_memory(
                        session, content=content, user_id=approver.id, project_id=project_id,
                    )
                except Exception:
                    logger.error("dec_conf_m: save failed", exc_info=True)
                    await query.edit_message_text("‏⚠️ לא הצלחתי לשמור את העובדה. נסה שוב.")
                    return
                msg = f"‏🧠 <b>נשמר בזיכרון הארגוני:</b>\n{_html.escape(note.content)}"
                if project_label:
                    msg += f"\n📁 שויך לפרויקט: {_html.escape(project_label)}"
                await query.edit_message_text(
                    msg, parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("↩️ שכח את זה", callback_data=f"mem_forget:{note.id}"),
                    ]]),
                )
                await self._notify_admins_new_memory(approver, note)
                return

            # --- Second brain: forget a memory note ---
            if action == "mem_forget":
                from app.services import memory_service as _mem_svc
                from app.models import RoleEnum as _RE_mem
                if approver.role == _RE_mem.VIEWER:
                    return
                ok = await _mem_svc.forget_memory(session, decision_id, approver.id)
                await query.edit_message_text(
                    "‏🧠 העובדה נמחקה מהזיכרון הארגוני." if ok else "‏⚠️ העובדה לא נמצאה או שכבר נמחקה.",
                )
                return

            # --- Decision classification confirmation (yes/no buttons) ---
            if action in ("dec_conf_y", "dec_conf_n"):
                original_text = _awaiting_decision_confirm.pop(telegram_id, None)
                if action == "dec_conf_n" or not original_text:
                    await query.edit_message_text("‏בסדר, ממשיכים.")
                    return
                # Show preview before committing
                await query.edit_message_text("‏⏳ <b>מנתח...</b>", parse_mode="HTML")
                try:
                    _dsvc_tmp = DecisionService(session, self.application)
                    pre_result = await _dsvc_tmp.analyze_only(approver, original_text)
                    _awaiting_decision_preview[telegram_id] = {
                        "text": original_text,
                        "result": pre_result,
                        "user_has_manager": _user_has_manager(approver),
                    }
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=_build_preview_text(pre_result),
                        parse_mode="HTML",
                        reply_markup=_decision_preview_keyboard(),
                    )
                except Exception as _err:
                    logger.error(f"analyze_only (dec_conf_y) failed: {_err}", exc_info=True)
                    if _user_has_manager(approver):
                        _awaiting_mgr_approval_confirm[telegram_id] = original_text
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text="‏👔 <b>האם החלטה זו דורשת אישור מנהל?</b>",
                            parse_mode="HTML",
                            reply_markup=_mgr_approval_keyboard(),
                        )
                    else:
                        decision_svc = DecisionService(session, self.application)
                        reply = await decision_svc.process(approver, original_text)
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id, text=reply, parse_mode="HTML",
                            reply_markup=get_menu_shortcut_keyboard(),
                        )
                return

            # --- Decision preview approve/dismiss ---
            if action in ("dec_prev_y", "dec_prev_n"):
                preview_data = _awaiting_decision_preview.pop(telegram_id, None)
                if action == "dec_prev_n" or not preview_data:
                    await query.edit_message_text("‏❌ בוטל — ההחלטה לא תועדה.")
                    return
                original_text = preview_data["text"]
                pre_result = preview_data["result"]
                if preview_data.get("user_has_manager"):
                    _awaiting_mgr_approval_confirm[telegram_id] = {"text": original_text, "result": pre_result}
                    await query.edit_message_text(
                        "‏👔 <b>האם החלטה זו דורשת אישור מנהל?</b>",
                        parse_mode="HTML",
                        reply_markup=_mgr_approval_keyboard(),
                    )
                else:
                    await query.edit_message_text("‏⏳ <b>מעבד את ההחלטה...</b>", parse_mode="HTML")
                    decision_svc = DecisionService(session, self.application)
                    reply = await decision_svc.process(approver, original_text, pre_result=pre_result)
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id, text=reply, parse_mode="HTML",
                        reply_markup=get_menu_shortcut_keyboard(),
                    )
                return

            # --- Manager approval answer ---
            if action in ("mgr_yes", "mgr_no"):
                entry = _awaiting_mgr_approval_confirm.pop(telegram_id, None)
                if not entry:
                    await query.edit_message_text("‏⚠️ פג תוקף הבקשה. שלח את ההחלטה מחדש.")
                    return
                if isinstance(entry, dict):
                    original_text = entry["text"]
                    pre_result = entry.get("result")
                else:
                    original_text = entry
                    pre_result = None
                await query.edit_message_text("‏⏳ <b>מעבד את ההחלטה...</b>", parse_mode="HTML")
                decision_svc = DecisionService(session, self.application)
                reply = await decision_svc.process(
                    approver, original_text,
                    force_approval=(action == "mgr_yes"),
                    pre_result=pre_result,
                )
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=reply,
                    parse_mode="HTML",
                    reply_markup=get_menu_shortcut_keyboard(),
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

    async def _handle_viewer_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, user, text: str
    ) -> None:
        """Handle all free-text from VIEWER role users."""
        from app.services.projects_menu_service import (
            get_menu_keyboard as pm_kb, get_menu_text as pm_text,
            get_total_active, build_project_card,
        )
        from app.models import Project
        from sqlalchemy import select as _sel

        # keyword: projects menu
        if "פרוייקטים" in text or "פרויקטים" in text:
            async with async_session_maker() as _s:
                _total = await get_total_active(_s)
            await update.message.reply_text(
                pm_text(_total),
                parse_mode="HTML",
                reply_markup=pm_kb(),
            )
            return

        # keyword: decisions blocked
        if "החלטות" in text:
            await update.message.reply_text(
                _VIEWER_DECISIONS_BLOCKED,
                reply_markup=_viewer_reply_keyboard(),
            )
            return

        # project name search
        async with async_session_maker() as _s:
            rows = list((await _s.scalars(
                _sel(Project)
                .where(Project.is_active.is_(True))
                .where(Project.name.ilike(f"%{text}%"))
                .limit(6)
            )).all())

        if len(rows) == 1:
            await update.message.reply_text(
                build_project_card(rows[0]),
                parse_mode="HTML",
                reply_markup=_viewer_reply_keyboard(),
            )
            return

        if 2 <= len(rows) <= 5:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            import html as _h
            btns = [
                [InlineKeyboardButton(
                    f"📁 {_h.escape(p.name or str(p.id))}",
                    callback_data=f"pm:d:{p.id}:viewer:0",
                )]
                for p in rows
            ]
            btns.append([InlineKeyboardButton("🔙 תפריט", callback_data="pm:menu")])
            await update.message.reply_text(
                "‏נמצאו מספר פרוייקטים — בחר:",
                reply_markup=InlineKeyboardMarkup(btns),
            )
            return

        # fallthrough: AI analysis display-only, no Decision saved
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        from app.services.decision_service import DecisionService
        async with async_session_maker() as _s:
            decision_svc = DecisionService(_s, self.application)
            try:
                pre_result = await decision_svc.analyze_only(user, text)
            except Exception as _err:
                logger.error(f"viewer analyze_only failed: {_err}", exc_info=True)
                await update.message.reply_text(
                    "‏⚠️ שגיאה בניתוח. נסה שוב.",
                    reply_markup=_viewer_reply_keyboard(),
                )
                return

        import html as _h2
        type_map = {"INFO": "מידע", "NORMAL": "רגיל", "CRITICAL": "קריטי", "UNCERTAIN": "לא ודאי"}
        t = (pre_result.get("type") or "").upper()
        reply = (
            "‏🔍 <b>ניתוח AI (לצפייה בלבד):</b>\n\n"
            f"<b>סוג:</b> {type_map.get(t, t or '—')}\n"
            f"<b>סיכום:</b> {_h2.escape(pre_result.get('summary') or '—')}\n"
            f"<b>פעולה מומלצת:</b> {_h2.escape(pre_result.get('recommended_action') or '—')}\n"
            f"<b>ביטחון:</b> {pre_result.get('confidence', 0):.0%}"
        )
        await update.message.reply_text(
            reply,
            parse_mode="HTML",
            reply_markup=_viewer_reply_keyboard(),
        )

    async def _handle_decisions_menu(
        self,
        query,
        context: ContextTypes.DEFAULT_TYPE,
        data: str,
        telegram_id: int,
        user,
    ) -> None:
        """Handle all dm:* and dm_cf:* callback actions."""
        from app.services.decisions_menu_service import (
            get_menu_keyboard, get_menu_text, get_menu_counts,
            build_custom_filter_keyboard, build_custom_filter_message,
            build_results_keyboard, build_custom_results_keyboard,
            format_results_message, format_decision_card, build_decision_card_keyboard,
            query_decisions, get_user_raci_roles, SHORTCUT_PRESETS,
            query_pending_feedback, build_feedback_results_keyboard,
        )
        from app.services.telegram_state import _decisions_menu_state, _awaiting_fb_menu_text
        from app.services.feedback_service import save_telegram_feedback_score
        import html as _html

        # ── dm:noop — page indicator button, do nothing ──────────────────────
        if data == "dm:noop":
            return

        # ── dm:menu — return to main menu ────────────────────────────────────
        if data == "dm:menu":
            _decisions_menu_state.pop(telegram_id, None)
            async with async_session_maker() as _cnt_s:
                counts = await get_menu_counts(_cnt_s, user.id)
            await query.edit_message_text(
                get_menu_text(counts),
                parse_mode="HTML",
                reply_markup=get_menu_keyboard(counts["feedback"]),
            )
            return

        # ── dm:custom — open stateful custom filter panel ────────────────────
        if data == "dm:custom":
            _decisions_menu_state[telegram_id] = {
                "owner": "all", "type": None, "status": None, "date_days": 30, "page": 0, "raci": None,
                "show_irrelevant": False,
            }
            await query.edit_message_text(
                build_custom_filter_message(),
                parse_mode="HTML",
                reply_markup=build_custom_filter_keyboard(_decisions_menu_state[telegram_id]),
            )
            return

        # ── dm:feedback:{page} — pending-feedback list ───────────────────────
        if data.startswith("dm:feedback:"):
            try:
                fb_page = int(data.split(":")[2])
            except (IndexError, ValueError):
                fb_page = 0
            async with async_session_maker() as session:
                fb_decisions, fb_total = await query_pending_feedback(session, user.id, fb_page)
            if not fb_decisions:
                await query.edit_message_text(
                    "‏⭐ <b>ממתין למשוב שלך</b>\n\nכל ההחלטות קיבלו משוב. כל הכבוד!",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 תפריט", callback_data="dm:menu"),
                    ]]),
                )
                return
            await query.edit_message_text(
                f"‏⭐ <b>ממתין למשוב שלך</b> ({fb_total})\n<i>בחר החלטה לדירוג:</i>",
                parse_mode="HTML",
                reply_markup=build_feedback_results_keyboard(fb_decisions, fb_page, fb_total),
            )
            return

        # ── dm:fbsel:{decision_id}:{page} — show score buttons ───────────────
        if data.startswith("dm:fbsel:"):
            parts = data.split(":")
            try:
                sel_dec_id, sel_page = int(parts[2]), int(parts[3])
            except (IndexError, ValueError):
                return
            async with async_session_maker() as session:
                from app.models import Decision as _Decision
                sel_dec = await session.get(_Decision, sel_dec_id)
            if not sel_dec:
                return
            score_labels = {1: "1️⃣ כישלון", 2: "2️⃣ לא טוב", 3: "3️⃣ בסדר", 4: "4️⃣ טוב", 5: "5️⃣ מצוין"}
            score_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(lbl, callback_data=f"dm:fbsc:{s}:{sel_dec_id}:{sel_page}")
                 for s, lbl in score_labels.items()],
                [InlineKeyboardButton("🔙 חזרה לרשימה", callback_data=f"dm:feedback:{sel_page}")],
            ])
            summary_short = (sel_dec.summary or "")[:50]
            await query.edit_message_text(
                f"‏⭐ <b>משוב — #{sel_dec_id}</b> — {_html.escape(summary_short)}\n\n"
                f"📋 <b>סיכום:</b> {_html.escape(sel_dec.summary or '')}\n"
                f"🎯 <b>פעולה:</b> {_html.escape(sel_dec.recommended_action or '')}\n\n"
                f"כיצד הסתיים הביצוע? בחר ציון:",
                parse_mode="HTML",
                reply_markup=score_kb,
            )
            return

        # ── dm:fbsc:{score}:{decision_id}:{page} — save score ────────────────
        if data.startswith("dm:fbsc:"):
            parts = data.split(":")
            try:
                fb_score, fb_dec_id, fb_back = int(parts[2]), int(parts[3]), int(parts[4])
            except (IndexError, ValueError):
                return
            async with async_session_maker() as session:
                ok = await save_telegram_feedback_score(session, user.id, fb_dec_id, fb_score)
            if ok:
                score_names = {1: "כישלון", 2: "לא טוב", 3: "בסדר", 4: "טוב", 5: "מצוין"}
                _awaiting_fb_menu_text[telegram_id] = (fb_dec_id, fb_back)
                await query.answer()
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=(
                        f"‏✅ ציון {fb_score} — {score_names.get(fb_score, '')} נשמר.\n"
                        f"רוצה להוסיף הערה? שלח טקסט, או /skip לדילוג."
                    ),
                    parse_mode="HTML",
                )
            return

        # ── dm:d:{id}:{back_shortcut}:{back_page} — decision detail card ────────
        if data.startswith("dm:d:"):
            parts = data.split(":")
            try:
                card_dec_id  = int(parts[2])
                back_shortcut = parts[3] if len(parts) > 3 else "my"
                back_page     = int(parts[4]) if len(parts) > 4 else 0
            except (IndexError, ValueError):
                return
            async with async_session_maker() as session:
                from app.models import Decision as _Decision
                card_dec = await session.get(_Decision, card_dec_id)
                if not card_dec:
                    await query.edit_message_text("‏⚠️ החלטה לא נמצאה.", parse_mode="HTML")
                    return
                raci_map = await get_user_raci_roles(session, [card_dec_id], user.id)
            raci_badge = raci_map.get(card_dec_id, "")
            await query.edit_message_text(
                format_decision_card(card_dec, raci_badge),
                parse_mode="HTML",
                reply_markup=build_decision_card_keyboard(card_dec, back_shortcut, back_page),
            )
            return

        # ── dm:{shortcut}:{page} — stateless shortcut results ────────────────
        if data.startswith("dm:"):
            parts = data.split(":")
            shortcut = parts[1] if len(parts) > 1 else ""
            try:
                page = int(parts[2]) if len(parts) > 2 else 0
            except ValueError:
                page = 0
            preset = SHORTCUT_PRESETS.get(shortcut)
            if not preset:
                return
            async with async_session_maker() as session:
                decisions, total = await query_decisions(
                    session, user.id,
                    preset["owner"], preset["type"], preset["status"], preset["date_days"],
                    page,
                )
                raci_map = await get_user_raci_roles(session, [d.id for d in decisions], user.id)
            await query.edit_message_text(
                format_results_message(preset["title"], decisions, total, page, raci_map),
                parse_mode="HTML",
                reply_markup=build_results_keyboard(decisions, shortcut, page, total),
            )
            return

        # ── dm_cf:* — custom filter session callbacks ─────────────────────────
        if data.startswith("dm_cf:"):
            parts = data.split(":")
            sub = parts[1]
            state = _decisions_menu_state.get(telegram_id)

            if sub == "o" and state is not None and len(parts) > 2:
                state["owner"] = parts[2]
                await query.edit_message_reply_markup(
                    reply_markup=build_custom_filter_keyboard(state)
                )
                return

            if sub == "t" and state is not None and len(parts) > 2:
                val = parts[2]
                state["type"] = None if val == "all" else val
                await query.edit_message_reply_markup(
                    reply_markup=build_custom_filter_keyboard(state)
                )
                return

            if sub == "s" and state is not None and len(parts) > 2:
                val = parts[2]
                state["status"] = None if val == "all" else val
                await query.edit_message_reply_markup(
                    reply_markup=build_custom_filter_keyboard(state)
                )
                return

            if sub == "d" and state is not None and len(parts) > 2:
                try:
                    state["date_days"] = int(parts[2])
                except ValueError:
                    return
                await query.edit_message_reply_markup(
                    reply_markup=build_custom_filter_keyboard(state)
                )
                return

            if sub == "r" and state is not None and len(parts) > 2:
                val = parts[2]
                state["raci"] = None if val == "all" else val
                await query.edit_message_reply_markup(
                    reply_markup=build_custom_filter_keyboard(state)
                )
                return

            if sub == "rel" and state is not None and len(parts) > 2:
                val = parts[2]
                if val == "yes":
                    state["show_irrelevant"] = False
                elif val == "no":
                    state["show_irrelevant"] = True
                else:  # "all"
                    state["show_irrelevant"] = None
                await query.edit_message_reply_markup(
                    reply_markup=build_custom_filter_keyboard(state)
                )
                return

            if sub == "show":
                if state is None:
                    await query.edit_message_text(
                        "‏⚠️ סשן הסינון פג. פתח את תפריט ההחלטות מחדש.",
                        reply_markup=get_menu_keyboard(),
                    )
                    return
                async with async_session_maker() as session:
                    decisions, total = await query_decisions(
                        session, user.id,
                        state["owner"], state["type"], state["status"], state["date_days"],
                        0, raci=state.get("raci"),
                        show_irrelevant=state.get("show_irrelevant", False),
                    )
                    raci_map = await get_user_raci_roles(session, [d.id for d in decisions], user.id)
                # Keep state for pagination — cleared only on dm:menu
                await query.edit_message_text(
                    format_results_message("🔍 תוצאות סינון מותאם", decisions, total, 0, raci_map),
                    parse_mode="HTML",
                    reply_markup=build_custom_results_keyboard(decisions, 0, total),
                )
                return

            if sub == "pg":
                try:
                    page = int(parts[2]) if len(parts) > 2 else 0
                except ValueError:
                    page = 0
                if state is None:
                    await query.edit_message_text(
                        "‏⚠️ סשן הסינון פג.",
                        reply_markup=get_menu_keyboard(),
                    )
                    return
                async with async_session_maker() as session:
                    decisions, total = await query_decisions(
                        session, user.id,
                        state["owner"], state["type"], state["status"], state["date_days"],
                        page, raci=state.get("raci"),
                        show_irrelevant=state.get("show_irrelevant", False),
                    )
                    raci_map = await get_user_raci_roles(session, [d.id for d in decisions], user.id)
                await query.edit_message_text(
                    format_results_message("🔍 תוצאות סינון מותאם", decisions, total, page, raci_map),
                    parse_mode="HTML",
                    reply_markup=build_custom_results_keyboard(decisions, page, total),
                )
                return

    # ------------------------------------------------------------------
    # Operations room (חדר מבצעים) — callback router + wizard text steps
    # ------------------------------------------------------------------

    _MISSION_LIST_TITLES = {
        "my":   "👤 המשימות שלי",
        "late": "⚠️ משימות באיחור",
        "hist": "✅ משימות שהושלמו",
    }

    async def _render_missions_menu(self, query) -> None:
        from app.services.missions_menu_service import get_menu_keyboard, get_menu_text, get_board_counts
        async with async_session_maker() as session:
            counts, overdue = await get_board_counts(session)
        await query.edit_message_text(
            get_menu_text(counts, overdue), parse_mode="HTML",
            reply_markup=get_menu_keyboard(counts),
        )

    async def _render_missions_list(self, query, origin: str, page: int, user) -> None:
        from app.services import missions_menu_service as oms
        async with async_session_maker() as session:
            if origin == "hist":
                missions, total = await oms.get_done_history(session, page=page)
                title = self._MISSION_LIST_TITLES["hist"]
            elif origin == "my":
                missions, total = await oms.query_missions(session, owner_id=user.id, page=page)
                title = self._MISSION_LIST_TITLES["my"]
            elif origin == "late":
                missions, total = await oms.query_missions(session, only_overdue=True, page=page)
                title = self._MISSION_LIST_TITLES["late"]
            elif origin.startswith("q"):
                quad = origin[1:]
                missions, total = await oms.query_missions(session, quadrant=quad, page=page)
                title = oms.quadrant_label(quad, with_axis=True)
            else:
                return
        await query.edit_message_text(
            oms.format_results_message(title, missions, total, page),
            parse_mode="HTML",
            reply_markup=oms.build_results_keyboard(
                origin, page, total, missions, with_done_shortcut=(origin == "my"),
            ),
        )

    async def _render_mission_card(self, query, mission_id: int, origin: str, page: int) -> None:
        from app.services import missions_menu_service as oms
        async with async_session_maker() as session:
            m = await oms.get_mission(session, mission_id)
            if not m:
                await query.edit_message_text("‏❌ המשימה לא נמצאה.")
                return
            text = oms.build_mission_card(m)
            kb = oms.build_mission_card_keyboard(m, origin, page)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

    async def _notify_mission_owner(self, bot, session, mission, actor_user) -> None:
        """Tell the owner they got a mission — skipped when the actor owns it."""
        if mission.owner_id == actor_user.id:
            return
        owner = await session.get(User, mission.owner_id)
        if not owner or not owner.telegram_id:
            return
        from app.services.missions_menu_service import quadrant_label, quadrant_key, format_due
        try:
            await bot.send_message(
                chat_id=owner.telegram_id,
                text=(
                    f"‏🎯 <b>משימה חדשה הוקצתה לך</b>\n"
                    f"<b>{_html.escape(mission.title or '')}</b>\n"
                    f"{quadrant_label(quadrant_key(mission), with_axis=True)}\n"
                    f"📅 יעד: {format_due(mission.due_date)}\n"
                    f"<i>הוקצתה ע\"י {_html.escape(actor_user.username or '')}</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(f"missions: owner notification failed: {e}")

    async def _handle_missions_menu(
        self, query, context, data: str, telegram_id: int, user,
    ) -> None:
        from app.services import missions_menu_service as oms
        from app.services.telegram_state import _missions_create_state, _missions_edit_state

        if data == "om:noop":
            return

        if data == "om:menu":
            _missions_create_state.pop(telegram_id, None)
            _missions_edit_state.pop(telegram_id, None)
            await self._render_missions_menu(query)
            return

        # ── Creation wizard ────────────────────────────────────────────
        if data == "om:new":
            _missions_create_state[telegram_id] = {"step": "title"}
            await query.edit_message_text(
                "‏➕ <b>משימה חדשה</b>  (שלב 1/5)\n\nשלח את <b>כותרת</b> המשימה:",
                parse_mode="HTML", reply_markup=oms.build_cancel_keyboard(),
            )
            return

        if data == "om:c:abort":
            _missions_create_state.pop(telegram_id, None)
            _missions_edit_state.pop(telegram_id, None)
            await self._render_missions_menu(query)
            return

        if data.startswith("om:c:"):
            state = _missions_create_state.get(telegram_id)
            if state is None:
                await query.edit_message_text(
                    "‏⌛ הפעולה הזו כבר לא פעילה. פתח שוב את חדר המבצעים.",
                )
                return
            parts = data.split(":")
            action = parts[2]

            if action == "skipdesc":
                state["step"] = "quadrant"
                await query.edit_message_text(
                    oms.format_create_progress(state) + "\n\n(שלב 3/5) בחר <b>רביע</b>:",
                    parse_mode="HTML", reply_markup=oms.build_quadrant_pick_keyboard("om:c:qd"),
                )
                return

            if action == "qd" and len(parts) > 3:
                state["quadrant"] = parts[3]
                state["step"] = "owner"
                async with async_session_maker() as session:
                    users = await oms.list_assignable_users(session, exclude_id=user.id)
                state["owner_ids"] = [u.id for u in users]
                state["owner_names"] = [u.username or f"#{u.id}" for u in users]
                await query.edit_message_text(
                    oms.format_create_progress(state) + "\n\n(שלב 4/5) בחר <b>אחראי</b>:",
                    parse_mode="HTML",
                    reply_markup=oms.build_owner_pick_keyboard(users, "om:c:own"),
                )
                return

            if action == "own" and len(parts) > 3:
                token = parts[3]
                if token == "me":
                    state["owner_id"] = user.id
                    state["owner_name"] = user.username or "אני"
                else:
                    try:
                        idx = int(token)
                        state["owner_id"] = state["owner_ids"][idx]
                        state["owner_name"] = state["owner_names"][idx]
                    except (ValueError, IndexError, KeyError):
                        return
                state["step"] = "due"
                await query.edit_message_text(
                    oms.format_create_progress(state) + "\n\n(שלב 5/5) בחר <b>תאריך יעד</b>:",
                    parse_mode="HTML", reply_markup=oms.build_due_pick_keyboard("om:c:due"),
                )
                return

            if action == "due" and len(parts) > 3:
                key = parts[3]
                if key == "custom":
                    state["step"] = "due_text"
                    await query.edit_message_text(
                        oms.format_create_progress(state)
                        + "\n\nשלח תאריך בפורמט <b>DD/MM</b> או <b>DD/MM/YYYY</b>:",
                        parse_mode="HTML", reply_markup=oms.build_cancel_keyboard(),
                    )
                    return
                handled, due = oms.resolve_due_quick_pick(key)
                if not handled:
                    return
                state["due_date"] = due
                state["step"] = "confirm"
                await query.edit_message_text(
                    oms.format_create_progress(state) + "\n\nלשמור את המשימה?",
                    parse_mode="HTML", reply_markup=oms.build_confirm_keyboard(),
                )
                return

            if action == "save":
                if state.get("step") != "confirm" or not state.get("title"):
                    return
                urg, imp = oms.quadrant_flags(state.get("quadrant", "backlog"))
                async with async_session_maker() as session:
                    m = await oms.create_mission(
                        session,
                        title=state["title"],
                        description=state.get("description"),
                        is_urgent=urg,
                        is_important=imp,
                        owner_id=state.get("owner_id", user.id),
                        created_by_id=user.id,
                        due_date=state.get("due_date"),
                    )
                    await self._notify_mission_owner(context.bot, session, m, user)
                    m = await oms.get_mission(session, m.id)
                    card = oms.build_mission_card(m)
                    kb = oms.build_mission_card_keyboard(m, f"q{oms.quadrant_key(m)}", 0)
                _missions_create_state.pop(telegram_id, None)
                await query.edit_message_text(
                    "‏✅ המשימה נשמרה!\n\n" + card, parse_mode="HTML", reply_markup=kb,
                )
                return
            return

        # ── Detail card ────────────────────────────────────────────────
        if data.startswith("om:d:"):
            _missions_edit_state.pop(telegram_id, None)
            parts = data.split(":")
            try:
                mission_id = int(parts[2])
                origin = parts[3] if len(parts) > 3 else "my"
                page = int(parts[4]) if len(parts) > 4 else 0
            except (ValueError, IndexError):
                return
            await self._render_mission_card(query, mission_id, origin, page)
            return

        # ── Card actions: om:a:{action}:{id}:{origin}:{page} ─────────
        if data.startswith("om:a:"):
            parts = data.split(":")
            try:
                action = parts[2]
                mission_id = int(parts[3])
                origin = parts[4] if len(parts) > 4 else "my"
                page = int(parts[5]) if len(parts) > 5 else 0
            except (ValueError, IndexError):
                return

            if action in ("start", "done", "reopen", "cancel"):
                new_status = {
                    "start": oms.MissionStatusEnum.IN_PROGRESS.value,
                    "done": oms.MissionStatusEnum.DONE.value,
                    "reopen": oms.MissionStatusEnum.OPEN.value,
                    "cancel": oms.MissionStatusEnum.CANCELLED.value,
                }[action]
                async with async_session_maker() as session:
                    m = await oms.get_mission(session, mission_id)
                    if m:
                        await oms.set_status(session, m, new_status)
                await self._render_mission_card(query, mission_id, origin, page)
                return

            tail = f"{mission_id}:{origin}:{page}"
            back_to_card = f"om:d:{tail}"
            if action == "quad":
                await query.edit_message_reply_markup(
                    reply_markup=oms.build_quadrant_pick_keyboard(f"om:e:qd:{tail}", abort_cd=back_to_card)
                )
                return
            if action == "own":
                async with async_session_maker() as session:
                    users = await oms.list_assignable_users(session, exclude_id=user.id)
                _missions_edit_state[telegram_id] = {
                    "mission_id": mission_id,
                    "owner_ids": [u.id for u in users],
                    "origin": origin, "page": page,
                }
                await query.edit_message_reply_markup(
                    reply_markup=oms.build_owner_pick_keyboard(users, f"om:e:own:{tail}", abort_cd=back_to_card)
                )
                return
            if action == "due":
                await query.edit_message_reply_markup(
                    reply_markup=oms.build_due_pick_keyboard(f"om:e:due:{tail}", abort_cd=back_to_card)
                )
                return
            return

        # ── Edit-picker values: om:e:{kind}:{id}:{origin}:{page}:{val} ─
        if data.startswith("om:e:"):
            parts = data.split(":")
            try:
                kind = parts[2]
                mission_id = int(parts[3])
                origin, page = parts[4], int(parts[5])
                value = parts[6]
            except (ValueError, IndexError):
                return
            async with async_session_maker() as session:
                m = await oms.get_mission(session, mission_id)
                if not m:
                    await query.edit_message_text("‏❌ המשימה לא נמצאה.")
                    return
                if kind == "qd":
                    await oms.update_mission(session, m, quadrant=value)
                elif kind == "own":
                    if value == "me":
                        new_owner_id = user.id
                    else:
                        edit_state = _missions_edit_state.get(telegram_id) or {}
                        try:
                            new_owner_id = edit_state["owner_ids"][int(value)]
                        except (ValueError, IndexError, KeyError):
                            await query.edit_message_text("‏⌛ הבחירה כבר לא פעילה. פתח את המשימה מחדש.")
                            return
                    await oms.update_mission(session, m, owner_id=new_owner_id)
                    await self._notify_mission_owner(context.bot, session, m, user)
                elif kind == "due":
                    if value == "custom":
                        _missions_edit_state[telegram_id] = {
                            "mission_id": mission_id, "mode": "due_text",
                            "origin": origin, "page": page,
                        }
                        await query.edit_message_text(
                            "‏📅 שלח תאריך יעד בפורמט <b>DD/MM</b> או <b>DD/MM/YYYY</b>:",
                            parse_mode="HTML", reply_markup=oms.build_cancel_keyboard(),
                        )
                        return
                    handled, due = oms.resolve_due_quick_pick(value)
                    if handled:
                        await oms.update_mission(session, m, due_date=due)
            _missions_edit_state.pop(telegram_id, None)
            await self._render_mission_card(query, mission_id, origin, page)
            return

        # ── List ✅ shortcut: om:ld:{id}:{origin}:{page} ───────────────
        if data.startswith("om:ld:"):
            parts = data.split(":")
            try:
                mission_id = int(parts[2])
                origin = parts[3] if len(parts) > 3 else "my"
                page = int(parts[4]) if len(parts) > 4 else 0
            except (ValueError, IndexError):
                return
            async with async_session_maker() as session:
                m = await oms.get_mission(session, mission_id)
                if m:
                    await oms.set_status(session, m, oms.MissionStatusEnum.DONE.value)
            await self._render_missions_list(query, origin, page, user)
            return

        # ── Digest ✔️: om:dg:done:{id} — keep the digest message intact ─
        if data.startswith("om:dg:done:"):
            try:
                mission_id = int(data.rsplit(":", 1)[1])
            except ValueError:
                return
            async with async_session_maker() as session:
                m = await oms.get_mission(session, mission_id)
                if not m:
                    return
                await oms.set_status(session, m, oms.MissionStatusEnum.DONE.value)
                title = m.title or ""
            # Drop just this button row so the rest of the digest stays actionable
            try:
                old_kb = query.message.reply_markup.inline_keyboard if query.message and query.message.reply_markup else []
                new_rows = [
                    row for row in old_kb
                    if not any(btn.callback_data == data for btn in row)
                ]
                await query.edit_message_reply_markup(
                    reply_markup=InlineKeyboardMarkup(new_rows) if new_rows else None
                )
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=telegram_id,
                text=f"‏✅ המשימה “{_html.escape(title)}” סומנה כבוצעה.",
                parse_mode="HTML",
            )
            return

        # ── Lists: om:{origin}:{page} ──────────────────────────────────
        parts = data.split(":")
        if len(parts) == 3:
            origin = parts[1]
            try:
                page = int(parts[2])
            except ValueError:
                return
            if origin in ("my", "late", "hist") or origin.startswith("q"):
                await self._render_missions_list(query, origin, page, user)
            return

    async def _handle_missions_text(self, update, context, user, text: str) -> None:
        """Text steps of the mission wizard / edit flows."""
        from app.services import missions_menu_service as oms
        from app.services.telegram_state import _missions_create_state, _missions_edit_state
        telegram_id = update.effective_user.id
        stripped = text.strip()

        # Reply-keyboard taps mid-flow must not be swallowed as free text
        _KB_LABELS = {
            "📁 פרוייקטים", "📋 החלטות", "📊 דוח שלי",
            "🎯 חדר מבצעים", "👥 דוח צוות", "📊 דוח פרויקטים",
        }
        if stripped in _KB_LABELS:
            await update.message.reply_text(
                "‏✋ אתה באמצע פעולה בחדר המבצעים. שלח טקסט להמשך, או בטל:",
                reply_markup=oms.build_cancel_keyboard(),
            )
            return

        # Edit flow: custom due date on an existing mission
        edit_state = _missions_edit_state.get(telegram_id)
        if edit_state and edit_state.get("mode") == "due_text":
            due = oms.parse_due_date_text(stripped)
            if due is None:
                await update.message.reply_text(
                    "‏❌ תאריך לא תקין. שלח בפורמט DD/MM או DD/MM/YYYY:",
                    reply_markup=oms.build_cancel_keyboard(),
                )
                return
            async with async_session_maker() as session:
                m = await oms.get_mission(session, edit_state["mission_id"])
                if m:
                    await oms.update_mission(session, m, due_date=due)
                    m = await oms.get_mission(session, m.id)
                    card = oms.build_mission_card(m)
                    kb = oms.build_mission_card_keyboard(
                        m, edit_state.get("origin", "my"), edit_state.get("page", 0),
                    )
            _missions_edit_state.pop(telegram_id, None)
            if m:
                await update.message.reply_text(card, parse_mode="HTML", reply_markup=kb)
            return

        # Creation wizard text steps
        state = _missions_create_state.get(telegram_id)
        if state is None:
            _missions_edit_state.pop(telegram_id, None)
            return

        step = state.get("step")
        if step == "title":
            state["title"] = stripped[:255]
            state["step"] = "desc"
            await update.message.reply_text(
                oms.format_create_progress(state)
                + "\n\n(שלב 2/5) שלח <b>תיאור</b> למשימה, או דלג:",
                parse_mode="HTML", reply_markup=oms.build_skip_desc_keyboard(),
            )
            return
        if step == "desc":
            state["description"] = stripped[:2000]
            state["step"] = "quadrant"
            await update.message.reply_text(
                oms.format_create_progress(state) + "\n\n(שלב 3/5) בחר <b>רביע</b>:",
                parse_mode="HTML", reply_markup=oms.build_quadrant_pick_keyboard("om:c:qd"),
            )
            return
        if step == "due_text":
            due = oms.parse_due_date_text(stripped)
            if due is None:
                await update.message.reply_text(
                    "‏❌ תאריך לא תקין. שלח בפורמט DD/MM או DD/MM/YYYY:",
                    reply_markup=oms.build_cancel_keyboard(),
                )
                return
            state["due_date"] = due
            state["step"] = "confirm"
            await update.message.reply_text(
                oms.format_create_progress(state) + "\n\nלשמור את המשימה?",
                parse_mode="HTML", reply_markup=oms.build_confirm_keyboard(),
            )
            return
        # Button-driven step — nudge instead of swallowing the text
        await update.message.reply_text(
            "‏☝️ בחר אחת מהאפשרויות בכפתורים למעלה, או בטל:",
            reply_markup=oms.build_cancel_keyboard(),
        )

    async def _handle_projects_menu(
        self, query, context, data: str, telegram_id: int, user,
    ) -> None:
        from app.services.projects_menu_service import (
            get_menu_keyboard, get_menu_text, get_total_active,
            get_th_sub_text, get_filter_field_text, get_filter_value_text,
            build_results_keyboard, build_th_sub_keyboard, build_th_results_keyboard,
            build_custom_results_keyboard, build_detail_back_keyboard,
            build_filter_field_keyboard, build_filter_value_keyboard, build_filter_date_keyboard,
            build_project_card, format_results_message, format_project_line,
            query_projects, get_filter_options, SHORTCUT_PRESETS, TYPE_ORDER,
        )
        from app.services.telegram_state import _projects_menu_state, _projects_detail_origin

        def _fresh_state():
            return {'stage': [], 'type': [], 'mgr': [], 'th': [], 'date': []}

        def _item_rows(projects, shortcut, page):
            rows = []
            for i, p in enumerate(projects):
                line = format_project_line(p)
                label = f"{page * 10 + i + 1}. " + _html.unescape(re.sub(r"<[^>]+>", "", line))[:55]
                rows.append([InlineKeyboardButton(label, callback_data=f"pm:d:{p.id}:{shortcut}:{page}")])
            return rows

        if data == 'pm:noop':
            return

        if data == 'pm:menu':
            _projects_menu_state.pop(telegram_id, None)
            _projects_detail_origin.pop(telegram_id, None)
            async with async_session_maker() as session:
                total = await get_total_active(session)
            await query.edit_message_text(
                get_menu_text(total), parse_mode='HTML', reply_markup=get_menu_keyboard(),
            )
            return

        if data == 'pm:th_menu':
            async with async_session_maker() as session:
                opts = await get_filter_options(session)
            await query.edit_message_text(
                get_th_sub_text(), parse_mode='HTML',
                reply_markup=build_th_sub_keyboard(opts.get('th', [])),
            )
            return

        if data.startswith('pm:th:'):
            parts = data.split(':')
            try:
                idx = int(parts[2]) if len(parts) > 2 else 0
                page = int(parts[3]) if len(parts) > 3 else 0
            except ValueError:
                return
            type_key = None
            if len(parts) > 4:
                try:
                    type_key = int(parts[4])
                except ValueError:
                    pass
            th_types = [TYPE_ORDER[type_key]] if type_key is not None and type_key < len(TYPE_ORDER) else None
            async with async_session_maker() as session:
                opts = await get_filter_options(session)
                th_options = opts.get('th', [])
                if idx >= len(th_options):
                    return
                th_val = th_options[idx]
                projects, total = await query_projects(
                    session, stages=None, types=th_types, mgrs=None,
                    ths=[th_val], dates=None, page=page,
                )
            shortcut_key = f'th{idx}'
            rows = _item_rows(projects, shortcut_key, page)
            kb = build_th_results_keyboard(idx, page, total, type_key=type_key)
            full_kb = InlineKeyboardMarkup(rows + list(kb.inline_keyboard))
            _projects_detail_origin[telegram_id] = (shortcut_key, page, type_key)
            label_th = th_val.replace('חסם לטיפול ', '')
            await query.edit_message_text(
                format_results_message(f"‏📌 {label_th}", projects, total, page),
                parse_mode="HTML", reply_markup=full_kb,
            )
            return

        if data.startswith('pm:') and not data.startswith('pm:d:'):
            parts = data.split(':')
            shortcut = parts[1] if len(parts) > 1 else ''
            try:
                page = int(parts[2]) if len(parts) > 2 else 0
            except ValueError:
                page = 0
            type_key = None
            if len(parts) > 3:
                try:
                    type_key = int(parts[3])
                except ValueError:
                    pass
            preset = SHORTCUT_PRESETS.get(shortcut)
            if not preset:
                return
            async with async_session_maker() as session:
                projects, total = await query_projects(
                    session,
                    stages=preset['stages'], types=preset['types'],
                    mgrs=preset['mgrs'], ths=preset['ths'],
                    dates=preset['dates'], page=page, type_key=type_key,
                )
            rows = _item_rows(projects, shortcut, page)
            kb = build_results_keyboard(shortcut, page, total, type_key=type_key)
            full_kb = InlineKeyboardMarkup(rows + list(kb.inline_keyboard))
            _projects_detail_origin[telegram_id] = (shortcut, page, type_key)
            await query.edit_message_text(
                format_results_message(preset['title'], projects, total, page),
                parse_mode='HTML', reply_markup=full_kb,
            )
            return

        if data.startswith('pm:d:'):
            parts = data.split(':')
            try:
                project_id = int(parts[2])
            except (ValueError, IndexError):
                return
            shortcut = parts[3] if len(parts) > 3 else 'late'
            try:
                page = int(parts[4]) if len(parts) > 4 else 0
            except ValueError:
                page = 0
            origin = _projects_detail_origin.get(telegram_id)
            if origin and len(origin) == 3:
                _, _, type_key = origin
            else:
                type_key = None
            async with async_session_maker() as session:
                p = await session.get(Project, project_id)
            if not p:
                await query.edit_message_text('‏⚠️ פרוייקט לא נמצא.', reply_markup=get_menu_keyboard())
                return
            _projects_detail_origin[telegram_id] = (shortcut, page, type_key)
            await query.edit_message_text(
                build_project_card(p), parse_mode='HTML',
                reply_markup=build_detail_back_keyboard(shortcut, page, type_key),
            )
            return

        if not data.startswith('pm_cf:'):
            return

        parts = data.split(':')
        sub = parts[1] if len(parts) > 1 else ''

        if sub == 'open':
            if telegram_id not in _projects_menu_state:
                _projects_menu_state[telegram_id] = _fresh_state()
            state = _projects_menu_state[telegram_id]
            await query.edit_message_text(
                get_filter_field_text(state), parse_mode='HTML',
                reply_markup=build_filter_field_keyboard(state),
            )
            return

        if sub == 'back':
            _projects_menu_state.pop(telegram_id, None)
            _projects_detail_origin.pop(telegram_id, None)
            async with async_session_maker() as session:
                total = await get_total_active(session)
            await query.edit_message_text(
                get_menu_text(total), parse_mode='HTML', reply_markup=get_menu_keyboard(),
            )
            return

        if sub == 'clr':
            _projects_menu_state[telegram_id] = _fresh_state()
            state = _projects_menu_state[telegram_id]
            await query.edit_message_text(
                get_filter_field_text(state), parse_mode='HTML',
                reply_markup=build_filter_field_keyboard(state),
            )
            return

        if sub == 'fd':
            state = _projects_menu_state.get(telegram_id, _fresh_state())
            await query.edit_message_text(
                get_filter_field_text(state), parse_mode='HTML',
                reply_markup=build_filter_field_keyboard(state),
            )
            return

        if sub == 'f' and len(parts) > 2:
            dim = parts[2]
            if telegram_id not in _projects_menu_state:
                _projects_menu_state[telegram_id] = _fresh_state()
            state = _projects_menu_state[telegram_id]
            selected = state.get(dim, [])
            if dim == 'date':
                await query.edit_message_text(
                    get_filter_value_text(dim), parse_mode='HTML',
                    reply_markup=build_filter_date_keyboard(selected),
                )
            else:
                async with async_session_maker() as session:
                    opts = await get_filter_options(session)
                await query.edit_message_text(
                    get_filter_value_text(dim), parse_mode='HTML',
                    reply_markup=build_filter_value_keyboard(dim, opts.get(dim, []), selected),
                )
            return

        if sub == 't' and len(parts) > 3:
            dim = parts[2]
            raw = parts[3]
            if telegram_id not in _projects_menu_state:
                _projects_menu_state[telegram_id] = _fresh_state()
            state = _projects_menu_state[telegram_id]
            selected = state.setdefault(dim, [])
            if dim == 'date':
                if raw in selected:
                    selected.remove(raw)
                else:
                    selected.append(raw)
                await query.edit_message_reply_markup(reply_markup=build_filter_date_keyboard(selected))
            else:
                try:
                    idx = int(raw)
                except ValueError:
                    return
                async with async_session_maker() as session:
                    opts = await get_filter_options(session)
                opt_list = opts.get(dim, [])
                if idx >= len(opt_list):
                    return
                val = opt_list[idx]
                if val in selected:
                    selected.remove(val)
                else:
                    selected.append(val)
                await query.edit_message_reply_markup(
                    reply_markup=build_filter_value_keyboard(dim, opt_list, selected)
                )
            return

        if sub == 'show':
            state = _projects_menu_state.get(telegram_id)
            if state is None:
                await query.edit_message_text(
                    '‏⚠️ סשן הסינון פג. פתח את תפריט הפרוייקטים מחדש.',
                    reply_markup=get_menu_keyboard(),
                )
                return
            async with async_session_maker() as session:
                projects, total = await query_projects(
                    session,
                    stages=state['stage'] or None, types=state['type'] or None,
                    mgrs=state['mgr'] or None, ths=state['th'] or None,
                    dates=state['date'] or None, page=0,
                )
            _projects_detail_origin[telegram_id] = ('cf', 0)
            rows = _item_rows(projects, 'cf', 0)
            cf_kb = build_custom_results_keyboard(0, total)
            full_kb = InlineKeyboardMarkup(rows + list(cf_kb.inline_keyboard))
            await query.edit_message_text(
                format_results_message('🔍 תוצאות סינון מותאם', projects, total, 0),
                parse_mode='HTML', reply_markup=full_kb,
            )
            return

        if sub == 'pg':
            try:
                page = int(parts[2]) if len(parts) > 2 else 0
            except ValueError:
                page = 0
            state = _projects_menu_state.get(telegram_id)
            if state is None:
                await query.edit_message_text('‏⚠️ סשן הסינון פג.', reply_markup=get_menu_keyboard())
                return
            async with async_session_maker() as session:
                projects, total = await query_projects(
                    session,
                    stages=state['stage'] or None, types=state['type'] or None,
                    mgrs=state['mgr'] or None, ths=state['th'] or None,
                    dates=state['date'] or None, page=page,
                )
            rows = _item_rows(projects, 'cf', page)
            cf_kb = build_custom_results_keyboard(page, total)
            full_kb = InlineKeyboardMarkup(rows + list(cf_kb.inline_keyboard))
            await query.edit_message_text(
                format_results_message('🔍 תוצאות סינון מותאם', projects, total, page),
                parse_mode='HTML', reply_markup=full_kb,
            )
            return


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
