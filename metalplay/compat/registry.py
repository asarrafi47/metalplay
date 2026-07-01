"""Registry tweaks for Steam + Wine macOS driver under MetalPlay."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from metalplay.compat.display import SteamDisplaySettings, steam_display_settings
from metalplay.runtime.wine import WineRuntime, wine_command

ProgressCallback = Callable[[str], None]

_BASE_REGISTRY_CMDS: list[list[str]] = [
    ["reg", "add", r"HKLM\Software\Microsoft\Windows NT\CurrentVersion",
     "/v", "ProductName", "/t", "REG_SZ", "/d", "Microsoft Windows 10 Pro", "/f"],
    ["reg", "add", r"HKLM\Software\Microsoft\Windows NT\CurrentVersion",
     "/v", "CurrentBuild", "/t", "REG_SZ", "/d", "19045", "/f"],
    ["reg", "add", r"HKLM\Software\Microsoft\Windows NT\CurrentVersion",
     "/v", "CurrentVersion", "/t", "REG_SZ", "/d", "6.3", "/f"],
    ["reg", "add", r"HKCU\Software\Wine\Mac Driver",
     "/v", "AllowImmovableWindows", "/t", "REG_SZ", "/d", "n", "/f"],
    ["reg", "add", r"HKCU\Software\Wine\Mac Driver",
     "/v", "UsePreciseCoords", "/t", "REG_SZ", "/d", "y", "/f"],
    ["reg", "add", r"HKCU\Control Panel\Mouse",
     "/v", "MouseSpeed", "/t", "REG_SZ", "/d", "0", "/f"],
    ["reg", "add", r"HKCU\Software\Valve\Steam",
     "/v", "DisableGPU", "/t", "REG_DWORD", "/d", "1", "/f"],
]


def _display_registry_cmds(settings: SteamDisplaySettings) -> list[list[str]]:
    pct = int(round(settings.scale_factor * 100))
    return [
        ["reg", "add", r"HKCU\Software\Wine\Mac Driver",
         "/v", "RetinaMode", "/t", "REG_SZ", "/d", settings.retina_mode, "/f"],
        ["reg", "add", r"HKCU\Control Panel\Desktop",
         "/v", "LogPixels", "/t", "REG_DWORD", "/d", str(settings.log_pixels), "/f"],
        ["reg", "add", r"HKCU\Control Panel\Desktop",
         "/v", "Win8DpiScaling", "/t", "REG_DWORD", "/d", "1", "/f"],
        ["reg", "add", r"HKCU\Control Panel\Desktop",
         "/v", "FontSmoothing", "/t", "REG_SZ", "/d", "2", "/f"],
        ["reg", "add", r"HKCU\Control Panel\Desktop",
         "/v", "FontSmoothingType", "/t", "REG_DWORD", "/d", "2", "/f"],
        ["reg", "add", r"HKCU\Control Panel\Desktop\WindowMetrics",
         "/v", "AppliedDPI", "/t", "REG_SZ", "/d", str(pct), "/f"],
    ]


def sync_display_registry(
    runtime: WineRuntime,
    bottle: Path,
    env: dict[str, str],
    callback: ProgressCallback | None = None,
) -> SteamDisplaySettings:
    """Refresh DPI / Retina registry from the current macOS display (fast path)."""
    settings = steam_display_settings()
    for cmd in _display_registry_cmds(settings):
        subprocess.run(
            wine_command(runtime.wine_bin, *cmd),
            env=env,
            capture_output=True,
            timeout=30,
        )
    if callback:
        cef = settings.cef_device_scale_factor or "auto (per-window)"
        callback(
            f"Compat layer: dynamic display {settings.logical_width}x{settings.logical_height} "
            f"@ {int(settings.scale_factor * 100)}%, DPI={settings.log_pixels}, CEF={cef}"
        )
    return settings


def apply_registry(
    runtime: WineRuntime,
    bottle: Path,
    env: dict[str, str],
    callback: ProgressCallback | None = None,
) -> SteamDisplaySettings:
    settings = steam_display_settings()
    for cmd in _BASE_REGISTRY_CMDS + _display_registry_cmds(settings):
        subprocess.run(
            wine_command(runtime.wine_bin, *cmd),
            env=env,
            capture_output=True,
            timeout=30,
        )
    if callback:
        cef = settings.cef_device_scale_factor or "auto (per-window)"
        callback(
            f"Compat layer: display RetinaMode={settings.retina_mode}, "
            f"DPI={settings.log_pixels} ({int(settings.scale_factor * 100)}%), "
            f"CEF={cef}"
        )
    return settings
