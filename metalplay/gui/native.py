"""Native macOS window for MetalPlay (pywebview / WKWebView)."""

from __future__ import annotations

from pathlib import Path


def open_native_window(url: str, *, icon_path: Path | None = None) -> None:
    """Open MetalPlay in a native window instead of the system browser."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "Native window requires pywebview. Install with: pip install pywebview"
        ) from exc

    window = webview.create_window(
        "MetalPlay",
        url,
        width=1180,
        height=820,
        min_size=(900, 640),
        text_select=True,
    )
    start_kwargs: dict = {"debug": False}
    if icon_path and icon_path.is_file():
        start_kwargs["icon"] = str(icon_path)
    webview.start(**start_kwargs)


def native_available() -> bool:
    try:
        import webview  # noqa: F401

        return True
    except ImportError:
        return False
