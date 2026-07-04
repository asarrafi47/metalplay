"""Windows Steam client integration for MetalPlay bottles."""

from __future__ import annotations

import os
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from metalplay import paths
from metalplay.bottle import manager as bottles
from metalplay.config import Config
from metalplay.runtime.wine import WineRuntime, wine_command
from metalplay.compat.rockstar import (
    apply_compat as apply_rockstar_compat,
    install_all_bypasses,
    is_rockstar_steam_app,
    rockstar_game_env,
)
from metalplay.compat.steam_ui import STEAM_CLIENT_ENV, STEAM_LAUNCH_ARGS, SteamUICompatLayer
from metalplay.launcher import run as launcher
from metalplay.controller.compat import controller_env
from metalplay.controller.profiles import controller_profile_for, steam_launch_args_for_controller

_steam_ui_lock = threading.RLock()

from metalplay.tune.apply import game_resolution_hint, performance_env, should_caffeinate
from metalplay.tune.power import enable_high_performance, restore_balanced_power, wrap_caffeinate
from metalplay.steam.install_layout import (
    STEAM_INSTALL_OK_CODES,
    ensure_steam_library,
    library_ready,
    prepare_install_dirs,
)

ProgressCallback = Callable[[str], None]

STEAM_BOTTLE_NAME = "steam"
STEAM_INSTALLER_URL = (
    "https://cdn.cloudflare.steamstatic.com/client/installer/SteamSetup.exe"
)
# Steam exits with 42 after self-updates — must relaunch (same as steamcmd.sh)
STEAM_RESTART_EXIT_CODE = 42
STEAM_MAX_RESTARTS = 8

# Re-exported from compat layer (single source of truth)
STEAM_CLIENT_LAUNCH_ARGS = STEAM_LAUNCH_ARGS

# Full env for launching games with DirectX → Metal
STEAM_GAME_ENV: dict[str, str] = {
    "STEAM_RUNTIME": "0",
    "STEAM_DISABLE_GPU": "0",
    "WINEDLLOVERRIDES": (
        "dxgi,d3d11,d3d10core=n,b;"
        "d3d12=n,b;"
        "vulkan-1=n,b;"
        "winemenubuilder.exe=d"
    ),
    "WINEESYNC": "0",
    "WINEFSYNC": "0",
    "WINEDEBUG": "-all",
}

# Legacy alias
STEAM_BASE_ENV = STEAM_GAME_ENV

WINETRICKS_PACKAGES = (
    "corefonts",
    "vcrun2019",
)

ROCKSTAR_WINETRICKS_PACKAGES = (
    "vcrun2022",
)


@dataclass
class SteamGame:
    app_id: str
    name: str
    install_dir: str
    install_path: Path | None
    exe_path: Path | None
    graphics: str  # dxmt, moltenvk, auto


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


def steam_dir(bottle: Path) -> Path | None:
    from metalplay.steam.bootstrap import steam_root
    return steam_root(bottle)


def steam_exe(bottle: Path) -> Path | None:
    from metalplay.steam.bootstrap import steam_exe_path, steam_root
    root = steam_root(bottle)
    return steam_exe_path(root) if root else None


def is_installed(bottle: Path) -> bool:
    from metalplay.steam.bootstrap import is_bootstrap_complete, steam_root
    root = steam_root(bottle)
    return bool(root and is_bootstrap_complete(root))


def is_stub_installed(bottle: Path) -> bool:
    """True if Steam stub exists but full client may not be downloaded yet."""
    from metalplay.steam.bootstrap import steam_root
    return steam_root(bottle) is not None


def ensure_bottle(runtime: WineRuntime, callback: ProgressCallback | None = None) -> Path:
    """Create or return the dedicated Windows Steam bottle."""
    bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)
    if bottle.is_dir() and _meta_path_exists(bottle):
        return bottle
    if bottle.is_dir():
        _configure_windows_bottle(runtime, bottle, callback)
        return bottle
    _log(f"Creating Steam bottle '{STEAM_BOTTLE_NAME}'...", callback)
    bottles.create(
        STEAM_BOTTLE_NAME, runtime, windows="win10", graphics="dxmt", install_dxmt=False,
    )
    _configure_windows_bottle(runtime, bottle, callback)
    return bottle


def _meta_path_exists(bottle: Path) -> bool:
    return (bottle / ".metalplay" / "bottle.json").is_file()


