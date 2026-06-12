"""Session and authentication management."""

import os
import secrets
from datetime import datetime, timedelta
from jose import JWTError, jwt

# Must be set in the environment (.env locally, service vars on Railway).
# Fallback is a random per-process key: app still works, but sessions
# reset on every restart until JWT_SECRET_KEY is configured.
SECRET_KEY = os.getenv("JWT_SECRET_KEY") or secrets.token_hex(32)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

def create_access_token(user_id: int, username: str) -> str:
    """Create a JWT token for a user."""
    to_encode = {
        "sub": str(user_id),
        "username": username,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    }
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> dict | None:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        username = payload.get("username")
        if user_id is None:
            return None
        return {"user_id": int(user_id), "username": username}
    except JWTError:
        return None
