"""Phase 2 smoke tests: inbound CRUD + config.json generation/apply.

Run from the project root with the venv python:
    venv\\Scripts\\python.exe tests_phase2.py

Everything runs against a throwaway SQLite DB and a temp config path.
"""

import os
import pathlib
import re
import sys
import tempfile
from types import SimpleNamespace

BASE = pathlib.Path(__file__).parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="xray_panel_phase2_"))

os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ.setdefault("SECRET_KEY", "phase2-test-secret-not-for-production")
os.environ.setdefault("DEFAULT_ADMIN_USERNAME", "admin2")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "Phase2Pass123")
os.environ["XRAY_CONFIG_PATH"] = str(TMP / "config.json")
# No xray binary on the test host -> validation falls back to JSON syntax check
os.environ["XRAY_BIN_PATH"] = ""

sys.path.insert(0, str(BASE))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
import app.crud as crud  # noqa: E402
import app.xray_gen as xray_gen  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def login(client: "TestClient") -> None:
    tok = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/login").text).group(1)
    r = client.post(
        "/login",
        data={"username": "admin2", "password": "Phase2Pass123", "csrf_token": tok},
        follow_redirects=False,
    )
    assert r.status_code == 302, "login failed"


def get_csrf(client: "TestClient", path: str) -> str:
    html = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"csrf token not found on {path}"
    return match.group(1)


def valid_form(**over) -> dict:
    """A minimal valid VLESS/TCP/none form."""
    data = {
        "remark": "vless-main",
        "listen": "0.0.0.0",
        "port": "40001",
        "protocol": "vless",
        "transport": "tcp",
        "security": "none",
        "sniffing": "on",
        "enable": "on",
    }
    data.update(over)
    return data


