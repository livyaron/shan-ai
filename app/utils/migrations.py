"""Migrations for database schema and data."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import User
from app.utils.auth import get_default_password_hash, is_default_password

async def migrate_user_passwords(session: AsyncSession):
    """Ensure all users have a password hash. Set default password (1234) for users without one."""
    result = await session.execute(select(User))
    users = result.scalars().all()

    default_hash = get_default_password_hash()
    updated = 0

    for user in users:
        if not user.password_hash or user.password_hash == "":
            user.password_hash = default_hash
            user.password_is_default = True
            updated += 1

    if updated > 0:
        await session.commit()
        print(f"✅ Migrated {updated} users with default password hash")

    return updated


async def backfill_password_is_default(session: AsyncSession) -> int:
    """Answer "עדיין 1234?" once per user and cache it on the row.

    One bcrypt verify costs ~0.25s, so asking it for every user on every page
    load is not an option — but asking it once, in the background, at startup
    is. Rows written after this (create / edit / reset) set the flag themselves,
    so this only ever sees users that predate the column.
    """
    rows = (await session.execute(
        select(User).where(User.password_is_default.is_(None))
    )).scalars().all()
    if not rows:
        return 0

    for i, user in enumerate(rows, 1):
        # bcrypt burns CPU; a thread keeps the event loop answering requests.
        user.password_is_default = await asyncio.to_thread(
            is_default_password, user.password_hash
        )
        # ~0.25s per user, so commit as we go rather than holding one
        # transaction open for minutes on a large user table.
        if i % 25 == 0:
            await session.commit()

    await session.commit()
    print(f"✅ Password default-flag backfilled for {len(rows)} users")
    return len(rows)
