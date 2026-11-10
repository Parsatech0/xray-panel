"""Traffic monitoring routes (Phase 4).

`POST /system/monitor` forces an immediate collection + auto-disable pass.
CSRF-protected and admin-only; redirects back to a sanitized `return_to`
target (whitelisted prefixes) instead of open-redirecting.
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.routers.helpers import check_csrf, flash, panel_url

router = APIRouter()


def _safe_return_to(return_to: str) -> str:
    """Allow only internal in-panel redirect targets (no open redirect)."""
    if return_to and return_to.startswith(("/inbounds", "/dashboard", "/system")):
        return return_to
    return "/inbounds"


@router.post("/system/monitor")
def system_monitor(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
    csrf_token: str = Form(None),
    return_to: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        flash(request, "error", "خطای CSRF — درخواست غیرمجاز است.")
        return RedirectResponse(url=panel_url("/inbounds"), status_code=status.HTTP_302_FOUND)

    import app.monitor as monitor

    try:
        updated, disabled = monitor.refresh_stats_and_auto_disable(db)
    except Exception:  # gRPC/socket/HTTP errors from the loopback API call
        flash(request, "error", "دریافت آمار ترافیک از Xray ناموفق بود — Xray در حال اجراست؟")
        return RedirectResponse(url=panel_url(_safe_return_to(return_to)), status_code=status.HTTP_302_FOUND)

    flash(
        request,
        "success",
        f"آمار به‌روز شد: {updated} کاربر ثبت شد و {disabled} کاربر به‌دلیل محدودیت غیرفعال شدند.",
    )
    return RedirectResponse(url=panel_url(_safe_return_to(return_to)), status_code=status.HTTP_302_FOUND)
