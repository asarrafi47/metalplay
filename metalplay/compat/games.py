"""Per-game compatibility: graphics backend, launch options, Wine AppDefaults."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from metalplay.compat.display import SteamDisplaySettings
from metalplay.runtime.wine import WineRuntime, wine_command

ProgressCallback = Callable[[str], None] | None

# Well-known Steam App IDs → engine / graphics profile
@dataclass(frozen=True)
class GameCompatProfile:
    name: str
    graphics: str  # wined3d | dxmt | moltenvk
    launch_options: str
    dll_overrides: str
    exe_names: tuple[str, ...] = ()
    game_retina_mode: str = "n"  # RetinaMode while playing (avoids zoomed viewport)
    extra_env: dict[str, str] = field(default_factory=dict)
    source_game: str = ""  # -game argument for direct Source launches


_GMOD_CEF_FLAGS = (
    "--disable-gpu --disable-gpu-compositing --use-angle=swiftshader "
    "--in-process-gpu --single-process --disable-dev-shm-usage"
)

def _gmod_launch_options() -> str:
    # +map gm_construct: skip HTML main-menu workshop promos when possible.
    return _windowed_launch_options() + " +mat_queue_mode 0 +map gm_construct"


def _parse_resolution(spec: str) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d{3,5})\s*[xX]\s*(\d{3,5})\s*$", spec or "")
    if not match:
        return None
    w, h = int(match.group(1)), int(match.group(2))
    if w < 640 or h < 480:
        return None
    return w, h


def _game_window_size() -> tuple[int, int]:
    """
    Borderless game window size.

    METALPLAY_GAME_RESOLUTION (env, then config extra_env) wins; otherwise fill the
    logical desktop so -noborder behaves like fullscreen instead of a small window.
    """
    from metalplay.config import Config
    from metalplay.tune.detect import _primary_screen_geometry

    for spec in (
        os.environ.get("METALPLAY_GAME_RESOLUTION"),
        Config.load().extra_env.get("METALPLAY_GAME_RESOLUTION"),
    ):
        size = _parse_resolution(spec or "")
        if size:
            return size
    lw, lh, _ = _primary_screen_geometry()
    return max(800, lw), max(600, lh)


def _windowed_launch_options() -> str:
    w, h = _game_window_size()
    return f"-windowed -noborder -w {w} -h {h} -nopreload -novid"

_SOURCE_OPTIONS = _windowed_launch_options() + " +mat_queue_mode 0"

_GMOD_DLL_OVERRIDES = (
    "d3d9=b;d3d11=b;d3d10core=b;dxgi=b;winemetal=d;"
    "dwrite=b;dinput8=n,b;xinput1_3=n,b;"
    "gameoverlayrenderer,gameoverlayrenderer64=d"
)

GAME_PROFILES: dict[str, GameCompatProfile] = {
  # Source engine — DirectX 9 / OpenGL via WineD3D (DXMT breaks viewport + crashes)
    "4000": GameCompatProfile(
        name="Garry's Mod",
        graphics="wined3d",
        launch_options=_gmod_launch_options(),
        dll_overrides=_GMOD_DLL_OVERRIDES,
        exe_names=("gmod.exe", "hl2.exe"),
        source_game="garrysmod",
        extra_env={"CHROMIUM_FLAGS": _GMOD_CEF_FLAGS},
    ),
    "220": GameCompatProfile(
        name="Half-Life 2",
        graphics="wined3d",
        launch_options=_SOURCE_OPTIONS,
        dll_overrides=(
            "d3d9=b;d3d11=b;d3d10core=b;dxgi=b;"
            "gameoverlayrenderer,gameoverlayrenderer64=d"
        ),
        exe_names=("hl2.exe",),
    ),
    "440": GameCompatProfile(
        name="Team Fortress 2",
        graphics="wined3d",
        launch_options=_SOURCE_OPTIONS,
        dll_overrides=(
            "d3d9=b;d3d11=b;d3d10core=b;dxgi=b;"
            "gameoverlayrenderer,gameoverlayrenderer64=d"
        ),
        exe_names=("hl2.exe",),
    ),
    "730": GameCompatProfile(
        name="Counter-Strike 2",
        graphics="dxmt",
        launch_options="-windowed -noborder -nopreload -novid",
        dll_overrides="dxgi,d3d11,d3d10core=n,b;gameoverlayrenderer,gameoverlayrenderer64=d",
        exe_names=("cs2.exe",),
    ),
}


def _source_install(install_path: Path | None) -> bool:
    if not install_path or not install_path.is_dir():
        return False
    if (install_path / "gmod.exe").is_file() or (install_path / "hl2.exe").is_file():
        return True
    return (install_path / "bin").is_dir() and any(install_path.glob("*.exe"))


def compat_profile(
    app_id: str,
    *,
    install_path: Path | None = None,
) -> GameCompatProfile | None:
    app_id = str(app_id)
    known = GAME_PROFILES.get(app_id)
    if known:
        return known
    if _source_install(install_path):
        return GameCompatProfile(
            name=f"App {app_id}",
            graphics="wined3d",
            launch_options=_SOURCE_OPTIONS,
            dll_overrides=(
                "d3d9=b;d3d11=b;d3d10core=b;dxgi=b;"
                "gameoverlayrenderer,gameoverlayrenderer64=d"
            ),
            exe_names=("hl2.exe",),
        )
    return None


def resolve_graphics(
    app_id: str,
    *,
    install_path: Path | None = None,
    requested: str | None = None,
    config_profile: dict | None = None,
) -> str:
    """Pick graphics backend (never DXMT for Source/DX9 titles)."""
    profile_cfg = config_profile or {}
    gfx = requested or profile_cfg.get("graphics", "auto")
    known = compat_profile(app_id, install_path=install_path)
    if known and known.graphics == "wined3d" and gfx in ("auto", "dxmt"):
        # DXMT has no D3D9: a stale config entry or GUI default of "dxmt" for a
        # Source title crashes at device init. Explicit dxvk/moltenvk stay honored.
        return "wined3d"
    if gfx and gfx != "auto":
        return gfx
    if known:
        return known.graphics
    return "dxmt"


def _userdata_localconfigs(steam_root: Path) -> list[Path]:
    userdata = steam_root / "userdata"
    if not userdata.is_dir():
        return []
    return sorted(userdata.glob("*/config/localconfig.vdf"))


def _set_launch_options_in_vdf(text: str, app_id: str, options: str) -> tuple[str, bool]:
    """Insert or update LaunchOptions inside apps/<app_id> { ... }."""
    app_key = f'"{app_id}"'
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if re.match(rf'^\s*{re.escape(app_key)}\s*$', line):
            if i + 1 < len(lines) and "{" in lines[i + 1]:
                out.append(lines[i + 1])
                i += 2
                block: list[str] = []
                while i < len(lines):
                    if lines[i].strip() == "}":
                        break
                    block.append(lines[i])
                    i += 1
                replaced = False
                new_block: list[str] = []
                for bl in block:
                    if '"LaunchOptions"' in bl:
                        new_block.append(f'\t\t\t\t"LaunchOptions"\t\t"{options}"')
                        replaced = True
                        changed = True
                    else:
                        new_block.append(bl)
                if not replaced:
                    new_block.insert(0, f'\t\t\t\t"LaunchOptions"\t\t"{options}"')
                    changed = True
                out.extend(new_block)
                if i < len(lines):
                    out.append(lines[i])
            else:
                i += 1
            continue
        i += 1
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), changed


def apply_steam_launch_options(
    steam_root: Path,
    app_id: str,
    options: str,
    callback: ProgressCallback = None,
) -> int:
    """Write Steam LaunchOptions so Play-in-Steam uses windowed mode."""
    updated = 0
    for path in _userdata_localconfigs(steam_root):
        try:
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError:
            continue
        new_text, changed = _set_launch_options_in_vdf(text, app_id, options)
        if not changed:
            continue
        path.write_text(new_text, encoding="utf-8", errors="surrogateescape")
        updated += 1
        if callback:
            callback(f"Game {app_id}: set Steam launch options in {path.parent.parent.name}")
    return updated


def apply_appmanifest_launch_options(
    manifest: Path,
    options: str,
    callback: ProgressCallback = None,
) -> bool:
    """Persist launch options in appmanifest (survives some Steam config rewrites)."""
    if not manifest.is_file():
        return False
    try:
        text = manifest.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return False
    if '"LaunchOptions"' in text:
        new_text, n = re.subn(
            r'"LaunchOptions"\s+"[^"]*"',
            f'"LaunchOptions"\t\t"{options}"',
            text,
            count=1,
        )
        if n == 0:
            return False
    elif '"UserConfig"' in text:
        new_text = text.replace(
            '"UserConfig"\n\t{',
            f'"UserConfig"\n\t{{\n\t\t"LaunchOptions"\t\t"{options}"',
            1,
        )
    else:
        return False
    if new_text == text:
        return False
    manifest.write_text(new_text, encoding="utf-8", errors="surrogateescape")
    if callback:
        callback(f"Game manifest: launch options → {manifest.name}")
    return True


def restore_gmod_cef_dlls(install_path: Path, callback: ProgressCallback = None) -> bool:
    """Restore CEF DLLs if a prior compat pass renamed them (GMod needs html_chromium)."""
    bin_dir = install_path / "bin"
    if not bin_dir.is_dir():
        return False
    restored_names: list[str] = []
    for name in ("html_chromium.dll", "libcef.dll", "chrome_elf.dll"):
        dst = bin_dir / name
        backup = bin_dir / f"{name}.metalplay-disabled"
        if backup.is_file() and not dst.is_file():
            backup.rename(dst)
            restored_names.append(name)
    if restored_names and callback:
        callback(f"Game compat: restored GMod HTML DLLs ({', '.join(restored_names)})")
    return bool(restored_names)


def write_source_video_cfg(install_path: Path, callback: ProgressCallback = None) -> bool:
    """Force windowed resolution for Source engine (works even if Steam drops launch options)."""
    cfg_dir = install_path / "garrysmod" / "cfg"
    if not cfg_dir.is_dir():
        cfg_dir = install_path / "hl2" / "cfg"
    if not cfg_dir.is_dir():
        cfg_dir = install_path / "cfg"
    if not cfg_dir.is_dir():
        return False
    w, h = _game_window_size()
    video = cfg_dir / "video.txt"
    contents = (
        f'"video.cfg"\n{{\n'
        f'\t"setting.defaultres"\t\t"{w}"\n'
        f'\t"setting.defaultresheight"\t\t"{h}"\n'
        f'\t"setting.fullscreen"\t\t"0"\n'
        f'\t"setting.mat_vsync"\t\t"0"\n'
        f'\t"setting.aspectratiomode"\t\t"1"\n'
        f"}}\n"
    )
    if video.is_file() and video.read_text() == contents:
        return False
    video.write_text(contents)
    if callback:
        callback(f"Game compat: wrote {w}x{h} windowed video.txt")
    return True


def write_source_autoexec_cfg(install_path: Path, callback: ProgressCallback = None) -> bool:
    """Run windowed resolution commands every launch (Steam often drops launch options)."""
    cfg_dir = install_path / "garrysmod" / "cfg"
    if not cfg_dir.is_dir():
        cfg_dir = install_path / "hl2" / "cfg"
    if not cfg_dir.is_dir():
        cfg_dir = install_path / "cfg"
    if not cfg_dir.is_dir():
        return False
    w, h = _game_window_size()
    marker = "// metalplay-windowed"
    html_marker = "// metalplay-html"
    snippet = (
        f"{marker}\n"
        f"mat_setvideomode {w} {h} 0\n"
        f"r_fullscreen 0\n"
        f"{html_marker}\n"
        f"cl_disablehtmlmotd 1\n"
    )
    mp_cfg = cfg_dir / "metalplay.cfg"
    # Exact-content compare: a stale copy with an old resolution would force the
    # window back to that size on every launch.
    if not mp_cfg.is_file() or mp_cfg.read_text() != snippet:
        mp_cfg.write_text(snippet)
        changed = True
    else:
        changed = False

    autoexec = cfg_dir / "autoexec.cfg"
    exec_line = "exec metalplay.cfg"
    if autoexec.is_file():
        text = autoexec.read_text(encoding="utf-8", errors="surrogateescape")
        if exec_line not in text:
            autoexec.write_text(
                text.rstrip() + f"\n{exec_line}\n",
                encoding="utf-8",
                errors="surrogateescape",
            )
            changed = True
    else:
        autoexec.write_text(f"{exec_line}\n")
        changed = True
    if changed and callback:
        callback(f"Game compat: autoexec → {w}x{h} windowed")
    return changed


def _reset_appdefaults_key(
    runtime: WineRuntime,
    env: dict[str, str],
    exe_name: str,
    subkey: str,
) -> None:
    subprocess.run(
        wine_command(
            runtime.wine_bin,
            "reg",
            "delete",
            rf"HKCU\Software\Wine\AppDefaults\{exe_name}\{subkey}",
            "/f",
        ),
        env=env,
        capture_output=True,
        timeout=15,
    )


def apply_appdefaults(
    runtime: WineRuntime,
    bottle: Path,
    profile: GameCompatProfile,
    env: dict[str, str],
    callback: ProgressCallback = None,
) -> int:
    """Per-exe DLL overrides + Mac driver RetinaMode for Source games."""
    applied = 0
    for exe in profile.exe_names:
        _reset_appdefaults_key(runtime, env, exe, "DllOverrides")
        for cmd in _appdefaults_cmds(exe, profile.dll_overrides):
            subprocess.run(
                wine_command(runtime.wine_bin, *cmd),
                env=env,
                capture_output=True,
                timeout=30,
            )
            applied += 1
        subprocess.run(
            wine_command(
                runtime.wine_bin,
                "reg",
                "add",
                rf"HKCU\Software\Wine\AppDefaults\{exe}\Mac Driver",
                "/v",
                "RetinaMode",
                "/t",
                "REG_SZ",
                "/d",
                profile.game_retina_mode,
                "/f",
            ),
            env=env,
            capture_output=True,
            timeout=30,
        )
        applied += 1
        for key, value in profile.extra_env.items():
            subprocess.run(
                wine_command(
                    runtime.wine_bin,
                    "reg",
                    "add",
                    rf"HKCU\Software\Wine\AppDefaults\{exe}\Environment",
                    "/v",
                    key,
                    "/t",
                    "REG_SZ",
                    "/d",
                    value,
                    "/f",
                ),
                env=env,
                capture_output=True,
                timeout=30,
            )
            applied += 1
        if callback:
            callback(
                f"Game compat: AppDefaults for {exe} "
                f"({profile.graphics}, RetinaMode={profile.game_retina_mode})",
            )
    return applied


def _appdefaults_cmds(exe_name: str, overrides: str) -> list[list[str]]:
    key = rf"HKCU\Software\Wine\AppDefaults\{exe_name}\DllOverrides"
    cmds: list[list[str]] = []
    for pair in overrides.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        dlls_part, mode = pair.split("=", 1)
        for dll in dlls_part.split(","):
            dll = dll.strip()
            if dll:
                cmds.append(
                    ["reg", "add", key, "/v", dll, "/t", "REG_SZ", "/d", mode, "/f"],
                )
    return cmds


def sync_game_display_registry(
    runtime: WineRuntime,
    bottle: Path,
    env: dict[str, str],
    profile: GameCompatProfile,
    callback: ProgressCallback = None,
) -> None:
    """Use RetinaMode=n during games to avoid zoomed/cropped viewports on Retina Macs."""
    from metalplay.compat.display import steam_display_settings

    base = steam_display_settings()
    settings = SteamDisplaySettings(
        retina_mode=profile.game_retina_mode,
        log_pixels=96,
        scale_factor=base.scale_factor,
        logical_width=base.logical_width,
        logical_height=base.logical_height,
        physical_width=base.physical_width,
        physical_height=base.physical_height,
        cef_scale_mode="auto",
    )
    from metalplay.compat.registry import _display_registry_cmds

    for cmd in _display_registry_cmds(settings):
        subprocess.run(
            wine_command(runtime.wine_bin, *cmd),
            env=env,
            capture_output=True,
            timeout=30,
        )
    if callback:
        callback(
            f"Game compat: display RetinaMode={settings.retina_mode}, "
            f"DPI={settings.log_pixels} for {profile.name}",
        )


def scrub_appcompat_flags(bottle: Path, callback: ProgressCallback = None) -> bool:
    """Remove DISABLEDXMAXIMIZEDWINDOWEDMODE — causes fullscreen monitor takeover on Wine."""
    user_reg = bottle / "user.reg"
    if not user_reg.is_file():
        return False
    try:
        data = user_reg.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError:
        return False
    if "DISABLEDXMAXIMIZEDWINDOWEDMODE" not in data:
        return False

    def fix(match: re.Match[str]) -> str:
        value = match.group(2)
        if "DISABLEDXMAXIMIZEDWINDOWEDMODE" not in value:
            return match.group(0)
        tokens = [t for t in value.split() if t and t != "DISABLEDXMAXIMIZEDWINDOWEDMODE"]
        new = "" if tokens == ["~"] else " ".join(tokens)
        return f'"{match.group(1)}"="{new}"'

    pattern = re.compile(r'"([^"]+\.exe)"="([^"]*)"')
    new_data = pattern.sub(fix, data)
    new_data = re.sub(r'\n"[^"]+\.exe"=""\n', "\n", new_data)
    if new_data == data:
        return False
    user_reg.write_text(new_data, encoding="utf-8", errors="surrogateescape")
    if callback:
        callback("Game compat: stripped DISABLEDXMAXIMIZEDWINDOWEDMODE AppCompat flags")
    return True


def prepare_game(
    runtime: WineRuntime,
    bottle: Path,
    app_id: str,
    *,
    install_path: Path | None = None,
    env: dict[str, str],
    callback: ProgressCallback = None,
    files_only: bool = False,
) -> GameCompatProfile | None:
    """Apply registry + Steam launch options for one installed game."""
    profile = compat_profile(app_id, install_path=install_path)
    if profile is None:
        return None

    from metalplay.steam.client import steam_dir

    steam_root = steam_dir(bottle)
    if steam_root:
        apply_steam_launch_options(steam_root, app_id, profile.launch_options, callback)
        manifest = steam_root / "steamapps" / f"appmanifest_{app_id}.acf"
        apply_appmanifest_launch_options(manifest, profile.launch_options, callback)

    if install_path:
        if app_id == "4000":
            restore_gmod_cef_dlls(install_path, callback)
        write_source_video_cfg(install_path, callback)
        write_source_autoexec_cfg(install_path, callback)

    if files_only:
        return profile

    scrub_appcompat_flags(bottle, callback)
    apply_appdefaults(runtime, bottle, profile, env, callback)
    return profile


def prepare_all_installed_games(
    runtime: WineRuntime,
    bottle: Path,
    env: dict[str, str],
    callback: ProgressCallback = None,
    *,
    files_only: bool = False,
) -> list[str]:
    """Refresh compat for every installed game (repair / setup)."""
    from metalplay.steam.library import list_games

    prepared: list[str] = []
    if not files_only:
        scrub_appcompat_flags(bottle, callback)
    for game in list_games(bottle):
        if prepare_game(
            runtime,
            bottle,
            game.app_id,
            install_path=game.install_path,
            env=env,
            callback=callback,
            files_only=files_only,
        ):
            prepared.append(game.app_id)
    return prepared


def game_env_overrides(profile: GameCompatProfile) -> dict[str, str]:
    """Env fragment for metalplay steam run (per-process overrides)."""
    if profile.graphics == "wined3d":
        base = {
            "WINEDLLOVERRIDES": profile.dll_overrides,
            "WINE_DISABLE_OPENGL": "0",
        }
    elif profile.graphics in ("moltenvk", "dxvk"):
        base = {
            "WINEDLLOVERRIDES": profile.dll_overrides,
        }
    else:
        base = {
            "WINEDLLOVERRIDES": profile.dll_overrides,
            "WINE_DISABLE_OPENGL": "1",
        }
    base.update(profile.extra_env)
    return base


def source_launch_exe(install_path: Path, profile: GameCompatProfile) -> Path | None:
    """Pick the game binary for a direct Source launch (bypasses Steam -applaunch)."""
    for name in profile.exe_names:
        exe = install_path / name
        if exe.is_file():
            return exe
    return None


def source_launch_argv(profile: GameCompatProfile) -> list[str]:
    """Command-line tail for a direct Source engine launch."""
    argv = ["-steam"]
    if profile.source_game:
        argv.extend(["-game", profile.source_game])
    if profile.launch_options:
        argv.extend(profile.launch_options.split())
    return argv
