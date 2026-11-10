"""System statistics for the dashboard (Phase 5).

Reads CPU / RAM / disk / swap / uptime through psutil (cross-platform).
Everything here is read-only and never raises for the route: the caller
turns an empty dict into an "unavailable" panel card.
"""

from __future__ import annotations

import os
import time


def _format_uptime(seconds: float) -> str:
    """Human-friendly uptime, e.g. '3d 4h' or '2h 15m'."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h {minutes % 60}m"
    days = hours // 24
    return f"{days}d {hours % 24}h"


def get_system_stats() -> dict:
    """Collect a one-shot system snapshot for the dashboard.

    Returns keys: ok, cpu_percent, ram_used_gb, ram_total_gb, ram_percent,
    swap_percent, disk_used_gb, disk_total_gb, disk_percent, uptime_text,
    boot_text. On psutil failure returns {"ok": False}.
    """
    try:
        import psutil
    except ImportError:
        return {"ok": False}

    try:
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        # Disk of the filesystem the panel runs from (works on POSIX and
        # Windows alike; on the server this is the root filesystem).
        try:
            disk = psutil.disk_usage(os.path.abspath(os.getcwd()))
        except OSError:
            # Unusual cwd: fall back to the boot drive / root filesystem.
            disk = psutil.disk_usage(os.path.abspath(os.sep))

        gb = 1024 ** 3
        boot_time = psutil.boot_time()
        return {
            "ok": True,
            "cpu_percent": round(psutil.cpu_percent(interval=0.2), 1),
            "ram_used_gb": round(vm.used / gb, 2),
            "ram_total_gb": round(vm.total / gb, 2),
            "ram_percent": round(vm.percent, 1),
            "swap_percent": round(swap.percent, 1),
            "disk_used_gb": round(disk.used / gb, 2),
            "disk_total_gb": round(disk.total / gb, 2),
            "disk_percent": round(disk.percent, 1),
            "uptime_text": _format_uptime(time.time() - boot_time),
            "boot_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(boot_time)),
        }
    except Exception:
        return {"ok": False}
