"""Generate, validate and safely apply Xray config.json (Phase 2).

The generation pipeline:
    DB rows -> build_config() -> render_config() -> validate_config_text()
    -> apply_config_text() (atomic replace with rollback copy)

If the `xray` binary is available on the host (settings.XRAY_BIN_PATH), its
built-in `xray run -test` is used for validation; otherwise only a JSON
syntax check is performed (useful on dev machines).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

import app.models as models
from app.config import settings

LOGLEVEL = "warning"


def build_inbound(inbound: models.Inbound) -> dict:
    """Build one Xray inbound object (dict) from an Inbound DB row."""
    s = inbound.settings or {}

    # --- streamSettings (transport + security) ----------------------------
    stream: dict = {"network": inbound.transport, "security": inbound.security}

    if inbound.transport == "ws":
        stream["wsSettings"] = {"path": s.get("ws_path", "/")}
    elif inbound.transport == "grpc":
        stream["grpcSettings"] = {"serviceName": s.get("grpc_service_name", "")}

    if inbound.security == "tls":
        stream["tlsSettings"] = {
            "alpn": ["http/1.1"],
            "certificates": [
                {
                    "certificateFile": s.get("tls_cert_file", ""),
                    "keyFile": s.get("tls_key_file", ""),
                }
            ],
        }
    elif inbound.security == "reality":
        stream["realitySettings"] = {
            "show": False,
            "dest": s.get("reality_dest", ""),
            "xver": 0,
            "serverNames": s.get("reality_server_names", []),
            "privateKey": s.get("reality_private_key", ""),
            "shortIds": s.get("reality_short_ids", []),
        }

    # --- inbound settings (per protocol) ----------------------------------
    if inbound.protocol == "shadowsocks":
        inbound_settings = {
            "method": s.get("ss_method", "aes-256-gcm"),
            "password": s.get("ss_password", ""),
            "network": "tcp,udp",
        }
    else:
        # Enabled clients only (see app.models.Client). Each protocol maps
        # its credential to the shape Xray expects.
        entries = []
        for client in (getattr(inbound, "clients", None) or []):
            if not client.enable:
                continue
            if inbound.protocol == "vless":
                entries.append(
                    {"id": client.uuid, "email": client.email, "flow": client.flow or ""}
                )
            elif inbound.protocol == "vmess":
                entries.append({"id": client.uuid, "email": client.email, "alterId": 0})
            else:  # trojan
                entries.append({"password": client.uuid, "email": client.email})
        inbound_settings = {"clients": entries}

    payload = {
        "tag": f"in-{inbound.id}",
        "listen": inbound.listen,
        "port": inbound.port,
        "protocol": inbound.protocol,
        "settings": inbound_settings,
        "streamSettings": stream,
    }
    if inbound.sniffing:
        payload["sniffing"] = {"enabled": True, "destOverride": ["http", "tls"]}
    return payload


def build_stats_services(port: int, host: str = "127.0.0.1") -> dict:
    """Xray config root additions enabling the gRPC stats service (Phase 4).

    Adds:
      * a `dokodemo-door` "api" inbound bound to `host:port` localhost-only
      * `statsService` in the `api` section
      * a `stats` section (with a system uptime counter)
      * a `policy` section keeping per-client user counters fresh
      * a routing rule that directs api-inbound traffic to `api -> statsService`
    """
    return {
        "log": {"loglevel": LOGLEVEL},
        "api": {
            "tag": "api",
            "services": ["StatsService"],
        },
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
            "system": {"statsStatsInboundUplink": True, "statsStatsInboundDownlink": True},
        },
        "inbounds": [
            {
                "tag": "api",
                "listen": host,
                "port": port,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            }
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["api"],
                    "outboundTag": "api",
                }
            ],
        },
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
    }


def build_config(inbounds: Iterable[models.Inbound]) -> dict:
    """Build the full Xray config root dict from active inbounds."""
    user_inbounds = [build_inbound(i) for i in inbounds]
    if settings.XRAY_STATS_ENABLED:
        # Localhost stats API in front of (rendered first for tag clarity)
        # the regular inbounds; the routing rule isolates it from users.
        root = build_stats_services(settings.XRAY_API_PORT, settings.XRAY_API_HOST)
        root["inbounds"] = user_inbounds + root["inbounds"]
        return root
    return {
        "log": {"loglevel": LOGLEVEL},
        "inbounds": user_inbounds,
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
    }


def render_config(config: dict) -> str:
    """Serialize the config dict to pretty JSON text."""
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def generate_config_text(active_inbounds) -> str:
    """Convenience: build + render from an iterable of Inbound rows."""
    return render_config(build_config(active_inbounds))


def _xray_binary_available() -> bool:
    """True when XRAY_BIN_PATH is set and the file exists."""
    return bool(settings.XRAY_BIN_PATH) and Path(settings.XRAY_BIN_PATH).exists()


def validate_config_text(text: str, config_path: Path) -> tuple[bool, str]:
    """Validate a candidate config text.

    1. Must be syntactically valid JSON.
    2. If the xray binary exists, run `xray run -test`.
    Returns (ok, detail) — detail is the error message when not ok.
    """
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return False, f"Invalid JSON: {exc}"

    if _xray_binary_available():
        probe_path = config_path.with_suffix(".json.validate")
        try:
            probe_path.write_text(text, encoding="utf-8")
            proc = subprocess.run(
                [settings.XRAY_BIN_PATH, "run", "-test", "-config", str(probe_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "xray config test failed").strip()
                return False, detail
        finally:
            probe_path.unlink(missing_ok=True)

    return True, ""


def apply_config_text(text: str) -> tuple[bool, str]:
    """Safely apply a candidate config text to the live config file.

    Steps:
      1. Validate (JSON + optional xray binary test) — on failure, nothing
         is written and the live config stays untouched.
      2. Back up the current config to `<config>.bak` (if it exists).
      3. Write the candidate next to the target and os.replace() it into
         place atomically.

    Returns (ok, detail).
    """
    target = Path(settings.XRAY_CONFIG_PATH)
    candidate = target.with_suffix(".json.new")
    backup = target.with_suffix(".json.bak")

    ok, detail = validate_config_text(text, target)
    if not ok:
        return False, detail

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(text, encoding="utf-8")
        if target.exists():
            shutil.copy2(target, backup)  # rollback copy
        os.replace(candidate, target)  # atomic on POSIX
        return True, ""
    except OSError as exc:
        candidate.unlink(missing_ok=True)
        return False, f"File error: {exc}"


def generate_and_apply(active_inbounds) -> tuple[bool, str]:
    """Full pipeline: generate from DB rows, validate, apply.

    Returns (ok, detail) — flash the detail when not ok.
    """
    text = generate_config_text(active_inbounds)
    return apply_config_text(text)
