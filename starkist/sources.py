"""Audio sources: one application's output (process loopback) or a system loopback device."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import numpy as np


class AudioUnavailable(Exception):
    """No usable audio backend or loopback device on this machine."""


@dataclass(frozen=True)
class AudioChunk:
    samples: np.ndarray  # float32 mono
    sample_rate: int
    t_end: float  # seconds since the source started, at the last sample


@runtime_checkable
class AudioSource(Protocol):
    sample_rate: int
    name: str

    def __enter__(self) -> AudioSource: ...

    def read(self, n_frames: int) -> np.ndarray:
        """Block until n_frames are available; return float32 mono of exactly that length."""
        ...

    def __exit__(self, *exc: object) -> None: ...


def iter_chunks(source: AudioSource, n_frames: int) -> Iterator[AudioChunk]:
    """Read fixed-size chunks forever (callers break out); t_end counts frames read."""
    total = 0
    while True:
        samples = source.read(n_frames)
        total += len(samples)
        yield AudioChunk(samples, source.sample_rate, total / source.sample_rate)


# -- system loopback through the `soundcard` package ---------------------------------------------


@dataclass(frozen=True)
class LoopbackDevice:
    id: str
    name: str
    is_default: bool


def _soundcard() -> Any:
    try:
        import soundcard
    except Exception as e:
        raise AudioUnavailable(f"soundcard backend unavailable: {type(e).__name__}: {e}") from e
    return soundcard


def _loopback_mics() -> tuple[list[Any], Any]:
    sc = _soundcard()
    try:
        mics = sc.all_microphones(include_loopback=True)
        mics = [m for m in mics if getattr(m, "isloopback", False)]
    except Exception as e:
        raise AudioUnavailable(f"cannot list audio devices: {type(e).__name__}: {e}") from e
    try:
        speaker = sc.default_speaker()
    except Exception:
        speaker = None
    return mics, speaker


def _is_default(mic: Any, speaker: Any) -> bool:
    if speaker is None:
        return False
    return str(speaker.id) in str(mic.id) or str(speaker.name) in str(mic.name)


def list_loopback_devices() -> list[LoopbackDevice]:
    mics, speaker = _loopback_mics()
    return [LoopbackDevice(str(m.id), str(m.name), _is_default(m, speaker)) for m in mics]


class DeviceSource:
    """AudioSource over a `soundcard` loopback microphone (hears everything on that output)."""

    def __init__(self, mic: Any, sample_rate: int = 22050, blocksize: int = 1024) -> None:
        self.mic = mic
        self.name = str(mic.name)
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self._rec: Any = None

    def __enter__(self) -> DeviceSource:
        self._rec = self.mic.recorder(
            samplerate=self.sample_rate, channels=1, blocksize=self.blocksize
        )
        self._rec.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._rec is not None:
            self._rec.__exit__(*exc)
            self._rec = None

    def read(self, n_frames: int) -> np.ndarray:
        data = np.asarray(self._rec.record(numframes=n_frames), dtype=np.float32)
        if data.ndim == 2:
            data = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
        return np.ascontiguousarray(data, dtype=np.float32)


def open_device(selector: str = "", *, sample_rate: int = 22050, blocksize: int = 1024) -> DeviceSource:
    """Open by exact id or name, else by id substring or case-insensitive name substring.

    An empty selector opens the monitor of the default output.
    """
    mics, speaker = _loopback_mics()
    if not mics:
        raise AudioUnavailable("no loopback devices found")
    if selector:
        wanted = selector.lower()
        mic = next((m for m in mics if selector in (str(m.id), str(m.name))), None) or next(
            (m for m in mics if selector in str(m.id) or wanted in str(m.name).lower()), None
        )
        if mic is None:
            raise AudioUnavailable(f"no loopback device matches {selector!r}; run `devices`")
    else:
        mic = next((m for m in mics if _is_default(m, speaker)), mics[0])
    return DeviceSource(mic, sample_rate, blocksize)


# -- choosing ------------------------------------------------------------------------------------


def open_capture(
    mode: str,
    *,
    pid: int | None = None,
    device: str = "",
    sample_rate: int = 22050,
    blocksize: int = 1024,
) -> Any:
    """`mode` "process": only `pid`'s own audio. `mode` "device": the loopback `device`."""
    if mode == "device":
        return open_device(device, sample_rate=sample_rate, blocksize=blocksize)
    if mode != "process":
        raise AudioUnavailable(f"unknown capture mode {mode!r} (use \"process\" or \"device\")")
    if sys.platform != "win32":
        raise AudioUnavailable("process capture is Windows-only; use capture.mode = \"device\"")
    if pid is None:
        raise AudioUnavailable("process capture needs a running target window (set window_title)")
    from starkist.proc_loopback import ProcessLoopbackSource

    return ProcessLoopbackSource(pid, sample_rate=sample_rate, blocksize=blocksize)
