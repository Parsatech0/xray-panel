"""Inbound management routes (Phase 2): list / create / edit / delete /
toggle / apply-config. All state-changing POSTs are CSRF-protected and
require an authenticated admin.
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import app.validators as validators
import app.xray_gen as xray_gen
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_admin
from app.routers.helpers import check_csrf, csrf_from_session, flash, panel_url, pop_flash

router = APIRouter()

templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_redirect(request: Request, message: str) -> RedirectResponse:
    flash(request, "error", message)
    return RedirectResponse(url=panel_url("/inbounds"), status_code=status.HTTP_302_FOUND)


def _error_codes_to_form_errors(codes: list[str]) -> list[str]:
    """Map validation error codes to Persian UI messages."""
    return [validators.error_message(code) for code in codes]


def _form_defaults(inbound=None) -> dict:
    """Field defaults for the form template (from an existing row or empty)."""
    s = (inbound.settings if inbound else None) or {}
    return {
        "remark": inbound.remark if inbound else "",
        "listen": inbound.listen if inbound else "0.0.0.0",
        "port": str(inbound.port) if inbound else "",
        "protocol": inbound.protocol if inbound else "vless",
        "transport": inbound.transport if inbound else "tcp",
        "security": inbound.security if inbound else "none",
        "sniffing": bool(inbound.sniffing) if inbound else True,
        "enable": bool(inbound.enable) if inbound else True,
        # per-protocol / per-security settings
        "ss_method": s.get("ss_method", "aes-256-gcm"),
        "ss_password": s.get("ss_password", ""),
        "ws_path": s.get("ws_path", "/"),
        "grpc_service_name": s.get("grpc_service_name", ""),
        "reality_dest": s.get("reality_dest", ""),
        "reality_server_names": ", ".join(s.get("reality_server_names", [])),
        "reality_private_key": s.get("reality_private_key", ""),
        "reality_short_ids": ", ".join(s.get("reality_short_ids", [])),
        "tls_cert_file": s.get("tls_cert_file", ""),
        "tls_key_file": s.get("tls_key_file", ""),
    }


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

@router.get("/inbounds", response_class=HTMLResponse)
def inbounds_list(
    request: Request,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    import app.crud as crud

    rows = crud.list_inbounds(db)
    flash_kind, flash_message = pop_flash(request)
    return templates.TemplateResponse(
        "inbounds/list.html",
        {
            "request": request, "admin": admin,
            "inbounds": rows,
            "flash_kind": flash_kind, "flash_message": flash_message,
            "csrf_token": csrf_from_session(request),
            "config_path": str(settings.XRAY_CONFIG_PATH),
        },
    )


# ---------------------------------------------------------------------------
# Regenerate + validate + apply config.json
# (registered BEFORE /inbounds/{inbound_id} routes so the literal path
# "/apply" is never captured as an inbound_id)
# ---------------------------------------------------------------------------

@router.post("/inbounds/apply")
def inbounds_apply(
    request: Request,
    db: Session = Depends(get_db),
    csrf_token: str = Form(None),
):
    if not check_csrf(request, csrf_token):
        return _error_redirect(request, "خطای CSRF — درخواست غیرمجاز است.")

    import app.crud as crud

    active_rows = crud.list_active_inbounds(db)
    ok, detail = xray_gen.generate_and_apply(active_rows)
    if ok:
        flash(request, "success", "کانفیگ تولید و با موفقیت اعمال شد (توجه: برای اعمال کامل، ری‌استارت Xray لازم است — فاز ۵).")
    else:
        flash(request, "error", f"اعمال کانفیگ ناموفق بود: {detail}")
    return RedirectResponse(url=panel_url("/inbounds"), status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.get("/inbounds/new", response_class=HTMLResponse)
def inbound_form_new(request: Request, admin=Depends(get_current_admin)):
    return templates.TemplateResponse(
        "inbounds/form.html",
        {
            "request": request, "admin": admin,
            "mode": "new",
            "inbound": None,
            "fields": _form_defaults(None),
            "errors": [],
            "csrf_token": csrf_from_session(request),
        },
    )


@router.post("/inbounds")
def inbound_create(
    request: Request,
    db: Session = Depends(get_db),
    remark: str = Form(""),
    listen: str = Form(""),
    port: str = Form(""),
    protocol: str = Form(""),
    transport: str = Form(""),
    security: str = Form("none"),
    sniffing: str = Form(""),
    enable: str = Form(""),
    ss_method: str = Form(""),
    ss_password: str = Form(""),
    ws_path: str = Form(""),
    grpc_service_name: str = Form(""),
    reality_dest: str = Form(""),
    reality_server_names: str = Form(""),
    reality_private_key: str = Form(""),
    reality_short_ids: str = Form(""),
    tls_cert_file: str = Form(""),
    tls_key_file: str = Form(""),
    csrf_token: str = Form(None),
):
    if not check_csrf(request, csrf_token):
        return _error_redirect(request, "خطای CSRF — فرم دیگه معتبر نیست. صفحه رو دوباره باز کنید.")

    import app.crud as crud

    form = dict(
        remark=remark, listen=listen, port=port, protocol=protocol, transport=transport,
        security=security, sniffing=sniffing,
        ss_method=ss_method, ss_password=ss_password,
        ws_path=ws_path, grpc_service_name=grpc_service_name,
        reality_dest=reality_dest, reality_server_names=reality_server_names,
        reality_private_key=reality_private_key, reality_short_ids=reality_short_ids,
        tls_cert_file=tls_cert_file, tls_key_file=tls_key_file,
    )
    clean, codes = validators.validate_inbound(form)

    # Port uniqueness (checked in addition to the pure-form validation).
    if "ERR_PORT" not in codes and clean and crud.get_inbound_by_port(db, clean["port"]):
        codes.append("ERR_PORT_DUP")

    if codes:
        errors = _error_codes_to_form_errors(codes)
        if "ERR_PORT_DUP" in codes:
            errors.append("این پورت قبلاً برای اینباند دیگری ثبت شده است.")
        # Re-render the form with submitted values preserved.
        fields = _form_defaults(None)
        fields.update({k: v for k, v in form.items() if k in fields})
        return templates.TemplateResponse(
            "inbounds/form.html",
            {
                "request": request, "mode": "new", "inbound": None,
                "fields": fields, "errors": errors,
                "csrf_token": csrf_from_session(request), "admin": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    clean["enable"] = bool(enable)
    crud.create_inbound(db, clean)

    flash(request, "success", "اینباند جدید ایجاد شد. برای اعمال کانفیگ، دکمه «اعمال کانفیگ» را بزنید.")
    return RedirectResponse(url=panel_url("/inbounds"), status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# Edit / update
# ---------------------------------------------------------------------------

@router.get("/inbounds/{inbound_id}/edit", response_class=HTMLResponse)
def inbound_form_edit(
    request: Request,
    inbound_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    import app.crud as crud

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _error_redirect(request, "اینباند یافت نشد.")
    return templates.TemplateResponse(
        "inbounds/form.html",
        {
            "request": request, "admin": admin,
            "mode": "edit",
            "inbound": inbound,
            "fields": _form_defaults(inbound),
            "errors": [],
            "csrf_token": csrf_from_session(request),
        },
    )


@router.post("/inbounds/{inbound_id}")
def inbound_update(
    request: Request,
    inbound_id: int,
    db: Session = Depends(get_db),
    remark: str = Form(""),
    listen: str = Form(""),
    port: str = Form(""),
    protocol: str = Form(""),
    transport: str = Form(""),
    security: str = Form("none"),
    sniffing: str = Form(""),
    enable: str = Form(""),
    ss_method: str = Form(""),
    ss_password: str = Form(""),
    ws_path: str = Form(""),
    grpc_service_name: str = Form(""),
    reality_dest: str = Form(""),
    reality_server_names: str = Form(""),
    reality_private_key: str = Form(""),
    reality_short_ids: str = Form(""),
    tls_cert_file: str = Form(""),
    tls_key_file: str = Form(""),
    csrf_token: str = Form(None),
):
    if not check_csrf(request, csrf_token):
        return _error_redirect(request, "خطای CSRF — فرم دیگه معتبر نیست. صفحه رو دوباره باز کنید.")

    import app.crud as crud

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _error_redirect(request, "اینباند یافت نشد.")

    form = dict(
        remark=remark, listen=listen, port=port, protocol=protocol, transport=transport,
        security=security, sniffing=sniffing,
        ss_method=ss_method, ss_password=ss_password,
        ws_path=ws_path, grpc_service_name=grpc_service_name,
        reality_dest=reality_dest, reality_server_names=reality_server_names,
        reality_private_key=reality_private_key, reality_short_ids=reality_short_ids,
        tls_cert_file=tls_cert_file, tls_key_file=tls_key_file,
    )
    clean, codes = validators.validate_inbound(form)

    # Port uniqueness (excluding this inbound itself).
    if "ERR_PORT" not in codes and clean:
        existing = crud.get_inbound_by_port(db, clean["port"])
        if existing and existing.id != inbound.id:
            codes.append("ERR_PORT_DUP")

    if codes:
        errors = _error_codes_to_form_errors(codes)
        if "ERR_PORT_DUP" in codes:
            errors.append("این پورت قبلاً برای اینباند دیگری ثبت شده است.")
        fields = _form_defaults(inbound)
        fields.update({k: v for k, v in form.items() if k in fields})
        return templates.TemplateResponse(
            "inbounds/form.html",
            {
                "request": request, "mode": "edit", "inbound": inbound,
                "fields": fields, "errors": errors,
                "csrf_token": csrf_from_session(request), "admin": None,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    clean["enable"] = bool(enable)
    crud.update_inbound(db, inbound, clean)

    flash(request, "success", "اینباند ویرایش شد. برای اعمال تغییرات، دکمه «اعمال کانفیگ» را بزنید.")
    return RedirectResponse(url=panel_url("/inbounds"), status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# Toggle enable / disable
# ---------------------------------------------------------------------------

@router.post("/inbounds/{inbound_id}/toggle")
def inbound_toggle(
    request: Request,
    inbound_id: int,
    db: Session = Depends(get_db),
    csrf_token: str = Form(None),
):
    if not check_csrf(request, csrf_token):
        return _error_redirect(request, "خطای CSRF — درخواست غیرمجاز است.")

    import app.crud as crud

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _error_redirect(request, "اینباند یافت نشد.")

    crud.toggle_inbound(db, inbound)
    state = "فعال" if inbound.enable else "غیرفعال"
    flash(request, "success", f"اینباند «{inbound.remark}» {state} شد.")
    return RedirectResponse(url=panel_url("/inbounds"), status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@router.post("/inbounds/{inbound_id}/delete")
def inbound_delete(
    request: Request,
    inbound_id: int,
    db: Session = Depends(get_db),
    csrf_token: str = Form(None),
):
    if not check_csrf(request, csrf_token):
        return _error_redirect(request, "خطای CSRF — درخواست غیرمجاز است.")

    import app.crud as crud

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _error_redirect(request, "اینباند یافت نشد.")

    crud.delete_inbound(db, inbound)
    flash(request, "success", f"اینباند «{inbound.remark}» حذف شد. برای اعمال تغییرات، «اعمال کانفیگ» را بزنید.")
    return RedirectResponse(url=panel_url("/inbounds"), status_code=status.HTTP_302_FOUND)
