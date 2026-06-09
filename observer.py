from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any
import ctypes
from ctypes import wintypes

import psutil


@dataclass(slots=True)
class ActiveWindowSnapshot:
    timestamp: str
    app_name: str
    window_title: str
    process_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _get_window_title(hwnd: int) -> str:
    if not hwnd:
        return ""

    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""

    buffer = ctypes.create_unicode_buffer(length + 1)
    ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value.strip()


def _get_process_name(pid: int | None) -> str:
    if not pid:
        return "unknown"

    try:
        return psutil.Process(pid).name() or "unknown"
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return "unknown"


def get_active_window() -> dict[str, Any]:
    """
    Return a snapshot of the current foreground window.

    The function is intentionally defensive: if Windows APIs fail or there is
    no foreground window, it returns a safe "unknown" snapshot instead of
    raising and interrupting the tracking loop.
    """

    try:
        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ActiveWindowSnapshot(
                timestamp=_now_iso(),
                app_name="unknown",
                window_title="",
                process_id=None,
            ).to_dict()

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        window_title = _get_window_title(hwnd)
        app_name = _get_process_name(pid.value)

        return ActiveWindowSnapshot(
            timestamp=_now_iso(),
            app_name=app_name,
            window_title=window_title,
            process_id=pid.value or None,
        ).to_dict()
    except Exception:
        return ActiveWindowSnapshot(
            timestamp=_now_iso(),
            app_name="unknown",
            window_title="",
            process_id=None,
        ).to_dict()
