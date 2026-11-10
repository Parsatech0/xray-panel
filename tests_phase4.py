"""Phase 4 smoke tests: protobuf codec, stats parsing, config api sections,
global email uniqueness, traffic refresh + auto-disable, monitor endpoint.

Run from the project root:
    venv\\Scripts\\python.exe tests_phase4.py
"""

import base64
import datetime
import json
import os
import pathlib
import re
import struct
import sys
import tempfile

BASE = pathlib.Path(__file__).parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="xray_panel_phase4_"))

os.environ["DATABASE_URL"] = f"sqlite:///{(TMP / 'test.db').as_posix()}"
os.environ.setdefault("SECRET_KEY", "phase4-test-secret-not-for-production")
os.environ.setdefault("DEFAULT_ADMIN_USERNAME", "admin4")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "Phase4Pass123")
os.environ["XRAY_CONFIG_PATH"] = str(TMP / "config.json")
os.environ["XRAY_BIN_PATH"] = ""
os.environ["MONITOR_INTERVAL_SECONDS"] = "0"  # no background loop in tests

sys.path.insert(0, str(BASE))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
import app.crud as crud  # noqa: E402
import app.monitor as monitor  # noqa: E402
import app.xray_gen as xray_gen  # noqa: E402
import app.xray_stats as xray_stats  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402


def login(client: "TestClient") -> str:
    r = client.get("/login")
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    resp = client.post(
        "/login",
        data={"username": "admin4", "password": "Phase4Pass123", "csrf_token": tok},
        follow_redirects=False,
    )
    assert resp.status_code == 302, "login failed"


def get_csrf(client: "TestClient", path: str) -> str:
    html = client.get(path).text
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"csrf token not found on {path}"
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
    assert r.status_code == 302, f"inbound create failed: {r.status_code}"
    with SessionLocal() as db:
        return max(row.id for row in crud.list_inbounds(db))


def make_client(inbound, email: str, total_gb: float = 0.0, expire=None) -> None:
    with SessionLocal() as db:
        inb = crud.get_inbound(db, inbound.id)
        crud.create_client(db, inb, {
            "email": email, "uuid": "", "flow": "",
            "total_gb": total_gb, "ip_limit": 0, "expire_at": expire, "enable": True,
        })


