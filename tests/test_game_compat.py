"""Unit tests for per-game compat: window size, launch options, Rockstar launch args."""

from __future__ import annotations

import os
from unittest.mock import patch

from metalplay.compat.games import (
    GAME_PROFILES,
    _parse_resolution,
    compat_profile,
)
from metalplay.compat.rockstar import (
    ROCKSTAR_BYPASSES,
    rockstar_crossover_launch_target,
)


def test_parse_resolution_valid():
    assert _parse_resolution("1728x1117") == (1728, 1117)
    assert _parse_resolution(" 2560 X 1440 ") == (2560, 1440)


def test_parse_resolution_invalid():
    assert _parse_resolution("") is None
    assert _parse_resolution("garbage") is None
    assert _parse_resolution("100x100") is None  # below sane minimum
    assert _parse_resolution("1728") is None


def test_game_window_size_honors_env():
    from metalplay.compat import games

    with patch.dict(os.environ, {"METALPLAY_GAME_RESOLUTION": "1600x1000"}):
        assert games._game_window_size() == (1600, 1000)


def test_game_window_size_falls_back_to_screen():
    from metalplay.compat import games

    with (
        patch.dict(os.environ, {"METALPLAY_GAME_RESOLUTION": "bogus"}),
        patch("metalplay.config.Config.load") as load,
        patch("metalplay.tune.detect._primary_screen_geometry", return_value=(1728, 1117, 2.0)),
    ):
        load.return_value.extra_env = {}
        assert games._game_window_size() == (1728, 1117)


def test_gmod_profile_stability_settings():
    prof = GAME_PROFILES["4000"]
    opts = prof.launch_options.split()
    assert "-gl" not in opts  # Windows GMod has no OpenGL renderer
    assert "-windowed" in opts and "-noborder" in opts
    assert prof.graphics == "wined3d"


def test_source_fallback_profile(tmp_path):
    (tmp_path / "hl2.exe").write_bytes(b"MZ")
    prof = compat_profile("999999", install_path=tmp_path)
    assert prof is not None
    assert prof.graphics == "wined3d"


def test_profile_dll_overrides_syntax():
    # Wine silently ignores malformed WINEDLLOVERRIDES entries (e.g. "dinput8,n,b"
    # instead of "dinput8=n,b"), so every profile string must parse as key=value.
    for app_id, prof in GAME_PROFILES.items():
        for pair in prof.dll_overrides.split(";"):
            if pair:
                assert "=" in pair, f"malformed override in {app_id}: {pair}"


def test_cxstart_command_shape(tmp_path):
    from unittest.mock import patch
    from metalplay.compat import crossover

    exe = tmp_path / "gmod.exe"
    with patch.object(crossover, "cxstart_bin", return_value=tmp_path / "cxstart"):
        cmd = crossover.cxstart_command(exe, ["-steam", "-game", "garrysmod"])
    assert cmd is not None
    assert "--no-update" in cmd  # suppress CrossOver package-install dialogs
    assert "--wait" in cmd  # caller waits on the game process
    assert cmd[cmd.index("--bottle") + 1] == crossover.CROSSOVER_BOTTLE_NAME
    assert cmd[-4:] == [str(exe), "-steam", "-game", "garrysmod"]


def test_rockstar_launch_args_clean(tmp_path):
    for bypass in ROCKSTAR_BYPASSES:
        _, args = rockstar_crossover_launch_target(tmp_path, bypass, bypass.app_id)
        loc = next(a for a in args if a.startswith("-steamLocation="))
        assert '"' not in loc  # Wine escapes embedded quotes into the command line
        assert not loc.endswith("\\")  # trailing backslash eats the closing quote
        assert loc.split("=", 1)[1].startswith("C:\\")


def test_dxvk_env_overrides():
    from metalplay.launcher.run import _dxvk_env

    env = _dxvk_env({})
    assert "d3d11,dxgi,d3d10core=n" in env["WINEDLLOVERRIDES"]
    assert "vulkan-1=b" in env["WINEDLLOVERRIDES"]
    assert env["DXVK_LOG_LEVEL"] == "error"


def test_moltenvk_env_skips_missing_icd():
    from metalplay.launcher.run import _moltenvk_env

    with patch("metalplay.launcher.run.Path.is_file", return_value=False):
        env = _moltenvk_env({})
    assert "VK_ICD_FILENAMES" not in env  # dangling ICD path breaks Vulkan
    assert env["MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS"] == "1"


def test_moltenvk_env_honors_profile_icd():
    from metalplay.launcher.run import _moltenvk_env

    env = _moltenvk_env({"vk_icd": "/tmp/MoltenVK_icd.json"})
    assert env["VK_ICD_FILENAMES"] == "/tmp/MoltenVK_icd.json"


def test_wined3d_env_valid_override_syntax():
    from metalplay.launcher.run import build_env
    from metalplay.config import Config
    from unittest.mock import MagicMock

    runtime = MagicMock()
    runtime.bin_dir = "/tmp/bin"
    env = build_env(runtime, __import__("pathlib").Path("/tmp/b"), Config(), graphics="wined3d")
    for pair in env["WINEDLLOVERRIDES"].split(";"):
        if pair:
            assert "=" in pair, f"malformed override: {pair}"
