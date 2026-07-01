"""Launch MetalPlay GUI — native window (default on macOS) or browser."""

from __future__ import annotations

import sys


def main() -> None:
    import platform

    port = 8765
    native = platform.system() == "Darwin"
    open_browser = True
    args = sys.argv[1:]

    while args:
        arg = args[0]
        if arg in ("--native", "-n"):
            native = True
            args = args[1:]
        elif arg in ("--browser", "-b"):
            native = False
            args = args[1:]
        elif arg == "--desktop" or arg == "-d":
            try:
                import tkinter  # noqa: F401
                from metalplay.gui.app import main as desktop_main

                desktop_main()
                return
            except ImportError:
                print("tkinter not available — using native/web UI instead.")
            args = args[1:]
        elif arg == "--port" and len(args) > 1:
            port = int(args[1])
            args = args[2:]
        else:
            args = args[1:]

    if native:
        from metalplay.gui.native import native_available

        if native_available():
            open_browser = False
        else:
            print("pywebview not installed — opening in browser.")
            print("For a native app window: pip install pywebview")
            native = False

    from metalplay.gui.web import main as web_main

    web_main(port=port, open_browser=open_browser, native_window=native)


if __name__ == "__main__":
    main()
