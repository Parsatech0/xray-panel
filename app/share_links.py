"""Client config share links and QR codes (Phase 3).

Supported schemes:
  * vless://uuid@host:port?params#fragment
  * vmess://base64(json)                          (V2Ray N format)
  * trojan://password@host:port?params#fragment
  * ss://base64(method:password)@host:port#fragment  (SIP002)

The host is supplied by the caller (usually the HTTP request's Host header),
so links always point to the address the admin is browsing the panel on.
QR codes are embedded as SVG data URIs (no PIL/PNG dependency).
"""

import base64
import io
import json
import uuid as uuid_lib
from typing import Optional
from urllib.parse import quote

import app.models as models


def generate_uuid() -> str:
    """Random client UUID (also used as trojan password)."""
    return str(uuid_lib.uuid4())


def _display_name(inbound: models.Inbound, client: models.Client) -> str:
    """URL fragment text shown in clients, e.g. 'my-inbound - alice'."""
    return f"{inbound.remark} - {client.email}"


def _http_params(inbound: models.Inbound) -> list[str]:
    """Common query-string parameters reflecting transport/security."""
    s = inbound.settings or {}
    params: list[str] = [f"type={inbound.transport}"]

    if inbound.transport == "ws":
        params.append(f"path={quote(s.get('ws_path', '/'), safe='/')}")
    elif inbound.transport == "grpc":
        sn = s.get("grpc_service_name", "")
        if sn:
            params.append(f"serviceName={quote(sn, safe='')}")

    if inbound.security == "tls":
        params.append("security=tls&fp=chrome")
    elif inbound.security == "reality":
        params.append("security=reality&fp=chrome")
        names = s.get("reality_server_names") or []
        if names:
            params.append(f"sni={quote(names[0], safe='')}")
        pk = s.get("reality_public_key", "")
        if pk:
            params.append(f"pbk={quote(pk, safe='')}")
        ids = s.get("reality_short_ids") or []
        if ids:
            params.append(f"sid={ids[0]}")
    else:
        params.append("security=none")

    return params


def _sni_host(inbound: models.Inbound, host: str) -> str:
    """Host to dial for the connection (sni override when Reality)."""
    s = inbound.settings or {}
    if inbound.security == "reality":
        names = s.get("reality_server_names") or []
        return names[0] if names else host
    return host


def build_share_link(
    inbound: models.Inbound, client: models.Client, host: str
) -> Optional[str]:
    """Build a client share link for the inbound's protocol.

    Returns None for protocols without per-client credentials.
    """
    if not host:
        return None
    host = _sni_host(inbound, host)
    params = "&".join(_http_params(inbound))
    frag = quote(_display_name(inbound, client), safe="")

    if inbound.protocol == "vless":
        flow = f"&flow={client.flow}" if client.flow else ""
        return f"vless://{client.uuid}@{host}:{inbound.port}?{params}{flow}#{frag}"

    if inbound.protocol == "vmess":
        s = inbound.settings or {}
        payload = {
            "v": "2",
            "ps": _display_name(inbound, client),
            "add": host,
            "port": str(inbound.port),
            "id": client.uuid,
            "aid": "0",
            "net": inbound.transport,
            "type": "none",
            "tls": "",
        }
        if inbound.transport == "ws":
            payload["path"] = s.get("ws_path", "/")
        if inbound.transport == "grpc":
            payload["path"] = s.get("grpc_service_name", "")
        if inbound.security == "tls":
            payload["tls"] = "tls"
            payload["sni"] = host
        elif inbound.security == "reality":
            payload["tls"] = "reality"
            payload["sni"] = host
            pk = s.get("reality_public_key", "")
            if pk:
                payload["pbk"] = pk
            ids = s.get("reality_short_ids") or []
            if ids:
                payload["sid"] = ids[0]
        b64 = base64.b64encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        return f"vmess://{b64}"

    if inbound.protocol == "trojan":
        return f"trojan://{quote(client.uuid, safe='')}@{host}:{inbound.port}?{params}#{frag}"

    # shadowsocks handled by build_inbound_ss_link (no per-client credential)
    return None


def build_inbound_ss_link(inbound: models.Inbound, host: str) -> Optional[str]:
    """Single-user ss:// link for shadowsocks inbounds."""
    if inbound.protocol != "shadowsocks" or not host:
        return None
    s = inbound.settings or {}
    method = s.get("ss_method", "")
    password = s.get("ss_password", "")
    userinfo = (
        base64.urlsafe_b64encode(f"{method}:{password}".encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    frag = quote(inbound.remark, safe="")
    return f"ss://{userinfo}@{_sni_host(inbound, host)}:{inbound.port}#{frag}"


# ---------------------------------------------------------------------------
# QR codes (SVG, no PIL required)
# ---------------------------------------------------------------------------

def make_qr_svg_data_uri(text: str) -> str:
    """Render `text` as an SVG QR code encoded into a data: URI."""
    import qrcode
    import qrcode.image.svg

    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(text, image_factory=factory, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"
