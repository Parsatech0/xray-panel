"""Phase 3 smoke tests: client CRUD, share links, QR codes, config wiring.

Run from the project root:
    venv\\Scripts\\python.exe tests_phase3.py
"""

import base64
import json
import os
import pathlib
import re
import sys
import tempfile
from types import SimpleNamespace

BASE = pathlib.Path(__file__).parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="xray_panel_phase3_"))

os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ.setdefault("SECRET_KEY", "phase3-test-secret-not-for-production")
os.environ.setdefault("DEFAULT_ADMIN_USERNAME", "admin3")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "Phase3Pass123")
os.environ["XRAY_CONFIG_PATH"] = str(TMP / "config.json")
os.environ["XRAY_BIN_PATH"] = ""

sys.path.insert(0, str(BASE))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
import app.crud as crud  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def login(client: "TestClient") -> str:
    r = client.get("/login")
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    resp = client.post(
        "/login",
        data={"username": "admin3", "password": "Phase3Pass123", "csrf_token": tok},
        follow_redirects=False,
    )
    assert resp.status_code == 302, "login failed"


def get_csrf(client: "TestClient", path: str) -> str:
    html = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"csrf token not found on {path}"
    return match.group(1)


def make_inbound(client: "TestClient", tok: str, **over) -> int:
    data = {
        "remark": over.pop("remark", "in-1"),
        "listen": "0.0.0.0",
        "port": str(over.pop("port", 41001)),
        "protocol": over.pop("protocol", "vless"),
        "transport": "tcp",
        "security": "none",
        "sniffing": "on",
        "enable": "on",
        "csrf_token": tok,
    }
    data.update(over)
    r = client.post("/inbounds", data=data, follow_redirects=False)
    assert r.status_code == 302, f"inbound create failed: {r.status_code}"
    with SessionLocal() as db:
        rows = crud.list_inbounds(db)
        return max(row.id for row in rows)


