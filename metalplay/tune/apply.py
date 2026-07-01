"""Build and apply hardware-tuned MetalPlay profiles."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from metalplay import paths
from metalplay.bottle import manager as bottles
from metalplay.config import Config
from metalplay.controller.profiles import GTA_V_APP_ID
from metalplay.runtime.wine import WineRuntime, wine_command
from metalplay.tune.detect import HardwareProfile, detect_hardware
from metalplay.tune.power import cooling_notes, enable_high_performance

ProgressCallback = Callable[[str], None]

TUNE_DIR = paths.home() / "tune"
HARDWARE_FILE = TUNE_DIR / "hardware.json"
PROFILE_FILE = TUNE_DIR / "profile.json"


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


def tune_dir() -> Path:
    return TUNE_DIR


def load_applied_profile() -> dict | None:
    if not PROFILE_FILE.is_file():
        return None
    try:
        return json.loads(PROFILE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _build_tune_settings(hw: HardwareProfile) -> dict:
    """Derive performance settings from detected hardware."""
    wine_cpus = hw.performance_cores or max(4, hw.cpu_cores // 2)
    is_high_end = hw.tier in ("m4-max", "m4-pro", "apple-max", "m4")

    return {
        "tier": hw.tier,
        "chip": hw.chip,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "performance_mode": True,
        "game_resolution": hw.recommended_game_resolution,
        "virtual_desktop": "0",
        "virtual_desktop_steam": "0",
        "caffeinate_gaming": True,
        "high_power_mode": True,
        "retina_mode": "y" if hw.display.retina and is_high_end else "n",
        "wine_cpu_count": wine_cpus,
        "dxmt_log_level": "error",
        "extra_env": {
            "WINEDEBUG": "-all",
            "DXMT_LOG_LEVEL": "error",
            "MVK_CONFIG_LOG_LEVEL": "0",
            "METALPLAY_VIRTUAL_DESKTOP": "auto",
            "METALPLAY_PERFORMANCE_MODE": "1",
            f"METALPLAY_GAME_RESOLUTION": hw.recommended_game_resolution,
            f"WINE_CPU_COUNT": str(wine_cpus),
        },
        "game_profiles": _default_game_profiles(hw),
        "cooling_note": cooling_notes(),
    }


def _default_game_profiles(hw: HardwareProfile) -> dict:
    w, h = hw.recommended_game_resolution.split("x", 1)
    gta_env = {
        "WINEDEBUG": "-all",
        "DXMT_LOG_LEVEL": "error",
        "METALPLAY_VIRTUAL_DESKTOP": "0",
        "METALPLAY_PERFORMANCE_MODE": "1",
        "WINE_CPU_COUNT": str(hw.performance_cores or 12),
        # Hint for in-game settings (logged at launch)
        "METALPLAY_TARGET_RESOLUTION": hw.recommended_game_resolution,
    }
    return {
        GTA_V_APP_ID: {
            "name": "Grand Theft Auto V",
            "graphics": "dxmt",
            "env": gta_env,
            "dll_overrides": "dxgi,d3d11,d3d10core=n,b;winemenubuilder.exe=d",
            "controller": {
                "steam_input": False,
                "prefer": "xinput",
            },
            "notes": (
                f"Set resolution to {w}x{h} in-game. "
                "DX11 via DXMT. Story Mode only (no GTA Online)."
            ),
        },
        "steam": {
            "graphics": "dxmt",
            "env": {
                "METALPLAY_VIRTUAL_DESKTOP": "auto",
            },
        },
    }


def _apply_gaming_registry(
    runtime: WineRuntime,
    bottle: Path,
    retina_mode: str,
    callback: ProgressCallback | None = None,
) -> None:
    """Wine Mac driver tweaks for sharp full-resolution rendering."""
    from metalplay.compat.registry import apply_registry

    env = {
        **dict(__import__("os").environ),
        "WINEPREFIX": str(bottle),
        "WINEARCH": "win64",
        "PATH": f"{runtime.bin_dir}:{__import__('os').environ.get('PATH', '')}",
    }
    # Keep tune profile in sync when apply_registry reads hardware/profile files.
    if PROFILE_FILE.is_file():
        try:
            profile = json.loads(PROFILE_FILE.read_text())
            profile["retina_mode"] = retina_mode
            PROFILE_FILE.write_text(json.dumps(profile, indent=2) + "\n")
        except (json.JSONDecodeError, OSError):
            pass
    apply_registry(runtime, bottle, env, callback=callback)


def apply_tune(
    callback: ProgressCallback | None = None,
    *,
    apply_power: bool = True,
    apply_registry: bool = True,
) -> dict:
    """
    Detect hardware, write tune profile, update config.json, and apply power settings.
    Returns summary dict.
    """
    paths.ensure_dirs()
    TUNE_DIR.mkdir(parents=True, exist_ok=True)

    hw = detect_hardware()
    settings = _build_tune_settings(hw)

    HARDWARE_FILE.write_text(json.dumps(hw.to_dict(), indent=2) + "\n")
    PROFILE_FILE.write_text(json.dumps(settings, indent=2) + "\n")
    _log(f"Detected: {hw.chip} — tier {hw.tier}", callback)
    _log(f"Target game resolution: {hw.recommended_game_resolution}", callback)

    config = Config.load()
    config.dxmt_log_level = settings["dxmt_log_level"]
    config.extra_env = {**config.extra_env, **settings["extra_env"]}
    config.game_profiles.update(settings["game_profiles"])
    config.save()
    _log("Updated ~/.metalplay/config.json", callback)

    power_actions: list[str] = []
    if apply_power and settings.get("high_power_mode"):
        power_actions = enable_high_performance(callback)
        settings["power_actions"] = power_actions
        if not power_actions:
            script = Path(__file__).resolve().parent / "enable-power-mode.sh"
            _log(
                "Power: pmset needs admin — run once: sudo bash "
                f"{script}",
                callback,
            )

    if apply_registry:
        from metalplay.runtime.wine import detect_installed_runtimes, get_runtime

        runtime = get_runtime(config.wine_runtime) or (
            detect_installed_runtimes()[0] if detect_installed_runtimes() else None
        )
        bottle = bottles.bottle_path("steam")
        if runtime and bottle.is_dir():
            _apply_gaming_registry(
                runtime, bottle, settings["retina_mode"], callback,
            )

    _log(cooling_notes(), callback)
    settings["hardware"] = hw.to_dict()
    return settings


def performance_env(config: Config | None = None) -> dict[str, str]:
    """Return merged performance environment from applied tune profile."""
    profile = load_applied_profile()
    if not profile:
        return {}
    env = dict(profile.get("extra_env", {}))
    if config:
        env.update(config.extra_env)
    return env


def should_caffeinate() -> bool:
    profile = load_applied_profile()
    return bool(profile and profile.get("caffeinate_gaming"))


def game_resolution_hint() -> str | None:
    profile = load_applied_profile()
    if not profile:
        return None
    return profile.get("game_resolution")
