"""Telegram bot service - handles incoming messages and Telegram operations."""

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import User, Message
from app.config import settings

logger = logging.getLogger(__name__)


class TelegramService:
    """Service for handling Telegram interactions."""

    def __init__(self, session: AsyncSession):
        """Initialize with database session."""
        self.session = session
        self.bot_token = settings.TELEGRAM_BOT_TOKEN

    async def handle_incoming_message(self, update: dict):
        """
        Handle incoming Telegram message update.

        Telegram sends updates with this structure:
        {
            "update_id": 123456,
            "message": {
                "message_id": 456,
                "from": {
                    "id": telegram_user_id,
                    "is_bot": false,
                    "first_name": "John",
                    ...
                },
                "text": "/start or user message"
            }
        }
        """
        if "message" not in update:
            logger.warning(f"Received update without message: {update}")
            return

        message = update["message"]
        telegram_user_id = message["from"]["id"]
        text = message.get("text", "").strip()

        # Get or create user
        user = await self._get_or_create_user(telegram_user_id, message["from"])

        if user is None:
            logger.error(f"Failed to process user {telegram_user_id}")
            return

        # Handle commands
        if text.startswith("/start"):
            await self._handle_start(user)
        elif text.startswith("/register"):
            await self._handle_register(user)
        else:
            # Store regular messages
            await self._store_message(user, text, message["message_id"])

    async def _get_or_create_user(self, telegram_user_id: int, from_data: dict) -> User:
        """Get existing user or create placeholder if not found."""
        stmt = select(User).where(User.telegram_id == telegram_user_id)
        user = await self.session.scalar(stmt)

        if user:
            return user

        # User doesn't exist yet - store in DB for later registration via API
        # Create with placeholder username and email
        user = User(
            telegram_id=telegram_user_id,
            username=from_data.get("first_name", "unknown"),
            email=f"placeholder_{telegram_user_id}@telegram.local",
            role=None,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)

        logger.info(f"Created placeholder user for telegram_id {telegram_user_id}")
        return user

    async def _handle_start(self, user: User):
        """Handle /start command - send welcome message."""
        logger.info(f"User {user.username} (ID: {user.id}) started bot")
        # In PHASE 3, we'll send actual Telegram messages via bot API
        # For now, just log
        pass

    async def _handle_register(self, user: User):
        """Handle /register command - prompt for registration."""
        logger.info(f"User {user.username} (ID: {user.id}) initiated registration")
        # In PHASE 3, we'll send Telegram form/keyboard
        # For now, just log
        pass

    async def _store_message(self, user: User, text: str, telegram_message_id: int):
        """Store message in database."""
        message = Message(
            user_id=user.id,
            content=text,
            telegram_message_id=telegram_message_id,
        )
        self.session.add(message)
        await self.session.commit()
        logger.debug(f"Stored message from user {user.username}: {text}")