def main() -> None:
    # ------------------------------------------------------------------
    # 1. protobuf codec round-trips (no server needed)
    # ------------------------------------------------------------------
    req = xray_stats.build_query_stats_request("user>>>", reset=True)
    # decode the request back through raw parsing
    assert req.startswith(b"\x0a\x07user>>>")  # field1 len-delim "user>>>"
    assert req.endswith(b"\x10\x01")  # field2 varint True
    assert xray_stats.grpc_frame(req) == b"\x00" + struct.pack(">I", len(req)) + req
    print("[OK] QueryStatsRequest encodes with correct field tags")

    for value in (0, 1, 127, 128, 300, 2 ** 40):
        enc = xray_stats.encode_varint(value)
        dec, pos = xray_stats._read_varint(enc, 0)
        assert dec == value and pos == len(enc)
    print("[OK] varint encode/decode round-trip for boundary values")

    # Build a fabricated response: QueryStatsResponse{ stat{ name, value } }
    def stat_msg(name: str, value: int) -> bytes:
        body = xray_stats.encode_string_field(1, name)
        body += xray_stats.encode_varint((2 << 3) | 0) + xray_stats.encode_varint(value)
        return body

    resp = xray_stats.encode_bytes_field(1, stat_msg("user>>>alice>>>uplink", 1234567))
    resp += xray_stats.encode_bytes_field(1, stat_msg("user>>>alice>>>downlink", 0))
    resp += xray_stats.encode_bytes_field(1, stat_msg("system>>>uptime", 99))
    out = xray_stats._finish(
        xray_stats.grpc_frame(resp), http_status=200, grpc_status=0
    )
    assert out["user>>>alice>>>uplink"] == 1234567, out
    assert out["user>>>alice>>>downlink"] == 0
    assert out["system>>>uptime"] == 99
    print("[OK] fabricated QueryStatsResponse decodes (stat + system keys)")

    # 2. user counter extraction
    users = monitor.parse_user_counters({
        "user>>>alice>>>uplink": 100,
        "user>>>alice>>>downlink": 200,
        "user>>>bob>>>uplink": 7,
        "system>>>uptime": 8888,
        "inbound>>>in-1>>>uplink": 55,
        "user>>>carol>>>weird": 1,
    })
    assert users == {
        "alice": {"upload": 100, "download": 200},
        "bob": {"upload": 7, "download": 0},
    }, users
    print("[OK] parse_user_counters filters to user uplink/downlink only")

    # 3. config generation: api inbound + stats sections when enabled
    root = xray_gen.build_config([])
    tags = {i["tag"] for i in root["inbounds"]}
    assert "api" in tags
    assert root["stats"] == {}
    assert root["api"] == {"tag": "api", "services": ["StatsService"]}
    assert root["policy"]["levels"]["0"]["statsUserUplink"] is True
    rule = root["routing"]["rules"][0]
    assert rule["inboundTag"] == ["api"] and rule["outboundTag"] == "api"
    api_in = next(i for i in root["inbounds"] if i["tag"] == "api")
    assert api_in["protocol"] == "dokodemo-door"
    assert api_in["listen"] == "127.0.0.1" and api_in["port"] == settings.XRAY_API_PORT
    print("[OK] config gains api/stats/policy/routing when stats enabled")

    # 4. stats can be disabled via settings (restored afterwards)
    settings.XRAY_STATS_ENABLED = False
    try:
        root2 = xray_gen.build_config([])
        assert "stats" not in root2
        assert {i["tag"] for i in root2["inbounds"]} == set()
    finally:
        settings.XRAY_STATS_ENABLED = True
    print("[OK] XRAY_STATS_ENABLED=0 removes api/stats sections")

    # ------------------------------------------------------------------
    # 5. HTTP flow: global (panel-wide) email uniqueness
    # ------------------------------------------------------------------
    with TestClient(app) as client:
        # monitor route is auth-protected
        r = client.post("/system/monitor", data={"csrf_token": "x"}, follow_redirects=False)
        assert r.status_code in (302, 303, 401, 403)
        r = client.get("/login", follow_redirects=False)
        assert r.status_code == 200
        print("[OK] /system/monitor is auth-protected")

        login(client)
        tok = get_csrf(client, "/inbounds/new")
        in_a = make_inbound(client, tok, "alpha", "42001")
        in_b = make_inbound(client, tok, "beta", "42002")

        # create same email on two different inbounds -> second rejected
        for inbound_id in (in_a, in_b):
            r = client.post(
                f"/inbounds/{inbound_id}/clients",
                data={
                    "email": "shared", "uuid": "", "flow": "", "total_gb": "0",
                    "ip_limit": "0", "expire_at": "", "enable": "on",
                    "csrf_token": tok,
                },
                follow_redirects=False,
            )
            expected = 302 if inbound_id == in_a else 400
            assert r.status_code == expected, (inbound_id, r.status_code)
        print("[OK] client email unique across the whole panel")

        # monitor with CSRF but no reachable Xray API -> Persian error flash
        r = client.post("/system/monitor", data={"csrf_token": tok}, follow_redirects=True)
        assert r.status_code == 200

        # ------------------------------------------------------------------
        # 6. refresh + auto-disable logic with a patched transport
        # ------------------------------------------------------------------
        with SessionLocal() as db:
            inb_a = crud.get_inbound(db, in_a)
            make_client(inb_a, "alice", total_gb=1.0)
            make_client(inb_a, "bob", total_gb=0.0)  # unlimited
            make_client(inb_a, "carol", total_gb=0.0,
                        expire=datetime.date.today() - datetime.timedelta(days=1))
            make_client(inb_a, "dave", total_gb=0.0,
                        expire=datetime.date.today())  # expiry day: still valid

        counters = {
            "user>>>alice>>>uplink": 300,
            "user>>>alice>>>downlink": 700,
            "user>>>bob>>>uplink": int(0.25 * 1024 ** 3),
            "user>>>bob>>>downlink": 0,
        }
        original_fetch = monitor.fetch_counters
        monitor.fetch_counters = lambda: counters

        updated, disabled = monitor.refresh_stats_and_auto_disable(SessionLocal())
        assert updated == 2, updated  # alice & bob carry counters
        # carol is already expired, so this very first sweep disables her
        assert disabled == 1
        with SessionLocal() as db:
            alice = crud.get_client(db, in_a, crud.get_client_by_email(db, "alice").id)
            assert alice.used_upload_bytes == 300
            assert alice.used_download_bytes == 700
        print("[OK] delta counters accumulate exactly into the DB")

        # second sweep: this time a 0.5GB slice (0.5GB total, under the 1GB quota)
        monitor.fetch_counters = lambda: {
            "user>>>alice>>>uplink": int(0.5 * 1024 ** 3),
            "user>>>alice>>>downlink": 700,
        }
        updated, disabled = monitor.refresh_stats_and_auto_disable(SessionLocal())
        assert disabled == 0
        with SessionLocal() as db:
            alice = crud.get_client(db, in_a, crud.get_client_by_email(db, "alice").id)
            assert alice.used_upload_bytes == 300 + int(0.5 * 1024 ** 3)
            assert alice.used_download_bytes == 1400
            bob = crud.get_client(db, in_a, crud.get_client_by_email(db, "bob").id)
            assert bob.enable is True
        print("[OK] repeated sweeps keep accumulating; unlimited bob untouched")

        # push alice over her 1GB quota (0.5 + 0.6 > 1.0 GB)
        monitor.fetch_counters = lambda: {
            "user>>>alice>>>uplink": int(0.6 * 1024 ** 3),
            "user>>>alice>>>downlink": 0,
            "user>>>bob>>>uplink": int(0.5 * 1024 ** 3),
        }
        updated, disabled = monitor.refresh_stats_and_auto_disable(SessionLocal())
        assert disabled == 1
        with SessionLocal() as db:
            alice = crud.get_client(db, in_a, crud.get_client_by_email(db, "alice").id)
            assert alice.enable is False
            assert alice.disabled_reason == monitor.REASON_TRAFFIC

            bob = crud.get_client(db, in_a, crud.get_client_by_email(db, "bob").id)
            assert bob.enable is True  # unlimited client untouched
        print("[OK] auto-disable on full quota (traffic_limit)")

        # monitor never resurrects: enforcement pass again -> still disabled
        monitor.fetch_counters = lambda: {"user>>>alice>>>uplink": 1}
        monitor.refresh_stats_and_auto_disable(SessionLocal())
        with SessionLocal() as db:
            alice = crud.get_client(db, in_a, crud.get_client_by_email(db, "alice").id)
            assert alice.enable is False and alice.disabled_reason == monitor.REASON_TRAFFIC
        print("[OK] monitor does not resurrect auto-disabled clients")

        # admin resets traffic to 0 -> still disabled (admin must toggle)
        with SessionLocal() as db:
            crud.reset_client_traffic(db, crud.get_client_by_email(db, "alice"))
        monitor.refresh_stats_and_auto_disable(SessionLocal())
        with SessionLocal() as db:
            alice = crud.get_client(db, in_a, crud.get_client_by_email(db, "alice").id)
            assert alice.enable is False
        print("[OK] resetting traffic alone does not revive the client")

        # expired carol gets disabled by the sweep; dave (expires today) not
        monitor.fetch_counters = lambda: {}
        updated, disabled = monitor.refresh_stats_and_auto_disable(SessionLocal())
        with SessionLocal() as db:
            carol = crud.get_client(db, in_a, crud.get_client_by_email(db, "carol").id)
            dave = crud.get_client(db, in_a, crud.get_client_by_email(db, "dave").id)
            assert carol.enable is False and carol.disabled_reason == monitor.REASON_EXPIRED
            assert dave.enable is True
        print("[OK] expiry auto-disable works (expiry day is still usable)")

        # bob: manual disable then huge usage -> reason stays "manual"
        with SessionLocal() as db:
            bob = crud.get_client_by_email(db, "bob")
            crud.toggle_client(db, bob)  # disable
            assert bob.disabled_reason == monitor.REASON_MANUAL
        monitor.fetch_counters = lambda: {"user>>>bob>>>uplink": int(9 * 1024 ** 3)}
        monitor.refresh_stats_and_auto_disable(SessionLocal())
        with SessionLocal() as db:
            bob = crud.get_client_by_email(db, "bob")
            assert bob.disabled_reason == monitor.REASON_MANUAL
        print("[OK] monitor keeps 'manual' disabled_reason untouched")

        # re-enable via toggle clears the reason
        with SessionLocal() as db:
            bob = crud.get_client_by_email(db, "bob")
            crud.toggle_client(db, bob)
            assert bob.disabled_reason == ""
        print("[OK] toggle re-enable clears disabled_reason")

        # 7. monitor endpoint with a patched fetch -> Persian success flash
        monitor.fetch_counters = lambda: counters
        r = client.post(
            "/system/monitor",
            data={"csrf_token": tok, "return_to": f"/inbounds/{in_a}/clients"},
            follow_redirects=True,
        )
        assert r.status_code == 200 and "آمار" in r.text
        print("[OK] monitor endpoint applies refresh and flashes progress")

        # bad return_to falls back to /inbounds (no open redirect)
        r = client.post(
            "/system/monitor",
            data={"csrf_token": tok, "return_to": "https://evil.example.com"},
            follow_redirects=False,
        )
        assert r.headers["location"].endswith("/inbounds")
        print("[OK] return_to is sanitized against open redirects")

        monitor.fetch_counters = original_fetch

    print()
    print("ALL PHASE 4 TESTS PASSED")


if __name__ == "__main__":
    main()
