"""SQLAlchemy database models.

Phase 1: Admin model.
Phase 2: Inbound model (Xray inbound definitions).
Phase 3 (planned): Client model.
"""

import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Admin(Base):
    """Panel administrator account."""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Admin(id={self.id}, username={self.username!r})>"


class Inbound(Base):
    """A single Xray inbound definition.

    Protocol/transport/security limited to what app.xray_gen can emit:

    * protocols   : vless, vmess, trojan, shadowsocks
    * transports  : tcp, ws, grpc
    * security    : none, tls, reality

    `settings` is a JSON column holding the per-protocol / per-security /
    per-transport fields, e.g.::

        {
          "ss_method": "aes-256-gcm", "ss_password": "...",   # shadowsocks
          "ws_path": "/wshere",                                # ws transport
          "grpc_service_name": "srv",                          # grpc transport
          "reality_dest": "sni.example.com:443",
          "reality_server_names": ["sni.example.com"],         # reality
          "reality_private_key": "...",
          "reality_short_ids": ["a1b2c3d4"],
          "tls_cert_file": "/etc/letsencrypt/.../fullchain.pem",
          "tls_key_file": "/etc/letsencrypt/.../privkey.pem",  # tls
          "clients": [...]                                     # phase 3
        }

    Keeping them in one JSON column avoids a wide table while allowing
    Xray to evolve.
    """

    __tablename__ = "inbounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    remark: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    listen: Mapped[str] = mapped_column(String(45), default="0.0.0.0", nullable=False)
    port: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(20), nullable=False)
    transport: Mapped[str] = mapped_column(String(20))
    security: Mapped[str] = mapped_column(String(20), default="none")
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sniffing: Mapped[bool] = mapped_column(Boolean, default=True)
    enable: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Phase 3: clients attached to this inbound; deleting the inbound
    # cascades to its clients (SQLite ON DELETE handled by SQLAlchemy ORM).
    clients: Mapped[list["Client"]] = relationship(
        back_populates="inbound",
        cascade="all, delete-orphan",
        order_by="Client.email",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Inbound(id={self.id},remark={self.remark!r},port={self.port},protocol={self.protocol})>"


class Client(Base):
    """A user/client inside an inbound (Phase 3).

    One credential row per protocol membership:

    * vless  -> `uuid` holds the client UUID (`id` in settings.clients)
    * vmess  -> `uuid` holds the client UUID (`aid` fixed 0)
    * trojan -> `uuid` holds the password (Xray expects `password`)
    * shadowsocks -> *not compatible*: SS inbounds are single-credential in
      Xray; the panel exposes the inbound `ss://` link instead of clients.

    Limits:
      * `total_gb` 0 means unlimited; Phase 4 compares it with usage bytes.
      * `expire_at` NULL means never expires.
      * `ip_limit` 0 means unlimited (pairing verified in Phase 4).
    """

    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    inbound_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inbounds.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    uuid: Mapped[str] = mapped_column(String(64), nullable=False)
    flow: Mapped[str] = mapped_column(String(32), default="")  # vless only
    total_gb: Mapped[float] = mapped_column(Float, default=0)  # 0 = unlimited
    expire_at: Mapped[Optional[datetime.date]] = mapped_column(Date, nullable=True)
    ip_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    enable: Mapped[bool] = mapped_column(Boolean, default=True)

    # Why this client is disabled (Phase 4): "" = not disabled, otherwise one
    # of "expired" | "traffic_limit" | "manual". The monitor never resets a
    # disabled_reason by itself — only an admin action (toggle/re-enable) does.
    disabled_reason: Mapped[str] = mapped_column(String(16), default="")

    # Usage counters filled by the Phase 4 stats collector (bytes).
    used_download_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    used_upload_bytes: Mapped[int] = mapped_column(BigInteger, default=0)

    # Public, unguessable token for the Phase 6 subscription URL
    # (/sub/<token>); empty until first requested/generated.
    sub_token: Mapped[Optional[str]] = mapped_column(
        String(48), index=True, unique=False, default=""
    )

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    inbound: Mapped["Inbound"] = relationship(back_populates="clients")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Client(id={self.id},email={self.email!r},inbound_id={self.inbound_id})>"


class PanelSetting(Base):
    """Tiny key/value store for panel runtime settings (Phase 6).

    Currently used by the Telegram bot to persist the owner chat id across
    restarts (`telegram_chat_id`), so the first /start stays owner.
    """

    __tablename__ = "panel_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PanelSetting(key={self.key!r})>"
