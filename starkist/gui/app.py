"""The Starkist window: a status bar you can act on, a live Monitor page and a Setup page."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from starkist import __version__
from starkist.config import (
    DEFAULT_CONFIG,
    PROJECT_ROOT,
    ActionConfig,
    CaptureConfig,
    Config,
    MatchConfig,
    load_config,
    save_config,
)
from starkist.fingerprint import load_fingerprints
from starkist.gui import theme as T
from starkist.gui.widgets import (
    THRESHOLD_MAX,
    THRESHOLD_MIN,
    KeyCapture,
    RangeField,
    TraceChart,
    ghost_button,
    key_label,
)
from starkist.keys import VK_CODES, KeySender, normalize_key
from starkist.window import find_window, list_window_titles

log = logging.getLogger("starkist")

POLL_MS = 100
WINDOW_CHECK_MS = 1500
AUTOSAVE_MS = 500
LEVEL_FLOOR_DB = -60.0
TEST_KEY_COUNTDOWN_S = 3
LOG_MAX_LINES = 400

MODE_APP = "One app"
MODE_DEVICE = "Everything on a device"
DEFAULT_DEVICE_LABEL = "Default output"
PAGE_MONITOR = "Monitor"
PAGE_SETUP = "Setup"
FULL_SIZE = (470, 760)
FULL_MIN = (430, 620)
COMPACT_SIZE = (400, 64)
MINI_TEXT_PX = 200  # room for the strip's two text lines beside the dot and buttons


class _QueueHandler(logging.Handler):
    """Hands log records to the UI thread as (time, tag, kind, text)."""

    def __init__(self, q: queue.Queue[tuple[str, str, str, str]]) -> None:
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        template = record.msg if isinstance(record.msg, str) else ""
        if template.startswith("cue %s (score"):
            kind = "cue"
        elif template.startswith("%s press: "):
            kind = "press"
        else:
            kind = ""
        if record.levelno >= logging.ERROR:
            tag = "error"
        elif record.levelno >= logging.WARNING:
            tag = "warn"
        else:
            tag = kind or "info"
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        self.q.put((stamp, tag, kind, record.getMessage()))


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fit(text: str, font: ctk.CTkFont, px: int) -> str:
    """Trims text with an ellipsis until it measures no wider than px."""
    if font.measure(text) <= px:
        return text
    while text and font.measure(text + "…") > px:
        text = text[:-1]
    return text.rstrip() + "…"


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}"


def _ago(seconds: float) -> str:
    s = int(seconds)
    if s < 2:
        return "just now"
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


class App:
    def __init__(self, root: ctk.CTk, config_path: str | None = None) -> None:
        self.root = root
        self.config_path = config_path or str(DEFAULT_CONFIG)
        self.f = T.Fonts()
        root.title("Starkist")
        root.geometry("%dx%d" % FULL_SIZE)
        root.minsize(*FULL_MIN)
        root.configure(fg_color=T.BG)
        if sys.platform == "win32" and T.ICON_FILE.exists():
            try:
                root.iconbitmap(str(T.ICON_FILE))
            except tk.TclError:
                pass

        self._log_q: queue.Queue[tuple[str, str, str, str]] = queue.Queue()
        self._stats_q: queue.Queue[tuple[float, dict[str, float]]] = queue.Queue(maxsize=16)
        self._devices_q: queue.Queue[tuple[list[str], str]] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._handler = _QueueHandler(self._log_q)
        log.addHandler(self._handler)
        log.setLevel(logging.INFO)

        self._state = "stopped"  # stopped | listening | stopping
        self._running_cfg: Config | None = None
        self._running_dry = False
        self._restart_pending = False
        self._started_at: float | None = None
        self._last_cue_at: float | None = None
        self._cue_count = 0
        self._press_count = 0
        self._window_open: bool | None = None
        self._cue_names: list[str] = []
        self._cues_path = "assets/cues"
        self._loading = True
        self._autosave_job: str | None = None
        self._form_error = ""
        self._devices_loaded = False
        self._compact = False
        self._full_size = FULL_SIZE
        self._pinned_before_compact = False

        first_run = not Path(self.config_path).exists()
        try:
            cfg = load_config(self.config_path)
            load_error = ""
        except (OSError, ValueError) as e:
            cfg, load_error = Config(), f"config.toml couldn't be read ({e}). Defaults are shown."

        self._build()
        self._load_into_form(cfg)
        self._loading = False
        self._reload_cues()
        self._refresh_windows()
        self._validate()
        self._show_page(PAGE_SETUP if first_run or not cfg.capture.window_title else PAGE_MONITOR)
        if load_error:
            self._say(load_error, "error")
        elif first_run:
            self._say("Welcome. Pick the app to listen to in Setup, then press Start.", "info")
        else:
            self._say("Ready. Press Start to begin listening.", "info")

        root.after(POLL_MS, self._poll)
        root.after(200, self._check_window)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------------------------------------
    # layout
    # ---------------------------------------------------------------------------------------------

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)
        self._build_header()
        self._build_hero()
        self._build_notice()
        self._build_mini()
        self.pages = ctk.CTkFrame(root, fg_color="transparent")
        self.pages.grid(row=3, column=0, sticky="nsew", padx=14, pady=(10, 12))
        self.pages.columnconfigure(0, weight=1)
        self.pages.rowconfigure(0, weight=1)
        self.monitor_page = ctk.CTkFrame(self.pages, fg_color="transparent")
        self.setup_page = ctk.CTkFrame(self.pages, fg_color="transparent")
        self._build_monitor(self.monitor_page)
        self._build_setup(self.setup_page)

    def _build_header(self) -> None:
        f = self.f
        bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="we", padx=14, pady=(12, 8))
        bar.columnconfigure(2, weight=1)
        mark = T.icon("logo", 30)
        if mark is not None:
            ctk.CTkLabel(bar, text="", image=mark).grid(row=0, column=0, padx=(2, 8))
        ctk.CTkLabel(bar, text="Starkist", font=f.brand, text_color=T.TEXT).grid(row=0, column=1, sticky="w")
        self.nav = ctk.CTkSegmentedButton(
            bar, values=[PAGE_MONITOR, PAGE_SETUP], command=self._show_page, font=f.body, height=30,
            corner_radius=8, border_width=2, fg_color=T.SURFACE, selected_color=T.FIELD,
            selected_hover_color=T.LINE, unselected_color=T.SURFACE, unselected_hover_color=T.FIELD,
            text_color=T.TEXT,
        )
        self.nav.grid(row=0, column=3, sticky="e")
        self._pinned = False
        self.pin_btn = ctk.CTkButton(
            bar, text="", image=T.icon("pin", 15), width=30, height=30, corner_radius=8,
            fg_color="transparent", hover_color=T.SURFACE, command=self._toggle_pin,
        )
        self.pin_btn.grid(row=0, column=4, padx=(8, 0))
        ctk.CTkButton(
            bar, text="", image=T.icon("compact", 15), width=30, height=30, corner_radius=8,
            fg_color="transparent", hover_color=T.SURFACE, command=lambda: self._set_compact(True),
        ).grid(row=0, column=5, padx=(2, 0))
        self.header = bar

    def _build_hero(self) -> None:
        f = self.f
        hero = ctk.CTkFrame(self.root, fg_color=T.SURFACE, corner_radius=14, border_width=1, border_color=T.LINE)
        hero.grid(row=1, column=0, sticky="we", padx=14)
        hero.columnconfigure(0, weight=1)
        self.hero = hero

        top = ctk.CTkFrame(hero, fg_color="transparent")
        top.grid(row=0, column=0, sticky="we", padx=16, pady=(14, 6))
        top.columnconfigure(1, weight=1)
        self.dot = ctk.CTkFrame(top, width=12, height=12, corner_radius=6, fg_color=T.FAINT)
        self.dot.grid(row=0, column=0, padx=(0, 10))
        words = ctk.CTkFrame(top, fg_color="transparent")
        words.grid(row=0, column=1, sticky="w")
        self.state_label = ctk.CTkLabel(words, text="Ready", font=f.state, text_color=T.TEXT, anchor="w", height=26)
        self.state_label.pack(anchor="w")
        self.sub_label = ctk.CTkLabel(words, text="", font=f.small, text_color=T.MUTED, anchor="w", height=16)
        self.sub_label.pack(anchor="w")
        self.start_btn = ctk.CTkButton(
            top, text="Start", image=T.icon("play", 13), compound="left", command=self._toggle,
            width=118, height=42, corner_radius=21, font=f.button,
            fg_color=T.CORAL, hover_color=T.CORAL_HOVER, text_color=T.INK,
        )
        self.start_btn.grid(row=0, column=2, sticky="e")

        chips = ctk.CTkFrame(hero, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="we", padx=12, pady=(4, 12))
        chips.columnconfigure(3, weight=1)
        self.chip_window = self._chip(chips, 0, "window")
        self.chip_key = self._chip(chips, 1, "key")
        self.chip_cues = self._chip(chips, 2, "note")
        self.chip_cues.configure(text="No sounds", border_color=T.AMBER, text_color=T.AMBER)
        self.chip_cues.grid_remove()
        self.dry_run = tk.BooleanVar(value=False)
        ctk.CTkSwitch(
            chips, text="Practice", variable=self.dry_run, command=self._practice_changed, font=f.small,
            text_color=T.MUTED, progress_color=T.GLASS_DIM, button_color=T.TEXT, button_hover_color=T.TEXT,
            fg_color=T.LINE, switch_width=32, switch_height=16, width=10,
        ).grid(row=0, column=4, sticky="e", padx=(0, 4))

    def _chip(self, parent: ctk.CTkFrame, col: int, icon_name: str) -> ctk.CTkButton:
        chip = ctk.CTkButton(
            parent, text="", image=T.icon(icon_name, 14), compound="left", height=26, width=10,
            corner_radius=13, fg_color=T.FIELD, hover_color=T.LINE, text_color=T.TEXT, font=self.f.small,
            border_width=1, border_color=T.FIELD, border_spacing=4,
            command=lambda: self._show_page(PAGE_SETUP),
        )
        chip.grid(row=0, column=col, padx=(4, 2))
        return chip

    def _build_notice(self) -> None:
        self.notice = ctk.CTkFrame(self.root, fg_color="#3a3320", corner_radius=10)
        self.notice.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            self.notice, text="Setup changed while listening.", font=self.f.small, text_color=T.AMBER, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=6)
        ctk.CTkButton(
            self.notice, text="Restart to apply", command=self._restart, height=26, width=10, corner_radius=8,
            fg_color=T.AMBER, hover_color="#f7d07a", text_color=T.INK, font=self.f.small, border_spacing=6,
        ).grid(row=0, column=1, padx=6, pady=5)

    def _build_mini(self) -> None:
        """The compact strip: state, Start/Stop and the pin, nothing else. Shown instead of everything."""
        f = self.f
        strip = ctk.CTkFrame(self.root, fg_color=T.SURFACE, corner_radius=0)
        strip.columnconfigure(1, weight=1)
        self.mini = strip
        self.mini_dot = ctk.CTkFrame(strip, width=10, height=10, corner_radius=5, fg_color=T.FAINT)
        self.mini_dot.grid(row=0, column=0, padx=(12, 8))
        words = ctk.CTkFrame(strip, fg_color="transparent")
        words.grid(row=0, column=1, sticky="w", pady=8)
        self.mini_state = ctk.CTkLabel(words, text="Ready", font=f.body_bold, text_color=T.TEXT, anchor="w", height=18)
        self.mini_state.pack(anchor="w")
        self.mini_sub = ctk.CTkLabel(words, text="", font=f.small, text_color=T.MUTED, anchor="w", height=16)
        self.mini_sub.pack(anchor="w")
        self.mini_start_btn = ctk.CTkButton(
            strip, text="Start", image=T.icon("play", 12), compound="left", command=self._toggle,
            width=86, height=32, corner_radius=16, font=f.body_bold,
            fg_color=T.CORAL, hover_color=T.CORAL_HOVER, text_color=T.INK,
        )
        self.mini_start_btn.grid(row=0, column=2, padx=(8, 6))
        self.mini_pin_btn = ctk.CTkButton(
            strip, text="", image=T.icon("pin", 14), width=28, height=28, corner_radius=8,
            fg_color="transparent", hover_color=T.FIELD, command=self._toggle_pin,
        )
        self.mini_pin_btn.grid(row=0, column=3)
        ctk.CTkButton(
            strip, text="", image=T.icon("expand", 14), width=28, height=28, corner_radius=8,
            fg_color="transparent", hover_color=T.FIELD, command=lambda: self._set_compact(False),
        ).grid(row=0, column=4, padx=(0, 8))

    # -- Monitor ---------------------------------------------------------------------------------

    def _build_monitor(self, page: ctk.CTkFrame) -> None:
        f = self.f
        page.columnconfigure(0, weight=1)
        page.rowconfigure(2, weight=1)

        match = self._card(page)
        match.grid(row=0, column=0, sticky="we")
        match.columnconfigure(0, weight=1)
        head = ctk.CTkFrame(match, fg_color="transparent")
        head.grid(row=0, column=0, sticky="we", padx=14, pady=(10, 6))
        head.columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="Sound match", font=f.heading, text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            head, text="Drag the coral line to tune", font=f.small, text_color=T.MUTED,
        ).grid(row=0, column=1, sticky="e")
        self.chart = TraceChart(match, f, self._threshold_dragged)
        self.chart.grid(row=1, column=0, sticky="we", padx=10)
        self.meter_box = ctk.CTkFrame(match, fg_color="transparent")
        self.meter_box.grid(row=2, column=0, sticky="we", padx=14, pady=(8, 12))
        self.meter_box.columnconfigure((0, 1), weight=1, uniform="m")
        self.level_bar, self.level_val = self._meter(self.meter_box, 0, 0, "Volume", T.MUTED, span=2)
        self.cue_rows: dict[str, tuple[ctk.CTkProgressBar, ctk.CTkLabel]] = {}

        stats = ctk.CTkFrame(page, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="we", pady=10)
        stats.columnconfigure((0, 1, 2), weight=1, uniform="s")
        self.stat_cues = self._stat(stats, 0, "Sounds heard")
        self.stat_presses = self._stat(stats, 1, "Keys pressed")
        self.stat_last = self._stat(stats, 2, "Last sound")

        act = self._card(page)
        act.grid(row=2, column=0, sticky="nsew")
        act.columnconfigure(0, weight=1)
        act.rowconfigure(1, weight=1)
        ahead = ctk.CTkFrame(act, fg_color="transparent")
        ahead.grid(row=0, column=0, sticky="we", padx=14, pady=(10, 4))
        ahead.columnconfigure(0, weight=1)
        ctk.CTkLabel(ahead, text="Activity", font=f.heading, text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            ahead, text="Clear", image=T.icon("trash", 13), command=self._clear_log, width=10, height=24,
            corner_radius=6, fg_color="transparent", hover_color=T.FIELD, text_color=T.MUTED, font=f.small,
        ).grid(row=0, column=1, sticky="e")
        self.log_text = ctk.CTkTextbox(
            act, font=f.mono, fg_color=T.WELL, text_color=T.TEXT, corner_radius=10, border_width=0,
            wrap="word", height=120, scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.FAINT,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        for tag, color in (
            ("time", T.FAINT), ("info", T.MUTED), ("cue", T.GLASS), ("press", T.TEXT),
            ("warn", T.AMBER), ("error", T.RED),
        ):
            self.log_text.tag_config(tag, foreground=color)
        self.log_text.configure(state="disabled")

    def _card(self, parent: tk.Misc) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=T.SURFACE, corner_radius=14, border_width=1, border_color=T.LINE)

    def _meter(
        self, parent: ctk.CTkFrame, row: int, col: int, name: str, color: str, span: int = 1
    ) -> tuple[ctk.CTkProgressBar, ctk.CTkLabel]:
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=row, column=col, columnspan=span, sticky="we", padx=(0 if col == 0 else 10, 0), pady=2)
        cell.columnconfigure(1, weight=1)
        ctk.CTkLabel(
            cell, text=_shorten(name, 12), font=self.f.small, text_color=T.MUTED, anchor="w", width=58, height=18,
        ).grid(row=0, column=0, sticky="w")
        bar = ctk.CTkProgressBar(cell, height=6, corner_radius=3, progress_color=color, fg_color=T.FIELD)
        bar.set(0)
        bar.grid(row=0, column=1, sticky="we", padx=6)
        val = ctk.CTkLabel(cell, text="–", font=self.f.figure, text_color=T.MUTED, anchor="e", width=52 if span > 1 else 30, height=18)
        val.grid(row=0, column=2, sticky="e")
        return bar, val

    def _stat(self, parent: ctk.CTkFrame, col: int, caption: str) -> ctk.CTkLabel:
        box = self._card(parent)
        box.grid(row=0, column=col, sticky="we", padx=(0 if col == 0 else 8, 0))
        val = ctk.CTkLabel(box, text="0", font=self.f.number, text_color=T.TEXT, anchor="w", height=24)
        val.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(box, text=caption, font=self.f.small, text_color=T.MUTED, anchor="w", height=16).pack(
            fill="x", padx=12, pady=(0, 8)
        )
        return val

    # -- Setup -----------------------------------------------------------------------------------

    def _section(self, parent: tk.Misc, row: int, icon_name: str, title: str) -> ctk.CTkFrame:
        card = self._card(parent)
        card.grid(row=row, column=0, sticky="we", pady=(0, 10), padx=(0, 6))
        card.columnconfigure(0, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="we", padx=14, pady=(10, 2))
        ctk.CTkLabel(head, text="", image=T.icon(icon_name, 16)).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(head, text=title, font=self.f.heading, text_color=T.TEXT).pack(side="left")
        return card

    def _row(self, card: ctk.CTkFrame, row: int, label: str, hint: str = "") -> ctk.CTkFrame:
        """A labelled row; returns the frame on the right that holds the control."""
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.grid(row=row, column=0, sticky="we", padx=14, pady=5)
        box.columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=label, font=self.f.body, text_color=T.TEXT, anchor="w", height=18).grid(
            row=0, column=0, sticky="w"
        )
        if hint:
            ctk.CTkLabel(box, text=hint, font=self.f.small, text_color=T.MUTED, anchor="w", height=16).grid(
                row=1, column=0, sticky="w"
            )
        right = ctk.CTkFrame(box, fg_color="transparent")
        right.grid(row=0, column=1, rowspan=2, sticky="e")
        return right

    def _hint(self, card: ctk.CTkFrame, row: int, text: str = "", color: str = T.MUTED) -> ctk.CTkLabel:
        lab = ctk.CTkLabel(
            card, text=text, font=self.f.small, text_color=color, anchor="w", justify="left", wraplength=380,
        )
        lab.grid(row=row, column=0, sticky="we", padx=14, pady=(0, 10))
        return lab

    def _build_setup(self, page: ctk.CTkFrame) -> None:
        f = self.f
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        scroll = ctk.CTkScrollableFrame(
            page, fg_color="transparent", scrollbar_button_color=T.LINE, scrollbar_button_hover_color=T.FAINT,
        )
        self.setup_scroll = scroll
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.columnconfigure(0, weight=1)

        # 1. what to listen to
        cap = self._section(scroll, 0, "window", "Listen to")
        self.mode_seg = ctk.CTkSegmentedButton(
            cap, values=[MODE_APP, MODE_DEVICE], command=self._mode_changed, font=f.body, height=32,
            corner_radius=8, border_width=2, fg_color=T.FIELD, selected_color=T.GLASS_DIM,
            selected_hover_color=T.GLASS_DIM, unselected_color=T.FIELD, unselected_hover_color=T.LINE,
            text_color=T.TEXT,
        )
        self.mode_seg.grid(row=1, column=0, sticky="we", padx=14, pady=(6, 8))
        wrow = ctk.CTkFrame(cap, fg_color="transparent")
        wrow.grid(row=2, column=0, sticky="we", padx=14, pady=(0, 4))
        wrow.columnconfigure(0, weight=1)
        self.window_title = tk.StringVar()
        self.window_box = ctk.CTkComboBox(
            wrow, variable=self.window_title, values=[], height=32, corner_radius=8, fg_color=T.FIELD,
            border_color=T.LINE, border_width=1, button_color=T.LINE, button_hover_color=T.FAINT,
            dropdown_fg_color=T.FIELD, dropdown_hover_color=T.LINE, dropdown_text_color=T.TEXT,
            text_color=T.TEXT, font=f.body, dropdown_font=f.body,
        )
        self.window_box.grid(row=0, column=0, sticky="we")
        ghost_button(wrow, f, "", self._refresh_windows, T.icon("refresh_on", 14), width=32).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.window_hint = self._hint(cap, 3)
        self.device_row = ctk.CTkFrame(cap, fg_color="transparent")
        self.device_row.columnconfigure(0, weight=1)
        self.device = tk.StringVar(value=DEFAULT_DEVICE_LABEL)
        self.device_box = ctk.CTkComboBox(
            self.device_row, variable=self.device, values=[DEFAULT_DEVICE_LABEL], height=32, corner_radius=8,
            fg_color=T.FIELD, border_color=T.LINE, border_width=1, button_color=T.LINE,
            button_hover_color=T.FAINT, dropdown_fg_color=T.FIELD, dropdown_hover_color=T.LINE,
            dropdown_text_color=T.TEXT, text_color=T.TEXT, font=f.body, dropdown_font=f.body,
        )
        self.device_box.grid(row=0, column=0, sticky="we")
        self.device_refresh = ghost_button(
            self.device_row, f, "", self._refresh_devices, T.icon("refresh_on", 14), width=32
        )
        self.device_refresh.grid(row=0, column=1, padx=(8, 0))

        # 2. the key
        keyc = self._section(scroll, 1, "key", "Key press")
        right = self._row(keyc, 1, "Key to press", "click it, then press the key you want")
        self.key = tk.StringVar(value="space")
        self.key_btn = KeyCapture(right, f, self.key, self._key_message)
        self.key_btn.pack(side="left")
        self.test_btn = ghost_button(right, f, "Test", self._test_key, width=58)
        self.test_btn.pack(side="left", padx=(8, 0))
        self.key_msg = ctk.CTkLabel(
            keyc, text="", font=f.small, text_color=T.AMBER, anchor="w", justify="left", wraplength=380,
        )
        self.require_focus = tk.BooleanVar(value=True)
        ctk.CTkSwitch(
            keyc, text="Only press while that window is in front", variable=self.require_focus, font=f.body,
            text_color=T.TEXT, progress_color=T.GLASS_DIM, button_color=T.TEXT, button_hover_color=T.TEXT,
            fg_color=T.LINE, switch_width=36, switch_height=18,
        ).grid(row=3, column=0, sticky="w", padx=14, pady=(4, 12))

        # 3. timing
        tim = self._section(scroll, 2, "clock", "Timing")
        self.first = RangeField(self._row(tim, 1, "Before the first press", "random wait after the sound"), f, "First press")
        self.second = RangeField(self._row(tim, 2, "Before the second press", "random wait after the first"), f, "Second press")
        self.hold = RangeField(self._row(tim, 3, "Hold each press for"), f, "Hold time")
        self.idle = RangeField(
            self._row(tim, 4, "Press again if quiet for", "0 turns this off"), f, "Press again", single=True
        )
        for field in (self.first, self.second, self.hold, self.idle):
            field.pack()
        self.timing_caption = self._hint(tim, 5)

        # 4. detection
        det = self._section(scroll, 3, "wave", "Detection")
        trow = ctk.CTkFrame(det, fg_color="transparent")
        trow.grid(row=1, column=0, sticky="we", padx=14, pady=(6, 0))
        trow.columnconfigure(1, weight=1)
        ctk.CTkLabel(trow, text="Match needed", font=f.body, text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.threshold = tk.DoubleVar(value=0.66)
        self.threshold_slider = ctk.CTkSlider(
            trow, from_=THRESHOLD_MIN, to=THRESHOLD_MAX, number_of_steps=65, variable=self.threshold,
            command=self._threshold_slid, progress_color=T.CORAL, button_color=T.TEXT,
            button_hover_color=T.CORAL_HOVER, fg_color=T.FIELD, height=16,
        )
        self.threshold_slider.grid(row=0, column=1, sticky="we", padx=12)
        self.threshold_val = ctk.CTkLabel(trow, text="0.66", font=f.figure, text_color=T.TEXT, width=34, anchor="e")
        self.threshold_val.grid(row=0, column=2, sticky="e")
        ends = ctk.CTkFrame(det, fg_color="transparent")
        ends.grid(row=2, column=0, sticky="we", padx=14, pady=(0, 4))
        ends.columnconfigure(0, weight=1)
        ctk.CTkLabel(ends, text="Looser: catches quiet sounds, may misfire", font=f.small, text_color=T.MUTED,
                     height=16).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(ends, text="Stricter", font=f.small, text_color=T.MUTED, height=16).grid(row=0, column=1, sticky="e")
        self.refractory = RangeField(
            self._row(det, 3, "Ignore repeats for", "after a sound is heard"), f, "Ignore repeats", single=True
        )
        self.refractory.pack()
        ctk.CTkFrame(det, fg_color="transparent", height=6).grid(row=4, column=0)

        # 5. the reference sounds
        cues = self._section(scroll, 4, "note", "Sounds to listen for")
        self.cue_list = ctk.CTkLabel(
            cues, text="", font=f.body, text_color=T.TEXT, anchor="w", justify="left", wraplength=380,
        )
        self.cue_list.grid(row=1, column=0, sticky="we", padx=14, pady=(4, 6))
        btns = ctk.CTkFrame(cues, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 12))
        ghost_button(btns, f, "Add a sound…", self._add_cues, T.icon("plus_on", 13)).pack(side="left")
        ghost_button(btns, f, "Open folder", self._open_cues_folder, T.icon("folder_on", 14)).pack(
            side="left", padx=(8, 0)
        )

        # footer
        foot = ctk.CTkFrame(scroll, fg_color="transparent")
        foot.grid(row=5, column=0, sticky="we", padx=(4, 10), pady=(0, 6))
        foot.columnconfigure(0, weight=1)
        self.save_label = ctk.CTkLabel(
            foot, text="Changes save automatically", font=f.small, text_color=T.MUTED, anchor="w",
            justify="left", wraplength=280,
        )
        self.save_label.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            foot, text="Restore defaults", command=self._restore_defaults, width=10, height=26, corner_radius=6,
            fg_color="transparent", hover_color=T.SURFACE, text_color=T.MUTED, font=f.small,
        ).grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(foot, text=f"Starkist {__version__}", font=f.small, text_color=T.FAINT).grid(
            row=1, column=0, sticky="w"
        )

        # anything that changes a setting goes through one place
        tracked: list[tk.Variable] = [self.window_title, self.device, self.key, self.require_focus]
        for field in (self.first, self.second, self.hold, self.idle, self.refractory):
            tracked += field.variables()
        for var in tracked:
            var.trace_add("write", lambda *_: self._changed())

    # ---------------------------------------------------------------------------------------------
    # form <-> config
    # ---------------------------------------------------------------------------------------------

    @property
    def _mode(self) -> str:
        return "process" if self.mode_seg.get() == MODE_APP else "device"

    def _load_into_form(self, cfg: Config) -> None:
        was_loading, self._loading = self._loading, True
        self.mode_seg.set(MODE_APP if cfg.capture.mode == "process" else MODE_DEVICE)
        self.window_title.set(cfg.capture.window_title)
        self.device.set(cfg.capture.device or DEFAULT_DEVICE_LABEL)
        self.key.set(normalize_key(cfg.action.key))
        self.require_focus.set(cfg.action.require_focus)
        self.first.set(*cfg.action.first_delay_s)
        self.second.set(*cfg.action.second_delay_s)
        self.hold.set(*cfg.action.hold_s)
        self.idle.set(cfg.action.idle_timeout_s)
        self.refractory.set(cfg.match.refractory_s)
        self._set_threshold(cfg.match.threshold)
        self._cues_path = cfg.match.cues
        self._loading = was_loading
        self._mode_changed()

    def _form_to_config(self) -> Config:
        """Raises ValueError with a message for the first thing that is wrong."""
        problems: list[str] = []
        values: dict[str, tuple[float, float]] = {}
        for name, field in (
            ("first", self.first), ("second", self.second), ("hold", self.hold),
            ("idle", self.idle), ("refractory", self.refractory),
        ):
            try:
                values[name] = field.value()
            except ValueError as e:
                problems.append(str(e))
        key = normalize_key(self.key.get())
        if key not in VK_CODES:
            problems.append("Choose a key to press")
        title = self.window_title.get().strip()
        if self._mode == "process" and not title:
            problems.append("Pick the app to listen to")
        self.window_box.configure(border_color=T.RED if self._mode == "process" and not title else T.LINE)
        if problems:
            raise ValueError(problems[0])
        return Config(
            capture=CaptureConfig(mode=self._mode, window_title=title, device=self._device_selector()),
            match=MatchConfig(
                cues=self._cues_path,
                threshold=round(float(self.threshold.get()), 2),
                refractory_s=values["refractory"][0],
            ),
            action=ActionConfig(
                key=key,
                first_delay_s=values["first"],
                second_delay_s=values["second"],
                hold_s=values["hold"],
                require_focus=bool(self.require_focus.get()),
                idle_timeout_s=values["idle"][0],
            ),
        )

    def _device_selector(self) -> str:
        text = self.device.get().strip()
        return "" if text in ("", DEFAULT_DEVICE_LABEL) else text

    def _validate(self) -> Config | None:
        """Checks the form, refreshes everything derived from it, returns the config if valid."""
        try:
            cfg = self._form_to_config()
            self._form_error = ""
        except ValueError as e:
            cfg, self._form_error = None, str(e)
        self._refresh_captions()
        self._refresh_hero()
        return cfg

    def _changed(self) -> None:
        if self._loading:
            return
        self._validate()
        if self._autosave_job is not None:
            self.root.after_cancel(self._autosave_job)
        self._autosave_job = self.root.after(AUTOSAVE_MS, self._autosave)

    def _autosave(self) -> None:
        self._autosave_job = None
        cfg = self._validate()
        if cfg is None:
            self.save_label.configure(text=f"Not saved yet. {self._form_error}", text_color=T.RED)
            return
        try:
            save_config(cfg, self.config_path)
        except OSError as e:
            self.save_label.configure(text=f"Couldn't save: {e}", text_color=T.RED)
            return
        self.save_label.configure(text=f"Saved at {time.strftime('%H:%M:%S')}", text_color=T.MUTED)
        self._refresh_notice(cfg)

    def _refresh_notice(self, cfg: Config | None = None) -> None:
        cfg = cfg if cfg is not None else self._validate()
        changed = (
            self._state == "listening"
            and cfg is not None
            and (cfg != self._running_cfg or bool(self.dry_run.get()) != self._running_dry)
        )
        if changed:
            self.notice.grid(row=2, column=0, sticky="we", padx=14, pady=(8, 0))
        else:
            self.notice.grid_remove()

    def _refresh_captions(self) -> None:
        key = key_label(normalize_key(self.key.get()) or "the key")
        text = (
            f"When the sound is heard: wait {self.first.text()}, press {key}, "
            f"wait {self.second.text()}, press {key} again."
        )
        idle = self.idle.lo.get().strip()
        if idle not in ("", "0", "0.0"):
            text += f" After {idle} s with no sound, press {key} once more."
        timing_names = ("First press", "Second press", "Hold time", "Press again")
        if self._form_error.startswith(timing_names):
            self.timing_caption.configure(text=self._form_error, text_color=T.RED)
        else:
            self.timing_caption.configure(text=text, text_color=T.MUTED)
        if self._mode == "process":
            hint = "Starkist hears only this app, so other sounds on the PC can't set it off. Pick an open window or type part of its title."
        else:
            hint = "Starkist hears everything played on the device below. The window is only used to check it's in front before pressing; leave it empty to skip that check."
        self.window_hint.configure(text=hint)

    # ---------------------------------------------------------------------------------------------
    # hero
    # ---------------------------------------------------------------------------------------------

    def _refresh_hero(self) -> None:
        title = self.window_title.get().strip()
        listening = self._state == "listening"
        dry = self._running_dry if listening else bool(self.dry_run.get())
        waiting = listening and bool(title) and self._window_open is False

        if self._state == "stopping":
            state, color, sub = "Stopping…", T.AMBER, ""
        elif waiting:
            state, color = "Waiting for the app", T.AMBER
            sub = f"Open {_shorten(title, 30)} and Starkist carries on"
        elif listening:
            state, color = "Listening", T.GLASS
            sub = "Practice: no keys are pressed" if dry else f"Running for {_clock(time.monotonic() - (self._started_at or time.monotonic()))}"
        elif self._form_error:
            state, color, sub = "Finish setup", T.AMBER, self._form_error
        else:
            state, color = "Ready", T.FAINT
            sub = "Practice: no keys will be pressed" if dry else "Press Start, then switch to the app"
        self.state_label.configure(text=state)
        self.sub_label.configure(text=sub)
        self.dot.configure(fg_color=color)
        self.mini_state.configure(text=_fit(state, self.f.body_bold, MINI_TEXT_PX))
        self.mini_dot.configure(fg_color=color)
        self._refresh_mini_sub(sub)

        # chips: what, which key, how many sounds
        if title:
            ok = self._window_open is not False
            self.chip_window.configure(
                text=_shorten(title, 22), border_color=T.FIELD if ok else T.AMBER,
                text_color=T.TEXT if ok else T.AMBER,
            )
        elif self._mode == "device":
            self.chip_window.configure(text="Whole device", border_color=T.FIELD, text_color=T.TEXT)
        else:
            self.chip_window.configure(text="No app chosen", border_color=T.AMBER, text_color=T.AMBER)
        key = normalize_key(self.key.get())
        self.chip_key.configure(text=key_label(key) if key in VK_CODES else "No key")
        if self._cue_names:
            self.chip_cues.grid_remove()
        else:
            self.chip_cues.grid()

    def _check_window(self) -> None:
        title = self.window_title.get().strip()
        if sys.platform != "win32" or not title:
            found: bool | None = None
        else:
            try:
                found = find_window(title) is not None
            except OSError:
                found = None
        if found != self._window_open:
            self._window_open = found
            self._refresh_hero()
        self.root.after(WINDOW_CHECK_MS, self._check_window)

    # ---------------------------------------------------------------------------------------------
    # actions
    # ---------------------------------------------------------------------------------------------

    def _show_page(self, name: str) -> None:
        self.nav.set(name)
        for page, key in ((self.monitor_page, PAGE_MONITOR), (self.setup_page, PAGE_SETUP)):
            if key == name:
                page.grid(row=0, column=0, sticky="nsew")
            else:
                page.grid_remove()

    def _refresh_mini_sub(self, fallback: str | None = None) -> None:
        """The strip's second line: counts while listening, otherwise whatever the hero says."""
        if self._state == "listening":
            parts = ["Practice"] if self._running_dry else []
            parts += [f"{self._cue_count} heard", f"{self._press_count} pressed"]
            if self._last_cue_at is not None:
                parts.append(_ago(time.monotonic() - self._last_cue_at))
            text = " · ".join(parts)
        else:
            text = self.sub_label.cget("text") if fallback is None else fallback
        self.mini_sub.configure(text=_fit(text, self.f.small, MINI_TEXT_PX))

    def _set_compact(self, compact: bool) -> None:
        """Fold the window down to the strip, or open it back up. The window keeps its position."""
        if compact == self._compact:
            return
        root = self.root
        self._compact = compact
        x, y = root.winfo_x(), root.winfo_y()
        if compact:
            self._full_size = (root.winfo_width(), root.winfo_height())
            for w in (self.header, self.hero, self.notice, self.pages):
                w.grid_remove()
            self.mini.grid(row=0, column=0, sticky="nsew")
            root.rowconfigure(3, weight=0)
            root.rowconfigure(0, weight=1)
            root.minsize(*COMPACT_SIZE)
            root.maxsize(4096, COMPACT_SIZE[1])
            root.geometry("%dx%d+%d+%d" % (COMPACT_SIZE[0], COMPACT_SIZE[1], x, y))
            self._pinned_before_compact = self._pinned
            if not self._pinned:
                self._set_pin(True)
        else:
            self.mini.grid_remove()
            root.rowconfigure(0, weight=0)
            root.rowconfigure(3, weight=1)
            root.maxsize(4096, 4096)
            root.minsize(*FULL_MIN)
            root.geometry("%dx%d+%d+%d" % (*self._full_size, x, y))
            self.header.grid()
            self.hero.grid()
            self.pages.grid()
            self._refresh_notice()
            if self._pinned != self._pinned_before_compact:
                self._set_pin(self._pinned_before_compact)
        self._refresh_hero()

    def _toggle_pin(self) -> None:
        self._set_pin(not self._pinned)
        self._say("Starkist stays on top of other windows." if self._pinned else "Starkist no longer stays on top.", "info")

    def _set_pin(self, pinned: bool) -> None:
        self._pinned = pinned
        self.root.attributes("-topmost", pinned)
        name = "pin_active" if pinned else "pin"
        self.pin_btn.configure(image=T.icon(name, 15), fg_color=T.SURFACE if pinned else "transparent")
        self.mini_pin_btn.configure(image=T.icon(name, 14), fg_color=T.FIELD if pinned else "transparent")

    def _practice_changed(self) -> None:
        self._refresh_hero()
        self._refresh_notice()

    def _mode_changed(self, _value: str | None = None) -> None:
        if self._mode == "device":
            self.device_row.grid(row=4, column=0, sticky="we", padx=14, pady=(0, 12))
            if not self._devices_loaded:
                self._refresh_devices()
        else:
            self.device_row.grid_remove()
        self._changed()

    def _set_threshold(self, value: float) -> None:
        value = round(min(THRESHOLD_MAX, max(THRESHOLD_MIN, float(value))), 2)
        self.threshold.set(value)
        self.threshold_val.configure(text=f"{value:.2f}")
        self.chart.set_threshold(value)

    def _threshold_slid(self, value: float) -> None:
        self._set_threshold(value)
        self._changed()

    def _threshold_dragged(self, value: float) -> None:
        self.threshold.set(value)
        self.threshold_val.configure(text=f"{value:.2f}")
        self._changed()

    def _key_message(self, text: str) -> None:
        if text:
            self.key_msg.configure(text=text)
            self.key_msg.grid(row=2, column=0, sticky="we", padx=14)
        else:
            self.key_msg.grid_remove()

    def _refresh_windows(self) -> None:
        try:
            titles = list_window_titles(exclude_pid=os.getpid())
        except OSError:
            titles = []
        current = self.window_title.get().strip()
        if current and current not in titles:
            titles.insert(0, current)
        self.window_box.configure(values=titles)

    def _refresh_devices(self) -> None:
        self._devices_loaded = True
        self.device_refresh.configure(state="disabled")

        def work() -> None:
            try:
                from starkist.sources import list_loopback_devices

                names = [DEFAULT_DEVICE_LABEL] + [d.name for d in list_loopback_devices()]
                error = ""
            except Exception as e:
                names, error = [DEFAULT_DEVICE_LABEL], f"Couldn't list audio devices: {e}"
            self._devices_q.put((names, error))  # picked up by _poll; Tk is only touched on its thread

        threading.Thread(target=work, daemon=True).start()

    def _set_devices(self, names: list[str], error: str) -> None:
        current = self.device.get().strip()
        if current and current not in names:
            names.append(current)
        self.device_box.configure(values=names)
        self.device_refresh.configure(state="normal")
        if error:
            self._say(error, "warn")

    def _restore_defaults(self) -> None:
        if not messagebox.askyesno(
            "Restore defaults", "Put timing, key and detection back to their defaults?\n\nThe chosen app is kept.",
            parent=self.root,
        ):
            return
        current_mode, current_title = self._mode, self.window_title.get().strip()
        base = Config()
        cfg = replace(
            base,
            capture=CaptureConfig(mode=current_mode, window_title=current_title, device=self._device_selector()),
            match=replace(base.match, cues=self._cues_path),
        )
        self._load_into_form(cfg)

    # -- sounds ----------------------------------------------------------------------------------

    def _cues_dir(self) -> Path:
        return Config(match=MatchConfig(cues=self._cues_path)).cues_dir

    def _reload_cues(self) -> None:
        try:
            self._cue_names = [fp.name for fp in load_fingerprints(self._cues_dir())]
            problem = ""
        except Exception as e:
            self._cue_names, problem = [], str(e)
        for bar, _val in self.cue_rows.values():
            bar.master.destroy()
        self.cue_rows.clear()
        for i, name in enumerate(self._cue_names):
            self.cue_rows[name] = self._meter(self.meter_box, 1 + i // 2, i % 2, name, T.GLASS)
        if self._cue_names:
            self.cue_list.configure(text="   ".join(self._cue_names), text_color=T.TEXT)
        else:
            self.cue_list.configure(
                text="No sounds yet. Add a short .wav recording of the sound Starkist should react to.",
                text_color=T.AMBER,
            )
            if problem:
                self._say(f"No sounds loaded: {problem}", "warn")
        self._refresh_hero()

    def _add_cues(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.root, title="Add a sound", filetypes=[("WAV audio", "*.wav")],
        )
        if not paths:
            return
        from starkist.wav import read_wav

        folder = self._cues_dir()
        folder.mkdir(parents=True, exist_ok=True)
        added = 0
        for p in map(Path, paths):
            dest = folder / p.name
            try:
                read_wav(p)
            except Exception as e:
                self._say(f"{p.name} wasn't added: {e}", "warn")
                continue
            if dest.exists():
                self._say(f"{p.name} is already in the sounds folder.", "warn")
                continue
            try:
                shutil.copyfile(p, dest)
                added += 1
            except OSError as e:
                self._say(f"{p.name} wasn't added: {e}", "warn")
        if added:
            self._reload_cues()
            self._say(f"Added {added} sound{'s' if added != 1 else ''}.", "info")
            if self._state == "listening":
                self.notice.grid(row=2, column=0, sticky="we", padx=14, pady=(8, 0))

    def _open_cues_folder(self) -> None:
        folder = self._cues_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(folder)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        except OSError as e:
            self._say(f"Couldn't open {folder}: {e}", "warn")

    # -- start / stop ----------------------------------------------------------------------------

    def _toggle(self) -> None:
        if self._state == "stopped":
            self._start()
        elif self._state == "listening":
            self._stop_run()

    def _start(self) -> None:
        cfg = self._validate()
        if cfg is None:
            self._set_compact(False)
            self._show_page(PAGE_SETUP)
            self.save_label.configure(text=self._form_error, text_color=T.RED)
            return
        if not self._cue_names:
            self._set_compact(False)
            self._show_page(PAGE_SETUP)
            self._say("Add at least one sound before starting.", "warn")
            return
        try:
            save_config(cfg, self.config_path)
        except OSError as e:
            self._say(f"Settings couldn't be saved: {e}", "warn")
        self._stop.clear()
        dry = bool(self.dry_run.get())
        self._running_cfg, self._running_dry = cfg, dry
        self._cue_count = self._press_count = 0
        self._last_cue_at = None
        self._started_at = time.monotonic()
        self.stat_cues.configure(text="0")
        self.stat_presses.configure(text="0")
        self.stat_last.configure(text="–")

        def work() -> None:
            from starkist import runner

            try:
                code = runner.run(cfg, stop=self._stop, dry_run=dry, on_stats=self._on_stats)
                if code != 0:
                    log.error("Stopped because of the problem above.")
            except Exception as e:
                log.error("%s: %s", type(e).__name__, e)

        self._worker = threading.Thread(target=work, name="starkist-run", daemon=True)
        self._worker.start()
        self._set_state("listening")
        self._show_page(PAGE_MONITOR)

    def _stop_run(self) -> None:
        self._stop.set()
        self._set_state("stopping")

    def _restart(self) -> None:
        if self._state == "listening":
            self._restart_pending = True
            self._stop_run()

    def _set_state(self, state: str) -> None:
        self._state = state
        buttons = ((self.start_btn, 13), (self.mini_start_btn, 12))
        if state == "listening":
            for btn, size in buttons:
                btn.configure(
                    text="Stop", image=T.icon("stop", size - 1), state="normal", fg_color=T.FIELD,
                    hover_color=T.LINE, text_color=T.TEXT, border_width=1, border_color=T.FAINT,
                )
            self.chart.set_live(True)
        elif state == "stopping":
            for btn, _size in buttons:
                btn.configure(state="disabled")
        else:
            for btn, size in buttons:
                btn.configure(
                    text="Start", image=T.icon("play", size), state="normal", fg_color=T.CORAL,
                    hover_color=T.CORAL_HOVER, text_color=T.INK, border_width=0,
                )
            self.chart.set_live(False)
            self.level_bar.set(0)
            self.level_val.configure(text="–")
            for bar, val in self.cue_rows.values():
                bar.set(0)
                bar.configure(progress_color=T.GLASS)
                val.configure(text="–", text_color=T.MUTED)
        self.notice.grid_remove()
        self._refresh_hero()

    def _on_stats(self, level_dbfs: float, peaks: dict[str, float]) -> None:
        try:
            self._stats_q.put_nowait((level_dbfs, peaks))
        except queue.Full:
            pass

    def _test_key(self) -> None:
        key = normalize_key(self.key.get())
        if key not in VK_CODES:
            self._key_message("Choose a key first.")
            return
        self.test_btn.configure(state="disabled")

        def countdown(n: int) -> None:
            if n > 0:
                self.test_btn.configure(text=f"in {n}…")
                self.root.after(1000, lambda: countdown(n - 1))
                return
            try:
                KeySender().press(key)
                self._say(f"Test: pressed {key_label(key)}.", "press")
                self._key_message("")
            except Exception as e:
                self._say(f"Test press failed: {e}", "error")
                self._key_message(f"The test press failed: {e}")
            self.test_btn.configure(text="Test", state="normal")

        self._key_message(f"Switch to the app now. {key_label(key)} is pressed in {TEST_KEY_COUNTDOWN_S} seconds.")
        countdown(TEST_KEY_COUNTDOWN_S)

    # ---------------------------------------------------------------------------------------------
    # activity and polling
    # ---------------------------------------------------------------------------------------------

    def _say(self, text: str, tag: str = "info") -> None:
        self._append_log(time.strftime("%H:%M:%S"), tag, "", text)

    def _append_log(self, stamp: str, tag: str, kind: str, text: str) -> None:
        box = self.log_text
        box.configure(state="normal")
        box.insert("end", stamp + "  ", "time")
        box.insert("end", text + "\n", tag)
        lines = int(box.index("end-1c").split(".")[0])
        if lines > LOG_MAX_LINES:
            box.delete("1.0", f"{lines - LOG_MAX_LINES}.0")
        box.see("end")
        box.configure(state="disabled")
        if kind == "cue":
            self._cue_count += 1
            self._last_cue_at = time.monotonic()
            self.stat_cues.configure(text=str(self._cue_count))
            self.chart.mark_cue()
        elif kind == "press":
            self._press_count += 1
            self.stat_presses.configure(text=str(self._press_count))
        if kind and self._compact:
            self._refresh_mini_sub()

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _poll(self) -> None:
        while True:
            try:
                self._append_log(*self._log_q.get_nowait())
            except queue.Empty:
                break

        try:
            self._set_devices(*self._devices_q.get_nowait())
        except queue.Empty:
            pass

        latest = None
        while True:
            try:
                latest = self._stats_q.get_nowait()
            except queue.Empty:
                break
            if self._state == "listening":
                self.chart.push(max(latest[1].values(), default=0.0))
        if latest is not None and self._state == "listening":
            level, peaks = latest
            self.level_bar.set(max(0.0, min(1.0, (level - LEVEL_FLOOR_DB) / -LEVEL_FLOOR_DB)))
            self.level_val.configure(text=f"{level:.0f} dB")
            thr = float(self.threshold.get())
            for name, score in peaks.items():
                row = self.cue_rows.get(name)
                if row is None:
                    continue
                bar, val = row
                bar.set(max(0.0, min(1.0, score)))
                hot = score >= thr
                bar.configure(progress_color=T.CORAL if hot else T.GLASS)
                val.configure(
                    text="–" if score < 0 else f"{score:.2f}",
                    text_color=T.CORAL if hot else (T.TEXT if score >= 0 else T.MUTED),
                )

        if self._state != "stopped":
            if self._state == "listening" and not self._running_dry and self._window_open is not False:
                self.sub_label.configure(text=f"Running for {_clock(time.monotonic() - (self._started_at or 0))}")
            if self._last_cue_at is not None:
                self.stat_last.configure(text=_ago(time.monotonic() - self._last_cue_at))
            if self._compact:
                self._refresh_mini_sub()
            if self._worker is not None and not self._worker.is_alive():
                self._worker = None
                self._set_state("stopped")
                if self._restart_pending:
                    self._restart_pending = False
                    self._start()
        self.root.after(POLL_MS, self._poll)

    def _on_close(self) -> None:
        self._stop.set()
        if self._autosave_job is not None:
            self.root.after_cancel(self._autosave_job)
            self._autosave()
        log.removeHandler(self._handler)
        self.root.destroy()


def _report_crash(error: BaseException) -> None:
    """The GUI launcher has no console, so a startup failure is written down and shown."""
    import traceback

    path = PROJECT_ROOT / "starkist-error.log"
    text = "".join(traceback.format_exception(error))
    try:
        path.write_text(text, encoding="utf-8")
        where = f"\n\nDetails were saved to {path}"
    except OSError:
        where = ""
    try:
        messagebox.showerror("Starkist", f"Starkist ran into a problem and has to close.\n\n{error}{where}")
    except tk.TclError:
        pass


def main(config_path: str | None = None) -> int:
    try:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        root = ctk.CTk()
        root.report_callback_exception = lambda _t, e, _tb: log.error("%s: %s", type(e).__name__, e)
        App(root, config_path)
        root.mainloop()
    except Exception as e:
        _report_crash(e)
        return 1
    return 0
