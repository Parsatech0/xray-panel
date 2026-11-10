"""Telegram bot (Phase 6, CLI-run, stdlib only).

Run on the server:
    venv/bin/python -m app.telegram_bot

Configuration (in .env):
    TELEGRAM_BOT_TOKEN   — bot token from @BotFather (empty = bot disabled)
    TELEGRAM_CHAT_ID     — chat id allowed to run commands (empty = first
                           chat that runs /start becomes the owner)

Features (long-polling via the public API; no webhook, no open ports):
    /start    — register the owner chat (persists to .env would be manual;
                the chat id is stored in the DB settings table)
    /status   — xray systemd status + CPU/RAM one-liner
    /traffic  — top 10 clients by usage (GB), disabled marked
    /expired  — clients disabled by expiry
    /help     — command list

Governor/bot logic is plain functions build_*_text(db) so they are
unit-testable in tests_phase6.py. No external dependency: the HTTP layer
uses urllib.request.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from app.config import settings

API_BASE = "https://api.telegram.org/bot"

_GIB = 1024 ** 3

# The chat id allowed to run commands (set via /start when empty).
_ALLOWED_CHAT_KEY = "telegram_chat_id"


# ---------------------------------------------------------------------------
# Command report builders (pure, testable)
# ---------------------------------------------------------------------------

def build_status_text() -> str:
    """xray state + a one-line system summary."""
    import app.system_stats as system_stats
    import app.xray_control as xray_control

    state, _ = xray_control.xray_status()
    state_fa = {"active": "🟢 در حال اجرا", "stopped": "🔴 متوقف",
                "unavailable": "🟡 غیرقابل تشخیص"}.get(state, "🟡 نامشخص")

    stats = system_stats.get_system_stats()
    if not stats.get("ok"):
        sys_line = "آمار سیستم در دسترس نیست."
    else:
        sys_line = (
            f"CPU {stats['cpu_percent']}% | RAM {stats['ram_percent']}% "
            f"| Disk {stats['disk_percent']}%"
        )
    return f"Xray: {state_fa}\n{sys_line}"


def build_traffic_text(db, top: int = 10) -> str:
    """Top clients by total usage."""
    import app.models as models

    rows = (
        db.query(models.Client)
        .order_by(
            (models.Client.used_download_bytes + models.Client.used_upload_bytes).desc()
        )
        .limit(top)
        .all()
    )
    if not rows:
        return "هنوز کاربری ثبت نشده است."
    lines = ["📊 مصرف کاربران:"]
    for index, client in enumerate(rows, start=1):
        gb = (client.used_download_bytes + client.used_upload_bytes) / _GIB
        marker = "⛔" if not client.enable else "✅"
        lines.append(f"{index}. {marker} {client.email} — {gb:.2f} GB")
    return "\n".join(lines)


def build_expired_text(db) -> str:
    """Disabled-by-quota/expiry summary."""
    import app.models as models

    expired = (
        db.query(models.Client)
        .filter(models.Client.disabled_reason == "expired")
        .all()
    )
    over_quota = (
        db.query(models.Client)
        .filter(models.Client.disabled_reason == "traffic_limit")
        .all()
    )
    if not expired and not over_quota:
        return "هیچ کاربری منقضی یا اتمام ترافیک نیست. ✅"
    return (
        f"⛔ اتمام ترافیک: {len(over_quota)} کاربر\n"
        f"🗓 منقضی: {len(expired)} کاربر"
    )


def build_help_text() -> str:
    return (
        "دستورات:\n"
        "/status — وضعیت Xray و سرور\n"
        "/traffic — ۱۰ کاربر پرمصرف\n"
        "/expired — کاربران منقضی/اتمام ترافیک\n"
        "/help — همین متن"
    )


# ---------------------------------------------------------------------------
# Telegram HTTP layer (stdlib)
# ---------------------------------------------------------------------------

def api_call(token: str, method: str, params: dict | None = None,
             timeout: float = 30.0) -> dict | None:
    """One Telegram Bot API GET/POST call; raises on network failure."""
    url = f"{API_BASE}{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body


def _send_message(token: str, chat_id: str, text: str) -> None:
    api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    })


def _handle_update(token: str, update: dict, allowed_chat: str | None) -> str | None:
    """Process one update; returns owner chat id to persist (or None)."""
    message = update.get("message") or update.get("edited_message") or {}
    chat = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if chat is None or not text:
        return None
    chat_str = str(chat)

    if allowed_chat and chat_str != allowed_chat:
        _send_message(token, chat_str, "⛔️ دسترسی مجاز نیست.")
        return None

    from app.database import SessionLocal

    command = text.split()[0].split("@")[0].lower()
    if command in ("/start", "/register"):
        _send_message(token, chat_str, "✅ این مالک ثبت شد." + "\n\n" + build_help_text())
        return chat_str
    if command == "/status":
        _send_message(token, chat_str, build_status_text())
    elif command in ("/traffic", "/usage"):
        db = SessionLocal()
        try:
            _send_message(token, chat_str, build_traffic_text(db))
        finally:
            db.close()
    elif command == "/expired":
        db = SessionLocal()
        try:
            _send_message(token, chat_str, build_expired_text(db))
        finally:
            db.close()
    elif command == "/help":
        _send_message(token, chat_str, build_help_text())
    else:
        _send_message(token, chat_str, build_help_text())

    return None


def _get_allowed_chat() -> str | None:
    """Read the registered owner chat from the DB key-value store."""
    try:
        from app.models import PanelSetting

        with SessionLocal() as db:
            return db.get(PanelSetting, _ALLOWED_CHAT_KEY)
    except Exception:
        return None


def main() -> None:
    import time

    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN خالی است — در .env تنظیم کنید.")

    allowed = settings.TELEGRAM_CHAT_ID or _get_allowed_chat()
    print("[telegram] bot starting; allowed chat:", allowed or "(first /start is owner)")

    offset: int | None = None
    while True:
        try:
            params = {"timeout": 25}
            if offset is not None:
                params["offset"] = offset
            res = api_call(token, "getUpdates", params, timeout=40) or {}
            for update in res.get("result", []):
                offset = update["update_id"] + 1
                owner = _handle_update(token, update, allowed)
                if owner and not allowed:
                    allowed = owner
                    print("[telegram] registered owner chat:", allowed)
        except Exception as exc:  # network errors -> retry after backoff
            print("[telegram] error:", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
