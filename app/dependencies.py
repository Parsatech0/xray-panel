"""FastAPI dependencies: database session, current admin, CSRF validation."""

import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt

from app.config import settings
from app.database import get_db
from app.models import Admin
from app.security import decode_access_token


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# Note: `get_db` is imported from app.database and used as the single session
# dependency. FastAPI caches dependency results per request, so the Admin
# object returned by get_current_admin and the route's `db` parameter share
# the same SQLAlchemy session.


# ---------------------------------------------------------------------------
# Current admin (JWT bearer token from Authorization header or cookie)
# ---------------------------------------------------------------------------

async def get_current_admin(request: Request, db=Depends(get_db)) -> Admin:
    """Return the authenticated Admin, or raise 401.

    The token is read from the `Authorization: Bearer <token>` header or, for
    browser clients, from the `access_token` cookie.
    """
    token = None
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
    else:
        token = request.cookies.get("access_token")

    # Some clients wrap cookie values in double quotes; strip them.
    if token:
        token = token.strip().strip('"').removeprefix("Bearer ")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        admin_id: int = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    admin = db.query(Admin).filter(Admin.id == admin_id, Admin.is_active == True).first()  # noqa: E712
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin not found or inactive",
        )
    return admin


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------

def verify_csrf_token(
    request: Request,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    """Validate a CSRF token for state-changing requests.

    The token is expected either in the `X-CSRF-Token` header (used by fetch/
    AJAX calls) or as a form field. The session must contain a matching token.
    """
    session_token = request.session.get("csrf_token")
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing from session",
        )

    provided_token = x_csrf_token
    if not provided_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing from request",
        )

    if not secrets.compare_digest(session_token, provided_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )
