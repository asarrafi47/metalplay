"""MetalPlay command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from metalplay import __version__, paths
from metalplay.bottle import manager as bottles
from metalplay.config import Config
from metalplay.launcher import run as launcher
from metalplay.runtime import dxmt
from metalplay.runtime.installer import install_brew_wine_stable, install_free_runtime, setup_all
from metalplay.runtime.wine import (
    check_rosetta,
    detect_installed_runtimes,
    get_runtime,
    register_runtime,
    rosetta_install_hint,
    system_info,
)


def _cmd_tune(args: argparse.Namespace) -> int:
    from metalplay.tune import apply_tune, detect_hardware, format_report, load_applied_profile
    from metalplay.tune.power import cooling_notes

    hw = detect_hardware()
    action = getattr(args, "tune_action", "report")

    if action == "show":
        applied = load_applied_profile()
        print(format_report(hw, applied))
        if applied and applied.get("cooling_note"):
            print(f"\n{applied['cooling_note']}")
        return 0

    if action == "apply":
        print("Tuning MetalPlay for your Mac...\n")
        result = apply_tune(callback=print, apply_power=not getattr(args, "no_power", False))
        print("\n" + format_report(hw, result))
        print(f"\n{result.get('cooling_note', '')}")
        print("\nDone. Launch GTA V: metalplay steam run 271590")
        return 0

    applied = load_applied_profile()
    print("MetalPlay hardware report\n" + "=" * 40)
    print(format_report(hw, applied))
    print(f"\n{cooling_notes()}")
    if not applied:
        print("\nApply: metalplay tune apply")
    return 0


def _cmd_doctor(_: argparse.Namespace) -> int:
    print(f"MetalPlay {__version__} — System Check\n")
    info = system_info()
    print(f"  macOS:     {info['macos']}")
    print(f"  Arch:      {info['arch']}")
    print(f"  Home:      {paths.home()}")

    if info["arch"] == "arm64":
        rosetta = check_rosetta()
        status = "installed" if rosetta else "NOT installed"
        print(f"  Rosetta 2: {status}")
        if not rosetta:
            print(f"\n  → {rosetta_install_hint()}")

    print("\nWine Runtimes:")
    runtimes = detect_installed_runtimes()
    if not runtimes:
        print("  ✗ No Wine installation found")
        print("\n  Install one of:")
        print("    • CrossOver (recommended): brew install --cask crossover")
        print("    • Wine Stable:             brew install --cask wine-stable")
        print("    • FOSS build:              metalplay runtime register /path/to/wine")
    else:
        for rt in runtimes:
            metal = "Metal-capable" if rt.is_metal_capable() else "may lack Metal support"
            print(f"  • {rt.name}: {rt.version()} [{metal}]")
            print(f"    {rt.root}")

    print("\nDXMT (Direct3D → Metal):")
    if dxmt.is_installed():
        print(f"  ✓ Installed at {paths.dxmt_dir()}")
    else:
        print("  ✗ Not installed — run: metalplay install dxmt")

    print("\nBottles:")
    bottle_list = bottles.list_bottles()
    if not bottle_list:
        print("  (none) — run: metalplay bottle create gaming")
    else:
        for name, path, meta in bottle_list:
            gfx = meta.graphics if meta else "unknown"
            print(f"  • {name} [{gfx}] — {path}")

    return 0 if runtimes and dxmt.is_installed() else 1


def _cmd_install(args: argparse.Namespace) -> int:
    if args.component == "dxmt":
        dxmt.setup(force=args.force)
        print(f"DXMT {paths.DXMT_VERSION} installed to {paths.dxmt_dir()}")

        runtimes = detect_installed_runtimes()
        metal_runtimes = [r for r in runtimes if r.is_metal_capable()]
        if metal_runtimes:
            for rt in metal_runtimes:
                dxmt.install_into_wine(rt)
                print(f"  Overlaid onto Wine: {rt.name}")
        else:
            print("\nNote: No Metal-capable Wine found yet.")
            print("Install free Wine: metalplay install wine")
        return 0

    if args.component == "wine":
        prefer = args.source or "gcenx"
        print(f"Installing free Wine runtime ({prefer})...")
        runtime = install_free_runtime(prefer=prefer)
        if dxmt.is_installed():
            dxmt.install_into_wine(runtime)
            print("DXMT overlaid onto Wine.")
        print(f"Ready: {runtime.name} — {runtime.version()}")
        return 0

    if args.component == "all":
        result = setup_all()
        print(f"Setup complete: Wine {result['version']}")
        return 0

    print(f"Unknown component: {args.component}")
    return 1


def _cmd_gui(args: argparse.Namespace) -> int:
    from metalplay.gui import __main__ as gui_launcher

    argv = ["metalplay-gui"]
    if getattr(args, "desktop", False):
        argv.append("--desktop")
    if getattr(args, "native", False):
        argv.append("--native")
    if getattr(args, "browser", False):
        argv.append("--browser")
    if getattr(args, "port", None):
        argv.extend(["--port", str(args.port)])
    sys.argv = argv
    gui_launcher.main()
    return 0


def _cmd_runtime(args: argparse.Namespace) -> int:
    if args.runtime_action == "list":
        runtimes = detect_installed_runtimes()
        if not runtimes:
            print("No Wine runtimes detected.")
            return 1
        for rt in runtimes:
            cap = "✓ Metal" if rt.is_metal_capable() else "✗ no Metal"
            print(f"{rt.name:16} {cap:12} {rt.version()}")
            print(f"  {rt.root}")
        return 0

    if args.runtime_action == "register":
        runtime = register_runtime(Path(args.path))
        print(f"Registered: {runtime.name} → {runtime.root}")
        return 0

    if args.runtime_action == "setup":
        if not dxmt.is_installed():
            dxmt.setup()
        runtimes = detect_installed_runtimes()
        if not runtimes:
            print("No Wine runtime found. Install CrossOver or register a custom build.")
            return 1
        for rt in runtimes:
            if rt.is_metal_capable() or args.force:
                dxmt.install_into_wine(rt)
                print(f"DXMT installed into {rt.name}")
            else:
                print(f"Skipped {rt.name} — not Metal-capable (use --force to try anyway)")
        return 0

    return 1


def _cmd_bottle(args: argparse.Namespace) -> int:
    config = Config.load()

    if args.bottle_action == "list":
        for name, path, meta in bottles.list_bottles():
            gfx = meta.graphics if meta else "?"
            print(f"{name:20} [{gfx}]  {path}")
        return 0

    if args.bottle_action == "create":
        runtime = get_runtime(config.wine_runtime)
        if not runtime:
            print("No Wine runtime found. Run: metalplay doctor")
            return 1
        path = bottles.create(
            args.name,
            runtime,
            windows=args.windows,
            graphics=args.graphics,
        )
        config.default_bottle = config.default_bottle or args.name
        config.save()
        print(f"Created bottle: {path}")
        return 0

    if args.bottle_action == "delete":
        bottles.remove(args.name)
        print(f"Deleted bottle: {args.name}")
        return 0

    if args.bottle_action == "config":
        runtime = get_runtime(config.wine_runtime)
        if not runtime:
            print("No Wine runtime found.")
            return 1
        bottle = bottles.bottle_path(args.name)
        if not bottle.is_dir():
            print(f"Bottle not found: {args.name}")
            return 1
        bottles.run_wine(runtime, bottle, ["winecfg"])
        return 0

    return 1


def _cmd_run(args: argparse.Namespace) -> int:
    config = Config.load()
    runtime = get_runtime(config.wine_runtime)
    if not runtime:
        print("No Wine runtime found. Run: metalplay doctor")
        return 1

    bottle_name = args.bottle or config.default_bottle
    if not bottle_name:
        print("No bottle specified. Use --bottle or set a default with bottle create.")
        return 1

    bottle = bottles.bottle_path(bottle_name)
    if not bottle.is_dir():
        print(f"Bottle not found: {bottle_name}")
        return 1

    meta = bottles.load_meta(bottle)
    graphics = args.graphics or (meta.graphics if meta else config.default_graphics)

    return launcher.launch(
        runtime,
        bottle,
        args.executable,
        args.exe_args,
        config,
        graphics=graphics,
        game_name=args.profile,
        cwd=args.cwd,
    )


def _cmd_config(args: argparse.Namespace) -> int:
    config = Config.load()

    if args.config_action == "show":
        print(json.dumps({
            "wine_runtime": config.wine_runtime,
            "default_graphics": config.default_graphics,
            "default_bottle": config.default_bottle,
            "dxmt_log_level": config.dxmt_log_level,
            "use_rosetta": config.use_rosetta,
        }, indent=2))
        return 0

    if args.config_action == "set":
        if args.key == "wine_runtime":
            config.wine_runtime = args.value
        elif args.key == "default_graphics":
            if args.value not in paths.GRAPHICS_BACKENDS:
                print(f"Invalid graphics backend. Choose: {', '.join(paths.GRAPHICS_BACKENDS)}")
                return 1
            config.default_graphics = args.value
        elif args.key == "default_bottle":
            config.default_bottle = args.value
        elif args.key == "dxmt_log_level":
            config.dxmt_log_level = args.value
        else:
            print(f"Unknown key: {args.key}")
            return 1
        config.save()
        print(f"Set {args.key} = {args.value}")
        return 0

    return 1


def _cmd_steam(args: argparse.Namespace) -> int:
    from metalplay.steam import (
        STEAM_BOTTLE_NAME,
        launch_client,
        launch_game,
        launch_game_direct,
        list_games,
        setup,
        status,
    )
    from metalplay.steam.client import (
        STEAM_RESTART_EXIT_CODE,
        describe_steam_exit,
        install_client,
        is_installed,
        set_game_graphics,
    )

    config = Config.load()
    runtime = get_runtime(config.wine_runtime)
    if not runtime and args.steam_action not in ("setup",):
        print("No Wine runtime found. Run: metalplay steam setup")
        return 1

    bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)

    if args.steam_action == "setup":
        print("Setting up Windows Steam bottle (Wine + DXMT + Steam client)...")
        result = setup(skip_deps=getattr(args, "skip_deps", False))
        print(f"  Bottle:    {result['bottle']}")
        print(f"  Steam:     {result['steam_exe']}")
        print("\nLaunch Steam: metalplay steam launch")
        return 0

    if args.steam_action == "install":
        if not runtime:
            print("No Wine runtime found.")
            return 1
        if not bottle.is_dir():
            from metalplay.steam.client import ensure_bottle
            ensure_bottle(runtime)
        install_client(runtime, bottle, skip_deps=getattr(args, "skip_deps", False))
        return 0

    if args.steam_action == "bootstrap":
        if not runtime:
            print("No Wine runtime found.")
            return 1
        from metalplay.steam.bootstrap import is_bootstrap_complete, run_bootstrap, steam_root
        from metalplay.steam.client import is_stub_installed

        if not is_stub_installed(bottle):
            print("Steam stub not found. Run: metalplay steam setup")
            return 1
        root = steam_root(bottle)
        if root and is_bootstrap_complete(root):
            print("Steam client already bootstrapped.")
            return 0
        run_bootstrap(runtime, bottle, callback=print)
        from metalplay.steam.client import repair_bottle
        repair_bottle(runtime, callback=print)
        print("Bootstrap complete. Launch: metalplay steam launch")
        return 0

    if args.steam_action == "launch":
        if not is_installed(bottle):
            print("Steam not installed. Run: metalplay steam setup")
            return 1
        from metalplay.compat.process import focus_steam_window, is_steam_running
        from metalplay.steam.client import stop_client

        if is_steam_running():
            if focus_steam_window():
                print("Steam window brought to front.")
                return 0
            print("Steam running without a visible window — restarting...")
            stop_client(runtime, bottle, callback=print)
        code = launch_client(runtime, bottle, config, callback=print)
        print(describe_steam_exit(code))
        return code if code != STEAM_RESTART_EXIT_CODE else 0

    if args.steam_action == "stop":
        from metalplay.steam.client import stop_client
        stop_client(runtime, bottle, callback=print)
        return 0

    if args.steam_action == "status":
        print(json.dumps(status(bottle), indent=2))
        return 0

    if args.steam_action == "games":
        if not is_installed(bottle):
            print("Steam not installed.")
            return 1
        for g in list_games(bottle, config):
            gfx = g.graphics
            installed = "✓" if g.install_path else "✗"
            print(f"{g.app_id:8}  [{gfx:8}]  {installed}  {g.name}")
        return 0

    if args.steam_action == "run":
        if not is_installed(bottle):
            print("Steam not installed.")
            return 1
        graphics = getattr(args, "graphics", None)
        if getattr(args, "direct", False):
            return launch_game_direct(runtime, bottle, args.app_id, config, graphics=graphics)
        return launch_game(runtime, bottle, args.app_id, config, graphics=graphics)

    if args.steam_action == "set-graphics":
        set_game_graphics(args.app_id, args.graphics, name=getattr(args, "name", "") or "")
        print(f"App {args.app_id} → {args.graphics}")
        return 0

    if args.steam_action == "repair":
        from metalplay.steam.client import repair_bottle
        if not runtime:
            print("No Wine runtime found.")
            return 1
        repair_bottle(runtime, callback=print)
        print("Steam bottle repaired (compat layer applied).")
        print("Try: metalplay steam launch")
        return 0

    if args.steam_action == "doctor":
        from metalplay.steam.client import diagnose_bottle
        if not runtime:
            print("No Wine runtime found.")
            return 1
        report = diagnose_bottle(runtime, bottle)
        for line in report.checks:
            print(line)
        for line in report.warnings:
            print(line)
        if report.ok:
            print("\nSteam UI compatibility: OK")
            return 0
        print("\nSteam UI compatibility: ISSUES FOUND — run: metalplay steam repair")
        return 1

    if args.steam_action == "reset":
        if not getattr(args, "force", False):
            print("This deletes the steam bottle. Re-run with --force to confirm.")
            return 1
        bottles.remove(STEAM_BOTTLE_NAME)
        print("Steam bottle deleted. Run: metalplay steam setup")
        return 0

    return 1


def _cmd_controller(args: argparse.Namespace) -> int:
    from metalplay.cli_controller import cmd_controller

    return cmd_controller(args)


def _cmd_app(args: argparse.Namespace) -> int:
    from metalplay.packaging.macos_app import build_app, install_app, open_app, uninstall_app

    action = getattr(args, "app_action", "install")
    port = getattr(args, "port", 8765)

    if action == "build":
        dest = build_app(port=port)
        print(f"Built {dest}")
        print(f"Drag to Applications or run: metalplay app install")
        return 0

    if action == "install":
        dest = install_app(port=port)
        if getattr(args, "open", False):
            open_app()
        return 0

    if action == "open":
        open_app()
        return 0

    if action == "uninstall":
        if uninstall_app():
            print("Removed MetalPlay.app from Applications")
        else:
            print("MetalPlay.app not found in Applications")
        return 0

    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metalplay",
        description="Run Windows games on macOS using Apple Metal (Wine + DXMT)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check system readiness").set_defaults(
        func=_cmd_doctor
    )

    tune = sub.add_parser("tune", help="Detect hardware and apply performance profile")
    tune_sub = tune.add_subparsers(dest="tune_action")
    tune_sub.add_parser("apply", help="Apply tuned settings for this Mac").set_defaults(
        tune_action="apply"
    )
    tune_sub.add_parser("show", help="Show applied tune profile").set_defaults(tune_action="show")
    tune.set_defaults(tune_action="report", func=_cmd_tune)
    tune_apply = tune_sub.choices["apply"]  # type: ignore[attr-defined]
    tune_apply.add_argument(
        "--no-power", action="store_true", help="Skip pmset High Power Mode changes"
    )

    gui = sub.add_parser("gui", help="Open MetalPlay (native window on macOS, or browser)")
    gui.add_argument("--native", "-n", action="store_true", help="Native app window (requires pywebview)")
    gui.add_argument("--browser", "-b", action="store_true", help="Open in system browser instead of native window")
    gui.add_argument("--desktop", "-d", action="store_true", help="Use tkinter desktop UI (requires python-tk)")
    gui.add_argument("--port", type=int, default=8765, help="Web UI port (default: 8765)")
    gui.set_defaults(func=_cmd_gui)

    app = sub.add_parser("app", help="Install MetalPlay as a macOS application")
    app_sub = app.add_subparsers(dest="app_action", required=True)
    app_install = app_sub.add_parser("install", help="Install MetalPlay.app to /Applications")
    app_install.add_argument("--port", type=int, default=8765)
    app_install.add_argument("--open", action="store_true", help="Launch after install")
    app_install.set_defaults(app_action="install")
    app_build = app_sub.add_parser("build", help="Build MetalPlay.app in dist/")
    app_build.add_argument("--port", type=int, default=8765)
    app_build.set_defaults(app_action="build")
    app_sub.add_parser("open", help="Open installed MetalPlay.app").set_defaults(app_action="open")
    app_sub.add_parser("uninstall", help="Remove MetalPlay.app").set_defaults(app_action="uninstall")
    app.set_defaults(func=_cmd_app)

    install = sub.add_parser("install", help="Install components")
    install.add_argument(
        "component",
        choices=["dxmt", "wine", "all"],
        help="Component to install (all = DXMT + free Wine + overlay)",
    )
    install.add_argument(
        "--source",
        choices=["gcenx", "brew"],
        default="gcenx",
        help="Wine source for 'install wine' (default: gcenx, free DXMT-capable build)",
    )
    install.add_argument("--force", action="store_true", help="Re-download if cached")
    install.set_defaults(func=_cmd_install)

    runtime = sub.add_parser("runtime", help="Manage Wine runtimes")
    rt_sub = runtime.add_subparsers(dest="runtime_action", required=True)
    rt_sub.add_parser("list", help="List detected Wine installations")
    reg = rt_sub.add_parser("register", help="Register a Wine installation")
    reg.add_argument("path", help="Path to Wine root directory")
    setup = rt_sub.add_parser("setup", help="Install DXMT into Wine runtimes")
    setup.add_argument("--force", action="store_true")
    runtime.set_defaults(func=_cmd_runtime)

    bottle = sub.add_parser("bottle", help="Manage Wine bottles")
    b_sub = bottle.add_subparsers(dest="bottle_action", required=True)
    b_sub.add_parser("list", help="List bottles")
    create = b_sub.add_parser("create", help="Create a new bottle")
    create.add_argument("name", help="Bottle name")
    create.add_argument("--windows", default="win10", choices=["win10", "win7", "winxp"])
    create.add_argument("--graphics", default="dxmt", choices=list(paths.GRAPHICS_BACKENDS))
    delete = b_sub.add_parser("delete", help="Delete a bottle")
    delete.add_argument("name")
    cfg = b_sub.add_parser("config", help="Open winecfg for a bottle")
    cfg.add_argument("name")
    bottle.set_defaults(func=_cmd_bottle)

    run = sub.add_parser("run", help="Launch a Windows executable")
    run.add_argument("executable", help="Path to .exe (Windows or Unix path)")
    run.add_argument("exe_args", nargs="*", help="Arguments passed to the executable")
    run.add_argument("-b", "--bottle", help="Bottle name")
    run.add_argument("-g", "--graphics", choices=list(paths.GRAPHICS_BACKENDS))
    run.add_argument("-p", "--profile", help="Game profile name from config")
    run.add_argument("--cwd", help="Working directory")
    run.set_defaults(func=_cmd_run)

    config = sub.add_parser("config", help="View or change settings")
    c_sub = config.add_subparsers(dest="config_action", required=True)
    c_sub.add_parser("show", help="Show current config")
    set_cmd = c_sub.add_parser("set", help="Set a config value")
    set_cmd.add_argument("key", choices=["wine_runtime", "default_graphics", "default_bottle", "dxmt_log_level"])
    set_cmd.add_argument("value")
    config.set_defaults(func=_cmd_config)

    steam = sub.add_parser("steam", help="Windows Steam — install, browse, and play games")
    s_sub = steam.add_subparsers(dest="steam_action", required=True)
    s_setup = s_sub.add_parser("setup", help="Full setup: Wine + bottle + Steam client")
    s_setup.add_argument("--skip-deps", action="store_true", help="Skip winetricks dependencies")
    s_setup.set_defaults(steam_action="setup")
    s_install = s_sub.add_parser("install", help="Install Steam client into bottle")
    s_install.add_argument("--skip-deps", action="store_true")
    s_install.set_defaults(steam_action="install")
    s_sub.add_parser(
        "bootstrap",
        help="Download full Steam client after stub install (~300MB, first run)",
    ).set_defaults(steam_action="bootstrap")
    s_sub.add_parser("launch", help="Open Windows Steam client").set_defaults(steam_action="launch")
    s_sub.add_parser("stop", help="Stop Windows Steam if running").set_defaults(steam_action="stop")
    s_sub.add_parser("status", help="Show Steam install and library status").set_defaults(steam_action="status")
    s_sub.add_parser("games", help="List installed Steam games").set_defaults(steam_action="games")
    s_run = s_sub.add_parser("run", help="Launch a game by Steam App ID")
    s_run.add_argument("app_id", help="Steam App ID (e.g. 1245620)")
    s_run.add_argument("-g", "--graphics", choices=["dxmt", "moltenvk", "wined3d", "auto"])
    s_run.add_argument("--direct", action="store_true", help="Launch game .exe directly (skip Steam UI)")
    s_run.set_defaults(steam_action="run")
    s_gfx = s_sub.add_parser("set-graphics", help="Set graphics backend for a game")
    s_gfx.add_argument("app_id")
    s_gfx.add_argument("graphics", choices=["dxmt", "moltenvk", "wined3d", "auto"])
    s_gfx.add_argument("--name", default="", help="Game name for reference")
    s_gfx.set_defaults(steam_action="set-graphics")
    s_sub.add_parser("repair", help="Apply Steam UI compatibility layer").set_defaults(
        steam_action="repair"
    )
    s_sub.add_parser("doctor", help="Diagnose Steam UI compatibility (winemetal, DXGI, wrapper)").set_defaults(
        steam_action="doctor"
    )
    s_reset = s_sub.add_parser("reset", help="Delete steam bottle and start fresh")
    s_reset.add_argument("--force", action="store_true", help="Confirm deletion")
    s_reset.set_defaults(steam_action="reset")
    steam.set_defaults(func=_cmd_steam)

    controller = sub.add_parser("controller", help="Game controllers — detect, diagnose, and configure")
    c_sub = controller.add_subparsers(dest="controller_action", required=True)
    c_list = c_sub.add_parser("list", help="List detected macOS game controllers")
    c_list.add_argument("--json", action="store_true", help="Output JSON")
    c_list.set_defaults(controller_action="list")
    c_sub.add_parser(
        "doctor",
        help="Diagnose controller support in Steam bottle (steam)",
    ).set_defaults(controller_action="doctor")
    c_sub.add_parser(
        "test",
        help="Quick test: verify Wine sees a joystick (registry query)",
    ).set_defaults(controller_action="test")
    c_profile = c_sub.add_parser("set-profile", help="Set per-game controller profile")
    c_profile.add_argument("app_id", help="Steam App ID (e.g. 271590)")
    c_profile.add_argument(
        "--steam-input",
        choices=["on", "off"],
        help="Enable or disable Steam Input for this game",
    )
    c_profile.add_argument(
        "--prefer",
        choices=["xinput", "dinput"],
        help="Prefer XInput or DirectInput API",
    )
    c_profile.add_argument("--name", default="", help="Game name for reference")
    c_profile.set_defaults(controller_action="set-profile")
    controller.set_defaults(func=_cmd_controller)

    return parser


def main() -> None:
    paths.ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))
