"""Traffic collector + auto-disable (Phase 4).

Data flow on each refresh:

    1. `fetch_counters()` calls the Xray gRPC stats API with `reset=True`,
       so Xray returns per-user counters since the last call.
    2. `parse_user_counters()` keeps only `user>>>email>>>uplink|downlink`
       keys and normalises them to `{email: {"upload": n, "download": n}}`.
    3. `refresh_stats_and_auto_disable(db)` adds the deltas to the database
       counters and then enforces limits:

       * traffic:  total_gb > 0 and used >= total_gb * 1024^3
       * expiry:   today() > expire_at (still usable on the expiry day)

    Disabled clients are marked `enable=False` with a `disabled_reason` of
    "traffic_limit" or "expired". The monitor never resurrects a client that
    was disabled manually, expired, or is over quota — even when its counter
    is reset afterwards.
"""

from __future__ import annotations

import datetime

from sqlalchemy.orm import Session

import app.models as models
from app.config import settings

REASON_TRAFFIC = "traffic_limit"
REASON_EXPIRED = "expired"
REASON_MANUAL = "manual"

#: Reason values that must never be cleared by the monitor itself.
BLOCKING_REASONS = {REASON_TRAFFIC, REASON_EXPIRED, REASON_MANUAL}


# ---------------------------------------------------------------------------
# Counters fetch/parse (indirection points for testing)
# ---------------------------------------------------------------------------

def fetch_counters() -> dict[str, int]:
    """Query the Xray stats API. Patchable in tests (monkeypatch).

    Raises on connection failure — callers decide how to report it.
    """
    import app.xray_stats as xray_stats

    return xray_stats.query_stats(
        host=settings.XRAY_API_HOST,
        port=settings.XRAY_API_PORT,
        pattern="user>>>",
        reset=True,
        timeout=5.0,
    )


def parse_user_counters(raw: dict[str, int]) -> dict[str, dict[str, int]]:
    """Extract per-user uplink/downlink from a raw counter map.

    Input keys look like `user>>>alice>>>uplink` / `...>>>downlink`
    (system and inbound counters are filtered out). The result maps
    email -> {"upload": bytes, "download": bytes} with missing keys as 0.
    """
    users: dict[str, dict[str, int]] = {}
    for key, value in raw.items():
        parts = key.split(">>>")
        if len(parts) != 3 or parts[0] != "user":
            continue
        email, direction = parts[1], parts[2]
        if direction not in ("uplink", "downlink"):
            continue
        entry = users.setdefault(email, {"upload": 0, "download": 0})
        entry["upload" if direction == "uplink" else "download"] += int(value)
    return users


# ---------------------------------------------------------------------------
# Refresh + auto-disable
# ---------------------------------------------------------------------------

def refresh_stats_and_auto_disable(db: Session) -> tuple[int, int]:
    """Collect from Xray, accumulate into DB, enforce limits.

    Returns (updated_count, disabled_count) for flash reporting.

    Network errors are allowed to bubble — the router turns them into a
    Persian error message.
    """
    raw = fetch_counters()
    users = parse_user_counters(raw)

    updated = 0
    disabled = 0
    for client in db.query(models.Client).all():
        counters = users.get(client.email)
        if counters:
            client.used_upload_bytes += counters["upload"]
            client.used_download_bytes += counters["download"]
            updated += 1

        # Limits are enforced for every client, traffic or not, so users
        # who never spent their quota still get disabled past expiry.
        if _should_auto_disable(db, client):
            disabled += 1
        else:
            db.add(client)

    db.commit()
    return updated, disabled


def _should_auto_disable(db: Session, client: models.Client) -> bool:
    """Disable a client when a limit is crossed (never un-disable)."""
    if client.enable and _reason_to_disable(client):
        client.enable = False
        client.disabled_reason = _reason_to_disable(client)
        db.add(client)
        return True
    return False


def _reason_to_disable(client: models.Client) -> str | None:
    """First applicable auto-disable reason, or None when all is fine."""
    expiry = _reason_expired(client)
    if expiry:
        return expiry
    return _reason_traffic(client)


def _reason_traffic(client: models.Client) -> str | None:
    """'traffic_limit' when a byte quota is set and fully consumed."""
    if not client.total_gb or client.total_gb <= 0:
        return None
    used = client.used_download_bytes + client.used_upload_bytes
    quota_bytes = int(client.total_gb * 1024 ** 3)
    if quota_bytes > 0 and used >= quota_bytes:
        return REASON_TRAFFIC
    return None


def _reason_expired(client: models.Client) -> str | None:
    """'expired' when the expiry day has fully passed (today > expire_at)."""
    if not client.expire_at:
        return None
    if datetime.date.today() > client.expire_at:
        return REASON_EXPIRED
    return None


def all_client_statuses(db: Session) -> list[models.Client]:
    """All clients across inbounds, for a global stats view (Phase 5)."""
    return db.query(models.Client).order_by(models.Client.email).all()
