"""User authentication and registration endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db_session
from app.models import User
from app.schemas import (
    UserRegisterRequest,
    UserResponse,
    RoleAssignmentRequest,
    RoleAssignmentResponse,
    RoleEnum,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    request: UserRegisterRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Register a new user with pending role approval.

    User will be created with role=None until Division Manager approves.
    """
    # Check if user already exists by telegram_id
    stmt = select(User).where(User.telegram_id == request.telegram_id)
    existing_user = await session.scalar(stmt)

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User with telegram_id {request.telegram_id} already registered",
        )

    # Create new user with no role (pending approval)
    new_user = User(
        telegram_id=request.telegram_id,
        username=request.username,
        email=request.email,
        role=None,
    )
    session.add(new_user)
    await session.commit()
    await session.refresh(new_user)

    return UserResponse(
        id=new_user.id,
        telegram_id=new_user.telegram_id,
        username=new_user.username,
        email=new_user.email,
        role=new_user.role,
        created_at=new_user.created_at.isoformat(),
        updated_at=new_user.updated_at.isoformat(),
    )


@router.post("/approve-role", response_model=RoleAssignmentResponse)
async def approve_user_role(
    request: RoleAssignmentRequest,
    session: AsyncSession = Depends(get_db_session),
):
    """
    Assign a role to a user (admin endpoint).

    Only Division Manager should call this endpoint.
    """
    # Fetch user by id
    stmt = select(User).where(User.id == request.user_id)
    user = await session.scalar(stmt)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {request.user_id} not found",
        )

    # Assign role
    user.role = request.role
    await session.commit()
    await session.refresh(user)

    return RoleAssignmentResponse(
        user_id=user.id,
        role=user.role,
        message=f"Role {request.role} assigned to user {user.username}",
    )


@router.get("/users/{telegram_id}", response_model=UserResponse)
async def get_user_by_telegram_id(
    telegram_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Fetch user profile by telegram_id."""
    stmt = select(User).where(User.telegram_id == telegram_id)
    user = await session.scalar(stmt)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with telegram_id {telegram_id} not found",
        )

    return UserResponse(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        email=user.email,
        role=user.role,
        created_at=user.created_at.isoformat(),
        updated_at=user.updated_at.isoformat(),
    )
