"""SendInput key presses with hardware scan codes."""

from __future__ import annotations

import ctypes
import string
import sys
import time
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
ULONG_PTR = ctypes.c_size_t


class InputUnavailable(Exception):
    pass


_VK_SPECIAL = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "-": 0xBD,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "\\": 0xDC,
}
VK_CODES: dict[str, int] = {
    **{k: ord(k.upper()) for k in string.ascii_lowercase},
    **{k: ord(k) for k in string.digits},
    **{f"f{i}": 0x70 + i - 1 for i in range(1, 13)},
    **_VK_SPECIAL,
}


def normalize_key(key: str) -> str:
    k = key.strip().lower()
    return {"return": "enter", "esc": "escape", "spacebar": "space", " ": "space"}.get(k, k)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class KeySender:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise InputUnavailable("key input is Windows-only")
        self._u = ctypes.WinDLL("user32", use_last_error=True)
        self._u.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self._u.MapVirtualKeyW.restype = wintypes.UINT
        self._u.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
        self._u.SendInput.restype = wintypes.UINT

    def _send(self, scan: int, up: bool) -> None:
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
        inp = _INPUT(type=INPUT_KEYBOARD)
        inp.u.ki = _KEYBDINPUT(0, scan, flags, 0, 0)
        if self._u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
            raise InputUnavailable(f"SendInput (error {ctypes.get_last_error()})")

    def press(self, key: str, hold_s: float = 0.06) -> None:
        name = normalize_key(key)
        if name not in VK_CODES:
            raise InputUnavailable(f"key {key!r} is not a supported key name")
        scan = self._u.MapVirtualKeyW(VK_CODES[name], 0)  # MAPVK_VK_TO_VSC
        if not scan:
            raise InputUnavailable(f"key {key!r} has no scan code")
        self._send(scan, up=False)
        time.sleep(hold_s)
        self._send(scan, up=True)
