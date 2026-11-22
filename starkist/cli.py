"""Command line: run, listen, devices, test-key."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from starkist import __version__
from starkist.config import DEFAULT_CONFIG, load_config


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="starkist", description="Sound cue -> randomized key presses")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="path to config.toml")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="listen for the cue and press the key")
    r.add_argument("--dry-run", action="store_true", help="log matches but never press keys")

    sub.add_parser("gui", help="open the graphical front end")
    sub.add_parser("listen", help="print levels and match scores for tuning the threshold")
    sub.add_parser("devices", help="list loopback devices for capture.mode = \"device\"")

    t = sub.add_parser("test-key", help="press the configured key after a countdown")
    t.add_argument("--seconds", type=float, default=3.0, help="countdown before pressing")
    return p


def _stop_on_ctrl_c(stop: threading.Event) -> None:
    import signal

    def handler(_sig: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, handler)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "gui":
        from starkist.gui import main as gui_main

        return gui_main(args.config)

    if args.cmd == "devices":
        from starkist.sources import AudioUnavailable, list_loopback_devices

        try:
            devices = list_loopback_devices()
        except AudioUnavailable as e:
            print(e, file=sys.stderr)
            return 2
        for d in devices:
            mark = " (default)" if d.is_default else ""
            print(f"{d.name}{mark}\n    id: {d.id}")
        return 0

    if args.cmd == "test-key":
        from starkist.keys import KeySender

        sender = KeySender()
        print(f"pressing {cfg.action.key!r} in {args.seconds:g} s; focus the window you want it in")
        time.sleep(args.seconds)
        sender.press(cfg.action.key)
        print("done")
        return 0

    from starkist import runner

    stop = threading.Event()
    _stop_on_ctrl_c(stop)
    try:
        if args.cmd == "listen":
            return runner.listen(cfg, stop=stop)
        return runner.run(cfg, stop=stop, dry_run=args.dry_run)
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        logging.getLogger("starkist").error("%s: %s", type(e).__name__, e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
