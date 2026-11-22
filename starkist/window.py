"""Windows window lookup through user32: the largest visible window whose title matches."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WindowRef:
    title: str
    pid: int | None
    handle: int
    width: int
    height: int


def _visible_windows() -> list[WindowRef]:
    """Every visible top-level window that has a title and a non-empty client area."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    user32: Any = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    found: list[WindowRef] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(
            WindowRef(
                buf.value,
                int(pid.value) or None,
                int(hwnd),
                rect.right - rect.left,
                rect.bottom - rect.top,
            )
        )
        return True

    user32.EnumWindows(proto(visit), 0)
    return [f for f in found if f.width > 0 and f.height > 0]


def find_window(title_substring: str) -> WindowRef | None:
    if sys.platform != "win32" or not title_substring:
        return None
    wanted = title_substring.lower()
    found = [w for w in _visible_windows() if wanted in w.title.lower()]
    if not found:
        return None
    return max(found, key=lambda r: r.width * r.height)


_SHELL_TITLES = {"Program Manager", "Windows Input Experience", "Settings"}


def list_window_titles(exclude_pid: int | None = None) -> list[str]:
    """Titles of the open application windows, for a window picker. Sorted, without duplicates."""
    titles = {
        w.title.strip()
        for w in _visible_windows()
        if w.pid != exclude_pid and w.title.strip() and w.title.strip() not in _SHELL_TITLES
    }
    return sorted(titles, key=str.lower)


def is_foreground(window: WindowRef | None) -> bool | None:
    """True/False when the window is/isn't in front; None when there is no window to check."""
    if window is None or sys.platform != "win32":
        return None
    from ctypes import wintypes

    user32: Any = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    return (user32.GetForegroundWindow() or 0) == window.handle
