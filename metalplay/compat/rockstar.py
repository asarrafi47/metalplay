"""Rockstar Games Launcher compatibility for Steam-launched titles."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Callable

from metalplay.compat.graphics import swap_bottle_to_wined3d
from metalplay.runtime.wine import WineRuntime, wine_command

ProgressCallback = Callable[[str], None]

# Steam App IDs that route through Rockstar Games Launcher.
ROCKSTAR_STEAM_APP_IDS: frozenset[str] = frozenset({"271590", "3240220"})

GTA_V_ENHANCED_INSTALL = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Grand Theft Auto V Enhanced\\"
)

_FAKE_LAUNCHER_EXE = "EpicGamesLauncher.exe"
_FIX_BAT = "fix.bat"
_FIX_BAT_CONTENT = 'start "" EpicGamesLauncher.exe PlayGTAV.exe %*\r\n'

@dataclass(frozen=True)
class RockstarBypass:
    app_id: str
    install_dir: str
    stub_exe: str
    game_exe: str

ROCKSTAR_BYPASSES: tuple[RockstarBypass, ...] = (
    RockstarBypass(
        "3240220",
        "Grand Theft Auto V Enhanced",
        "PlayGTAV.exe",
        "GTA5_Enhanced.exe",
    ),
    RockstarBypass(
        "271590",
        "Grand Theft Auto V",
        "PlayGTAV.exe",
        "GTA5.exe",
    ),
)

# Rockstar Launcher CEF needs wined3d — not DXMT. Force Wine builtins per exe.
_ROCKSTAR_UI_EXES: tuple[str, ...] = (
    "launcher.exe",
    "azlauncher.exe",
    "azlauncherpatcher.exe",
    "launcherpatcher.exe",
    "playgtav.exe",
    "playrdr.exe",
    "rockstarservice.exe",
    "rockstarsteamhelper.exe",
    "socialclubhelper.exe",
)

# Game binaries load DXMT from WINEDLLPATH_PREPEND (native) before bottle builtins.
_ROCKSTAR_GAME_EXES: tuple[str, ...] = (
    "gta5.exe",
    "gta5_enhanced.exe",
    "gta5_enhanced_be.exe",
    "rdr2.exe",
)

# Load swapped bottle DLLs (n only — n,b falls back to DXMT builtins, and DXGI/D3D11
# then require winemetal.dll — CEF dies at "Initializing group 1").
# d3d11/d3d10core in the bottle are DXVK (Vulkan/MoltenVK) with a wined3d fallback;
# vulkan-1 stays builtin so DXVK can reach winevulkan + libMoltenVK.
_LAUNCHER_D3D_NATIVE = ("d3d11", "dxgi", "d3d10core", "d3d10_1")
_LAUNCHER_D3D_BUILTIN = ("d2d1", "dwrite", "vulkan-1")
_LAUNCHER_D3D_DISABLED = ("winemetal", "d3d12")
_D3D_DXMT = ("d3d11", "dxgi", "d3d10core", "d3d12", "vulkan-1", "winemetal")


def is_rockstar_steam_app(app_id: str | int | None) -> bool:
    if app_id is None:
        return False
    return str(app_id) in ROCKSTAR_STEAM_APP_IDS


def _reg_cmds_for_exe(exe: str, dlls: tuple[str, ...], mode: str) -> list[list[str]]:
    key = rf"HKCU\Software\Wine\AppDefaults\{exe}\DllOverrides"
    return [
        ["reg", "add", key, "/v", dll, "/t", "REG_SZ", "/d", mode, "/f"]
        for dll in dlls
    ]


def registry_cmds(install_dir: str = GTA_V_ENHANCED_INSTALL) -> list[list[str]]:
    """Registry keys required for Rockstar + Steam integration."""
    cmds: list[list[str]] = [
        ["reg", "add", r"HKLM\Software\Rockstar Games\GTA V Enhanced",
         "/v", "InstallFolderSteam", "/t", "REG_SZ", "/d", install_dir, "/f"],
        ["reg", "add", r"HKLM\Software\Rockstar Games\Steam\Launcher",
         "/v", "101072944", "/t", "REG_DWORD", "/d", "1", "/f"],
        ["reg", "add", r"HKLM\Software\Wow6432Node\Rockstar Games\Steam\Launcher",
         "/v", "101072944", "/t", "REG_DWORD", "/d", "1", "/f"],
    ]
    for exe in _ROCKSTAR_UI_EXES:
        cmds.extend(_reg_cmds_for_exe(exe, _LAUNCHER_D3D_NATIVE, "n"))
        cmds.extend(_reg_cmds_for_exe(exe, _LAUNCHER_D3D_BUILTIN, "b"))
        cmds.extend(_reg_cmds_for_exe(exe, _LAUNCHER_D3D_DISABLED, "d"))
    for exe in _ROCKSTAR_GAME_EXES:
        cmds.extend(_reg_cmds_for_exe(exe, _D3D_DXMT, "native,builtin"))
    return cmds


def apply_compat(
    runtime: WineRuntime,
    bottle: Path,
    env: dict[str, str],
) -> None:
    """Apply Rockstar registry + per-exe DLL overrides into a bottle."""
    from contextlib import nullcontext

    from metalplay.compat.crossover import without_crossover_conf
    from metalplay.compat.process import kill_wineserver

    # v2: vulkan-1 builtin for DXVK launcher graphics (was disabled)
    marker = bottle / ".metalplay/rockstar_compat.v2.ok"
    if marker.is_file():
        return

    guard = (
        without_crossover_conf(bottle)
        if not runtime.name.startswith("crossover")
        else nullcontext()
    )
    with guard:
        kill_wineserver(runtime.wineserver_bin, bottle)
        reg_body = _registry_reg_file_body()
        reg_file = bottle / "drive_c/rockstar_metalplay.reg"
        reg_file.write_text(reg_body, encoding="utf-16le")
        subprocess.run(
            wine_command(runtime.wine_bin, "regedit", "/s", r"C:\rockstar_metalplay.reg"),
            env=env,
            capture_output=True,
            timeout=120,
        )
        kill_wineserver(runtime.wineserver_bin, bottle)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="ascii")


def _registry_reg_file_body() -> str:
    """Build a UTF-16LE .reg file for Rockstar keys (single import, no hang)."""
    lines = [
        r"Windows Registry Editor Version 5.00",
        "",
        r"[HKEY_LOCAL_MACHINE\Software\Rockstar Games\GTA V Enhanced]",
        f'"InstallFolderSteam"="{GTA_V_ENHANCED_INSTALL}"',
        "",
        r"[HKEY_LOCAL_MACHINE\Software\Rockstar Games\Steam\Launcher]",
        '"101072944"=dword:00000001',
        "",
        r"[HKEY_LOCAL_MACHINE\Software\Wow6432Node\Rockstar Games\Steam\Launcher]",
        '"101072944"=dword:00000001',
        "",
    ]
    for exe in _ROCKSTAR_UI_EXES:
        lines.append(rf"[HKEY_CURRENT_USER\Software\Wine\AppDefaults\{exe}\DllOverrides]")
        for dll in _LAUNCHER_D3D_NATIVE:
            lines.append(f'"{dll}"="n"')
        for dll in _LAUNCHER_D3D_BUILTIN:
            lines.append(f'"{dll}"="b"')
        for dll in _LAUNCHER_D3D_DISABLED:
            lines.append(f'"{dll}"="d"')
        lines.append("")
    for exe in _ROCKSTAR_GAME_EXES:
        lines.append(rf"[HKEY_CURRENT_USER\Software\Wine\AppDefaults\{exe}\DllOverrides]")
        for dll in _D3D_DXMT:
            lines.append(f'"{dll}"="native,builtin"')
        lines.append("")
    text = "\r\n".join(lines) + "\r\n"
    return "\ufeff" + text


def _steamapps_common(bottle: Path) -> Path:
    return (
        bottle
        / "drive_c/Program Files (x86)/Steam/steamapps/common"
    )


def bypass_for_app(app_id: str | int) -> RockstarBypass | None:
    app_id = str(app_id)
    for bypass in ROCKSTAR_BYPASSES:
        if bypass.app_id == app_id:
            return bypass
    return None


def restore_playgtav_stub(
    bottle: Path,
    bypass: RockstarBypass,
    callback: ProgressCallback | None = None,
) -> bool:
    """Restore the real PlayGTAV Social Club stub (required for DRM / launcher auth)."""
    game_dir = _steamapps_common(bottle) / bypass.install_dir
    if not game_dir.is_dir():
        return False

    stub = game_dir / bypass.stub_exe
    candidates = [
        game_dir / f"{bypass.stub_exe}.rockstar.bak",
        game_dir / f"{bypass.stub_exe}.metalplay.bak",
    ]
    source: Path | None = None
    for candidate in candidates:
        if not candidate.is_file():
            continue
        # Real stub is ~500 KB; patched copies are the full game binary (~50+ MB).
        if candidate.stat().st_size < 5_000_000:
            source = candidate
            break

    if source is None:
        if callback:
            callback(
                f"Rockstar: could not restore {bypass.stub_exe} for {bypass.install_dir} "
                "(verify game files in Steam, then run: metalplay steam repair)"
            )
        return False

    if stub.is_file() and stub.stat().st_size == source.stat().st_size:
        if callback:
            callback(f"Rockstar: {bypass.stub_exe} already restored ({bypass.install_dir})")
        return True

    shutil.copy2(source, stub)
    if callback:
        callback(f"Rockstar: restored {bypass.stub_exe} ({bypass.install_dir})")
    return True


def _fake_launcher_asset() -> Path:
    return Path(resources.files("metalplay.compat") / "assets" / _FAKE_LAUNCHER_EXE)


def install_launcher_wrapper(
    bottle: Path,
    bypass: RockstarBypass,
    callback: ProgressCallback | None = None,
) -> bool:
    """
    Install Heroic-style EpicGamesLauncher.exe wrapper next to PlayGTAV.exe.

    Helps Rockstar skip the buy-screen on some stores; harmless on Steam.
    """
    game_dir = _steamapps_common(bottle) / bypass.install_dir
    if not game_dir.is_dir():
        return False

    try:
        asset = _fake_launcher_asset()
    except (FileNotFoundError, TypeError, ModuleNotFoundError):
        if callback:
            callback("Rockstar: EpicGamesLauncher.exe asset missing — skipping wrapper")
        return False

    dest = game_dir / _FAKE_LAUNCHER_EXE
    if not dest.is_file() or dest.stat().st_size != asset.stat().st_size:
        shutil.copy2(asset, dest)
        if callback:
            callback(f"Rockstar: installed {_FAKE_LAUNCHER_EXE} ({bypass.install_dir})")

    fix_bat = game_dir / _FIX_BAT
    if not fix_bat.is_file():
        fix_bat.write_text(_FIX_BAT_CONTENT, encoding="ascii")
        if callback:
            callback(f"Rockstar: wrote {_FIX_BAT} ({bypass.install_dir})")
    return True


def _disable_social_club_cef_dll(sc_dir: Path, name: str) -> bool:
    target = sc_dir / name
    disabled = sc_dir / f"{name}.metalplay-disabled"
    if target.is_file() and not disabled.is_file():
        target.rename(disabled)
        return True
    return False


def prepare_launcher_graphics(
    bottle: Path,
    callback: ProgressCallback | None = None,
    *,
    launch_runtime: WineRuntime | None = None,
) -> None:
    """
    Prepare the bottle for Rockstar Launcher CEF.

    - CrossOver: keep D3DMetal/DXMT stack (CEF works with CrossOver Wine)
    - Gcenx/free Wine: swap to stock wined3d + native DLL overrides
    """
    from metalplay.compat.rockstar_cef import (
        install_into_bottle,
        purge_rockstar_cef_locks,
        restore_launcher_exe,
        wrapper_needs_redeploy,
    )

    from metalplay.compat.crossover import crossover_runtime, ensure_bottle_registered

    use_crossover = (
        launch_runtime.name.startswith("crossover")
        if launch_runtime is not None
        else crossover_runtime() is not None
    )
    if use_crossover:
        ensure_bottle_registered(bottle)
        if callback:
            callback("Rockstar: using CrossOver Wine — keeping D3DMetal graphics for launcher CEF")
        removed = purge_rockstar_cef_locks(bottle)
        if removed and callback:
            callback(f"Rockstar: purged {removed} CEF lock file(s)")
        restore_launcher_exe(bottle, callback)
        if wrapper_needs_redeploy(bottle):
            try:
                install_into_bottle(bottle, callback)
            except RuntimeError as exc:
                if callback:
                    callback(f"Rockstar: SocialClubHelper wrapper skipped ({exc})")
        return

    sc = bottle / "drive_c/Program Files/Rockstar Games/Social Club"
    vulkan = sc / "vulkan-1.dll"
    disabled = sc / "vulkan-1.dll.metalplay-disabled"
    if vulkan.is_file() and not disabled.is_file():
        vulkan.rename(disabled)
        if callback:
            callback("Rockstar: disabled Social Club bundled vulkan-1.dll")
    elif disabled.is_file() and callback:
        callback("Rockstar: Social Club vulkan override already applied")

    for dll in ("dxcompiler.dll", "dxil.dll", "SocialClubD3D12Renderer.dll"):
        if _disable_social_club_cef_dll(sc, dll) and callback:
            callback(f"Rockstar: disabled Social Club {dll}")

    from metalplay.compat.graphics import swap_bottle_to_dxvk

    try:
        swapped = swap_bottle_to_dxvk(bottle)
        if swapped and callback:
            callback(
                f"Rockstar: launcher graphics → DXVK over MoltenVK "
                f"({len(swapped)} DLL(s))"
            )
    except Exception as exc:  # download/extract failure — keep the old wined3d path
        if callback:
            callback(f"Rockstar: DXVK unavailable ({exc}) — falling back to wined3d")
        try:
            swapped = swap_bottle_to_wined3d(bottle)
        except FileNotFoundError as exc2:
            if callback:
                callback(f"Rockstar: wined3d swap skipped ({exc2})")
            swapped = []
        if swapped and callback:
            callback(f"Rockstar: swapped {len(swapped)} graphics DLL(s) to wined3d for launcher")

    windows = bottle / "drive_c/windows"
    for sub in ("system32", "syswow64"):
        wm = windows / sub / "winemetal.dll"
        disabled = windows / sub / "winemetal.dll.metalplay-rockstar-disabled"
        if not wm.is_file():
            continue
        if disabled.is_file():
            # Steam UI compat may copy winemetal back after we disabled it.
            wm.unlink()
            if callback:
                callback(f"Rockstar: removed restored {sub}/winemetal.dll for launcher CEF")
        else:
            wm.rename(disabled)
            if callback:
                callback(f"Rockstar: disabled {sub}/winemetal.dll for launcher CEF")

    removed = purge_rockstar_cef_locks(bottle)
    if removed and callback:
        callback(f"Rockstar: purged {removed} CEF lock file(s)")

    restore_launcher_exe(bottle, callback)
    if wrapper_needs_redeploy(bottle):
        try:
            install_into_bottle(bottle, callback)
        except RuntimeError as exc:
            if callback:
                callback(f"Rockstar: SocialClubHelper wrapper skipped ({exc})")


def restore_all_stubs(
    bottle: Path,
    callback: ProgressCallback | None = None,
) -> list[str]:
    """Restore PlayGTAV stubs and prepare the launcher for Wine."""
    restored: list[str] = []
    for bypass in ROCKSTAR_BYPASSES:
        manifest = (
            bottle
            / f"drive_c/Program Files (x86)/Steam/steamapps/appmanifest_{bypass.app_id}.acf"
        )
        if not manifest.is_file():
            continue
        if restore_playgtav_stub(bottle, bypass, callback):
            restored.append(bypass.app_id)
        install_launcher_wrapper(bottle, bypass, callback)
    prepare_launcher_graphics(bottle, callback)
    for bypass in ROCKSTAR_BYPASSES:
        game_dir = _steamapps_common(bottle) / bypass.install_dir
        if game_dir.is_dir():
            (game_dir / "args.txt").touch(exist_ok=True)
    return restored


def install_launch_bypass(
    bottle: Path,
    bypass: RockstarBypass,
    callback: ProgressCallback | None = None,
) -> bool:
    """Deprecated alias — restores the PlayGTAV stub instead of replacing it."""
    return restore_playgtav_stub(bottle, bypass, callback)


def install_all_bypasses(
    bottle: Path,
    callback: ProgressCallback | None = None,
) -> list[str]:
    """Restore Rockstar stubs and prepare the launcher for Wine."""
    return restore_all_stubs(bottle, callback)


def rockstar_game_env(base: dict[str, str]) -> dict[str, str]:
    """
    Env for launching Rockstar Steam titles via Steam -applaunch.

    The Rockstar Launcher chain needs wined3d (bottle swap + per-exe overrides).
    The game binary loads DXMT via WINEDLLPATH_PREPEND + AppDefaults native,builtin.
    """
    env = dict(base)
    env.pop("WINE_DISABLE_OPENGL", None)
    # Launcher/CEF: DXVK d3d11 (bottle copy) over builtin vulkan-1 → MoltenVK.
    # Game: DXMT from prepend (native) when gta5_enhanced.exe starts.
    overrides = (
        "d3d11,dxgi,d3d10core,d3d10_1=n;"
        "d2d1,dwrite,vulkan-1=b;"
        "d3d12,winemetal=d;"
        "winemenubuilder.exe=d;"
        "gameoverlayrenderer,gameoverlayrenderer64=d;"
        "ucrtbase=b"
    )
    existing = env.get("WINEDLLOVERRIDES", "")
    env["WINEDLLOVERRIDES"] = f"{existing};{overrides}" if existing else overrides
    env.setdefault("DXVK_LOG_LEVEL", "error")
    env.setdefault("MVK_CONFIG_RESUME_LOST_DEVICE", "1")
    env.setdefault("MVK_CONFIG_FULL_IMAGE_VIEW_SWIZZLE", "1")
    env.setdefault(
        "CHROMIUM_FLAGS",
        "--disable-gpu --disable-gpu-compositing --use-angle=swiftshader --disable-dev-shm-usage",
    )
    env.setdefault("METALPLAY_CEF_DEVICE_SCALE_FACTOR", "auto")
    return env


def rockstar_crossover_env(base: dict[str, str]) -> dict[str, str]:
    """Env for Rockstar launcher/game via CrossOver (D3DMetal — no wined3d swap)."""
    env = dict(base)
    env.pop("WINE_DISABLE_OPENGL", None)
    env.pop("WINEDLLPATH_PREPEND", None)
    overrides = (
        "winemenubuilder.exe=d;"
        "gameoverlayrenderer,gameoverlayrenderer64=d;"
        "ucrtbase=b"
    )
    existing = env.get("WINEDLLOVERRIDES", "")
    env["WINEDLLOVERRIDES"] = f"{existing};{overrides}" if existing else overrides
    env.setdefault(
        "CHROMIUM_FLAGS",
        "--disable-gpu --disable-gpu-compositing --use-angle=swiftshader --disable-dev-shm-usage",
    )
    env.setdefault("METALPLAY_CEF_DEVICE_SCALE_FACTOR", "auto")
    return env


def rockstar_crossover_launch_target(
    bottle: Path,
    bypass: RockstarBypass,
    app_id: str | int,
) -> tuple[Path, list[str]]:
    """CrossOver launch via Launcher.exe (avoids PlayGTAV stub + long wineboot on mixed prefixes)."""
    launcher = bottle / "drive_c/Program Files/Rockstar Games/Launcher/Launcher.exe"
    if bypass.app_id == "3240220":
        steam_loc = GTA_V_ENHANCED_INSTALL
    else:
        steam_loc = rf"C:\Program Files (x86)\Steam\steamapps\common\{bypass.install_dir}"
    # No embedded quotes and no trailing backslash: Wine escapes literal quotes when it
    # rebuilds the Windows command line, and a trailing backslash before the closing
    # quote swallows it — the launcher then fails to read <path>\title.rgl.
    steam_loc = steam_loc.rstrip("\\")
    args = [
        "-skipPatcherCheck",
        f"-steamAppId={app_id}",
        f"-steamLocation={steam_loc}",
    ]
    return launcher, args
