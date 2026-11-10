"""Phase 5 smoke tests: dashboard aggregate cards, system stats, xray
control routes with a stubbed systemctl layer.

Run from the project root:
    venv\\Scripts\\python.exe tests_phase5.py
"""

import os
import pathlib
import re
import sys
import tempfile

BASE = pathlib.Path(__file__).parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="xray_panel_phase5_"))

os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ.setdefault("SECRET_KEY", "phase5-test-secret-not-for-production")
os.environ.setdefault("DEFAULT_ADMIN_USERNAME", "admin5")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "Phase5Pass123")
os.environ["XRAY_CONFIG_PATH"] = str(TMP / "config.json")
os.environ["XRAY_BIN_PATH"] = ""
os.environ["MONITOR_INTERVAL_SECONDS"] = "0"

sys.path.insert(0, str(BASE))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
import app.crud as crud  # noqa: E402
import app.system_stats as system_stats  # noqa: E402
import app.xray_control as xray_control  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def login(client: "TestClient") -> str:
    r = client.get("/login")
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    resp = client.post(
        "/login",
        data={"username": "admin5", "password": "Phase5Pass123", "csrf_token": tok},
        follow_redirects=False,
    )
    assert resp.status_code == 302, "login failed"


def get_csrf(client: "TestClient", path: str) -> str:
    html = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"csrf not found on {path}"
    return match.group(1)


