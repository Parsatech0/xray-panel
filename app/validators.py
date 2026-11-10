"""Input validation logic for inbound forms (Phase 2).

Validators return error *codes* (stable identifiers); the router maps them
to user-facing messages via `error_message()`. This keeps validation logic
easy to unit test and free of UI concerns.
"""

import ipaddress
import re

# Allowed values, mirroring what the generated Xray config supports.
SUPPORTED_PROTOCOLS = {"vless", "vmess", "trojan", "shadowsocks"}
SUPPORTED_TRANSPORTS = {"tcp", "ws", "grpc"}
SUPPORTED_SECURITY = {"none", "tls", "reality"}

# Shadowsocks ciphers accepted by Xray-core for inbound connections.
SUPPORTED_SS_METHODS = {"aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "none"}

# VLESS flow options (empty = no flow control; vision for xtls-rprx-vision).
SUPPORTED_VLESS_FLOWS = {"", "xtls-rprx-vision"}

# Xray `email` identifiers are used in the stats API, so keep them strict.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Reality private keys are raw 32-byte x25519 keys, base64url-encoded (43 chars).
_PRIVATE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
# dest must be host:port, e.g. "www.example.com:443"
_DEST_RE = re.compile(r"^[A-Za-z0-9._-]+:[0-9]{1,5}$")
# Simple hostname/domain validation
_DOMAIN_RE = re.compile(r"^(?:[a-zA-Z0-9](-*[a-zA-Z0-9])*\.)+[a-zA-Z]{2,}$")
# Xray Reality shortIds: even-length hex strings, max 8 bytes (16 hex chars)
_SHORT_ID_RE = re.compile(r"^[0-9a-fA-F]{2,16}$")

# Persian (UI) messages for each error code.
ERROR_MESSAGES = {
    "ERR_REMARK": "عنوان اینباند نامعتبر است (۱ تا ۶۴ کاراکتر، بدون <>\\;).",
    "ERR_PORT": "پورت نامعتبر است (باید عددی بین ۱ تا ۶۵۵۳۵ باشد).",
    "ERR_PROTOCOL": "پروتکل انتخاب‌شده پشتیبانی نمی‌شود.",
    "ERR_TRANSPORT": "ترنسپورت انتخاب‌شده پشتیبانی نمی‌شود.",
    "ERR_SECURITY": "سطح امنیتی انتخاب‌شده پشتیبانی نمی‌شود.",
    "ERR_LISTEN": "آدرس IP گوش‌دادن (listen) نامعتبر است.",
    "ERR_WS_PATH": "مسیر WebSocket باید با «/» شروع شود.",
    "ERR_GRPC_NAME": "نام سرویس gRPC (serviceName) نمی‌تواند خالی باشد.",
    "ERR_SS_METHOD": "روش رمزنگاری Shadowsocks نامعتبر است.",
    "ERR_SS_PASSWORD": "رمز Shadowsocks باید حداقل ۸ کاراکتر باشد.",
    "ERR_REALITY_DEST": "مقصد Reality باید به شکل host:port باشد.",
    "ERR_REALITY_NAMES": "فهرست نام‌های سرور Reality نامعتبر است.",
    "ERR_REALITY_KEY": "کلید خصوصی Reality نامعتبر است (۴۳ کاراکتر base64url).",
    "ERR_REALITY_IDS": "شناسه‌های کوتاه باید جفت‌کاراکتر هگز (۲ تا ۱۶ رقم) باشند.",
    "ERR_TLS_FILES": "برای TLS، مسیر گواهی و کلید هر دو الزامی است.",
    # Client (Phase 3)
    "ERR_EMAIL": "ایمیل/نام کاربر نامعتبر است (فقط حروف، عدد، نقطه، خط زیر و خط تیره؛ حداکثر ۶۴ کاراکتر).",
    "ERR_EMAIL_DUP": "این نام کاربر (email) قبلاً در پنل استفاده شده است — باید سراسر پنل یکتا باشد.",
    "ERR_UUID": "شناسه کلاینت (UUID/رمز) نامعتبر است.",
    "ERR_FLOW": "مقدار flow برای VLESS نامعتبر است.",
    "ERR_TOTAL_GB": "محدودیت ترافیک باید عددی مثبت یا صفر (نامحدود) باشد.",
    "ERR_IP_LIMIT": "محدودیت IP باید عددی مثبت یا صفر (نامحدود) باشد.",
    "ERR_EXPIRE": "تاریخ انقضا نامعتبر است (فرمت YYYY-MM-DD).",
    "ERR_PROTOCOL_NO_CLIENTS": "برای Shadowsocks، کاربر اضافه نمی‌شود (این پروتکل تک‌کاربر است).",
}


def error_message(code: str) -> str:
    """Translate a validation error code into a user-facing Persian message."""
    return ERROR_MESSAGES.get(code, code)


def _clean_str(value) -> str:
    """Return a trimmed string or '' for None input."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_comma_list(raw) -> list[str]:
    """Split a comma-separated string into a cleaned list of tokens."""
    return [t.strip() for t in _clean_str(raw).split(",") if t.strip()]


def _as_int(value):
    """Convert to int, returning None instead of raising."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def validate_inbound(data: dict) -> tuple[dict, list[str]]:
    """Validate an inbound form submission.

    Args:
        data: raw form fields as dict-like (e.g. `dict(request.form)` values).

    Returns:
        (clean, errors): `clean` is a dict of validated fields (empty when
        there are errors) ready for the DB layer; `errors` is a list of
        error codes (empty when valid).
    """
    errors: list[str] = []

    # --- remark ---------------------------------------------------------
    remark = _clean_str(data.get("remark"))
    if not (1 <= len(remark) <= 64) or any(ch in remark for ch in "<>\\;"):
        errors.append("ERR_REMARK")

    # --- port / listen --------------------------------------------------
    port = _as_int(data.get("port"))
    if port is None or not (1 <= port <= 65535):
        errors.append("ERR_PORT")

    listen = _clean_str(data.get("listen")) or "0.0.0.0"
    try:
        ipaddress.ip_address(listen)
    except ValueError:
        errors.append("ERR_LISTEN")

    # --- protocol / transport / security --------------------------------
    protocol = _clean_str(data.get("protocol")).lower()
    if protocol not in SUPPORTED_PROTOCOLS:
        errors.append("ERR_PROTOCOL")

    transport = _clean_str(data.get("transport")).lower()
    if transport not in SUPPORTED_TRANSPORTS:
        errors.append("ERR_TRANSPORT")

    security = _clean_str(data.get("security")).lower()
    if security not in SUPPORTED_SECURITY:
        errors.append("ERR_SECURITY")

    # --- per-transport settings ------------------------------------------
    settings: dict = {}

    if transport == "ws":
        ws_path = _clean_str(data.get("ws_path")) or "/"
        if not ws_path.startswith("/"):
            errors.append("ERR_WS_PATH")
        settings["ws_path"] = ws_path

    if transport == "grpc":
        grpc_name = _clean_str(data.get("grpc_service_name"))
        if not grpc_name:
            errors.append("ERR_GRPC_NAME")
        settings["grpc_service_name"] = grpc_name

    # --- per-protocol settings (shadowsocks) ------------------------------
    if protocol == "shadowsocks":
        method = _clean_str(data.get("ss_method")).lower()
        if method not in SUPPORTED_SS_METHODS:
            errors.append("ERR_SS_METHOD")
        password = data.get("ss_password") or ""
        if len(password) < 8:
            errors.append("ERR_SS_PASSWORD")
        settings["ss_method"] = method
        settings["ss_password"] = password

    # --- per-security settings -------------------------------------------
    if security == "reality":
        dest = _clean_str(data.get("reality_dest"))
        if not _DEST_RE.fullmatch(dest):
            errors.append("ERR_REALITY_DEST")

        names = _parse_comma_list(data.get("reality_server_names"))
        if not names or any(not _DOMAIN_RE.fullmatch(n) for n in names):
            errors.append("ERR_REALITY_NAMES")

        private_key = _clean_str(data.get("reality_private_key"))
        if not _PRIVATE_KEY_RE.fullmatch(private_key):
            errors.append("ERR_REALITY_KEY")

        short_ids = _parse_comma_list(data.get("reality_short_ids"))
        if any(not _SHORT_ID_RE.fullmatch(s) for s in short_ids):
            errors.append("ERR_REALITY_IDS")

        settings["reality_dest"] = dest
        settings["reality_server_names"] = [n.lower() for n in names]
        settings["reality_private_key"] = private_key
        settings["reality_short_ids"] = short_ids

    if security == "tls":
        cert_file = _clean_str(data.get("tls_cert_file"))
        key_file = _clean_str(data.get("tls_key_file"))
        if not cert_file or not key_file:
            errors.append("ERR_TLS_FILES")
        settings["tls_cert_file"] = cert_file
        settings["tls_key_file"] = key_file

    if errors:
        return {}, errors

    clean = {
        "remark": remark,
        "listen": listen,
        "port": port,
        "protocol": protocol,
        "transport": transport,
        "security": security,
        "settings": settings,
        "sniffing": bool(data.get("sniffing")),
    }
    return clean, []


def validate_client(data: dict, protocol: str) -> tuple[dict, list[str]]:
    """Validate a client form submission for an inbound of `protocol`.

    Returns `(clean, error_codes)` with the same conventions as
    `validate_inbound`. `clean["expire_at"]` is a `datetime.date` or None.
    """
    from datetime import date

    errors: list[str] = []

    if protocol == "shadowsocks":
        return {}, ["ERR_PROTOCOL_NO_CLIENTS"]

    email = _clean_str(data.get("email"))
    if not _EMAIL_RE.fullmatch(email):
        errors.append("ERR_EMAIL")

    uuid_value = _clean_str(data.get("uuid"))
    if uuid_value and len(uuid_value) > 64:
        errors.append("ERR_UUID")

    flow = _clean_str(data.get("flow"))
    if protocol == "vless":
        if flow not in SUPPORTED_VLESS_FLOWS:
            errors.append("ERR_FLOW")
    else:
        flow = ""

    total_gb = _as_int(data.get("total_gb"))
    if total_gb is None or total_gb < 0:
        errors.append("ERR_TOTAL_GB")

    ip_limit = _as_int(data.get("ip_limit"))
    if ip_limit is None or ip_limit < 0:
        errors.append("ERR_IP_LIMIT")

    raw_expire = _clean_str(data.get("expire_at"))
    expire_at = None
    if raw_expire:
        try:
            expire_at = date.fromisoformat(raw_expire)
        except ValueError:
            errors.append("ERR_EXPIRE")

    if errors:
        return {}, errors

    clean = {
        "email": email,
        "uuid": uuid_value,
        "flow": flow,
        "total_gb": float(total_gb),
        "ip_limit": ip_limit,
        "expire_at": expire_at,
        "enable": bool(data.get("enable")),
    }
    return clean, []