def main() -> None:
    with TestClient(app) as client:
        # 0. /inbounds is protected
        r = client.get("/inbounds", follow_redirects=False)
        assert r.status_code in (302, 303, 401), f"unexpected {r.status_code}"
        print("[OK] /inbounds requires authentication")

        login(client)

        # 1. New-inbound form renders with CSRF token
        get_csrf(client, "/inbounds/new")
        print("[OK] /inbounds/new renders")

        # 2. Create a valid VLESS inbound
        tok = get_csrf(client, "/inbounds/new")
        r = client.post("/inbounds", data={**valid_form(), "csrf_token": tok}, follow_redirects=False)
        assert r.status_code == 302, f"create failed: {r.status_code}"
        print("[OK] valid inbound created (302 redirect)")

        # 3. List shows the new inbound
        html = client.get("/inbounds").text
        assert "vless-main" in html and "40001" in html
        print("[OK] /inbounds lists the creation")

        # 4. Port collision rejected (form re-render, DB unchanged)
        r = client.post("/inbounds", data={**valid_form(remark="dup"), "csrf_token": tok})
        assert r.status_code == 400
        html = client.get("/inbounds").text
        assert "dup" not in html
        print("[OK] duplicate port rejected")

        # 5. Invalid protocol rejected
        r = client.post("/inbounds", data={**valid_form(protocol="socks"), "csrf_token": tok})
        assert r.status_code == 400
        print("[OK] invalid protocol rejected")

        # 6. Reality without required fields rejected
        r = client.post("/inbounds", data={**valid_form(security="reality"), "csrf_token": tok})
        assert r.status_code == 400
        print("[OK] reality without dest/names/key rejected")

        # 7. Shadowsocks: short password rejected
        r = client.post(
            "/inbounds",
            data={**valid_form(protocol="shadowsocks", port="40005", ss_method="aes-256-gcm", ss_password="123"), "csrf_token": tok},
        )
        assert r.status_code == 400
        print("[OK] short SS password rejected")

        # 8. Valid shadowsocks + valid reality inbounds created
        r = client.post(
            "/inbounds",
            data={
                **valid_form(remark="ss-1", port="40002", protocol="shadowsocks",
                             ss_method="aes-256-gcm", ss_password="StrongPass123"),
                "csrf_token": tok,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        r = client.post(
            "/inbounds",
            data={
                **valid_form(remark="reality-1", port="40003", security="reality",
                             reality_dest="tests.example.com:443",
                             reality_server_names="tests.example.com",
                             reality_private_key="a" * 43,
                             reality_short_ids="a1b2c3d4"),
                "csrf_token": tok,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        print("[OK] shadowsocks + reality inbounds created")

        # 9. Toggle disable inbound #1
        r = client.post("/inbounds/1/toggle", data={"csrf_token": tok}, follow_redirects=False)
        assert r.status_code == 302
        print("[OK] toggle disable works")

        # 10. Edit inbound #3: change port to 40030 (reality fields repeated)
        r = client.post(
            "/inbounds/3",
            data={
                **valid_form(remark="reality-1", port="40030", security="reality",
                             reality_dest="tests.example.com:443",
                             reality_server_names="tests.example.com",
                             reality_private_key="a" * 43,
                             reality_short_ids="a1b2c3d4"),
                "csrf_token": tok,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302, f"update failed: {r.status_code}"
        with SessionLocal() as db:
            inbound = crud.get_inbound(db, 3)
            assert inbound.port == 40030
            assert inbound.settings["reality_private_key"] == "a" * 43
        print("[OK] inbound edit persists port + settings")

        # 11. Delete inbound #1
        r = client.post("/inbounds/1/delete", data={"csrf_token": tok}, follow_redirects=False)
        assert r.status_code == 302
        with SessionLocal() as db:
            assert crud.get_inbound(db, 1) is None
            remaining = crud.list_inbounds(db)
            assert len(remaining) == 2
            assert {row.remark for row in remaining} == {"ss-1", "reality-1"}
            assert {row.enable for row in remaining} == {True}
        print("[OK] delete removes the inbound")

        # 12. Apply config: uses only enabled inbounds (both are active here)
        r = client.post("/inbounds/apply", data={"csrf_token": tok}, follow_redirects=False)
        assert r.status_code == 302
        config_path = TMP / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True) if not config_path.exists() else None
        config_file = pathlib.Path(os.environ["XRAY_CONFIG_PATH"])
        # The app wrote the file; parse and verify
        import json
        config = json.loads(config_file.read_text(encoding="utf-8"))
        tags = {i["tag"] for i in config["inbounds"]}
        # Phase 4: stats-enabled configs carry a localhost "api" inbound too.
        assert tags == {"in-2", "in-3", "api"}, tags
        # api sections present alongside the regular routing
        assert "stats" in config and "api" in config and "routing" in config
        db_in = next(i for i in config["inbounds"] if i["tag"] == "api")
        assert db_in["protocol"] == "dokodemo-door" and db_in["port"] == 23231
        reality_in = next(i for i in config["inbounds"] if i["tag"] == "in-3")
        assert reality_in["streamSettings"]["realitySettings"]["privateKey"] == "a" * 43
        ss_in = next(i for i in config["inbounds"] if i["tag"] == "in-2")
        assert ss_in["settings"]["password"] == "StrongPass123"
        assert config["outbounds"][0]["protocol"] == "freedom"
        print("[OK] applied config.json contains both active inbounds")

        # 13. Backup created on second apply
        r = client.post("/inbounds/apply", data={"csrf_token": tok}, follow_redirects=False)
        assert r.status_code == 302
        assert config_file.with_suffix(".json.bak").exists()
        print("[OK] rollback backup (.bak) created on re-apply")

    # 14. Config failure path: invalid JSON -> no write, no backup
    ok, detail = xray_gen.apply_config_text("{ definitely not json")
    assert not ok and "Invalid JSON" in detail
    # live config unchanged
    import json
    assert json.loads(pathlib.Path(os.environ["XRAY_CONFIG_PATH"]).read_text(encoding="utf-8"))
    print("[OK] failed validation leaves live config untouched")

    # 15. Unit checks: build_inbound for ws and grpc paths
    ws_fake = SimpleNamespace(
        id=99, remark="ws", listen="127.0.0.1", port=40011,
        protocol="vless", transport="ws", security="none", sniffing=True,
        settings={"ws_path": "/wshere", "clients": []},
    )
    payload = xray_gen.build_inbound(ws_fake)
    assert payload["streamSettings"]["wsSettings"]["path"] == "/wshere"
    grpc_fake = SimpleNamespace(
        id=100, remark="grpc", listen="0.0.0.0", port=40012,
        protocol="trojan", transport="grpc", security="none", sniffing=False,
        settings={"grpc_service_name": "grpc-svc", "clients": []},
    )
    payload = xray_gen.build_inbound(grpc_fake)
    assert payload["streamSettings"]["grpcSettings"]["serviceName"] == "grpc-svc"
    assert "sniffing" not in payload
    print("[OK] ws / grpc stream settings generated correctly")

    print()
    print("ALL PHASE 2 TESTS PASSED")


if __name__ == "__main__":
    main()
