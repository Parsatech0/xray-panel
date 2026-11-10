"""CRUD operations: Admin (Phase 1), Inbound (Phase 2), Client (Phase 3)."""

from sqlalchemy.orm import Session

import app.models as models
import app.monitor as monitor
import app.security as security
from app.schemas import AdminCreate, AdminUpdate
from app.share_links import generate_uuid


def get_admin_by_username(db: Session, username: str):
    return db.query(models.Admin).filter(models.Admin.username == username).first()


def get_admin(db: Session, admin_id: int):
    return db.query(models.Admin).filter(models.Admin.id == admin_id).first()


def create_admin(db: Session, admin_in: AdminCreate):
    """Create a new admin with a bcrypt-hashed password."""
    db_admin = models.Admin(
        username=admin_in.username,
        password_hash=security.hash_password(admin_in.password),
    )
    db.add(db_admin)
    db.commit()
    db.refresh(db_admin)
    return db_admin


def update_admin(db: Session, db_admin: models.Admin, admin_in: AdminUpdate):
    """Update admin username and/or password."""
    update_data = admin_in.model_dump(exclude_unset=True)
    if "password" in update_data:
        update_data["password_hash"] = security.hash_password(update_data.pop("password"))
    for field, value in update_data.items():
        setattr(db_admin, field, value)
    db.add(db_admin)
    db.commit()
    db.refresh(db_admin)
    return db_admin


# ---------------------------------------------------------------------------
# Inbounds (Phase 2)
# ---------------------------------------------------------------------------

def list_inbounds(db: Session) -> list[models.Inbound]:
    """All inbounds, ordered by port."""
    return db.query(models.Inbound).order_by(models.Inbound.port).all()


def list_active_inbounds(db: Session) -> list[models.Inbound]:
    """Only enabled inbounds, ordered by port (used for config generation)."""
    return (
        db.query(models.Inbound)
        .filter(models.Inbound.enable == True)  # noqa: E712
        .order_by(models.Inbound.port)
        .all()
    )


def get_inbound(db: Session, inbound_id: int) -> models.Inbound | None:
    return db.query(models.Inbound).filter(models.Inbound.id == inbound_id).first()


def get_inbound_by_port(db: Session, port: int) -> models.Inbound | None:
    return db.query(models.Inbound).filter(models.Inbound.port == port).first()


def create_inbound(db: Session, fields: dict) -> models.Inbound:
    """Create an inbound from validated fields (see app.validators)."""
    db_inbound = models.Inbound(
        remark=fields["remark"],
        listen=fields["listen"],
        port=fields["port"],
        protocol=fields["protocol"],
        transport=fields["transport"],
        security=fields["security"],
        settings=fields["settings"],
        sniffing=fields["sniffing"],
        enable=fields.get("enable", True),
    )
    db.add(db_inbound)
    db.commit()
    db.refresh(db_inbound)
    return db_inbound


def update_inbound(db: Session, db_inbound: models.Inbound, fields: dict) -> models.Inbound:
    """Update an inbound from validated/full form fields."""
    for field in ("remark", "listen", "port", "protocol", "transport", "security", "settings", "sniffing", "enable"):
        if field in fields:
            setattr(db_inbound, field, fields[field])
    db.add(db_inbound)
    db.commit()
    db.refresh(db_inbound)
    return db_inbound


def toggle_inbound(db: Session, db_inbound: models.Inbound) -> models.Inbound:
    """Enable <-> disable, then persist."""
    db_inbound.enable = not db_inbound.enable
    db.add(db_inbound)
    db.commit()
    db.refresh(db_inbound)
    return db_inbound


def delete_inbound(db: Session, db_inbound: models.Inbound) -> None:
    db.delete(db_inbound)
    db.commit()


# ---------------------------------------------------------------------------
# Clients (Phase 3)
# ---------------------------------------------------------------------------

def list_clients(db: Session, inbound_id: int) -> list[models.Client]:
    """Clients of one inbound, ordered by email."""
    return (
        db.query(models.Client)
        .filter(models.Client.inbound_id == inbound_id)
        .order_by(models.Client.email)
        .all()
    )


def get_client(db: Session, inbound_id: int, client_id: int) -> models.Client | None:
    return (
        db.query(models.Client)
        .filter(models.Client.inbound_id == inbound_id, models.Client.id == client_id)
        .first()
    )


def get_client_by_email(db: Session, email: str) -> models.Client | None:
    """Global email lookup — email must be unique panel-wide because Xray
    stats counters (`user>>>email>>>...`) key on email alone, with no
    inbound in the key."""
    return db.query(models.Client).filter(models.Client.email == email).first()


def create_client(db: Session, inbound: models.Inbound, fields: dict) -> models.Client:
    """Create a client from validated fields (uuid auto-generated if empty)."""
    uuid_value = fields["uuid"] or generate_uuid()
    db_client = models.Client(
        inbound_id=inbound.id,
        email=fields["email"],
        uuid=uuid_value,
        flow=fields["flow"],
        total_gb=fields["total_gb"],
        expire_at=fields["expire_at"],
        ip_limit=fields["ip_limit"],
        enable=fields["enable"],
    )
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    return db_client


def update_client(db: Session, db_client: models.Client, fields: dict) -> models.Client:
    """Update a client; empty uuid keeps the existing credential.

    Usage counters (used_*_bytes) and disabled_reason are never modified
    here — the Phase 4 monitor owns both.
    """
    for field in ("email", "flow", "total_gb", "expire_at", "ip_limit"):
        if field in fields:
            setattr(db_client, field, fields[field])
    if fields.get("uuid"):
        db_client.uuid = fields["uuid"]
    if "enable" in fields:
        new_enable = fields["enable"]
        db_client.enable = new_enable
        if not new_enable and db_client.disabled_reason not in monitor.BLOCKING_REASONS:
            db_client.disabled_reason = monitor.REASON_MANUAL
        elif new_enable:
            db_client.disabled_reason = ""
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    return db_client


def toggle_client(db: Session, db_client: models.Client) -> models.Client:
    """Enable <-> disable, tracking why it ended up disabled.

    * disable -> `disabled_reason = "manual"`
    * enable  -> clear the reason (admins may revive auto-disabled clients)
    """
    db_client.enable = not db_client.enable
    if not db_client.enable:
        db_client.disabled_reason = monitor.REASON_MANUAL
    else:
        db_client.disabled_reason = ""
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    return db_client


def reset_client_traffic(db: Session, db_client: models.Client) -> models.Client:
    """Zero the usage counters (Phase 4 fills them from the stats API)."""
    db_client.used_download_bytes = 0
    db_client.used_upload_bytes = 0
    db.add(db_client)
    db.commit()
    db.refresh(db_client)
    return db_client


def delete_client(db: Session, db_client: models.Client) -> None:
    db.delete(db_client)
    db.commit()


# ---------------------------------------------------------------------------
# Subscription tokens (Phase 6)
# ---------------------------------------------------------------------------

def ensure_sub_token(db: Session, db_client: models.Client) -> str:
    """Return the client's subscription token, generating one if empty."""
    if not db_client.sub_token:
        db_client.sub_token = security.generate_api_token()
        db.add(db_client)
        db.commit()
        db.refresh(db_client)
    return db_client.sub_token


def get_client_by_sub_token(db: Session, token: str) -> models.Client | None:
    """Public lookup used by the anonymous /sub/<token> route."""
    if not token or len(token) > 64:
        return None
    return (
        db.query(models.Client)
        .filter(models.Client.sub_token == token)
        .first()
    )
