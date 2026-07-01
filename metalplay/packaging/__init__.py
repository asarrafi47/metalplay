"""macOS application packaging."""

from metalplay.packaging.macos_app import (
    APP_NAME,
    build_app,
    install_app,
    open_app,
    uninstall_app,
)

__all__ = ["APP_NAME", "build_app", "install_app", "open_app", "uninstall_app"]
