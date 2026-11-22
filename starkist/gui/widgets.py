"""Custom widgets: the live match trace, the key-capture button and small form pieces."""

from __future__ import annotations

import tkinter as tk
from collections import deque
from collections.abc import Callable

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from starkist.gui import theme as T
from starkist.gui.theme import Fonts
from starkist.keys import VK_CODES

THRESHOLD_MIN, THRESHOLD_MAX = 0.30, 0.95


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class TraceChart(ctk.CTkFrame):
    """The last ~40 s of the best match score, with the trigger line drawn across it.

    Drag the line (or its handle on the right) to change the threshold. Cues that fired are
    marked with a coral tick.
    """

    SPAN = 160  # samples kept; the runner reports four per second
    SS = 2  # supersampling for smooth lines

    def __init__(
        self, master: tk.Misc, fonts: Fonts, on_threshold: Callable[[float], None], height: int = 132
    ) -> None:
        super().__init__(master, fg_color=T.WELL, corner_radius=10, height=height)
        self.grid_propagate(False)
        self.pack_propagate(False)
        self._fonts = fonts
        self._on_threshold = on_threshold
        self._scores: deque[float] = deque(maxlen=self.SPAN)
        self._marks: deque[int] = deque(maxlen=64)
        self._tick = 0
        self._threshold = 0.66
        self._live = False
        self._dragging = False
        self._photo: ImageTk.PhotoImage | None = None
        self._redraw_job: str | None = None

        self.canvas = tk.Canvas(self, bg=T.WELL, highlightthickness=0, bd=0, cursor="sb_v_double_arrow")
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Configure>", lambda _e: self._schedule())
        self.canvas.bind("<Button-1>", self._drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

    # -- data ------------------------------------------------------------------------------------

    def set_threshold(self, value: float) -> None:
        self._threshold = min(THRESHOLD_MAX, max(THRESHOLD_MIN, float(value)))
        self._schedule()

    def set_live(self, live: bool) -> None:
        self._live = live
        if live:
            self._scores.clear()
            self._marks.clear()
            self._tick = 0
        self._schedule()

    def push(self, best_score: float) -> None:
        self._tick += 1
        self._scores.append(max(0.0, min(1.0, best_score)))
        self._schedule()

    def mark_cue(self) -> None:
        self._marks.append(self._tick)
        self._schedule()

    # -- interaction -----------------------------------------------------------------------------

    def _drag(self, event: tk.Event) -> None:
        h = self.canvas.winfo_height()
        top, bottom = self._plot_bounds(h)
        score = 1.0 - (event.y - top) / max(1, bottom - top)
        value = round(min(THRESHOLD_MAX, max(THRESHOLD_MIN, score)), 2)
        self._dragging = True
        if value != self._threshold:
            self._threshold = value
            self._on_threshold(value)
            self._schedule()

    def _release(self, _event: tk.Event) -> None:
        self._dragging = False
        self._schedule()

    # -- drawing ---------------------------------------------------------------------------------

    @staticmethod
    def _plot_bounds(h: int) -> tuple[int, int]:
        return 8, h - 6

    def _schedule(self) -> None:
        if self._redraw_job is None:
            self._redraw_job = self.after(16, self._redraw)

    def _redraw(self) -> None:
        self._redraw_job = None
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w < 20 or h < 20:
            return
        s = self.SS
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        img = Image.new("RGBA", (w * s, h * s), _rgb(T.WELL) + (255,))
        d = ImageDraw.Draw(img, "RGBA")
        top, bottom = self._plot_bounds(h)
        handle_w = int(46 * scale)
        right = w - handle_w - 4

        def y_of(score: float) -> float:
            return (bottom - (bottom - top) * score) * s

        for g in (0.25, 0.5, 0.75):
            d.line([(0, y_of(g)), (right * s, y_of(g))], fill=_rgb(T.LINE) + (110,), width=s)

        ty = y_of(self._threshold)
        # everything above the trigger line is the "fires" zone
        d.rectangle([0, y_of(1.0), right * s, ty], fill=_rgb(T.CORAL) + (16,))

        n = len(self._scores)
        if self._live and n >= 2:
            dx = right * s / (self.SPAN - 1)
            x0 = right * s - (n - 1) * dx
            pts = [(x0 + i * dx, y_of(v)) for i, v in enumerate(self._scores)]
            d.polygon([(pts[0][0], bottom * s), *pts, (pts[-1][0], bottom * s)], fill=_rgb(T.GLASS) + (38,))
            d.line(pts, fill=_rgb(T.GLASS) + (255,), width=int(2 * s), joint="curve")
            for mark in self._marks:
                age = self._tick - mark
                if age < n:
                    x = right * s - age * dx
                    d.line([(x, y_of(1.0)), (x, bottom * s)], fill=_rgb(T.CORAL) + (150,), width=s)
                    r = 3.5 * s
                    d.ellipse([x - r, ty - r, x + r, ty + r], fill=_rgb(T.CORAL) + (255,))
            lx, ly = pts[-1]
            r = 3 * s
            d.ellipse([lx - r, ly - r, lx + r, ly + r], fill=_rgb(T.TEXT) + (255,))

        # trigger line, dashed, ending in a handle that carries the value
        dash, x = 6 * s, 0
        while x < right * s:
            d.line([(x, ty), (min(x + dash, right * s), ty)], fill=_rgb(T.CORAL) + (230,), width=int(1.5 * s))
            x += dash * 2
        hh = int(10 * scale) * s
        d.rounded_rectangle(
            [right * s + 2 * s, ty - hh, (w - 1) * s, ty + hh],
            radius=hh,
            fill=_rgb(T.CORAL_HOVER if self._dragging else T.CORAL) + (255,),
        )

        img = img.resize((w, h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        c = self.canvas
        c.delete("all")
        c.create_image(0, 0, image=self._photo, anchor="nw")
        px = -round(11 * scale)
        c.create_text(
            right + 2 + (w - right - 2) / 2, ty / s, text=f"{self._threshold:.2f}",
            fill=T.INK, font=(self._fonts.display_family, px, "bold"),
        )
        if not (self._live and n >= 2):
            c.create_text(
                right / 2, (top + bottom) / 2 + (14 if self._threshold > 0.6 else -14) * scale,
                text="Listening…" if self._live else "Match scores appear here while Starkist listens",
                fill=T.FAINT, font=(self._fonts.body_family, -round(12 * scale)),
            )


# -- key capture ---------------------------------------------------------------------------------

_KEYSYMS = {
    "space": "space", "Return": "enter", "KP_Enter": "enter", "Tab": "tab",
    "minus": "-", "equal": "=", "bracketleft": "[", "bracketright": "]", "semicolon": ";",
    "apostrophe": "'", "quoteright": "'", "comma": ",", "period": ".", "slash": "/",
    "grave": "`", "quoteleft": "`", "backslash": "\\",
}
_MODIFIERS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Super_L", "Super_R",
    "Win_L", "Win_R", "Caps_Lock", "Meta_L", "Meta_R",
}


def key_from_event(event: tk.Event) -> str | None:
    """The Starkist key name for a Tk key event, or None when the key isn't supported."""
    sym = event.keysym
    if sym in _KEYSYMS:
        return _KEYSYMS[sym]
    low = sym.lower()
    return low if low in VK_CODES else None


def key_label(name: str) -> str:
    if len(name) == 1:
        return name.upper()
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    return name.capitalize()


class KeyCapture(ctk.CTkButton):
    """Shows the bound key as a key cap. Click it, then press the key you want."""

    def __init__(self, master: tk.Misc, fonts: Fonts, variable: tk.StringVar, on_message: Callable[[str], None]) -> None:
        super().__init__(
            master, text="", width=96, height=34, corner_radius=8, font=fonts.body_bold,
            fg_color=T.FIELD, hover_color=T.LINE, text_color=T.TEXT,
            border_width=1, border_color=T.LINE, command=self._begin,
        )
        self.var = variable
        self._on_message = on_message
        self._listening = False
        self._bind_id: str | None = None
        variable.trace_add("write", lambda *_: self._show())
        self._show()

    def _show(self) -> None:
        if not self._listening:
            self.configure(text=key_label(self.var.get() or "—"), border_color=T.LINE, text_color=T.TEXT)

    def _begin(self) -> None:
        if self._listening:
            self._end()
            return
        self._listening = True
        self.configure(text="Press a key…", border_color=T.CORAL, text_color=T.CORAL)
        top = self.winfo_toplevel()
        top.focus_set()
        self._bind_id = top.bind("<KeyPress>", self._captured, add="+")

    def _captured(self, event: tk.Event) -> str | None:
        if not self._listening or event.keysym in _MODIFIERS:
            return None
        if event.keysym == "Escape":
            self._end()
            return "break"
        name = key_from_event(event)
        if name is None:
            self._on_message("That key can't be sent. Use a letter, digit, F1–F12, Space, Enter, Tab or punctuation.")
            return "break"
        self._on_message("")
        self._end()
        self.var.set(name)
        return "break"

    def _end(self) -> None:
        self._listening = False
        if self._bind_id is not None:
            self.winfo_toplevel().unbind("<KeyPress>", self._bind_id)
            self._bind_id = None
        self._show()


# -- form pieces ---------------------------------------------------------------------------------


def entry(master: tk.Misc, fonts: Fonts, var: tk.StringVar, width: int = 60, **kw: object) -> ctk.CTkEntry:
    return ctk.CTkEntry(
        master, textvariable=var, width=width, height=32, corner_radius=8, fg_color=T.FIELD,
        border_color=T.LINE, border_width=1, text_color=T.TEXT, font=fonts.body, **kw,
    )


def ghost_button(
    master: tk.Misc, fonts: Fonts, text: str, command: Callable[[], None], image: ctk.CTkImage | None = None,
    width: int = 0,
) -> ctk.CTkButton:
    return ctk.CTkButton(
        master, text=text, command=command, image=image, width=width or 10, height=32, corner_radius=8,
        fg_color=T.FIELD, hover_color=T.LINE, text_color=T.TEXT, font=fonts.body,
        border_width=1, border_color=T.LINE, border_spacing=6,
    )


class RangeField(ctk.CTkFrame):
    """Two number boxes, 'from … to … s'. `value()` raises ValueError with a readable message."""

    def __init__(self, master: tk.Misc, fonts: Fonts, name: str, single: bool = False) -> None:
        super().__init__(master, fg_color="transparent")
        self.name = name
        self.single = single
        self.lo, self.hi = tk.StringVar(), tk.StringVar()
        self._entries = [entry(self, fonts, self.lo, justify="center")]
        self._entries[0].pack(side="left")
        if not single:
            ctk.CTkLabel(self, text="to", font=fonts.small, text_color=T.MUTED).pack(side="left", padx=6)
            self._entries.append(entry(self, fonts, self.hi, justify="center"))
            self._entries[1].pack(side="left")
        ctk.CTkLabel(self, text="s", font=fonts.small, text_color=T.MUTED, width=10).pack(side="left", padx=(6, 0))

    def variables(self) -> list[tk.StringVar]:
        return [self.lo] if self.single else [self.lo, self.hi]

    def set(self, lo: float, hi: float | None = None) -> None:
        self.lo.set(f"{lo:g}")
        if hi is not None:
            self.hi.set(f"{hi:g}")

    def mark(self, ok: bool) -> None:
        for e in self._entries:
            e.configure(border_color=T.LINE if ok else T.RED)

    def value(self) -> tuple[float, float]:
        try:
            a = float(self.lo.get().replace(",", "."))
            b = a if self.single else float(self.hi.get().replace(",", "."))
        except ValueError:
            self.mark(False)
            raise ValueError(f"{self.name}: enter a number of seconds") from None
        if a < 0 or b < a:
            self.mark(False)
            raise ValueError(
                f"{self.name}: can't be negative" if a < 0 else f"{self.name}: the first value can't be above the second"
            )
        self.mark(True)
        return (a, b)

    def text(self) -> str:
        """'0.4–2.2 s' for captions; falls back to the raw text while the user is typing."""
        a, b = self.lo.get().strip() or "?", self.hi.get().strip() or "?"
        return f"{a} s" if self.single or a == b else f"{a}–{b} s"
