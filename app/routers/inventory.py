"""Read-only project inventory for a later bot.

GET /api/inventory is JSON only. It does not replace the dashboard session
login, and it does not write anything. The route stays closed (503) until
INVENTORY_API_KEY is set.
"""

import hmac
import os
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db_session
from app.models import KnowledgeFile, Project

router = APIRouter(prefix="/api", tags=["inventory"])


def _configured_key() -> str:
    """The key the bearer token is checked against.

    The process environment wins, so a value set at runtime (Railway, tests)
    is visible. Settings covers a local .env that was not exported. Empty or
    whitespace means the endpoint is not configured.
    """
    if "INVENTORY_API_KEY" in os.environ:
        raw = os.environ.get("INVENTORY_API_KEY") or ""
    else:
        raw = settings.INVENTORY_API_KEY or ""
    return raw.strip()


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, rest = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = rest.strip()
    return token or None


def _require_inventory_key(authorization: str | None) -> None:
    expected = _configured_key()
    if not expected:
        # Do not leave the route open when the key was never set.
        raise HTTPException(status_code=503, detail="Inventory API is not configured")
    token = _bearer_token(authorization)
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat() + "Z"
        return value.isoformat()
    return value.isoformat()


def _project_row(project: Project) -> dict:
    row = {
        "project_identifier": project.project_identifier,
        "name": project.name,
        "manager": project.manager,
        "stage": project.stage,
        "last_updated": _iso(project.last_updated),
    }
    # Only a real column. Do not derive a status from stage.
    if "status" in Project.__table__.columns:
        row["status"] = project.status
    return row


@router.get("/inventory")
async def inventory(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    """Active projects plus the latest master upload. Read-only."""
    _require_inventory_key(authorization)

    result = await session.execute(
        select(Project).where(Project.is_active).order_by(Project.name)
    )
    projects = result.scalars().all()

    master_result = await session.execute(
        select(KnowledgeFile)
        .where(KnowledgeFile.is_master)
        .order_by(KnowledgeFile.created_at.desc())
        .limit(1)
    )
    master = master_result.scalars().first()

    return {
        "master": {
            "uploaded_at": _iso(master.created_at) if master else None,
            "filename": master.original_name if master else None,
        },
        "projects": [_project_row(p) for p in projects],
    }
