"""Client management routes (Phase 3): list / create / edit / delete /
toggle / reset-traffic under a single inbound. Includes share links and
QR codes rendered in the list page.

All state-changing POSTs require a CSRF token; all routes require login.
"""

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import app.validators as validators
from app.database import get_db
from app.dependencies import get_current_admin
from app.routers import subscription as subscription_router
from app.routers.helpers import check_csrf, csrf_from_session, flash, panel_url, pop_flash
from app.share_links import build_inbound_ss_link, build_share_link, make_qr_svg_data_uri

router = APIRouter()

templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _host_for_links(request: Request) -> str:
    """Public-facing host for share links (falls back to the listen IP)."""
    return request.headers.get("host", "server.example.com")


def _client_defaults(client=None) -> dict:
    s = client if client else None
    return {
        "email": s.email if s else "",
        "uuid": s.uuid if s else "",
        "flow": (s.flow if s else "") or "",
        "total_gb": (s.total_gb if s else 0),
        "expire_at": (s.expire_at.isoformat() if s and s.expire_at else ""),
        "ip_limit": (s.ip_limit if s else 0),
        "enable": bool(s.enable) if s else True,
    }


def _form_to_fields(**kwargs) -> dict:
    """Collect raw form kwargs into the dict shape validators expect."""
    return {
        "email": kwargs.get("email", ""),
        "uuid": kwargs.get("uuid", ""),
        "flow": kwargs.get("flow", ""),
        "total_gb": kwargs.get("total_gb", ""),
        "ip_limit": kwargs.get("ip_limit", ""),
        "expire_at": kwargs.get("expire_at", ""),
        "enable": kwargs.get("enable", ""),
    }


def _client_error_redirect(request: Request, inbound_id: int, message: str) -> RedirectResponse:
    flash(request, "error", message)
    return RedirectResponse(
        url=panel_url(f"/inbounds/{inbound_id}/clients"), status_code=status.HTTP_302_FOUND
    )


def _context(request: Request, admin, inbound, extra: dict) -> dict:
    flash_kind, flash_message = pop_flash(request)
    host = _host_for_links(request)
    ctx = {
        "request": request,
        "admin": admin,
        "inbound": inbound,
        "flash_kind": flash_kind,
        "flash_message": flash_message,
        "csrf_token": csrf_from_session(request),
        "ss_link": build_inbound_ss_link(inbound, host),
        "supported_protocols": validators.SUPPORTED_PROTOCOLS,
        "vless_flows": ["", "xtls-rprx-vision"],
    }
    ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------------
# List (with share links + QR)
# ---------------------------------------------------------------------------