def main() -> None:
    with TestClient(app) as client:
        # 0. clients pages are protected
        r = client.get("/inbounds/1/clients", follow_redirects=False)
        assert r.status_code in (302, 303, 401, 403)
        print("[OK] /inbounds/1/clients requires authentication")

        login(client)
        tok = get_csrf(client, "/inbounds/new")

        inbound_id = make_inbound(client, tok, remark="vless-main", port="41001")

        # 1. new-client form renders
        get_csrf(client, f"/inbounds/{inbound_id}/clients/new")
        print("[OK] client creation form renders")

        # 2. create client without uuid -> auto-generated
        r = client.post(
            f"/inbounds/{inbound_id}/clients",
            data={
                "email": "alice", "uuid": "", "flow": "",
                "total_gb": "10", "ip_limit": "2", "expire_at": "2030-01-01",
                "enable": "on", "csrf_token": tok,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302, r.status_code
        with SessionLocal() as db:
            alice = crud.get_client_by_email(db, "alice")
            assert alice is not None
            assert len(alice.uuid) == 36  # uuid4 format
            assert alice.total_gb == 10 and alice.ip_limit == 2
            assert alice.expire_at is not None
        print("[OK] client created with auto-generated uuid")

        # 3. duplicate email rejected
        r = client.post(
            f"/inbounds/{inbound_id}/clients",
            data={
                "email": "alice", "uuid": "", "flow": "", "total_gb": "0",
                "ip_limit": "0", "expire_at": "", "csrf_token": tok,
            },
        )
        assert r.status_code == 400
        print("[OK] duplicate client email rejected")

        # 4. invalid email rejected
        r = client.post(
            f"/inbounds/{inbound_id}/clients",
            data={
                "email": "bad email!", "uuid": "", "flow": "", "total_gb": "0",
                "ip_limit": "0", "expire_at": "", "csrf_token": tok,
            },
        )
        assert r.status_code == 400
        print("[OK] invalid email rejected")

        # 5. bad expiry rejected
        r = client.post(
            f"/inbounds/{inbound_id}/clients",
            data={
                "email": "bob", "uuid": "", "flow": "", "total_gb": "0",
                "ip_limit": "0", "expire_at": "not-a-date", "csrf_token": tok,
            },
        )
        assert r.status_code == 400
        print("[OK] invalid expiry date rejected")

        # 6. list page shows client, share link and QR
        bob = client.post(
            f"/inbounds/{inbound_id}/clients",
            data={
                "email": "bob", "uuid": "", "flow": "", "total_gb": "5",
                "ip_limit": "1", "expire_at": "2029-12-31", "enable": "on",
                "csrf_token": tok,
            },
            follow_redirects=False,
        )
        assert bob.status_code == 302
        html = client.get(f"/inbounds/{inbound_id}/clients").text
        assert "alice" in html and "bob" in html
        assert "data:image/svg+xml;base64," in html  # QR code present
        assert re.search(r"vless://[0-9a-f-]{36}@[\w\.\-:<>]+", html), "vless link missing"
        print("[OK] list page shows share links + QR codes")

        # 7. edit bob: change total and flow
        with SessionLocal() as db:
            bob_row = crud.get_client_by_email(db, "bob")
            bob_id = bob_row.id
        r = client.post(
            f"/inbounds/{inbound_id}/clients/{bob_id}",
            data={
                "email": "bob", "uuid": "", "flow": "xtls-rprx-vision",
                "total_gb": "99", "ip_limit": "1", "expire_at": "2029-12-31",
                "enable": "on", "csrf_token": tok,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        with SessionLocal() as db:
            bob_row = crud.get_client(db, inbound_id, bob_id)
            assert bob_row.total_gb == 99 and bob_row.flow == "xtls-rprx-vision"
        print("[OK] client edit persists")

        # 8. toggle disable bob
        r = client.post(
            f"/inbounds/{inbound_id}/clients/{bob_id}/toggle",
            data={"csrf_token": tok}, follow_redirects=False,
        )
        assert r.status_code == 302
        with SessionLocal() as db:
            assert crud.get_client(db, inbound_id, bob_id).enable is False
        print("[OK] client toggle works")

        # 9. reset traffic zeroes counters
        r = client.post(
            f"/inbounds/{inbound_id}/clients/{bob_id}/reset-traffic",
            data={"csrf_token": tok}, follow_redirects=False,
        )
        assert r.status_code == 302
        print("[OK] reset traffic works")

        # 10. delete bob
        r = client.post(
            f"/inbounds/{inbound_id}/clients/{bob_id}/delete",
            data={"csrf_token": tok}, follow_redirects=False,
        )
        assert r.status_code == 302
        with SessionLocal() as db:
            assert crud.get_client(db, inbound_id, bob_id) is None
        print("[OK] client delete works")

        # 11. csfr violation blocked
        r = client.post(
            f"/inbounds/{inbound_id}/clients", data={"email": "evil", "csrf_token": "WRONG"},
        )
        html = client.get(f"/inbounds/{inbound_id}/clients").text
        assert "evil" not in html
        print("[OK] CSRF mismatch blocked on client create")

    # 12. config generation includes clients (enabled only)
    with SessionLocal() as db:
        import app.xray_gen as xray_gen

        rows = crud.list_active_inbounds(db)
        text = xray_gen.generate_config_text(rows)
    config = json.loads(text)
    vless_in = next(i for i in config["inbounds"] if i["protocol"] == "vless")
    emails = {c["email"] for c in vless_in["settings"]["clients"]}
    assert emails == {"alice"}, emails
    print("[OK] config.json transports enabled clients only")

    # 13. share link builders: vmess (base64 json), trojan, ss, reality
    import app.share_links as sl

    with SessionLocal() as db:
        inbound = crud.get_inbound(db, inbound_id)
        alice = crud.get_client_by_email(db, "alice")

        link = sl.build_share_link(inbound, alice, "srv.example.com:8080")
        assert link.startswith("vless://")
        assert "#vless-main%20-%20alice" in link

        # vmess: fabricate inbound/client to test
        vmess_in = SimpleNamespace(
            id=2, remark="vm", listen="0.0.0.0", port=101, protocol="vmess",
            transport="tcp", security="none", sniffing=True, settings={},
        )
        vmess_cl = SimpleNamespace(id=1, email="u1", uuid=" UID-VALUE", flow="")
        vmess_link = sl.build_share_link(vmess_in, vmess_cl, "h1")
        assert vmess_link.startswith("vmess://")
        payload = json.loads(base64.b64decode(vmess_link[8:]))
        assert payload["port"] == "101" and payload["add"] == "h1"

        trojan_in = SimpleNamespace(
            id=3, remark="tr", listen="0.0.0.0", port=102, protocol="trojan",
            transport="tcp", security="tls", sniffing=True, settings={},
        )
        trojan_cl = SimpleNamespace(id=2, email="u2", uuid="PASS123", flow="")
        trojan_link = sl.build_share_link(trojan_in, trojan_cl, "h2")
        assert trojan_link.startswith("trojan://PASS123@h2:102?")

        ss_in = SimpleNamespace(
            id=4, remark="ss-a", listen="0.0.0.0", port=103, protocol="shadowsocks",
            transport="tcp", security="none", sniffing=True,
            settings={"ss_method": "aes-256-gcm", "ss_password": "StrongPass123"},
        )
        ss_link = sl.build_inbound_ss_link(ss_in, "h3")
        assert ss_link.startswith("ss://")
        info = ss_link[5:].split("@")[0]
        assert base64.urlsafe_b64decode(info + "=" * (-len(info) % 4)) == b"aes-256-gcm:StrongPass123"
        print("[OK] vmess / trojan / ss share links decode correctly")

        # reality link includes pbk / sid / sni
        reality_in = SimpleNamespace(
            id=5, remark="re", listen="0.0.0.0", port=104, protocol="vless",
            transport="tcp", security="reality", sniffing=True,
            settings={
                "reality_dest": "www.example.com:443",
                "reality_server_names": ["www.example.com"],
                "reality_public_key": "PUBLICKEY42",
                "reality_short_ids": ["ab12"],
            },
        )
        reality_link = sl.build_share_link(reality_in, alice, "server.io")
        assert "pbk=PUBLICKEY42" in reality_link
        assert "sid=ab12" in reality_link
        assert "sni=www.example.com" in reality_link
        assert "@www.example.com:104?" in reality_link
        print("[OK] reality share link contains pbk/sid/sni")

        # ws + grpc params
        ws_in = SimpleNamespace(
            id=6, remark="ws-in", listen="0.0.0.0", port=105, protocol="vless",
            transport="ws", security="none", sniffing=True,
            settings={"ws_path": "/mypath"},
        )
        ws_link = sl.build_share_link(ws_in, alice, "h5")
        assert "type=ws" in ws_link and "path=/mypath" in ws_link
        grpc_in = SimpleNamespace(
            id=7, remark="grpc-in", listen="0.0.0.0", port=106, protocol="vless",
            transport="grpc", security="none", sniffing=True,
            settings={"grpc_service_name": "svc"},
        )
        grpc_link = sl.build_share_link(grpc_in, alice, "h6")
        assert "type=grpc" in grpc_link and "serviceName=svc" in grpc_link
        print("[OK] ws / grpc share link params correct")

    # 14. QR data uri decodes to svg
    data_uri = sl.make_qr_svg_data_uri("vless://")
    assert data_uri.startswith("data:image/svg+xml;base64,")
    decoded = base64.b64decode(data_uri.split(",", 1)[1])
    assert b"<svg" in decoded or b"<SVG" in decoded or b"<?xml" in decoded
    print("[OK] QR SVG data URI valid")

    print()
    print("ALL PHASE 3 TESTS PASSED")


if __name__ == "__main__":
    main()
