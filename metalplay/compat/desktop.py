"""Wine virtual desktop wrapper — keeps the Steam session in one macOS window."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from metalplay.runtime.wine import WineRuntime, wine_command

DEFAULT_DESKTOP_NAME = "metalplay-steam"


def detect_display_size() -> str:
    """Return WxH in logical pixels for the primary (menu-bar) display."""
    from metalplay.tune.detect import _primary_screen_geometry

    w, h, _ = _primary_screen_geometry()
    if w > 0 and h > 0:
        return f"{w}x{h}"
    return "1920x1080"


def _monitor_count() -> int:
    try:
        import Quartz

        return len(Quartz.NSScreen.screens())
    except Exception:
        return 1


def _virtual_desktop_default() -> str:
    """Steam virtual desktop helps on single-monitor Macs; it often breaks on multi-monitor."""
    if sys.platform != "darwin":
        return "0"
    return "auto" if _monitor_count() <= 1 else "0"


def virtual_desktop_enabled() -> bool:
    val = os.environ.get("METALPLAY_VIRTUAL_DESKTOP", _virtual_desktop_default()).lower()
    if val in ("0", "false", "no", "off", ""):
        return False
    if val == "auto":
        return _monitor_count() <= 1
    if val in ("1", "true", "yes", "on"):
        return True
    return bool(re.match(r"^\d+x\d+$", val))


def virtual_desktop_resolution() -> str | None:
    val = os.environ.get("METALPLAY_VIRTUAL_DESKTOP", _virtual_desktop_default()).lower()
    if val in ("0", "false", "no", "off", ""):
        return None
    if val == "auto":
        if _monitor_count() > 1:
            return None
        return detect_display_size()
    if re.match(r"^\d+x\d+$", val):
        return val
    return detect_display_size()


def build_steam_command(
    runtime: WineRuntime,
    steam_exe_win: str,
    launch_args: list[str],
    *,
    desktop_name: str = DEFAULT_DESKTOP_NAME,
    resolution: str | None = None,
) -> list[str]:
    """
    Build wine command line, optionally wrapping Steam in explorer /desktop=.
    steam_exe_win must use Windows path separators (C:\\...).
    """
    if resolution:
        return wine_command(
            runtime.wine_bin,
            "explorer.exe",
            f"/desktop={desktop_name},{resolution}",
            steam_exe_win,
            *launch_args,
        )
    return wine_command(runtime.wine_bin, steam_exe_win, *launch_args)


def to_wine_path(native_path: Path, runtime: WineRuntime, bottle: Path) -> str:
    """Best-effort Z: path for wine; fallback to manual C: mapping."""
    env = {
        **os.environ,
        "WINEPREFIX": str(bottle),
        "PATH": f"{runtime.bin_dir}:{os.environ.get('PATH', '')}",
    }
    try:
        result = subprocess.run(
            wine_command(runtime.wine_bin, "winepath", "-w", str(native_path)),
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().replace("\r", "")
    except (OSError, subprocess.SubprocessError):
        pass
    # Fallback: map WINEPREFIX/drive_c -> C:\
    drive_c = bottle / "drive_c"
    try:
        rel = native_path.resolve().relative_to(drive_c.resolve())
        return "C:\\" + str(rel).replace("/", "\\")
    except ValueError:
        return str(native_path)
