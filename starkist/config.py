"""Settings loaded from config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.toml"


@dataclass(frozen=True)
class CaptureConfig:
    mode: str = "process"  # "process" (one application's audio) or "device" (loopback device)
    window_title: str = ""  # substring of the target window's title
    device: str = ""  # loopback device id or name substring for mode = "device"


@dataclass(frozen=True)
class MatchConfig:
    cues: str = "assets/cues"  # folder of reference .wav files, relative to the project root
    threshold: float = 0.66
    refractory_s: float = 3.0


@dataclass(frozen=True)
class ActionConfig:
    key: str = "space"
    first_delay_s: tuple[float, float] = (0.4, 1.2)
    second_delay_s: tuple[float, float] = (0.8, 2.0)
    hold_s: tuple[float, float] = (0.04, 0.10)
    require_focus: bool = True
    idle_timeout_s: float = 35.0  # press the key again after this long with no cue; 0 disables


@dataclass(frozen=True)
class Config:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    match: MatchConfig = field(default_factory=MatchConfig)
    action: ActionConfig = field(default_factory=ActionConfig)

    @property
    def cues_dir(self) -> Path:
        p = Path(self.match.cues)
        return p if p.is_absolute() else PROJECT_ROOT / p


def _range(value: Any, name: str) -> tuple[float, float]:
    if isinstance(value, int | float):
        return (float(value), float(value))
    if isinstance(value, list | tuple) and len(value) == 2:
        lo, hi = float(value[0]), float(value[1])
        if lo < 0 or hi < lo:
            raise ValueError(f"{name}: expected [min, max] with 0 <= min <= max, got {value!r}")
        return (lo, hi)
    raise ValueError(f"{name}: expected a number or [min, max], got {value!r}")


def _non_negative(value: Any, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: expected a number, got {value!r}") from None
    if v < 0:
        raise ValueError(f"{name}: must be 0 or more, got {value!r}")
    return v


_TEMPLATE = """\
# Starkist settings. Delays are seconds; a [min, max] pair is re-rolled uniformly every time.

[capture]
# "process": hear only the target application's own audio (Windows 10 2004+). Nothing else
#            playing on the PC can trigger or drown the cue. Needs window_title.
# "device":  hear a loopback device (everything on that output). See `starkist devices`.
mode = {mode}
# A substring of the target window's title. Used to find the process for "process" capture and
# for the focus check before each press. Leave empty only with mode = "device".
window_title = {window_title}
# Loopback device id or name substring for mode = "device". Empty = default output's monitor.
device = {device}

[match]
# Folder of reference .wav cues (relative to the project root). Each file becomes one cue.
cues = {cues}
# Normalized correlation needed to count as a cue (0..1). Run `starkist listen` to see scores.
threshold = {threshold}
# Ignore further cues for this long after one fires.
refractory_s = {refractory_s}

[action]
key = {key}
# Wait this long after the cue before the first press.
first_delay_s = {first_delay_s}
# Wait this long after the first press before the second press.
second_delay_s = {second_delay_s}
# How long each press is held down.
hold_s = {hold_s}
# Skip a press when the target window is not the foreground window.
require_focus = {require_focus}
# If no cue is heard for this long after the last press, press the key again. 0 disables.
idle_timeout_s = {idle_timeout_s}
"""


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_range(r: tuple[float, float]) -> str:
    return f"[{r[0]:g}, {r[1]:g}]"


def save_config(cfg: Config, path: Path | str | None = None) -> Path:
    path = Path(path) if path else DEFAULT_CONFIG
    text = _TEMPLATE.format(
        mode=_toml_str(cfg.capture.mode),
        window_title=_toml_str(cfg.capture.window_title),
        device=_toml_str(cfg.capture.device),
        cues=_toml_str(cfg.match.cues),
        threshold=f"{cfg.match.threshold:g}",
        refractory_s=f"{cfg.match.refractory_s:g}",
        key=_toml_str(cfg.action.key),
        first_delay_s=_toml_range(cfg.action.first_delay_s),
        second_delay_s=_toml_range(cfg.action.second_delay_s),
        hold_s=_toml_range(cfg.action.hold_s),
        require_focus="true" if cfg.action.require_focus else "false",
        idle_timeout_s=f"{cfg.action.idle_timeout_s:g}",
    )
    path.write_text(text, encoding="utf-8")
    return path


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    if not path.exists():
        return Config()
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cap = raw.get("capture", {})
    match = raw.get("match", {})
    act = raw.get("action", {})
    mode = str(cap.get("mode", "process")).lower()
    if mode not in ("process", "device"):
        raise ValueError(f'capture.mode must be "process" or "device", got {mode!r}')
    return Config(
        capture=CaptureConfig(
            mode=mode,
            window_title=str(cap.get("window_title", "")),
            device=str(cap.get("device", "")),
        ),
        match=MatchConfig(
            cues=str(match.get("cues", "assets/cues")),
            threshold=float(match.get("threshold", 0.66)),
            refractory_s=float(match.get("refractory_s", 3.0)),
        ),
        action=ActionConfig(
            key=str(act.get("key", "space")),
            first_delay_s=_range(act.get("first_delay_s", [0.4, 1.2]), "action.first_delay_s"),
            second_delay_s=_range(act.get("second_delay_s", [0.8, 2.0]), "action.second_delay_s"),
            hold_s=_range(act.get("hold_s", [0.04, 0.10]), "action.hold_s"),
            require_focus=bool(act.get("require_focus", True)),
            idle_timeout_s=_non_negative(act.get("idle_timeout_s", 35.0), "action.idle_timeout_s"),
        ),
    )