def make_inbound(client: "TestClient", tok: str, remark: str, port: str) -> int:
    r = client.post(
        "/inbounds",
        data={
            "remark": remark, "listen": "0.0.0.0", "port": port,
            "protocol": "vless", "transport": "tcp", "security": "none",
            "sniffing": "on", "enable": "on", "csrf_token": tok,
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    with SessionLocal() as db:
        return max(row.id for row in crud.list_inbounds(db))


def main() -> None:
    # 0. psutil-based snapshot works standalone
    stats = system_stats.get_system_stats()
    assert stats["ok"] is True, "system stats must collect on Windows/Linux"
    for key in ("cpu_percent", "ram_percent", "disk_percent", "uptime_text", "boot_text"):
        assert key in stats
    assert 0 <= stats["cpu_percent"] <= 100
    assert stats["ram_total_gb"] > 0
    print("[OK] system_stats snapshot collects cpu/ram/disk/uptime")

    # systemctl is unavailable on this dev host -> graceful degradation
    state, detail = xray_control.xray_status()
    assert state == "unavailable", state
    print("[OK] xray_status degrades gracefully without systemctl")

    with TestClient(app) as client:
        # 1. dashboard is auth-protected
        r = client.get("/dashboard", follow_redirects=False)
        assert r.status_code in (302, 303, 401, 403)
        print("[OK] /dashboard requires authentication")

        login(client)
        tok = get_csrf(client, "/dashboard")

        # xray control route also auth-protected
        r = client.post("/system/xray/restart", data={"csrf_token": "bad"},
                        follow_redirects=False)
        assert r.status_code in (302, 303, 401, 403)
        print("[OK] /system/xray/* auth-protected")

        # 2. dashboard renders stats cards + xray status badge
        html = client.get("/dashboard").text
        assert "وضعیت سرور" in html
        assert 'id="stat-cpu"' in html
        assert 'id="stat-ram"' in html
        assert 'id="stat-disk"' in html
        assert 'id="xray-state"' in html
        assert "غیرقابل دسترسی" in html  # unavailable badge on dev host
        assert 'action="' in html and "restart" in html  # control buttons present
        print("[OK] dashboard renders system + xray status cards")

        # 3. aggregates start at zero
        assert re.search(r'id="stat-users">\s*0\s*<', html)
        assert re.search(r'id="stat-inbounds">\s*0\s*<', html)
        assert re.search(r'id="stat-traffic-total">\s*0\.?0?\s*<', html)
        print("[OK] dashboard aggregates are zero on a fresh panel")

        # 4. counts update after creating inbounds/clients
        inb = make_inbound(client, tok, "dash-in", "45001")
        with SessionLocal() as db:
            inb_row = crud.get_inbound(db, inb)
            crud.create_client(db, inb_row, {
                "email": "u1", "uuid": "", "flow": "", "total_gb": 1.0,
                "ip_limit": 0, "expire_at": None, "enable": True,
            })
            crud.create_client(db, inb_row, {
                "email": "u2", "uuid": "", "flow": "", "total_gb": 0.0,
                "ip_limit": 0, "expire_at": None, "enable": True,
            })
            # simulate a disabled-on-traffic-limit client
            u3 = crud.create_client(db, inb_row, {
                "email": "u3", "uuid": "", "flow": "", "total_gb": 2.0,
                "ip_limit": 0, "expire_at": None, "enable": False,
            })
            u3.disabled_reason = "traffic_limit"
            db.add(u3)
            db.commit()

        # simulate usage: 0.5GB of u1's traffic to check the GB total
        with SessionLocal() as db:
            u1 = crud.get_client_by_email(db, "u1")
            u1.used_download_bytes = int(0.25 * 1024 ** 3)
            u1.used_upload_bytes = int(0.25 * 1024 ** 3)
            db.commit()

        html = client.get("/dashboard").text
        assert re.search(r'id="stat-inbounds">\s*1\s*<', html)
        assert re.search(r'id="stat-users">\s*3\s*<', html)
        assert re.search(r'id="stat-users">\s*3\s*<', html) and "2 فعال" in html
        assert re.search(r'id="stat-disabled-traffic">\s*1\s*<', html)
        assert re.search(r'id="stat-traffic-total">\s*0\.5\s*<', html)
        print("[OK] dashboard counts reflect inbound/client/usage state")

        # 5. control route: CSRF-protected before anything else
        r = client.post("/system/xray/start", data={"csrf_token": "WRONG"},
                        follow_redirects=False)
        assert r.status_code in (302, 303)
        html = client.get("/dashboard").text
        assert "خطای CSRF" in html
        print("[OK] xray control rejects bad CSRF token")

        # 6. control verbs hit the whitelisted mapper only (stub systemctl)
        calls: list[list[str]] = []
        original = xray_control._systemctl
        xray_control._systemctl = lambda args: calls.append(args) or type(
            "P", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()

        for verb in ("start", "restart", "stop"):
            r = client.post(
                f"/system/xray/{verb}", data={"csrf_token": tok},
                follow_redirects=False,
            )
            assert r.status_code in (200, 302, 303)
        # verbs translate 1:1; is-active entries from the rendered dashboard
        # context may interleave, service name always comes from settings
        verbs_seen = [c[0] for c in calls if c[0] in ("start", "restart", "stop")]
        assert verbs_seen == ["start", "restart", "stop"], calls
        assert all(c[-1] == xray_control.settings.XRAY_SERVICE_NAME for c in calls), calls
        print("[OK] control verbs map 1:1 to whitelisted systemctl calls")

        # 7. unknown action is rejected without touching systemctl
        calls.clear()
        r = client.post("/system/xray/hax", data={"csrf_token": tok},
                        follow_redirects=False)
        assert r.status_code in (200, 302, 303)
        assert calls == []
        html = client.get("/dashboard").text
        assert "نامعتبر" in html
        print("[OK] unknown action rejected without calling systemctl")

        # 8. systemctl failure surfaces as a Persian error flash
        xray_control._systemctl = lambda args: type(
            "P", (), {"returncode": 5, "stdout": "", "stderr": "unit not found"}
        )()
        r = client.post("/system/xray/start", data={"csrf_token": tok},
                        follow_redirects=False)
        assert r.status_code in (200, 302, 303)
        assert "خطا" in r.text and "start" in r.text
        xray_control._systemctl = original
        print("[OK] systemctl failure reported with error flash")

        # 9. success flash after a real-looking start
        xray_control._systemctl = lambda args: type(
            "P", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()
        r = client.post("/system/xray/restart", data={"csrf_token": tok},
                        follow_redirects=False)
        assert r.status_code in (200, 302, 303)
        assert "restart" in r.text and "اجرا شد" in r.text
        xray_control._systemctl = original
        print("[OK] successful action flashes Persian progress message")

        xray_control._systemctl = original

    print()
    print("ALL PHASE 5 TESTS PASSED")


if __name__ == "__main__":
    main()