def _configure_windows_bottle(
    runtime: WineRuntime,
    bottle: Path,
    callback: ProgressCallback | None = None,
) -> None:
    """Apply Steam UI compatibility layer (registry, graphics, fonts, CEF wrapper)."""
    from metalplay.compat.crossover import without_crossover_conf

    _log("Applying Steam UI compatibility layer...", callback)
    with without_crossover_conf(bottle):
        SteamUICompatLayer(runtime, bottle).apply(callback)


def _bottle_env(bottle: Path, runtime: WineRuntime) -> dict[str, str]:
    env = dict(os.environ)
    env["WINEPREFIX"] = str(bottle)
    env["WINEARCH"] = "win64"
    env["PATH"] = f"{runtime.bin_dir}:{env.get('PATH', '')}"
    return env


def download_installer(force: bool = False) -> Path:
    paths.ensure_dirs()
    dest = paths.cache_dir() / "SteamSetup.exe"
    if dest.is_file() and not force:
        return dest
    _log(f"Downloading Steam installer...")
    urllib.request.urlretrieve(STEAM_INSTALLER_URL, dest)
    return dest


def describe_steam_exit(code: int) -> str:
    """Human-readable explanation for Steam process exit codes."""
    if code == 0:
        return "Steam closed normally."
    if code == STEAM_RESTART_EXIT_CODE:
        return "Steam is updating — it will restart automatically."
    if code in (-9, 137):
        return (
            "Steam was force-killed (signal 9). "
            "Do not click Open Steam repeatedly, and avoid Repair/Setup while Steam is open."
        )
    if code in (-15, -1):
        return "Steam was stopped (signal 15). Use Stop Steam once, wait, then Open Steam."
    return f"Steam exited with code {code}"


def _finalize_steam_install(
    runtime: WineRuntime,
    bottle: Path,
    callback: ProgressCallback | None = None,
) -> Path:
    """Verify install, create library folders, apply compat layer."""
    from metalplay.steam.bootstrap import is_bootstrap_complete, steam_root

    root = steam_root(bottle)
    if not root or not is_bootstrap_complete(root):
        raise RuntimeError(
            "Steam client download incomplete (steamui.dll missing). "
            "Run: metalplay steam bootstrap"
        )
    exe = steam_exe(bottle)
    if not exe:
        raise RuntimeError("Steam install finished but steam.exe was not found")
    if ensure_steam_library(root, bottle):
        _log("Created Steam library folder (steamapps).", callback)
    _configure_windows_bottle(runtime, bottle, callback)
    _log(f"Steam installed at {exe}", callback)
    meta = bottles.load_meta(bottle)
    if meta:
        meta.notes = "Windows Steam client — DXMT/D3D12→Metal"
        meta.programs = ["Steam"]
        bottles.save_meta(bottle, meta)
    return exe


