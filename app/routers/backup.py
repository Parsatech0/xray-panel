"""Backup / restore (Phase 6).

* GET  /backup/download — streams the whole panel state (inbounds + clients,
  NOT admin passwords) as a JSON attachment (CSRF-protected via form POST? we
  use GET with CSRF query impossible...) — implemented as POST returning the
  file so browsers cannot be induced to leak data through plain links.
* POST /backup/restore  — uploads a backup JSON, validates it, then replaces
  all inbounds/clients with the file's contents (admin rows untouched to
  avoid lockout).

Full admin session + CSRF required for both.
"""

import datetime
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

import app.models as models
from app.database import get_db
from app.dependencies import get_current_admin
from app.routers.helpers import check_csrf, flash, panel_url

router = APIRouter()

#: Current backup schema version.
BACKUP_VERSION = 1

MAX_UPLOAD_BYTES = 4 * 1024 * 1024  # 4 MiB is plenty for a panel backup


def export_backup(db: Session) -> str:
    """Dump inbounds + clients as pretty JSON text."""
    payload = {
        "version": BACKUP_VERSION,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "inbounds": [],
    }
    for inbound in db.query(models.Inbound).order_by(models.Inbound.id).all():
        payload["inbounds"].append({
            "id": inbound.id,
            "remark": inbound.remark,
            "listen": inbound.listen,
            "port": inbound.port,
            "protocol": inbound.protocol,
            "transport": inbound.transport,
            "security": inbound.security,
            "settings": inbound.settings,
            "sniffing": inbound.sniffing,
            "enable": inbound.enable,
            "clients": [
                {
                    "id": c.id,
                    "email": c.email,
                    "uuid": c.uuid,
                    "flow": c.flow,
                    "total_gb": c.total_gb,
                    "expire_at": c.expire_at.isoformat() if c.expire_at else None,
                    "ip_limit": c.ip_limit,
                    "enable": c.enable,
                    "disabled_reason": c.disabled_reason,
                    "used_download_bytes": c.used_download_bytes,
                    "used_upload_bytes": c.used_upload_bytes,
                }
                for c in inbound.clients
            ],
        })
    return json.dumps(payload, indent=2, ensure_ascii=False)


class BackupValidationError(ValueError):
    """Raised when the uploaded backup file fails structural validation."""


def validate_backup_payload(data: dict) -> list[dict]:
    """Validate the decoded backup; returns list of inbound dicts.

    Raises BackupValidationError on bad structure/types.
    """
    if not isinstance(data, dict):
        raise BackupValidationError("فرمت فایل پشتیبان نامعتبر است.")
    if "inbounds" not in data or not isinstance(data["inbounds"], list):
        raise BackupValidationError("فایل پشتیبان فهرست اینباند‌ها ندارد.")

    seen_emails: set[str] = set()

    for inbound in data["inbounds"]:
        if not isinstance(inbound, dict):
            raise BackupValidationError("ساختار اینباند نامعتبر است.")
        for key in ("remark", "port", "protocol", "transport"):
            value = inbound.get(key)
            if not isinstance(value, (str, int)) or value in ("", None):
                raise BackupValidationError(f"فیلد الزامی {key!r} در اینباند گم شده است.")

        port_raw = inbound.get("port")
        if not str(port_raw).isdigit() or not (1 <= int(port_raw) <= 65535):
            raise BackupValidationError("شماره پورت اینباند نامعتبر است.")
        for key in ("settings", "clients"):
            if not isinstance(inbound.get(key), (dict, list)):
                raise BackupValidationError(f"فیلد {key!r} باید {type(key).__name__} باشد.")

        for client in inbound.get("clients") or []:
            if not isinstance(client, dict):
                raise BackupValidationError("ساختار کاربر نامعتبر است.")
            for key in ("email", "uuid"):
                if not isinstance(client.get(key), str) or not client[key]:
                    raise BackupValidationError(f"فیلد الزامی {key!r} در کاربر گم شده است.")
            # Xray stats keys are email-only: keep emails unique panel-wide.
            if client["email"] in seen_emails:
                raise BackupValidationError(
                    f"ایمیل تکراری در فایل پشتیبان: {client['email']}"
                )
            seen_emails.add(client["email"])
    return data["inbounds"]


def apply_backup(db: Session, inbounds: list[dict]) -> tuple[int, int]:
    """Replace all inbounds/clients with the validated content.

    NOTE: destructive — wipes existing inbounds (clients cascade). Admin
    accounts are never touched, so the operator cannot be locked out.
    Returns (inbound_count, client_count).
    """
    for client in db.query(models.Client).all():
        db.delete(client)
    for inbound in db.query(models.Inbound).all():
        db.delete(inbound)
    db.flush()

    new_inbounds = new_clients = 0
    for data in inbounds:
        created = models.Inbound(
            remark=str(data["remark"]),
            listen=str(data.get("listen") or "0.0.0.0"),
            port=int(data["port"]),
            protocol=str(data["protocol"]),
            transport=str(data["transport"] or "tcp"),
            security=str(data.get("security") or "none"),
            settings=data.get("settings") or {},
            sniffing=bool(data.get("sniffing", True)),
            enable=bool(data.get("enable", True)),
        )
        db.add(created)
        db.flush()  # need created.id for the clients below
        new_inbounds += 1

        for fields in data.get("clients") or []:
            client_email = str(fields["email"])
            db.add(models.Client(
                inbound_id=created.id,
                email=client_email,
                uuid=str(fields["uuid"]),
                flow=str(fields.get("flow") or ""),
                total_gb=float(fields.get("total_gb") or 0),
                expire_at=_parse_date(fields.get("expire_at")),
                ip_limit=int(fields.get("ip_limit") or 0),
                enable=bool(fields.get("enable", True)),
                disabled_reason=str(fields.get("disabled_reason") or ""),
                used_download_bytes=int(fields.get("used_download_bytes") or 0),
                used_upload_bytes=int(fields.get("used_upload_bytes") or 0),
            ))
            new_clients += 1
    db.commit()
    return new_inbounds, new_clients


def _parse_date(value) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        return None


@router.post("/backup/download")
def backup_download(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
    csrf_token: str = Form(None),
):
    if not check_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
    content = export_backup(db)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    from fastapi.responses import Response

    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="xray-panel-backup-{stamp}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/backup/restore")
def backup_restore(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
    csrf_token: str = Form(None),
    backup_file: UploadFile | None = None,
):
    url = panel_url("/dashboard")

    if not check_csrf(request, csrf_token):
        flash(request, "error", "خطای CSRF — درخواست غیرمجاز است.")
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    if backup_file is None:
        flash(request, "error", "فایل پشتیبان انتخاب نشده است.")
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    raw = backup_file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        flash(request, "error", "حجم فایل پشتیبان بیش از حد مجاز است (۴ مگابایت).")
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    try:
        data = json.loads(raw.decode("utf-8"))
        inbounds = validate_backup_payload(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        flash(request, "error", "فایل پشتیبان JSON معتبر نیست.")
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    except BackupValidationError as exc:
        flash(request, "error", str(exc))
        return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)

    count_inb, count_cl = apply_backup(db, inbounds)
    flash(
        request,
        "success",
        f"بازیابی انجام شد: {count_inb} اینباند با {count_cl} کاربر جایگزین شدند. "
        "برای اعمال کانفیگ، «اعمال کانفیگ» را بزنید.",
    )
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
