"""Desktop front end (customtkinter). `starkist gui` or the `starkist-gui` launcher opens it."""

from starkist.gui.app import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
