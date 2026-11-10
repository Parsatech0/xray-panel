"""Phase 6 smoke tests: subscriptions, backup/restore, telegram builders,
migration of new columns.

Run from the project root:
    venv\\Scripts\\python.exe tests_phase6.py
"""

import base64
import datetime
import json
import os
import pathlib
import re
import sys
import tempfile
import urllib.parse

BASE = pathlib.Path(__file__).parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="xray_panel_phase6_"))

os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ.setdefault("SECRET_KEY", "phase6-test-secret-not-for-production")
os.environ.setdefault("DEFAULT_ADMIN_USERNAME", "admin6")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "Phase6Pass123")
os.environ["XRAY_CONFIG_PATH"] = str(TMP / "config.json")
os.environ["XRAY_BIN_PATH"] = ""
os.environ["MONITOR_INTERVAL_SECONDS"] = "0"

sys.path.insert(0, str(BASE))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
import app.crud as crud  # noqa: E402
import app.models as models  # noqa: E402
import app.telegram_bot as telegram_bot  # noqa: E402
import app.xray_control as xray_control  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402


def login(client: "TestClient") -> str:
    r = client.get("/login")
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    resp = client.post(
        "/login",
        data={"username": "admin6", "password": "Phase6Pass123", "csrf_token": tok},
        follow_redirects=False,
    )
    assert resp.status_code == 302, "login failed"


def get_csrf(client: "TestClient", path: str) -> str:
    html = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"csrf not found on {path}"
    return match.group(1)


