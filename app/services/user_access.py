"""Who may write in Shan-AI — one rule for every create button and endpoint.

A viewer-role user is read-only: missions (the war room), decisions (web and
Telegram alike). Lessons have their own rule in `lessons_access.Identity`.
"""
from app.models import RoleEnum


def can_write(user) -> bool:
    return user.role != RoleEnum.VIEWER
