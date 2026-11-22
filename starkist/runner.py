"""The main loop: capture audio, match cues, and run the key sequence on a worker thread."""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
from collections.abc import Callable

from starkist.config import Config
from starkist.fingerprint import load_fingerprints
from starkist.keys import KeySender
from starkist.matcher import CueEvent, CueMatcher
from starkist.sources import AudioUnavailable, iter_chunks, open_capture
from starkist.window import WindowRef, find_window, is_foreground

log = logging.getLogger("starkist")

BLOCKSIZE = 1024  # ~46 ms at 22050 Hz
WINDOW_POLL_S = 2.0
RETRY_S = 3.0


def _uniform(r: tuple[float, float]) -> float:
    return random.uniform(r[0], r[1])


class Sequencer:
    """Runs the two-press sequence off the audio thread; drops cues that arrive while busy.

    If `action.idle_timeout_s` is positive and no cue arrives for that long after the last press,
    the key is pressed once more and the timer restarts."""

    def __init__(self, cfg: Config, sender: KeySender) -> None:
        self.cfg = cfg
        self.sender = sender
        self.window: WindowRef | None = None
        self._q: queue.Queue[CueEvent] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="sequencer", daemon=True)
        self._last_activity = time.monotonic()

    def start(self) -> None:
        self._last_activity = time.monotonic()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def trigger(self, event: CueEvent) -> None:
        self._last_activity = time.monotonic()
        try:
            self._q.put_nowait(event)
        except queue.Full:
            log.info("cue %s ignored: a sequence is already running", event.name)

    def _focused(self) -> bool:
        if not self.cfg.action.require_focus:
            return True
        state = is_foreground(self.window)
        return state is None or state

    def _press(self, which: str) -> bool:
        self._last_activity = time.monotonic()
        if not self._focused():
            log.warning("%s press skipped: target window is not in front", which)
            return False
        hold = _uniform(self.cfg.action.hold_s)
        self.sender.press(self.cfg.action.key, hold_s=hold)
        log.info("%s press: %s held %.0f ms", which, self.cfg.action.key, hold * 1000)
        return True

    def _idle_check(self) -> None:
        timeout = self.cfg.action.idle_timeout_s
        if timeout <= 0:
            return
        idle = time.monotonic() - self._last_activity
        if idle >= timeout:
            log.info("no cue for %.0f s: pressing %s again", idle, self.cfg.action.key)
            self._press("idle")

    def _loop(self) -> None:
        a = self.cfg.action
        while not self._stop.is_set():
            try:
                event = self._q.get(timeout=0.2)
            except queue.Empty:
                self._idle_check()
                continue
            d1 = _uniform(a.first_delay_s)
            d2 = _uniform(a.second_delay_s)
            log.info(
                "cue %s (score %.2f): first press in %.2f s, second %.2f s after",
                event.name, event.score, d1, d2,
            )
            time.sleep(d1)
            if not self._press("first"):
                continue
            time.sleep(d2)
            self._press("second")


def _wait_for_window(cfg: Config, stop: threading.Event) -> WindowRef | None:
    title = cfg.capture.window_title
    if not title:
        return None
    announced = False
    while not stop.is_set():
        w = find_window(title)
        if w is not None:
            log.info("target window: %r (pid %s)", w.title, w.pid)
            return w
        if not announced:
            log.warning("no window with %r in its title; waiting...", title)
            announced = True
        stop.wait(RETRY_S)
    return None


StatsCallback = Callable[[float, dict[str, float]], None]
STATS_INTERVAL_S = 0.25


def run(
    cfg: Config,
    *,
    stop: threading.Event | None = None,
    dry_run: bool = False,
    on_stats: StatsCallback | None = None,
) -> int:
    """Capture, match and act until `stop` is set. `on_stats(level_dbfs, peaks)` is called about
    four times a second from the audio thread when given."""
    stop = stop or threading.Event()
    fps = load_fingerprints(cfg.cues_dir)
    log.info("loaded %d reference cue(s): %s", len(fps), ", ".join(fp.name for fp in fps))
    sender = KeySender()
    seq = Sequencer(cfg, sender)
    if not dry_run:
        seq.start()

    try:
        while not stop.is_set():
            window = _wait_for_window(cfg, stop)
            if stop.is_set():
                break
            if cfg.capture.mode == "process" and window is None:
                log.error("capture.mode = \"process\" needs capture.window_title; set it in config.toml")
                return 2
            seq.window = window
            matcher = CueMatcher(fps, threshold=cfg.match.threshold, refractory_s=cfg.match.refractory_s)
            try:
                source = open_capture(
                    cfg.capture.mode,
                    pid=window.pid if window else None,
                    device=cfg.capture.device,
                    blocksize=BLOCKSIZE,
                )
            except AudioUnavailable as e:
                log.error("cannot open audio: %s", e)
                return 2
            log.info("listening on %s (threshold %.2f)", source.name, cfg.match.threshold)
            last_poll = last_stats = time.monotonic()
            try:
                with source:
                    for chunk in iter_chunks(source, BLOCKSIZE):
                        if stop.is_set():
                            break
                        for event in matcher.feed(chunk):
                            if dry_run:
                                log.info("cue %s (score %.2f) at %.2f s [dry run]", event.name, event.score, event.t)
                            else:
                                seq.trigger(event)
                        now = time.monotonic()
                        if on_stats is not None and now - last_stats >= STATS_INTERVAL_S:
                            last_stats = now
                            on_stats(matcher.level_dbfs, matcher.take_peaks())
                        if window is not None and now - last_poll >= WINDOW_POLL_S:
                            last_poll = now
                            current = find_window(cfg.capture.window_title)
                            if current is None or current.pid != window.pid:
                                log.warning("target window went away; reopening capture")
                                break
            except RuntimeError as e:
                log.warning("%s; reopening capture", e)
                stop.wait(1.0)
            except AudioUnavailable as e:
                log.error("audio error: %s; retrying in %.0f s", e, RETRY_S)
                stop.wait(RETRY_S)
    finally:
        seq.stop()  # also on the early error returns, so no press can follow a failed start
    return 0


def listen(cfg: Config, *, stop: threading.Event | None = None, interval_s: float = 0.5) -> int:
    """Print level and per-cue peak scores so the threshold can be tuned."""
    stop = stop or threading.Event()
    fps = load_fingerprints(cfg.cues_dir)
    window = _wait_for_window(cfg, stop)
    if stop.is_set():
        return 0
    if cfg.capture.mode == "process" and window is None:
        log.error("capture.mode = \"process\" needs capture.window_title; set it in config.toml")
        return 2
    matcher = CueMatcher(fps, threshold=cfg.match.threshold, refractory_s=cfg.match.refractory_s)
    source = open_capture(
        cfg.capture.mode,
        pid=window.pid if window else None,
        device=cfg.capture.device,
        blocksize=BLOCKSIZE,
    )
    log.info("listening on %s; Ctrl+C to stop", source.name)
    last = time.monotonic()
    with source:
        for chunk in iter_chunks(source, BLOCKSIZE):
            if stop.is_set():
                break
            for event in matcher.feed(chunk):
                print(f"  >>> CUE {event.name} score {event.score:.2f} at {event.t:.2f} s")
            if time.monotonic() - last >= interval_s:
                last = time.monotonic()
                peaks = matcher.take_peaks()
                cols = "  ".join(f"{n}={v:5.2f}" for n, v in peaks.items())
                print(f"{chunk.t_end:8.1f} s  level {matcher.level_dbfs:6.1f} dBFS  {cols}")
    return 0