def install_winetricks_deps(
    runtime: WineRuntime,
    bottle: Path,
    callback: ProgressCallback | None = None,
    packages: tuple[str, ...] | None = None,
) -> None:
    """Install common Steam dependencies via winetricks if available."""
    import shutil

    wt = shutil.which("winetricks")
    if not wt:
        _log("winetricks not found — skipping dependency install (optional)", callback)
        return
    env = _bottle_env(bottle, runtime)
    for pkg in packages or WINETRICKS_PACKAGES:
        _log(f"winetricks {pkg}...", callback)
        result = subprocess.run(
            [wt, "-q", pkg],
            env=env,
            timeout=1200,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else f"exit {result.returncode}"
            _log(f"winetricks {pkg} failed: {tail}", callback)


def install_client(
    runtime: WineRuntime,
    bottle: Path | None = None,
    callback: ProgressCallback | None = None,
    skip_deps: bool = False,
) -> Path:
    """Install Windows Steam client into the bottle."""
    from metalplay.steam.bootstrap import is_bootstrap_complete, run_bootstrap, steam_root

    bottle = bottle or ensure_bottle(runtime, callback)
    if is_installed(bottle):
        root = steam_dir(bottle)
        if root and ensure_steam_library(root, bottle):
            _log("Repaired Steam library folder (steamapps).", callback)
        if not library_ready(bottle):
            _configure_windows_bottle(runtime, bottle, callback)
        _log("Steam already installed.", callback)
        return steam_exe(bottle)  # type: ignore[return-value]

    root = steam_root(bottle)
    if root and not is_bootstrap_complete(root):
        run_bootstrap(runtime, bottle, callback)
        return _finalize_steam_install(runtime, bottle, callback)

    if not skip_deps:
        install_winetricks_deps(runtime, bottle, callback)

    prepare_install_dirs(bottle)
    installer = download_installer()
    _log("Running Steam silent installer (/S) — no directory wizard...", callback)
    # Minimal env for the 32-bit NSIS installer (no CEF / game overrides).
    env = _bottle_env(bottle, runtime)
    env["WINEDEBUG"] = "-all"
    result = subprocess.run(
        wine_command(runtime.wine_bin, str(installer), "/S"),
        env=env,
        cwd=bottle / "drive_c",
        timeout=900,
    )
    if result.returncode not in STEAM_INSTALL_OK_CODES:
        raise RuntimeError(
            f"Steam installer failed (exit {result.returncode}). "
            "Try: metalplay steam reset --force && metalplay steam setup"
        )

    run_bootstrap(runtime, bottle, callback)
    return _finalize_steam_install(runtime, bottle, callback)


def setup(
    callback: ProgressCallback | None = None,
    skip_deps: bool = False,
) -> dict[str, str]:
    """Full Steam setup: ensure runtime, bottle, and Windows Steam client."""
    from metalplay.runtime import dxmt
    from metalplay.runtime.installer import setup_all
    from metalplay.runtime.wine import detect_installed_runtimes, get_runtime

    config = Config.load()
    runtime = get_runtime(config.wine_runtime)
    if not runtime:
        if not dxmt.is_installed():
            _log("Installing DXMT + Wine...", callback)
            setup_all(callback=callback)
        runtimes = detect_installed_runtimes()
        if not runtimes:
            raise RuntimeError("No Wine runtime. Run: metalplay install all")
        runtime = runtimes[0]

    bottle = ensure_bottle(runtime, callback)
    exe = install_client(runtime, bottle, callback, skip_deps=skip_deps)

    config.default_bottle = STEAM_BOTTLE_NAME
    config.save()

    return {
        "bottle": str(bottle),
        "steam_exe": str(exe),
        "runtime": runtime.name,
    }


def steam_env(
    config: Config,
    graphics: str = "dxmt",
    *,
    for_client: bool = False,
    app_id: str | None = None,
) -> dict[str, str]:
    """Build launch environment. Use for_client=True when opening Steam UI."""
    base = STEAM_CLIENT_ENV if for_client else STEAM_GAME_ENV
    env = dict(base)
    if not for_client:
        env["DXMT_LOG_LEVEL"] = config.dxmt_log_level
        if is_rockstar_steam_app(app_id):
            env = rockstar_game_env(env)
        elif graphics == "wined3d":
            env["WINEDLLOVERRIDES"] = (
                "d3d9=b;d3d11=b;d3d10core=b;dxgi=b;winemetal=d;winemenubuilder.exe=d"
            )
            env.pop("WINEDLLPATH_PREPEND", None)
        elif graphics == "dxvk":
            env["WINEDLLOVERRIDES"] = (
                "d3d11,dxgi,d3d10core=n;vulkan-1=b;winemetal=d;winemenubuilder.exe=d"
            )
            env["DXVK_LOG_LEVEL"] = config.extra_env.get("DXVK_LOG_LEVEL", "error")
            env["MVK_CONFIG_RESUME_LOST_DEVICE"] = "1"
            env.pop("WINEDLLPATH_PREPEND", None)
        elif graphics == "moltenvk":
            env["WINEDLLOVERRIDES"] = (
                "d3d12=n,b;d3d11=n,b;dxgi=n,b;vulkan-1=b;"
                "winemenubuilder.exe=d"
            )
            env["MVK_CONFIG_USE_METAL_ARGUMENT_BUFFERS"] = "1"
            env["MVK_CONFIG_RESUME_LOST_DEVICE"] = "1"
        else:
            dxmt_root = paths.dxmt_dir()
            if dxmt_root.is_dir():
                env["WINEDLLPATH_PREPEND"] = str(dxmt_root)
    env.update(config.extra_env)
    if not for_client:
        env.update(performance_env(config))
        ctrl = controller_profile_for(app_id, config)
        ctrl_env = controller_env(ctrl, existing_overrides=env.get("WINEDLLOVERRIDES", ""))
        env.update(ctrl_env)
    return env


def launch_client(
    runtime: WineRuntime,
    bottle: Path,
    config: Config | None = None,
    args: list[str] | None = None,
    callback: ProgressCallback | None = None,
    *,
    wait_for_exit: bool = True,
    wait_for_window: bool = True,
) -> int:
    """Launch the Windows Steam client. Auto-restarts on exit code 42 (Steam update)."""
    from metalplay.compat.crossover import without_crossover_conf
    from metalplay.compat.process import focus_steam_window, wait_for_steam_window

    with _steam_ui_lock:
        with without_crossover_conf(bottle):
            return _launch_client_locked(
                runtime,
                bottle,
                config,
                args,
                callback,
                wait_for_exit=wait_for_exit,
                wait_for_window=wait_for_window,
            )


def _launch_client_locked(
    runtime: WineRuntime,
    bottle: Path,
    config: Config | None = None,
    args: list[str] | None = None,
    callback: ProgressCallback | None = None,
    *,
    wait_for_exit: bool = True,
    wait_for_window: bool = True,
) -> int:
    """Internal launch_client body (caller must hold _steam_ui_lock)."""
    from metalplay.compat.process import focus_steam_window, wait_for_steam_window

    config = config or Config.load()
    exe = steam_exe(bottle)
    if not exe:
        raise FileNotFoundError("Steam not installed. Run: metalplay steam setup")

    layer = SteamUICompatLayer(runtime, bottle)
    layer.prepare_launch(callback)
    cmd = layer.build_launch_command(exe, args, callback=callback)
    env = {**layer.launch_env(), **steam_env(config, "dxmt", for_client=True)}
    if runtime.name.startswith("crossover"):
        from metalplay.compat.crossover import crossover_env, ensure_bottle_registered

        ensure_bottle_registered(bottle)
        env = crossover_env(env)
    ctrl_env = controller_env(
        controller_profile_for(None, config),
        existing_overrides=env.get("WINEDLLOVERRIDES", ""),
    )
    env.update(ctrl_env)

    code = 0
    for attempt in range(STEAM_MAX_RESTARTS):
        label = "Launching Windows Steam client…" if attempt == 0 else f"Relaunching Steam (update cycle {attempt + 1})…"
        _log(label, callback)
        proc = subprocess.Popen(cmd, env=env)
        if wait_for_window:
            if wait_for_steam_window(timeout=120.0, callback=callback):
                _log("Steam window is up.", callback)
                focus_steam_window()
            else:
                _log(
                    "Steam started but no window appeared yet — check the Wine icon in the Dock "
                    "or click Stop Steam and try again.",
                    callback,
                )
        if not wait_for_exit:
            return 0
        code = proc.wait()
        if code == STEAM_RESTART_EXIT_CODE:
            _log("Steam updated — restarting automatically...", callback)
            layer.prepare_launch(callback)
            continue
        break
    return code


def ensure_steam_ui(
    runtime: WineRuntime,
    bottle: Path,
    config: Config | None = None,
    callback: ProgressCallback | None = None,
    *,
    window_wait: float = 35.0,
) -> None:
    """Ensure a single visible Windows Steam session before launching a game."""
    from metalplay.compat.process import (
        count_steam_ui_windows,
        focus_steam_window,
        is_steam_running,
        stop_steam_client,
        wait_for_steam_window,
    )

    with _steam_ui_lock:
        config = config or Config.load()
        from metalplay.compat.crossover import without_crossover_conf

        def _ensure() -> None:
            if is_steam_running():
                if count_steam_ui_windows(force=True) > 0:
                    _log("Steam is already running — bringing window to front.", callback)
                    focus_steam_window()
                    return
                _log("Steam is starting — waiting for the window…", callback)
                if wait_for_steam_window(timeout=window_wait, callback=callback):
                    _log("Steam window is up.", callback)
                    focus_steam_window()
                    return
                _log("Steam is running without a visible window — restarting…", callback)
                stop_steam_client(runtime, bottle)
            _launch_client_locked(
                runtime,
                bottle,
                config,
                callback=callback,
                wait_for_exit=False,
                wait_for_window=True,
            )

        if runtime.name.startswith("crossover"):
            _ensure()
        else:
            with without_crossover_conf(bottle):
                _ensure()


def diagnose_bottle(runtime: WineRuntime, bottle: Path | None = None):
    """Run Steam UI compatibility diagnostics."""
    bottle = bottle or bottles.bottle_path(STEAM_BOTTLE_NAME)
    return SteamUICompatLayer(runtime, bottle).diagnose()


def launch_game(
    runtime: WineRuntime,
    bottle: Path,
    app_id: str | int,
    config: Config | None = None,
    graphics: str | None = None,
    callback: ProgressCallback | None = None,
) -> int:
    """
    Launch a Steam game by App ID through the Windows Steam client.
    Uses -applaunch so Steam handles updates and DRM where possible.
    """
    config = config or Config.load()
    app_id = str(app_id)
    exe = steam_exe(bottle)
    if not exe:
        raise FileNotFoundError("Steam not installed")

    if is_rockstar_steam_app(app_id):
        from metalplay.compat.rockstar import (
            apply_compat,
            bypass_for_app,
            restore_playgtav_stub,
        )
        from metalplay.compat.crossover import crossover_env, crossover_runtime, ensure_bottle_registered
        from metalplay.runtime.wine import detect_installed_runtimes, get_runtime

        launch_runtime = runtime
        for candidate in detect_installed_runtimes():
            if candidate.name.startswith("crossover"):
                launch_runtime = candidate
                ensure_bottle_registered(bottle)
                _log(
                    f"Rockstar title: using {candidate.name} Wine (launcher CEF compatibility)",
                    callback,
                )
                break

        bypass = bypass_for_app(app_id)
        if bypass:
            restore_playgtav_stub(bottle, bypass, callback=callback)
        # Registry edits must use the same Wine build as the running Steam client (Gcenx).
        reg_runtime = get_runtime()
        bottle_env = {
            **os.environ,
            "WINEPREFIX": str(bottle),
            "WINEARCH": "win64",
            "PATH": f"{reg_runtime.bin_dir}:{os.environ.get('PATH', '')}",
        }
        apply_compat(reg_runtime, bottle, bottle_env)
        runtime = launch_runtime

    profile = config.get_game_profile(app_id) or config.get_game_profile("steam")
    from metalplay.compat.games import (
        compat_profile,
        game_env_overrides,
        prepare_game,
        resolve_graphics,
        source_launch_argv,
        source_launch_exe,
    )
    from metalplay.steam.library import list_games

    games = list_games(bottle, config)
    game = next((g for g in games if g.app_id == app_id), None)
    install_path = game.install_path if game else None

    gfx = resolve_graphics(
        app_id,
        install_path=install_path,
        requested=graphics or profile.get("graphics"),
        config_profile=profile,
    )
    if graphics is None and gfx != profile.get("graphics"):
        _log(f"Game {app_id}: auto-selected graphics backend → {gfx}", callback)

    crossover_launch = is_rockstar_steam_app(app_id) and crossover_runtime() is not None
    launch_runtime = crossover_runtime() if crossover_launch else runtime

    # Source/D3D9 titles: the free runtime's 32-bit wined3d→GL crashes during
    # device init on Apple GL, so route them through CrossOver when available.
    source_cx = False
    if gfx == "wined3d" and not crossover_launch:
        from metalplay.compat.crossover import crossover_runtime, cxstart_bin

        pre_compat = compat_profile(app_id, install_path=install_path)
        source_cx = (
            pre_compat is not None
            and pre_compat.graphics == "wined3d"
            and install_path is not None
            and source_launch_exe(install_path, pre_compat) is not None
            and crossover_runtime() is not None
            and cxstart_bin() is not None
        )

    if is_rockstar_steam_app(app_id):
        from metalplay.compat.rockstar import prepare_launcher_graphics

        prepare_launcher_graphics(
            bottle, callback=callback, launch_runtime=launch_runtime,
        )

    if crossover_launch:
        from metalplay.compat.crossover import ensure_bottle_registered, without_crossover_conf
        from metalplay.compat.graphics import restore_steam_client_graphics
        from metalplay.compat.process import is_steam_running, kill_wineserver, stop_steam_client
        from metalplay.runtime.wine import get_runtime

        ensure_bottle_registered(bottle)
        reg_runtime = get_runtime()
        with without_crossover_conf(bottle):
            if is_steam_running():
                _log(
                    "Rockstar + CrossOver: stopping Steam (CrossOver cannot share the bottle wineserver with Gcenx)",
                    callback,
                )
                stop_steam_client(reg_runtime, bottle)
            kill_wineserver(reg_runtime.wineserver_bin, bottle)
        restore_steam_client_graphics(launch_runtime.root, bottle)
    elif not source_cx:
        # CrossOver Source launches start Steam under CrossOver's own wineserver
        # instead (in _launch_source_crossover) — don't boot the Gcenx one here.
        ensure_steam_ui(runtime, bottle, config, callback=callback)

    bottle_env = {
        **os.environ,
        "WINEPREFIX": str(bottle),
        "WINEARCH": "win64",
        "PATH": f"{runtime.bin_dir}:{os.environ.get('PATH', '')}",
    }
    from metalplay.compat.crossover import without_crossover_conf

    game_compat = None
    with without_crossover_conf(bottle):
        game_compat = prepare_game(
            runtime,
            bottle,
            app_id,
            install_path=install_path,
            env=bottle_env,
            callback=callback,
        )

    if gfx == "dxvk" and not crossover_launch:
        from metalplay.compat.graphics import swap_bottle_to_dxvk

        try:
            swapped = swap_bottle_to_dxvk(bottle)
            if swapped:
                _log(f"DXVK: placed {len(swapped)} graphics DLL(s) in bottle", callback)
        except Exception as exc:
            _log(f"DXVK unavailable ({exc}) — falling back to dxmt", callback)
            gfx = "dxmt"

    env = steam_env(config, gfx, for_client=False, app_id=app_id)
    if crossover_launch:
        env = crossover_env(env)
    env["WINEPREFIX"] = str(bottle)
    env["WINEARCH"] = "win64"
    env["PATH"] = f"{launch_runtime.bin_dir}:{os.environ.get('PATH', '')}"
    env.update(profile.get("env", {}))
    if game_compat:
        env.update(game_env_overrides(game_compat))
    env.update(performance_env(config))

    ctrl = controller_profile_for(app_id, config)
    ctrl_env = controller_env(ctrl, existing_overrides=env.get("WINEDLLOVERRIDES", ""))
    env.update(ctrl_env)

    hint = env.get("METALPLAY_TARGET_RESOLUTION") or game_resolution_hint()
    if hint:
        _log(f"Performance mode: target in-game resolution {hint}", callback)

    if crossover_launch:
        from metalplay.compat.rockstar import (
            bypass_for_app,
            rockstar_crossover_env,
            rockstar_crossover_launch_target,
        )

        bypass = bypass_for_app(app_id)
        if not bypass:
            raise FileNotFoundError(f"No Rockstar bypass for app {app_id}")
        launcher_exe, launcher_args = rockstar_crossover_launch_target(bottle, bypass, app_id)
        if not launcher_exe.is_file():
            raise FileNotFoundError(f"Rockstar Launcher missing: {launcher_exe}")
        env = crossover_env({**os.environ, **rockstar_crossover_env(env)})
        env["SteamAppId"] = app_id
        _log(
            "Rockstar + CrossOver: launching Rockstar Launcher (log into Steam in MetalPlay first; "
            "Steam closes briefly while the launcher starts)",
            callback,
        )
        cmd = wine_command(launch_runtime.wine_bin, str(launcher_exe), *launcher_args)
    else:
        direct_exe = (
            source_launch_exe(install_path, game_compat)
            if install_path and game_compat and game_compat.graphics == "wined3d"
            else None
        )
        if direct_exe:
            if source_cx:
                return _launch_source_crossover(
                    bottle,
                    direct_exe,
                    source_launch_argv(game_compat),
                    game_compat,
                    config,
                    callback=callback,
                )
            from metalplay.compat.process import focus_steam_window, wait_for_steam_login

            if not wait_for_steam_login(
                launch_runtime.wine_bin, bottle, bottle_env, callback=callback,
            ):
                # Launching without a logged-in user is a guaranteed Sys_Error
                # ("No SteamUser") — a crash dump and an error dialog, nothing else.
                focus_steam_window()
                _log(
                    "Not signed in to Steam — sign in to the Steam window, "
                    "then run this launch again.",
                    callback,
                )
                return 3
            launch_argv = source_launch_argv(game_compat)
            _log(
                f"Direct Source launch: {direct_exe.name} {' '.join(launch_argv)}",
                callback,
            )
            cmd = wine_command(launch_runtime.wine_bin, str(direct_exe), *launch_argv)
        else:
            game_args: list[str] = []
            if game_compat and game_compat.launch_options:
                game_args = game_compat.launch_options.split()
                _log(f"Game launch options: {game_compat.launch_options}", callback)
            cmd = wine_command(
                launch_runtime.wine_bin,
                str(exe),
                "-no-cef-sandbox",
                *steam_launch_args_for_controller(ctrl),
                "-applaunch",
                app_id,
                *game_args,
            )
    if should_caffeinate() or env.get("METALPLAY_PERFORMANCE_MODE") == "1":
        enable_high_performance(callback)
        cmd = wrap_caffeinate(cmd)
        power_boosted = True
    else:
        power_boosted = False

    _log(f"Starting game via Steam (app {app_id})…", callback)
    if is_rockstar_steam_app(app_id) and not crossover_launch:
        _log(
            "Rockstar title: Steam must be running before launch. "
            "Use MetalPlay ▶ Play, not Play inside the Wine Steam library.",
            callback,
        )
        from metalplay.compat.rockstar_cef import diagnose_launch_failure, rockstar_log_paths, tail_log

        paths = rockstar_log_paths(bottle)
        stub = Path(paths["stub_log"]) if paths.get("stub_log") else None
        launcher = Path(paths["launcher_log"]) if paths.get("launcher_log") else None
        diagnosis = diagnose_launch_failure(
            tail_log(stub) if stub else [],
            tail_log(launcher) if launcher else [],
        )
        if diagnosis:
            _log(diagnosis, callback)
    proc = subprocess.Popen(cmd, env={**os.environ, **env})
    try:
        code = proc.wait()
    finally:
        if power_boosted:
            restore_balanced_power(callback)
    if crossover_launch:
        from metalplay.compat.crossover import without_crossover_conf
        from metalplay.compat.graphics import restore_steam_client_graphics
        from metalplay.runtime.wine import get_runtime

        gcenx = get_runtime()
        with without_crossover_conf(bottle):
            restore_steam_client_graphics(gcenx.root, bottle)
    if is_rockstar_steam_app(app_id) and code not in (0, 1):
        _log(
            f"Rockstar/Steam launch exited with code {code}. "
            "If the launcher died immediately, run: metalplay steam repair",
            callback,
        )
    return code


def _launch_source_crossover(
    bottle: Path,
    direct_exe: Path,
    launch_argv: list[str],
    game_compat,
    config: Config,
    callback: ProgressCallback | None = None,
) -> int:
    """
    Launch a Source game (with its Steam session) under CrossOver's Wine.

    The free runtime's 32-bit wined3d→GL path access-violates during D3D9
    device init on Apple GL, and the free alternatives are dead ends there:
    DXVK's d3d9 needs geometry shaders MoltenVK cannot offer, and wined3d's
    Vulkan renderer cannot compile SM3 shaders. CrossOver's patched wined3d
    runs these titles. The whole session (Steam + game) moves to CrossOver
    because two Wine builds cannot share the bottle's wineserver.
    """
    from metalplay.compat.crossover import (
        apply_crossover_display_fix,
        crossover_env,
        crossover_runtime,
        cxstart_command,
        ensure_bottle_registered,
    )
    from metalplay.compat.process import (
        activate_game_when_up,
        focus_steam_window,
        is_steam_running,
        kill_wineserver,
        stop_steam_client,
        wait_for_steam_login,
    )
    from metalplay.runtime.wine import get_runtime

    cx = crossover_runtime()
    cmd = cxstart_command(direct_exe, launch_argv)
    if cx is None or cmd is None:
        raise FileNotFoundError("CrossOver runtime unavailable")
    exe = steam_exe(bottle)
    if not exe:
        raise FileNotFoundError("Steam not installed")

    ensure_bottle_registered(bottle)

    gcenx = get_runtime()
    if is_steam_running():
        _log(
            "Source + CrossOver: restarting Steam under CrossOver "
            "(the runtimes cannot share the bottle's wineserver)",
            callback,
        )
        stop_steam_client(gcenx, bottle)
    kill_wineserver(gcenx.wineserver_bin, bottle)

    env = crossover_env(dict(os.environ))
    env["WINEPREFIX"] = str(bottle)
    env.update(performance_env(config))
    apply_crossover_display_fix(bottle, env, exe_names=game_compat.exe_names)

    subprocess.Popen(
        wine_command(cx.wine_bin, str(exe), "-no-cef-sandbox", "-noverifyfiles"),
        env=env,
    )
    if not wait_for_steam_login(cx.wine_bin, bottle, env, callback=callback):
        focus_steam_window()
        _log(
            "Not signed in to Steam — sign in to the Steam window, "
            "then run this launch again.",
            callback,
        )
        return 3

    if should_caffeinate() or env.get("METALPLAY_PERFORMANCE_MODE") == "1":
        enable_high_performance(callback)
        cmd = wrap_caffeinate(cmd)
        power_boosted = True
    else:
        power_boosted = False

    # CrossOver re-stamps Retina/DPI settings when its session boots the
    # desktop (the Steam start above) — re-assert 1:1 right before the game.
    apply_crossover_display_fix(bottle, env, exe_names=game_compat.exe_names)

    _log(
        f"Direct Source launch (CrossOver): {direct_exe.name} {' '.join(launch_argv)}",
        callback,
    )
    # cxstart maps the window when it brings up the CrossOver-Hosted app, but
    # relaunches into an already-running host leave it unmapped/minimized —
    # keep activating the game app while it boots.
    threading.Thread(
        target=activate_game_when_up, args=(direct_exe.name,), daemon=True
    ).start()
    proc = subprocess.Popen(cmd, env=env)
    try:
        code = proc.wait()
    finally:
        if power_boosted:
            restore_balanced_power(callback)
        # Return the bottle to the Gcenx runtime so GUI/CLI operations (which
        # spawn Gcenx wine) don't collide with a live CrossOver wineserver.
        stop_steam_client(cx, bottle)
        kill_wineserver(cx.wineserver_bin, bottle)
    return code


def launch_game_direct(
    runtime: WineRuntime,
    bottle: Path,
    app_id: str | int,
    config: Config | None = None,
    graphics: str | None = None,
) -> int:
    """Launch a game executable directly (bypasses Steam UI, game must be installed)."""
    config = config or Config.load()
    app_id = str(app_id)
    from metalplay.steam.library import list_games

    games = list_games(bottle)
    game = next((g for g in games if g.app_id == app_id), None)
    if not game or not game.exe_path:
        raise FileNotFoundError(
            f"Game {app_id} not found or not installed. Install it via Steam first."
        )

    profile = config.get_game_profile(app_id)
    from metalplay.compat.games import resolve_graphics

    install_path = game.install_path
    gfx = resolve_graphics(
        app_id,
        install_path=install_path,
        requested=graphics or profile.get("graphics"),
        config_profile=profile,
    )
    return launcher.launch(
        runtime,
        bottle,
        game.exe_path,
        config=config,
        graphics=gfx,
        game_name=app_id,
        cwd=game.install_path,
    )


def set_game_graphics(app_id: str | int, graphics: str, name: str = "") -> None:
    """Persist per-game graphics backend (dxmt for D3D11, moltenvk for D3D12)."""
    config = Config.load()
    app_id = str(app_id)
    profile = config.game_profiles.get(app_id, {})
    profile["graphics"] = graphics
    if name:
        profile["name"] = name
    config.game_profiles[app_id] = profile
    config.save()


def repair_bottle(
    runtime: WineRuntime,
    callback: ProgressCallback | None = None,
) -> Path:
    """Reconfigure an existing Steam bottle (registry + DXMT, no reinstall)."""
    bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)
    if not bottle.is_dir():
        raise FileNotFoundError("Steam bottle not found. Run: metalplay steam setup")
    _log("Repairing Steam bottle...", callback)
    from metalplay.compat.crossover import crossover_runtime, ensure_bottle_registered, prepare_crossover_template, without_crossover_conf
    from metalplay.compat.process import kill_wineserver, purge_steam_cef_data

    with without_crossover_conf(bottle):
        kill_wineserver(runtime.wineserver_bin, bottle)
        _configure_windows_bottle(runtime, bottle, callback)
        env = _bottle_env(bottle, runtime)
        _log("Applying Rockstar Games Launcher compatibility...", callback)
        apply_rockstar_compat(runtime, bottle, env)
    cef_dirs = purge_steam_cef_data(bottle)
    if cef_dirs:
        _log(f"Cleared {cef_dirs} Steam CEF cache dir(s) (fixes blank UI after DPI changes).", callback)
    if crossover_runtime():
        prepare_crossover_template()
        ensure_bottle_registered(bottle)
        _log("CrossOver: registered MetalPlay-Steam bottle for Rockstar launcher", callback)
    patched = install_all_bypasses(bottle, callback)
    if patched:
        _log(f"Rockstar launcher prepared for app(s): {', '.join(patched)}", callback)
    from metalplay.compat.games import prepare_all_installed_games

    env = _bottle_env(bottle, runtime)
    with without_crossover_conf(bottle):
        game_ids = prepare_all_installed_games(runtime, bottle, env, callback)
    if game_ids:
        _log(f"Game compat applied for: {', '.join(game_ids)}", callback)
    _log("Bottle repaired.", callback)
    return bottle


def stop_client(
    runtime: WineRuntime,
    bottle: Path | None = None,
    callback: ProgressCallback | None = None,
) -> int:
    """Stop Windows Steam if running. Returns number of processes killed."""
    from metalplay.compat.process import stop_steam_client

    bottle = bottle or bottles.bottle_path(STEAM_BOTTLE_NAME)
    return stop_steam_client(runtime, bottle, callback=callback)
