"""Pydantic schemas for request/response validation."""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from enum import Enum


class RoleEnum(str, Enum):
    """User role hierarchy."""
    PROJECT_MANAGER = "project_manager"
    DEPARTMENT_MANAGER = "department_manager"
    DEPUTY_DIVISION_MANAGER = "deputy_division_manager"
    DIVISION_MANAGER = "division_manager"


class UserRegisterRequest(BaseModel):
    """Request to register a new user."""
    username: str = Field(..., min_length=3, max_length=50)
    telegram_id: int = Field(..., gt=0)
    email: EmailStr


class UserResponse(BaseModel):
    """Response model for user data."""
    id: int
    telegram_id: int
    username: str
    email: str
    role: Optional[RoleEnum] = None
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class RoleAssignmentRequest(BaseModel):
    """Request to assign a role to a user."""
    user_id: int = Field(..., gt=0)
    role: RoleEnum


class RoleAssignmentResponse(BaseModel):
    """Response confirming role assignment."""
    user_id: int
    role: RoleEnum
    message: str

    class Config:
        from_attributes = True