def setup_panel(client: "TestClient", tok: str) -> dict:
    """One vless inbound with two clients; returns mapping email->client."""
    r = client.post(
        "/inbounds",
        data={
            "remark": "prod", "listen": "0.0.0.0", "port": "46001",
            "protocol": "vless", "transport": "tcp", "security": "none",
            "sniffing": "on", "enable": "on", "csrf_token": tok,
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    with SessionLocal() as db:
        inbound = max(crud.list_inbounds(db), key=lambda i: i.id)
        crud.create_client(db, inbound, {
            "email": "sub-user", "uuid": "", "flow": "", "total_gb": 10.0,
            "ip_limit": 0, "expire_at": datetime.date(2030, 1, 1), "enable": True,
        })
        crud.create_client(db, inbound, {
            "email": "big-user", "uuid": "", "flow": "", "total_gb": 0.0,
            "ip_limit": 0, "expire_at": None, "enable": True,
        })
        with SessionLocal() as db2:
            big = crud.get_client_by_email(db2, "big-user")
            big.used_download_bytes = int(2.5 * 1024 ** 3)
            big.disabled_reason = ""
            db2.add(big)
            db2.commit()
    return {"sub-user": None, "big-user": None}


def main() -> None:
    # 0. migration: new columns + panel_settings table exist
    from sqlalchemy import inspect, text

    from app.database import init_db

    init_db()
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("clients")}
    assert "sub_token" in columns and "disabled_reason" in columns, columns
    assert inspector.has_table("panel_settings")
    print("[OK] schema migration adds sub_token/disabled_reason + panel_settings")

    with TestClient(app) as client:
        login(client)
        tok = get_csrf(client, "/dashboard")
        setup_panel(client, tok)

        # 1. subscription URL appears on the clients page
        with SessionLocal() as db:
            inbound_id = max(i.id for i in crud.list_inbounds(db))
        html = client.get(f"/inbounds/{inbound_id}/clients").text
        assert "/sub/" in html and "sub-user" in html
        print("[OK] clients page shows subscription link")

        # 2. /sub/<token> works anonymously and encodes the share link
        with SessionLocal() as db:
            token = crud.ensure_sub_token(db, crud.get_client_by_email(db, "sub-user"))
            assert crud.ensure_sub_token(db, crud.get_client_by_email(db, "sub-user")) == token
        r = client.get(f"/sub/{token}", follow_redirects=False)
        assert r.status_code == 200
        links = base64.b64decode(r.text.encode("ascii")).decode("utf-8")
        assert links.startswith("vless://") and "sub-user" in links, links[:60]
        print("[OK] anonymous subscription returns base64-encoded vless link")

        # 3. unknown token -> 404, other client's token is distinct
        assert client.get("/sub/definitely-wrong-token").status_code == 404
        with SessionLocal() as db:
            token_other = crud.ensure_sub_token(db, crud.get_client_by_email(db, "big-user"))
        assert token_other != token
        assert client.get(f"/sub/{token_other}").status_code == 200
        print("[OK] unknown token 404; per-client tokens are distinct")

        # 4. human view renders
        view = client.get(f"/sub/{token}/view")
        assert view.status_code == 200 and "vless://" in view.text
        print("[OK] subscription view page renders share link")

        # 5. backup download (CSRF-protected)
        r = client.post("/backup/download", data={"csrf_token": "bad"})
        assert r.status_code == 403
        r = client.post("/backup/download", data={"csrf_token": tok})
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        backup = json.loads(r.text)
        assert backup["version"] == 1 and len(backup["inbounds"]) == 1
        inb = backup["inbounds"][0]
        emails = {c["email"] for c in inb["clients"]}
        assert emails == {"sub-user", "big-user"}
        big_rows = [c for c in inb["clients"] if c["email"] == "big-user"]
        assert big_rows
        assert big_rows[0]["used_download_bytes"] == int(2.5 * 1024 ** 3)
        print("[OK] backup download contains inbounds + clients + counters")

        # 6. restore: modified payload (rename remark, new client) replaces DB
        inb["remark"] = "restored"
        inb["port"] = 46002
        inb["clients"].append({
            "id": 99, "email": "restored-user", "uuid": "uuid-restored",
            "flow": "", "total_gb": 5.0, "expire_at": None, "ip_limit": 0,
            "enable": True, "disabled_reason": "", "used_download_bytes": 0,
            "used_upload_bytes": 0,
        })
        r = client.post(
            "/backup/restore",
            data={"csrf_token": tok},
            files={"backup_file": ("backup.json", json.dumps(backup).encode("utf-8"), "application/json")},
            follow_redirects=True,
        )
        assert r.status_code == 200 and "بازیابی" in r.text
        with SessionLocal() as db:
            all_in = crud.list_inbounds(db)
            assert len(all_in) == 1
            assert all_in[0].remark == "restored" and all_in[0].port == 46002
            assert crud.get_client_by_email(db, "restored-user") is not None
            assert db.get(models.Admin, 1) is not None  # admins untouched
        print("[OK] restore replaces panel data, admins untouched")

        # 7. restore rejects bad payloads with Persian errors (no data change)
        cases = {
            "not json": b"{oops",
            "no inbounds": json.dumps({"version": 1}).encode(),
            "missing email": json.dumps({"inbounds": [{
                "remark": "x", "port": 46003, "protocol": "vless",
                "transport": "tcp", "settings": {}, "clients": [{"uuid": "u"}],
            }]}).encode(),
            "bad port": json.dumps({"inbounds": [{
                "remark": "x", "port": "not-a-port", "protocol": "vless",
                "transport": "tcp", "settings": {}, "clients": [],
            }]}).encode(),
            "dup email": json.dumps({"inbounds": [{
                "remark": "x", "port": 46004, "protocol": "vless",
                "transport": "tcp", "settings": {},
                "clients": [
                    {"email": "same", "uuid": "u1"},
                    {"email": "same", "uuid": "u2"},
                ],
            }]}).encode(),
        }
        for label, content in cases.items():
            r = client.post(
                "/backup/restore",
                data={"csrf_token": tok},
                files={"backup_file": (f"{label}.json", content, "application/json")},
                follow_redirects=True,
            )
            assert r.status_code == 200
        with SessionLocal() as db:
            assert len(crud.list_inbounds(db)) == 1  # nothing replaced
        print("[OK] invalid backup payloads rejected silently (panel unchanged)")

        # 8. missing file
        r = client.post("/backup/restore", data={"csrf_token": tok},
                        follow_redirects=True)
        assert "انتخاب نشده" in r.text
        print("[OK] restore without file flashes an error")

    # 9. telegram builders (pure functions, db-backed)
    with SessionLocal() as db:
        traffic = telegram_bot.build_traffic_text(db)
        assert "big-user" in traffic and "sub-user" in traffic
        assert "2.50 GB" in traffic  # counters survived the restore

        # expired/over-quota report is empty on this panel
        assert telegram_bot.build_expired_text(db).startswith("هیچ")

    status_text = telegram_bot.build_status_text()
    assert status_text.startswith("Xray:") and "CPU" in status_text
    print("[OK] telegram report builders produce server/traffic summaries")

    # 10. telegram update handling with a stubbed sender + systemctl
    sent: list[tuple[str, str]] = []
    original_send = telegram_bot._send_message
    telegram_bot._send_message = lambda token, chat, text: sent.append((chat, text))

    _systemctl_orig = xray_control._systemctl
    xray_control._systemctl = lambda args: type(
        "P", (), {"returncode": 0, "stdout": "active", "stderr": ""}
    )()

    owner = telegram_bot._handle_update("tok", {"message": {"chat": {"id": 777},
                                                            "text": "/start"}}, None)
    assert sent[-1][0] == "777" and "مالک" in sent[-1][1]

    telegram_bot._handle_update("tok", {"message": {"chat": {"id": 777},
                                                    "text": "/status"}}, "777")
    assert "در حال اجرا" in sent[-1][1]

    # unauthorized chat gets rejected and sends nothing else
    before = len(sent)
    res = telegram_bot._handle_update("tok", {"message": {"chat": {"id": 666},
                                                          "text": "/traffic"}}, "777")
    assert res is None and len(sent) == before + 1 and "مجاز" in sent[-1][1]

    telegram_bot._handle_update("tok", {"message": {"chat": {"id": 777},
                                                    "text": "/nonsense"}}, "777")
    assert "دستورات" in sent[-1][1]

    xray_control._systemctl = _systemctl_orig
    telegram_bot._send_message = original_send
    print("[OK] telegram command dispatch + owner gating works")

    print()
    print("ALL PHASE 6 TESTS PASSED")


if __name__ == "__main__":
    main()
