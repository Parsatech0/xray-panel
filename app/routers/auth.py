"""Authentication routes: login, logout, change password."""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import app.crud as crud
import app.security as security
from app.database import get_db
from app.dependencies import get_current_admin
from app.models import Admin
from app.routers.helpers import check_csrf, csrf_from_session, panel_url
from app.schemas import AdminUpdate

router = APIRouter()

templates = Jinja2Templates(directory="templates")


# Make the CSRF token and current user available to all templates.
@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    """Render the login form."""
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "csrf_token": csrf_from_session(request)},
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    # CSRF token is optional here so a missing token yields a friendly 403
    # page instead of FastAPI's raw 422 validation error.
    csrf_token: str = Form(None),
    db: Session = Depends(get_db),
):
    """Authenticate an admin and set a session cookie + JWT cookie."""
    # CSRF check
    if not check_csrf(request, csrf_token):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "csrf_token": csrf_from_session(request),
                "error": "Invalid CSRF token. Please try again.",
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    admin = crud.get_admin_by_username(db, username)
    if not admin or not security.verify_password(password, admin.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "csrf_token": csrf_from_session(request),
                "error": "Invalid username or password.",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not admin.is_active:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "csrf_token": csrf_from_session(request),
                "error": "This account is disabled.",
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Regenerate CSRF token on login (session fixation protection)
    request.session["csrf_token"] = security.generate_csrf_token()
    request.session["admin_id"] = admin.id

    token = security.create_access_token(admin.id)

    response = RedirectResponse(url=panel_url("/dashboard"), status_code=status.HTTP_302_FOUND)
    # Set the JWT directly in an HttpOnly cookie. Note: no "Bearer " prefix —
    # the Bearer scheme only applies to the Authorization header; a plain
    # token value avoids cookie quoting issues. The browser sends it on
    # subsequent requests.
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,  # Set to True behind HTTPS in production
        samesite="lax",
        max_age=security.ACCESS_TOKEN_EXPIRE_SECONDS,
        path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request):
    """Clear the session and auth cookie."""
    request.session.clear()
    response = RedirectResponse(url=panel_url("/login"), status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key="access_token", path="/")
    return response


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, admin: Admin = Depends(get_current_admin)):
    """Render the change-password form."""
    return templates.TemplateResponse(
        "login.html",  # reuse a simple form; in phase 2+ we add a dedicated template
        {"request": request, "admin": admin, "csrf_token": csrf_from_session(request), "change_password": True},
    )


@router.post("/change-password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    # CSRF token is optional here so a missing token yields a friendly 403
    # page instead of FastAPI's raw 422 validation error.
    csrf_token: str = Form(None),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Change the authenticated admin's password."""
    # CSRF check
    if not check_csrf(request, csrf_token):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "admin": admin,
                "csrf_token": csrf_from_session(request),
                "change_password": True,
                "error": "Invalid CSRF token.",
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if not security.verify_password(current_password, admin.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "admin": admin,
                "csrf_token": csrf_from_session(request),
                "change_password": True,
                "error": "Current password is incorrect.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if new_password != confirm_password:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "admin": admin,
                "csrf_token": csrf_from_session(request),
                "change_password": True,
                "error": "New password and confirmation do not match.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    crud.update_admin(db, admin, AdminUpdate(password=new_password))

    # Rotate session token after password change
    request.session["csrf_token"] = security.generate_csrf_token()

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "admin": admin,
            "csrf_token": csrf_from_session(request),
            "change_password": True,
            "success": "Password changed successfully.",
        },
    )
