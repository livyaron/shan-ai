"""Migrations for database schema and data."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import User
from app.utils.auth import get_default_password_hash

async def migrate_user_passwords(session: AsyncSession):
    """Ensure all users have a password hash. Set default password (1234) for users without one."""
    result = await session.execute(select(User))
    users = result.scalars().all()

    default_hash = get_default_password_hash()
    updated = 0

    for user in users:
        if not user.password_hash or user.password_hash == "":
            user.password_hash = default_hash
            updated += 1

    if updated > 0:
        await session.commit()
        print(f"✅ Migrated {updated} users with default password hash")

    return updated
