"""Dashboard + Xray service control (Phase 5).

GET  /dashboard            -> live stats cards (system, traffic, xray status)
POST /system/xray/{action} -> systemctl start/stop/restart (CSRF, admin)

System metrics come from app.system_stats (psutil); service control from
app.xray_control. Aggregates are plain SQLAlchemy count queries.
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

import app.models as models
import app.system_stats as system_stats
import app.xray_control as xray_control
from app.database import get_db
from app.dependencies import get_current_admin
from app.routers.helpers import check_csrf, csrf_from_session, flash, panel_url, pop_flash

router = APIRouter()

templates = Jinja2Templates(directory="templates")

#: Persian labels for Xray state codes (display only).
_XRAY_STATE_LABELS = {
    "active": ("فعال", "status-active"),
    "stopped": ("متوقف", "status-stopped"),
    "unavailable": ("غیرقابل دسترسی", "status-unknown"),
    "unknown": ("نامشخص", "status-unknown"),
}

_GIB = 1024 ** 3


def _dashboard_context(
    request: Request, admin, db: Session, stats: dict | None = None
) -> dict:
    """Shared GET/POST-render context for the dashboard template."""
    flash_kind, flash_message = pop_flash(request)

    inbounds_total = db.query(models.Inbound).count()
    inbounds_active = db.query(models.Inbound).filter(models.Inbound.enable == True).count()  # noqa: E712

    clients_total = db.query(models.Client).count()
    clients_active = db.query(models.Client).filter(models.Client.enable == True).count()  # noqa: E712
    clients_disabled_over_traffic = (
        db.query(models.Client)
        .filter(models.Client.disabled_reason == "traffic_limit")
        .count()
    )
    clients_disabled_expired = (
        db.query(models.Client)
        .filter(models.Client.disabled_reason == "expired")
        .count()
    )

    used_bytes = (
        db.query(
            func.coalesce(
                func.sum(models.Client.used_download_bytes)
                + func.sum(models.Client.used_upload_bytes),
                0,
            )
        ).scalar()
        or 0
    )

    state_code, state_detail = xray_control.xray_status()
    state_label, state_class = _XRAY_STATE_LABELS.get(
        state_code, ("نامشخص", "status-unknown")
    )

    if stats is None:
        stats = system_stats.get_system_stats()

    return {
        "request": request,
        "admin": admin,
        "flash_kind": flash_kind,
        "flash_message": flash_message,
        "csrf_token": csrf_from_session(request),
        "counts": {
            "inbounds_total": inbounds_total,
            "inbounds_active": inbounds_active,
            "clients_total": clients_total,
            "clients_active": clients_active,
            "clients_disabled_traffic": clients_disabled_over_traffic,
            "clients_disabled_expired": clients_disabled_expired,
        },
        "used_total_gb": round(used_bytes / _GIB, 2),
        "xray": {
            "state_code": state_code,
            "state_label": state_label,
            "state_class": state_class,
            "state_detail": state_detail,
            "service_name": xray_control.settings.XRAY_SERVICE_NAME,
        },
        "system_stats": stats,
        "allowed_actions": xray_control.ALLOWED_ACTIONS,
    }


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Live dashboard: system stats, traffic aggregates, xray status."""
    return templates.TemplateResponse(
        "dashboard.html", _dashboard_context(request, admin, db)
    )


@router.post("/system/xray/{action}")
def xray_action(
    request: Request,
    action: str,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
    csrf_token: str = Form(None),
):
    """Change the xray systemd unit state (start/stop/restart)."""
    url = panel_url("/dashboard")

    if not check_csrf(request, csrf_token):
        flash(request, "error", "خطای CSRF — درخواست غیرمجاز است.")
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    if action not in xray_control.ALLOWED_ACTIONS:
        flash(request, "error", "عملیات نامعتبر است.")
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    ok, detail = xray_control.run_action(action)
    if ok:
        flash(request, "success", f"دستور {action} روی سرویس Xray اجرا شد.")
    elif detail == "invalid action":
        flash(request, "error", "عملیات نامعتبر است.")
    else:
        flash(request, "error", f"خطا در اجرای {action}: {detail}")

    return templates.TemplateResponse(
        "dashboard.html", _dashboard_context(request, admin, db)
    )
