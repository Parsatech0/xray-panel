"""Shared helpers for routers: CSRF tokens, URL building, flash messages."""

from typing import Optional

from fastapi import Request

import app.security as security
from app.config import settings


def panel_url(path: str) -> str:
    """Join a route path with the configured PANEL_PATH.

        panel_url("/login")  + PANEL_PATH="/"    -> "/login"
        panel_url("/login")  + PANEL_PATH="/panel" -> "/panel/login"
    """
    return settings.PANEL_PATH.rstrip("/") + path


def csrf_from_session(request: Request) -> str:
    """Return the session's CSRF token, creating and storing one if absent.

    The token shown in a form MUST match the session copy for POST checks
    to pass. Call this when rendering any form.
    """
    token = request.session.get("csrf_token")
    if not token:
        token = security.generate_csrf_token()
        request.session["csrf_token"] = token
    return token


def check_csrf(request: Request, provided: Optional[str]) -> bool:
    """Constant-time compare of the submitted CSRF token with the session's."""
    session_token = request.session.get("csrf_token")
    return security.constant_time_compare(session_token, provided)


def flash(request: Request, kind: str, message: str) -> None:
    """Store a one-shot message in the session ('success' | 'error')."""
    request.session["flash"] = {"kind": kind, "message": message}


def pop_flash(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Pop the stored flash message; returns (kind, message) or (None, None)."""
    data = request.session.pop("flash", None) or {}
    return data.get("kind"), data.get("message")
