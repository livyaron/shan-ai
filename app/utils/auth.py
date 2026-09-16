"""Password hashing and verification utilities.

bcrypt is one-way on purpose: nothing here can turn a stored hash back into
the password that made it. "הצג סיסמה קיימת" is therefore not implementable —
what an admin gets instead is `generate_password` (reset, show once) and
`is_default_password` (who never changed the password we handed them).
"""

import secrets

import bcrypt

# The password every new user is created with. The ONLY place it is spelled.
DEFAULT_PASSWORD = "1234"

# Unambiguous alphabet for generated passwords: no 0/O, no 1/l/I — these get
# read out loud over the phone and typed by someone else.
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False

def get_default_password_hash() -> str:
    """Get the default password hash for DEFAULT_PASSWORD."""
    return hash_password(DEFAULT_PASSWORD)

def is_default_password(hashed_password: str) -> bool:
    """Is this hash still the password we handed the user on day one?

    bcrypt cannot reveal a password, but it can answer a yes/no question about
    one we already know. This is the whole of what the 🔓 badge knows.
    """
    if not hashed_password:
        return False
    return verify_password(DEFAULT_PASSWORD, hashed_password)

def generate_password(length: int = 10) -> str:
    """A readable one-off password for an admin-initiated reset.

    Shown to the admin exactly once and never stored in clear text — only its
    bcrypt hash is written, same as any other password.
    """
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    # A dash in the middle so it survives being dictated over the phone.
    mid = length // 2
    return f"{raw[:mid]}-{raw[mid:]}"