@router.get("/inbounds/{inbound_id}/clients", response_class=HTMLResponse)
def clients_list(
    request: Request,
    inbound_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    import app.crud as crud

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _client_error_redirect(request, inbound_id, "اینباند یافت نشد.")

    host = _host_for_links(request)
    rows = []
    for client in crud.list_clients(db, inbound_id):
        link = build_share_link(inbound, client, host)
        sub_url, sub_qr = None, None
        if link:  # subscription URLs only make sense with a shareable config
            sub_url = subscription_router.subscription_url(request, db, client)
            sub_qr = make_qr_svg_data_uri(sub_url)
        rows.append({
            "client": client,
            "link": link,
            "qr": make_qr_svg_data_uri(link) if link else None,
            "sub_url": sub_url,
            "sub_qr": sub_qr,
        })

    return templates.TemplateResponse(
        "clients/list.html",
        _context(request, admin, inbound, {"rows": rows}),
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@router.get("/inbounds/{inbound_id}/clients/new", response_class=HTMLResponse)
def client_form_new(
    request: Request,
    inbound_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    import app.crud as crud

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _client_error_redirect(request, inbound_id, "اینباند یافت نشد.")
    return templates.TemplateResponse(
        "clients/form.html",
        _context(request, admin, inbound, {
            "mode": "new", "client": None, "fields": _client_defaults(None), "errors": [],
        }),
    )


@router.post("/inbounds/{inbound_id}/clients")
def client_create(
    request: Request,
    inbound_id: int,
    db: Session = Depends(get_db),
    email: str = Form(""),
    uuid: str = Form(""),
    flow: str = Form(""),
    total_gb: str = Form("0"),
    ip_limit: str = Form("0"),
    expire_at: str = Form(""),
    enable: str = Form(""),
    csrf_token: str = Form(None),
):
    return _client_write(
        request, db, inbound_id, None,
        email=email, uuid=uuid, flow=flow, total_gb=total_gb,
        ip_limit=ip_limit, expire_at=expire_at, enable=enable, csrf_token=csrf_token,
    )


# ---------------------------------------------------------------------------
# Edit / update
# ---------------------------------------------------------------------------

@router.get("/inbounds/{inbound_id}/clients/{client_id}/edit", response_class=HTMLResponse)
def client_form_edit(
    request: Request,
    inbound_id: int,
    client_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    import app.crud as crud

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _client_error_redirect(request, inbound_id, "اینباند یافت نشد.")
    client = crud.get_client(db, inbound_id, client_id)
    if not client:
        return _client_error_redirect(request, inbound_id, "کاربر یافت نشد.")
    return templates.TemplateResponse(
        "clients/form.html",
        _context(request, admin, inbound, {
            "mode": "edit", "client": client, "fields": _client_defaults(client), "errors": [],
        }),
    )


@router.post("/inbounds/{inbound_id}/clients/{client_id}")
def client_update(
    request: Request,
    inbound_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    email: str = Form(""),
    uuid: str = Form(""),
    flow: str = Form(""),
    total_gb: str = Form("0"),
    ip_limit: str = Form("0"),
    expire_at: str = Form(""),
    enable: str = Form(""),
    csrf_token: str = Form(None),
):
    return _client_write(
        request, db, inbound_id, client_id,
        email=email, uuid=uuid, flow=flow, total_gb=total_gb,
        ip_limit=ip_limit, expire_at=expire_at, enable=enable, csrf_token=csrf_token,
    )


def _client_write(request: Request, db: Session, inbound_id: int, client_id, **kwargs):
    """Shared create/update logic (kwargs are raw form fields)."""
    import app.crud as crud

    if not check_csrf(request, kwargs.get("csrf_token")):
        return _client_error_redirect(request, inbound_id, "خطای CSRF — درخواست غیرمجاز است.")

    inbound = crud.get_inbound(db, inbound_id)
    if not inbound:
        return _client_error_redirect(request, inbound_id, "اینباند یافت نشد.")

    form = _form_to_fields(**kwargs)
    clean, codes = validators.validate_client(form, inbound.protocol)

    # Unique email panel-wide (only when the rest of the form is sane).
    # Xray stats counters key on email alone, so duplicates across inbounds
    # would mix two users' traffic into one counter.
    if clean and "ERR_EMAIL" not in codes:
        existing = crud.get_client_by_email(db, clean["email"])
        dup_here = existing and (client_id is None or existing.id != client_id)
        if dup_here:
            codes.append("ERR_EMAIL_DUP")

    if codes:
        errors = [validators.error_message(c) for c in codes]
        client = crud.get_client(db, inbound_id, client_id) if client_id else None
        fields = _client_defaults(client)
        fields.update({k: v for k, v in form.items() if k in fields})
        if fields.get("expire_at") and hasattr(fields["expire_at"], "isoformat"):
            fields["expire_at"] = fields["expire_at"].isoformat()
        return templates.TemplateResponse(
            "clients/form.html",
            _context(request, None, inbound, {
                "mode": "edit" if client else "new",
                "client": client, "fields": fields, "errors": errors,
            }),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    clean["enable"] = bool(kwargs.get("enable"))
    if kwargs.get("total_gb") in (None, ""):
        clean["total_gb"] = 0.0

    if client_id is None:
        crud.create_client(db, inbound, clean)
        flash(request, "success", "کاربر جدید ایجاد شد. برای اعمال تغییرات، «اعمال کانفیگ» را در صفحه اینباند‌ها بزنید.")
    else:
        client = crud.get_client(db, inbound_id, client_id)
        if not client:
            return _client_error_redirect(request, inbound_id, "کاربر یافت نشد.")
        crud.update_client(db, client, clean)
        flash(request, "success", "کاربر ویرایش شد. برای اعمال تغییرات، «اعمال کانفیگ» را در صفحه اینباند‌ها بزنید.")

    return RedirectResponse(
        url=panel_url(f"/inbounds/{inbound_id}/clients"), status_code=status.HTTP_302_FOUND
    )


# ---------------------------------------------------------------------------
# Toggle / reset traffic / delete
# ---------------------------------------------------------------------------

@router.post("/inbounds/{inbound_id}/clients/{client_id}/toggle")
def client_toggle(
    request: Request,
    inbound_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    csrf_token: str = Form(None),
):
    import app.crud as crud

    if not check_csrf(request, csrf_token):
        return _client_error_redirect(request, inbound_id, "خطای CSRF — درخواست غیرمجاز است.")
    client = crud.get_client(db, inbound_id, client_id)
    if not client:
        return _client_error_redirect(request, inbound_id, "کاربر یافت نشد.")
    crud.toggle_client(db, client)
    state = "فعال" if client.enable else "غیرفعال"
    flash(request, "success", f"کاربر «{client.email}» {state} شد.")
    return RedirectResponse(
        url=panel_url(f"/inbounds/{inbound_id}/clients"), status_code=status.HTTP_302_FOUND
    )


@router.post("/inbounds/{inbound_id}/clients/{client_id}/reset-traffic")
def client_reset_traffic(
    request: Request,
    inbound_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    csrf_token: str = Form(None),
):
    import app.crud as crud

    if not check_csrf(request, csrf_token):
        return _client_error_redirect(request, inbound_id, "خطای CSRF — درخواست غیرمجاز است.")
    client = crud.get_client(db, inbound_id, client_id)
    if not client:
        return _client_error_redirect(request, inbound_id, "کاربر یافت نشد.")
    crud.reset_client_traffic(db, client)
    flash(request, "success", f"ترافیک کاربر «{client.email}» صفر شد.")
    return RedirectResponse(
        url=panel_url(f"/inbounds/{inbound_id}/clients"), status_code=status.HTTP_302_FOUND
    )


@router.post("/inbounds/{inbound_id}/clients/{client_id}/delete")
def client_delete(
    request: Request,
    inbound_id: int,
    client_id: int,
    db: Session = Depends(get_db),
    csrf_token: str = Form(None),
):
    import app.crud as crud

    if not check_csrf(request, csrf_token):
        return _client_error_redirect(request, inbound_id, "خطای CSRF — درخواست غیرمجاز است.")
    client = crud.get_client(db, inbound_id, client_id)
    if not client:
        return _client_error_redirect(request, inbound_id, "کاربر یافت نشد.")
    crud.delete_client(db, client)
    flash(request, "success", f"کاربر «{client.email}» حذف شد.")
    return RedirectResponse(
        url=panel_url(f"/inbounds/{inbound_id}/clients"), status_code=status.HTTP_302_FOUND
    )
