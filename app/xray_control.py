"""Xray service control via systemctl (Phase 5).

The panel drives the host's systemd unit (default `xray`, see
`XRAY_SERVICE_NAME`). Only verbs `start | stop | restart` are accepted;
everything is executed through `subprocess.run` with a whitelist, never a
shell. Getting status uses `systemctl is-active`.

On machines without systemd (dev boxes) the calls fail gracefully and the
dashboard shows an "unavailable" badge.
"""

from __future__ import annotations

import subprocess

from app.config import settings

#: The only control verbs accepted from HTTP input.
ALLOWED_ACTIONS = ("start", "stop", "restart")

_ACTIONS_SYSTEMCTL = {"start": "start", "stop": "stop", "restart": "restart"}

_TIMEOUT_S = 15


def _systemctl(args: list[str]) -> subprocess.CompletedProcess:
    """Run `systemctl <args>` without a shell (patchable in tests)."""
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )


def xray_status() -> tuple[str, str]:
    """Return (state_code, raw_output) for the Xray systemd unit.

    state_code is one of:
      * "active"       -> unit running
      * "stopped"      -> unit inactive
      * "unavailable"  -> no systemctl/no unit on this host
      * "unknown"      -> anything else (status strings passthrough)
    """
    try:
        proc = _systemctl(["is-active", settings.XRAY_SERVICE_NAME])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unavailable", "systemctl not found/timeout"

    state = (proc.stdout or "").strip()
    if state == "active" and proc.returncode == 0:
        return "active", ""
    if state in ("inactive", "failed"):
        return "stopped", state
    return "unknown", state


def run_action(action: str) -> tuple[bool, str]:
    """Run one whitelisted systemctl action; returns (ok, detail).

    `action` MUST have been validated against ALLOWED_ACTIONS by the caller.
    """
    verb = _ACTIONS_SYSTEMCTL.get(action)
    if verb is None:
        return False, "invalid action"

    try:
        proc = _systemctl([verb, settings.XRAY_SERVICE_NAME])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"systemctl failed: {exc}"

    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout or "systemctl error").strip()
    return False, detail
