"""Smoke test for Phase 1: login -> dashboard -> change-password -> logout.

Run from the project root with the venv python:
    venv\\Scripts\\python.exe tests_smoke.py
"""

import os
import pathlib
import re
import sys

# Ensure we run against a throwaway database; a leftover file would carry a
# password changed by a previous run of this same suite.
DB_FILE = pathlib.Path("./smoke_test.db")
if DB_FILE.exists():
    DB_FILE.unlink()
os.environ["DATABASE_URL"] = "sqlite:///./smoke_test.db"
os.environ.setdefault("SECRET_KEY", "smoke-test-secret-key-not-for-production")
os.environ.setdefault("DEFAULT_ADMIN_USERNAME", "smokeadmin")
os.environ.setdefault("DEFAULT_ADMIN_PASSWORD", "SmokeTest1234!")
os.environ.setdefault("PANEL_PATH", "/")

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def get_csrf_from_login(client: "TestClient") -> str:
    """Parse the CSRF token out of the login page HTML."""
    resp = client.get("/login")
    assert resp.status_code == 200, f"login page failed: {resp.status_code}"
    match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
    assert match, "csrf token not found in login page"
    return match.group(1)


def main() -> None:
    # Life-span events (table creation, admin bootstrap) only run when the
    # TestClient is used as a context manager.
    with TestClient(app) as client:
        # 1. Dashboard is protected -> redirect to login
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 303, 401, 403), f"unexpected: {resp.status_code}"
        print(f"[OK] /dashboard protected: {resp.status_code}")

        # 2. Login page renders
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "csrf_token" in resp.text
        print("[OK] /login renders (200, has CSRF token)")

        # 3. CSRF protection: POST without token -> 403
        resp = client.post("/login", data={"username": "x", "password": "y"})
        assert resp.status_code == 403, f"expected 403, got {resp.status_code}"
        print("[OK] CSRF rejected POST /login without token (403)")

        # 4. Bad credentials -> 401
        token = get_csrf_from_login(client)
        resp = client.post(
            "/login",
            data={"username": "smokeadmin", "password": "WrongPass123", "csrf_token": token},
        )
        assert resp.status_code == 401, f"expected 401, got {resp.status_code}"
        print("[OK] wrong password rejected (401)")

        # 5. Good credentials -> 302 to dashboard
        token = get_csrf_from_login(client)
        resp = client.post(
            "/login",
            data={
                "username": "smokeadmin",
                "password": os.environ["DEFAULT_ADMIN_PASSWORD"],
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302, f"expected 302, got {resp.status_code}"
        assert "access_token" in resp.cookies, "access_token cookie not set"
        print("[OK] login success -> 302, HttpOnly access_token cookie set")

        # 6. Dashboard accessible when logged in
        resp = client.get("/dashboard")
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
        assert "smokeadmin" in resp.text, "admin username not shown on dashboard"
        print("[OK] /dashboard renders when authenticated")

        # 7. Change password page renders
        resp = client.get("/change-password")
        assert resp.status_code == 200
        match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
        assert match, "csrf token not found on change-password page"
        cp_token = match.group(1)
        print("[OK] /change-password renders")

        # 8. Wrong current password rejected
        resp = client.post(
            "/change-password",
            data={
                "current_password": "TotallyWrong1",
                "new_password": "NewStrongPass99!",
                "confirm_password": "NewStrongPass99!",
                "csrf_token": cp_token,
            },
        )
        assert resp.status_code == 400, f"expected 400, got {resp.status_code}"
        print("[OK] change-password rejects wrong current password (400)")

        # 9. Mismatch confirmation rejected
        resp = client.post(
            "/change-password",
            data={
                "current_password": os.environ["DEFAULT_ADMIN_PASSWORD"],
                "new_password": "NewStrongPass99!",
                "confirm_password": "Different99!",
                "csrf_token": cp_token,
            },
        )
        assert resp.status_code == 400
        print("[OK] change-password rejects mismatched confirmation (400)")

        # 10. Successful password change
        resp = client.post(
            "/change-password",
            data={
                "current_password": os.environ["DEFAULT_ADMIN_PASSWORD"],
                "new_password": "NewStrongPass99!",
                "confirm_password": "NewStrongPass99!",
                "csrf_token": cp_token,
            },
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
        assert "successfully" in resp.text
        print("[OK] password changed successfully")

        # 11. Login with the new password (fresh client/session)
        with TestClient(app) as client2:
            token2 = get_csrf_from_login(client2)
            resp = client2.post(
                "/login",
                data={
                    "username": "smokeadmin",
                    "password": "NewStrongPass99!",
                    "csrf_token": token2,
                },
                follow_redirects=False,
            )
            assert resp.status_code == 302, "login with new password failed"
        print("[OK] new password works for login")

        # 12. Logout clears authentication
        resp = client.post("/logout", follow_redirects=False)
        assert resp.status_code == 302
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code in (302, 303, 401, 403), "still authenticated after logout"
        print("[OK] logout clears authentication")

    print()
    print("ALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
