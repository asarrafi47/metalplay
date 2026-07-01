"""Display scale helpers for sharp Wine / Steam UI on Retina Macs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from metalplay import paths
from metalplay.tune.detect import DisplayInfo, _primary_screen_geometry, detect_hardware

HARDWARE_FILE = paths.home() / "tune" / "hardware.json"
PROFILE_FILE = paths.home() / "tune" / "profile.json"


@dataclass(frozen=True)
class SteamDisplaySettings:
    retina_mode: str  # y | n
    log_pixels: int  # 96 = 100%, 192 = 200%
    scale_factor: float
    logical_width: int
    logical_height: int
    physical_width: int
    physical_height: int
    cef_scale_mode: str  # auto | forced

    @property
    def cef_device_scale_factor(self) -> str | None:
        """
        Chromium --force-device-scale-factor override, or None for dynamic DPI.
        When None, CEF reads Windows LogPixels and stays sharp at any window size.
        """
        override = os.environ.get("METALPLAY_CEF_DEVICE_SCALE_FACTOR", "").strip().lower()
        if override in ("", "auto", "dynamic"):
            pass
        elif override in ("0", "off", "false", "no"):
            return None
        else:
            return os.environ.get("METALPLAY_CEF_DEVICE_SCALE_FACTOR", "").strip()

        if self.retina_mode != "y":
            return None
        # LogPixels (see steam_display_settings) handles sharpness — do not also force
        # CEF scale or SwiftShader hits EGL_BAD_ALLOC and the window goes blank.
        return None


def _load_hardware_display() -> DisplayInfo | None:
    if not HARDWARE_FILE.is_file():
        return None
    try:
        data = json.loads(HARDWARE_FILE.read_text())
        d = data.get("display", {})
        return DisplayInfo(
            name=d.get("name", "Unknown"),
            physical_width=int(d.get("physical_width", 1920)),
            physical_height=int(d.get("physical_height", 1080)),
            logical_width=int(d.get("logical_width", 1920)),
            logical_height=int(d.get("logical_height", 1080)),
            retina=bool(d.get("retina", True)),
            gpu_cores=int(d.get("gpu_cores", 0)),
        )
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def _preferred_retina_mode(display: DisplayInfo) -> str:
    profile_mode: str | None = None
    if PROFILE_FILE.is_file():
        try:
            profile = json.loads(PROFILE_FILE.read_text())
            profile_mode = profile.get("retina_mode")
        except (json.JSONDecodeError, OSError):
            profile_mode = None
    if profile_mode in ("y", "n"):
        return profile_mode
    return "y" if display.retina else "n"


def _live_display_info() -> DisplayInfo:
    """Current macOS logical size + panel physical size (primary screen, not monitor span)."""
    base = _load_hardware_display()
    if not base:
        base = detect_hardware().display

    lw, lh, backing = _primary_screen_geometry()
    lw, lh = max(1, lw), max(1, lh)
    pw = int(round(lw * backing))
    ph = int(round(lh * backing))
    retina = backing >= 1.25 or base.retina

    return DisplayInfo(
        name=base.name,
        physical_width=pw,
        physical_height=ph,
        logical_width=lw,
        logical_height=lh,
        retina=retina,
        gpu_cores=base.gpu_cores,
    )


def steam_display_settings() -> SteamDisplaySettings:
    """Resolve Wine + CEF display settings from the current macOS display layout."""
    display = _live_display_info()

    lw = display.logical_width
    lh = display.logical_height
    pw = display.physical_width
    ph = display.physical_height

    _, _, backing = _primary_screen_geometry()
    scale = round(backing if backing >= 1.0 else pw / lw, 2)
    if scale < 1.0:
        scale = 1.0

    # Single-process CPU raster (notpop path): keep LogPixels at 96 so CEF stays at
    # device-scale-factor=1. RetinaMode=y on Retina Macs gives a sharp backing store.
    profile_retina = _preferred_retina_mode(display)
    if os.environ.get("METALPLAY_STEAM_RETINA", "").lower() in ("0", "n", "no", "off"):
        retina_mode = "n"
    elif os.environ.get("METALPLAY_STEAM_RETINA", "").lower() in ("1", "y", "yes", "on"):
        retina_mode = "y"
    else:
        retina_mode = profile_retina
    # Single-process CEF (--disable-gpu): LogPixels can match Retina scale for readable UI.
    # Multi-process SwiftShader blacks out at scale>1; single-process does not.
    if retina_mode == "y" and scale > 1.0:
        log_pixels = int(round(96 * scale))
    else:
        log_pixels = 96

    draft = SteamDisplaySettings(
        retina_mode=retina_mode,
        log_pixels=log_pixels,
        scale_factor=scale,
        logical_width=lw,
        logical_height=lh,
        physical_width=pw,
        physical_height=ph,
        cef_scale_mode="auto",
    )
    if draft.cef_device_scale_factor:
        return SteamDisplaySettings(
            retina_mode=draft.retina_mode,
            log_pixels=draft.log_pixels,
            scale_factor=draft.scale_factor,
            logical_width=draft.logical_width,
            logical_height=draft.logical_height,
            physical_width=draft.physical_width,
            physical_height=draft.physical_height,
            cef_scale_mode="forced",
        )
    return draft


def virtual_desktop_resolution_for_steam() -> str | None:
    """
    Virtual desktop size when enabled.
    Match current logical desktop; Wine RetinaMode supplies the sharp backing store.
    """
    settings = steam_display_settings()
    return f"{settings.logical_width}x{settings.logical_height}"
