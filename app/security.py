"""Security utilities: password hashing, JWT tokens, CSRF protection."""

import secrets
import time

import bcrypt
from jose import JWTError, jwt

from app.config import settings


# Token lifetime: 24 hours
ACCESS_TOKEN_EXPIRE_SECONDS = 24 * 60 * 60
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    encoded = password.encode("utf-8")
    hashed = bcrypt.hashpw(encoded, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def create_access_token(subject: str | int) -> str:
    """Create a signed JWT access token for the given subject (admin id)."""
    now = int(time.time())
    to_encode = {
        "sub": str(subject),
        "iat": now,
        "exp": now + ACCESS_TOKEN_EXPIRE_SECONDS,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Decode and validate a JWT access token. Returns the subject or None."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def generate_csrf_token() -> str:
    """Generate a random CSRF token."""
    return secrets.token_urlsafe(32)


def generate_api_token() -> str:
    """Generate an unguessable URL-safe token (subscription URLs, etc.)."""
    return secrets.token_urlsafe(16)


def constant_time_compare(a: str | None, b: str | None) -> bool:
    """Constant-time string comparison to avoid timing attacks.

    Non-string or missing values never compare equal, so a missing CSRF
    token simply fails the check instead of raising.
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return secrets.compare_digest(a, b)
