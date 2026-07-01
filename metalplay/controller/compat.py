"""Wine controller compatibility for MetalPlay bottles."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from metalplay.controller.detect import list_controllers
from metalplay.runtime.wine import WineRuntime, wine_command

ProgressCallback = Callable[[str], None]

CONTROLLER_DLL_OVERRIDES = "xinput9_1_0,xinput1_4,dinput8=n,b"

_CONTROLLER_REGISTRY_CMDS: list[list[str]] = [
    ["reg", "add", r"HKCU\Software\Wine\DirectInput",
     "/v", "DefaultDeadZone", "/t", "REG_SZ", "/d", "0", "/f"],
]


def merge_dll_overrides(existing: str, controller_part: str) -> str:
    """Merge controller DLL overrides into an existing WINEDLLOVERRIDES string."""
    if not existing.strip():
        return controller_part.strip()
    if not controller_part.strip():
        return existing.strip()

    merged: dict[str, str] = {}
    order: list[str] = []

    def _ingest(part: str) -> None:
        for chunk in part.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" in chunk:
                dlls, flags = chunk.split("=", 1)
                for dll in dlls.split(","):
                    dll = dll.strip()
                    if not dll:
                        continue
                    if dll not in merged:
                        order.append(dll)
                    merged[dll] = flags.strip()
            else:
                dll = chunk.strip()
                if dll and dll not in merged:
                    order.append(dll)
                    merged[dll] = ""

    _ingest(existing)
    _ingest(controller_part)

    flag_order: list[str] = []
    flag_to_dlls: dict[str, list[str]] = {}
    for dll in order:
        flags = merged[dll]
        if flags not in flag_to_dlls:
            flag_to_dlls[flags] = []
            flag_order.append(flags)
        flag_to_dlls[flags].append(dll)

    grouped: list[str] = []
    for flags in flag_order:
        dlls = flag_to_dlls[flags]
        grouped.append(f"{','.join(dlls)}={flags}" if flags else dlls[0])
    return ";".join(grouped)


def test_wine_joystick(runtime: WineRuntime, bottle: Path) -> tuple[bool, str]:
    """Lightweight check that Wine can query DirectInput registry in the bottle."""
    import os

    env = {
        **os.environ,
        "WINEPREFIX": str(bottle),
        "WINEARCH": "win64",
        "PATH": f"{runtime.bin_dir}:{os.environ.get('PATH', '')}",
        "WINEDEBUG": "-all",
    }
    result = subprocess.run(
        wine_command(
            runtime.wine_bin,
            "reg",
            "query",
            r"HKCU\Software\Wine\DirectInput",
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode == 0:
        return True, "DirectInput registry reachable in bottle"
    return False, "DirectInput registry missing — run: metalplay steam repair"


def _steam_input_enabled(profile: dict) -> bool:
    value = profile.get("steam_input", False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")
    return bool(value)


def controller_env(
    profile: dict | None = None,
    *,
    existing_overrides: str = "",
) -> dict[str, str]:
    """Return env vars for controller DLL overrides and Steam Input preferences."""
    profile = profile or {}
    env: dict[str, str] = {}

    prefer = profile.get("prefer", "xinput")
    part = CONTROLLER_DLL_OVERRIDES if prefer == "xinput" else "dinput8=n,b"
    merged = merge_dll_overrides(existing_overrides, part)
    if merged:
        env["WINEDLLOVERRIDES"] = merged

    if not _steam_input_enabled(profile):
        env["SteamGamepad_EnableConfigSupport"] = "0"

    return env


def _registry_key_present(runtime: WineRuntime, env: dict[str, str], key: str) -> bool:
    result = subprocess.run(
        wine_command(runtime.wine_bin, "reg", "query", key),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def apply_controller_registry(
    runtime: WineRuntime,
    bottle: Path,
    env: dict[str, str],
    callback: ProgressCallback | None = None,
) -> list[str]:
    """Apply Wine registry tweaks for gamepad support if not already present."""
    actions: list[str] = []
    key = r"HKCU\Software\Wine\DirectInput"
    if _registry_key_present(runtime, env, key):
        return actions

    for cmd in _CONTROLLER_REGISTRY_CMDS:
        subprocess.run(
            wine_command(runtime.wine_bin, *cmd),
            env=env,
            capture_output=True,
            timeout=10,
        )
    actions.append("controller-registry")
    if callback:
        callback("Compat layer: applied controller DirectInput registry")
    return actions


def _dll_overrides_ok(overrides: str) -> bool:
    normalized = overrides.lower().replace(" ", "")
    return "xinput9_1_0" in normalized and "dinput8" in normalized


def doctor(bottle: Path, runtime: WineRuntime | None = None, *, quick: bool = False) -> dict:
    """Check controller DLL overrides, detected hardware, and give recommendations."""
    from metalplay.config import Config

    config = Config.load()
    env_overrides = config.extra_env.get("WINEDLLOVERRIDES", "")
    controllers = list_controllers()
    connected = [c for c in controllers if c.connected]

    checks: list[str] = []
    recommendations: list[str] = []

    if _dll_overrides_ok(env_overrides):
        checks.append("OK: controller DLL overrides in config extra_env")
    else:
        checks.append("WARN: controller DLL overrides not in config — applied at launch via controller_env")
        recommendations.append(
            "Run metalplay tune apply or launch via metalplay steam run to merge xinput/dinput overrides"
        )

    if connected:
        names = ", ".join(c.name for c in connected)
        checks.append(f"OK: {len(connected)} controller(s) connected ({names})")
    elif controllers:
        checks.append(f"WARN: {len(controllers)} paired controller(s) but none connected")
        recommendations.append("Turn on your controller (Bluetooth or USB) before launching")
    else:
        checks.append("INFO: no game controllers detected")
        recommendations.append("Pair an Xbox, DualSense, or DualShock controller in System Settings")

    steam_profiles = [
        (app_id, prof.get("controller", {}))
        for app_id, prof in config.game_profiles.items()
        if prof.get("controller")
    ]
    disabled = [
        app_id for app_id, ctrl in steam_profiles if not _steam_input_enabled(ctrl)
    ]
    if disabled:
        checks.append(
            f"INFO: Steam Input disabled in profile for app(s): {', '.join(disabled)}"
        )
        recommendations.append(
            "Also disable Steam Input in Steam → game Properties → Controller → "
            "Override → Disable Steam Input (MetalPlay sets SteamGamepad_EnableConfigSupport=0 at launch)"
        )

    if not quick and runtime and bottle.is_dir():
        reg_env = {
            **dict(__import__("os").environ),
            "WINEPREFIX": str(bottle),
            "WINEARCH": "win64",
            "PATH": f"{runtime.bin_dir}:{__import__('os').environ.get('PATH', '')}",
        }
        if _registry_key_present(runtime, reg_env, r"HKCU\Software\Wine\DirectInput"):
            checks.append("OK: Wine DirectInput registry present in bottle")
        else:
            checks.append("WARN: Wine DirectInput registry not applied — run metalplay steam repair")
            recommendations.append("Run: metalplay steam repair (applies controller registry)")

    return {
        "ok": bool(connected) or not controllers,
        "checks": checks,
        "recommendations": recommendations,
        "controllers": [
            {"name": c.name, "kind": c.kind, "connection": c.connection, "connected": c.connected}
            for c in controllers
        ],
    }
