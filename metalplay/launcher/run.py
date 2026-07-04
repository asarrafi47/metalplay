"""Game launcher with Metal/DXMT environment configuration."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from metalplay.config import Config
from metalplay.controller.compat import controller_env
from metalplay.controller.profiles import controller_profile_for
from metalplay.runtime.wine import WineRuntime, check_rosetta, wine_command
from metalplay.tune.apply import performance_env, should_caffeinate
from metalplay.tune.power import restore_balanced_power, wrap_caffeinate


def build_env(
    runtime: WineRuntime,
    bottle: Path,
    config: Config,
    *,
    graphics: str | None = None,
    game_profile: dict | None = None,
    app_id: str | None = None,
) -> dict[str, str]:
    """Build environment variables for launching a Windows game via Metal."""
    graphics = graphics or config.default_graphics
    profile = game_profile or {}

    env: dict[str, str] = {
        "WINEPREFIX": str(bottle),
        "PATH": f"{runtime.bin_dir}:{os.environ.get('PATH', '')}",
    }

    # Graphics backend → Metal translation layer
    if graphics in ("dxmt", "auto"):
        env.update(_dxmt_env(config, profile))
    elif graphics == "dxvk":
        env.update(_dxvk_env(profile))
    elif graphics == "moltenvk":
        env.update(_moltenvk_env(profile))
    elif graphics == "wined3d":
        env["WINEDLLOVERRIDES"] = "d3d9,d3d11,d3d10core,dxgi=b;winemetal=d"

    # Merge profile and user overrides
    env.update(performance_env(config))
    env.update(config.extra_env)
    env.update(profile.get("env", {}))

    ctrl = controller_profile_for(app_id, config)
    env.update(controller_env(ctrl, existing_overrides=env.get("WINEDLLOVERRIDES", "")))
    return env


def _dxmt_env(config: Config, profile: dict) -> dict[str, str]:
    """Environment for DXMT — Direct3D 10/11 translated directly to Metal."""
    overrides = profile.get("dll_overrides", "dxgi,d3d11,d3d10core=n,b;")
    env = {
        "WINEDLLOVERRIDES": overrides,
        "DXMT_LOG_LEVEL": profile.get("dxmt_log_level", config.dxmt_log_level),
        # Disable Wine's OpenGL renderer — we want Metal via DXMT
        "WINE_DISABLE_OPENGL": "1",
    }
    if profile.get("dxmt_shader_cache") is False:
        env["DXMT_SHADER_CACHE"] = "0"
    log_path = profile.get("dxmt_log_path")
    if log_path:
        env["DXMT_LOG_PATH"] = str(log_path)
    return env


_MOLTENVK_ICD_PATHS = (
    "/opt/homebrew/etc/vulkan/icd.d/MoltenVK_icd.json",
    "/opt/homebrew/share/vulkan/icd.d/MoltenVK_icd.json",
    "/usr/local/share/vulkan/icd.d/MoltenVK_icd.json",
)


def _moltenvk_env(profile: dict) -> dict[str, str]:
    """Environment for D3D12 via Wine's vkd3d → winevulkan → MoltenVK → Metal."""
    env = {
        "WINEDLLOVERRIDES": profile.get(
            "dll_overrides",
            "d3d12=n,b;d3d11=n,b;dxgi=n,b;vulkan-1=b;",
        ),
        "MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS": "1",
        "MVK_CONFIG_RESUME_LOST_DEVICE": "1",
    }
    # Only point the loader at an ICD that actually exists. Without one,
    # CrossOver-lineage winevulkan loads the runtime's bundled libMoltenVK directly —
    # a dangling VK_ICD_FILENAMES breaks Vulkan entirely.
    icd = profile.get("vk_icd")
    if not icd:
        icd = next((p for p in _MOLTENVK_ICD_PATHS if Path(p).is_file()), None)
    if icd:
        env["VK_ICD_FILENAMES"] = str(icd)
    return env


def _dxvk_env(profile: dict) -> dict[str, str]:
    """
    Environment for DXVK — D3D10/11 → Vulkan → MoltenVK → Metal.

    Alternative to DXMT for titles DXMT does not handle yet. Requires the bottle
    to carry DXVK d3d11/d3d10core (see swap_bottle_to_dxvk).
    """
    return {
        "WINEDLLOVERRIDES": profile.get(
            "dll_overrides",
            "d3d11,dxgi,d3d10core=n;vulkan-1=b;winemetal=d",
        ),
        "DXVK_LOG_LEVEL": profile.get("dxvk_log_level", "error"),
        "MVK_CONFIG_RESUME_LOST_DEVICE": "1",
        "MVK_CONFIG_FULL_IMAGE_VIEW_SWIZZLE": "1",
    }


def launch(
    runtime: WineRuntime,
    bottle: Path,
    executable: str | Path,
    args: list[str] | None = None,
    config: Config | None = None,
    *,
    graphics: str | None = None,
    game_name: str | None = None,
    cwd: str | Path | None = None,
    use_rosetta: bool | None = None,
) -> int:
    """Launch a Windows game through Wine with Metal graphics."""
    config = config or Config.load()
    profile = config.get_game_profile(game_name) if game_name else {}
    gfx = graphics or config.default_graphics
    if gfx == "dxvk":
        from metalplay.compat.graphics import swap_bottle_to_dxvk

        try:
            swap_bottle_to_dxvk(bottle)
        except Exception as exc:
            print(f"Warning: DXVK unavailable ({exc}) — falling back to DXMT.")
            graphics = "dxmt"
    env = build_env(
        runtime, bottle, config, graphics=graphics, game_profile=profile, app_id=game_name,
    )

    use_rosetta = use_rosetta if use_rosetta is not None else config.use_rosetta

    exe = str(executable)
    wine_cmd = wine_command(runtime.wine_bin, exe, use_rosetta=use_rosetta)
    if args:
        wine_cmd.extend(args)

    if use_rosetta and platform.machine() == "arm64" and not check_rosetta():
        print("Warning: Rosetta 2 may be required for x86_64 Wine builds.")
    cmd = wine_cmd
    power_boosted = False
    if should_caffeinate() or os.environ.get("METALPLAY_PERFORMANCE_MODE") == "1":
        from metalplay.tune.power import enable_high_performance

        enable_high_performance()
        cmd = wrap_caffeinate(cmd)
        power_boosted = True

    work_dir = str(cwd) if cwd else None
    try:
        result = subprocess.run(cmd, env={**os.environ, **env}, cwd=work_dir)
        return result.returncode
    finally:
        if power_boosted:
            restore_balanced_power()
