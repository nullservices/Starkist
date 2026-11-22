"""Colours, fonts and artwork for the desktop front end."""

from __future__ import annotations

import tkinter.font as tkfont
from functools import cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from starkist.config import PROJECT_ROOT

UI_ASSETS = PROJECT_ROOT / "assets" / "ui"
ICON_FILE = UI_ASSETS / "starkist.ico"

# Deep-water palette: teal-black surfaces, sea-glass for live signal, the float's coral for action.
BG = "#0b1a22"
SURFACE = "#11252f"
FIELD = "#183442"
WELL = "#0c1f28"  # recessed areas: the trace and the activity log
LINE = "#234452"
TEXT = "#e6f2ef"
MUTED = "#86a3a8"
FAINT = "#567278"
GLASS = "#4fd6b8"
GLASS_DIM = "#2b7f73"
CORAL = "#ff6f4d"
CORAL_HOVER = "#ff8a6d"
AMBER = "#f3c04f"
RED = "#ff5470"
INK = "#0b1a22"  # text on coral / sea-glass

_DISPLAY = ("Bahnschrift", "Segoe UI Variable Display", "Segoe UI", "Inter Display", "Inter")
_BODY = ("Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans")
_MONO = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono")


def _pick(candidates: tuple[str, ...]) -> str:
    have = set(tkfont.families())
    return next((f for f in candidates if f in have), candidates[-1])


class Fonts:
    """Created once the Tk root exists, because the installed families are asked from Tk."""

    def __init__(self) -> None:
        self.display_family = _pick(_DISPLAY)
        self.body_family = _pick(_BODY)
        self.mono_family = _pick(_MONO)
        d, b = self.display_family, self.body_family
        self.brand = ctk.CTkFont(d, 19, "bold")
        self.state = ctk.CTkFont(d, 22, "bold")
        self.number = ctk.CTkFont(d, 20, "bold")
        self.heading = ctk.CTkFont(b, 14, "bold")
        self.body = ctk.CTkFont(b, 13)
        self.body_bold = ctk.CTkFont(b, 13, "bold")
        self.small = ctk.CTkFont(b, 12)
        self.button = ctk.CTkFont(b, 14, "bold")
        self.figure = ctk.CTkFont(d, 13)
        self.mono = ctk.CTkFont(self.mono_family, 12)


@cache
def _image(name: str) -> Image.Image:
    return Image.open(UI_ASSETS / f"{name}.png").convert("RGBA")


def icon(name: str, size: int = 16) -> ctk.CTkImage | None:
    """A CTkImage for assets/ui/<name>.png, or None when the artwork is missing."""
    try:
        img = _image(name)
    except OSError:
        return None
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))


def asset_path(name: str) -> Path:
    return UI_ASSETS / name
