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
from metalplay.tune.power import wrap_caffeinate


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
    elif graphics == "moltenvk":
        env.update(_moltenvk_env(profile))
    elif graphics == "wined3d":
        env["WINEDLLOVERRIDES"] = "d3d11,b;d3d10core,b;dxgi,b"

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


def _moltenvk_env(profile: dict) -> dict[str, str]:
    """Environment for D3D12 via vkd3d → MoltenVK → Metal."""
    return {
        "WINEDLLOVERRIDES": profile.get(
            "dll_overrides",
            "d3d12=n,b;d3d11=n,b;dxgi=n,b;vulkan-1=n,b;",
        ),
        "VK_ICD_FILENAMES": profile.get(
            "vk_icd",
            "/usr/local/share/vulkan/icd.d/MoltenVK_icd.json",
        ),
        "MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS": "1",
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
    if should_caffeinate() or os.environ.get("METALPLAY_PERFORMANCE_MODE") == "1":
        cmd = wrap_caffeinate(cmd)

    work_dir = str(cwd) if cwd else None
    result = subprocess.run(cmd, env={**os.environ, **env}, cwd=work_dir)
    return result.returncode
