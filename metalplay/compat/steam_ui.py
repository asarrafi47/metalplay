"""
Steam UI compatibility layer for Wine on macOS.

Orchestrates the fixes required for Valve's CEF-based Steam client:
- Correct PE architecture for graphics DLLs (winemetal 32-bit in system32 breaks DXGI)
- No DXMT in the Steam bottle (games get DXMT at launch time only)
- steamwebhelper wrapper (--disable-gpu --use-angle=swiftshader)
- Registry, fonts, Chromium lock cleanup, optional virtual desktop
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from metalplay.compat.desktop import (
    build_steam_command,
    to_wine_path,
    virtual_desktop_enabled,
    virtual_desktop_resolution,
)
from metalplay.compat.display import steam_display_settings
from metalplay.compat.fonts import install_ui_fonts
from metalplay.compat.graphics import pe_machine, restore_steam_client_graphics, restore_wine_graphics_dlls
from metalplay.compat.process import (
    kill_stale_steam_processes,
    kill_wineserver,
    purge_chromium_locks,
)
from metalplay.compat.registry import apply_registry, sync_display_registry
from metalplay.compat.ssl import ensure_ca_bundle
from metalplay.controller.compat import apply_controller_registry
from metalplay.runtime.wine import WineRuntime

ProgressCallback = Callable[[str], None]

# PE machine types for diagnostics
_PE_I386 = 0x014C
_PE_AMD64 = 0x8664

_DISPLAY_SYNC_TTL = 45.0
_last_display_sync: float = 0.0

STEAM_LAUNCH_ARGS: tuple[str, ...] = (
    "-no-cef-sandbox",
    "-cef-single-process",
    "-noverifyfiles",
)

STEAM_CLIENT_ENV: dict[str, str] = {
    "STEAM_RUNTIME": "0",
    "WINEESYNC": "0",
    "WINEFSYNC": "0",
    # No dxgi/d3d overrides — restored native Wine DLLs in system32 load correctly
    # Do not set dwrite=d — CEF/libcef requires DWrite.dll (c0000135 if disabled).
    "WINEDLLOVERRIDES": (
        "winemenubuilder.exe=d;mscoree=;mshtml=;"
        "bcrypt=b;ncrypt=b;"
        "gameoverlayrenderer,gameoverlayrenderer64=d"
    ),
    "WINEDEBUG": "-all",
    "MVK_CONFIG_LOG_LEVEL": "0",
    "CHROMIUM_FLAGS": "--disable-gpu",
}


@dataclass
class CompatReport:
    ok: bool
    checks: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


class SteamUICompatLayer:
    """Apply and verify Steam UI compatibility fixes for a Wine bottle."""

    def __init__(self, runtime: WineRuntime, bottle: Path) -> None:
        self.runtime = runtime
        self.bottle = bottle

    def _bottle_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["WINEPREFIX"] = str(self.bottle)
        env["WINEARCH"] = "win64"
        env["PATH"] = f"{self.runtime.bin_dir}:{env.get('PATH', '')}"
        if self.runtime.name.startswith("crossover"):
            from metalplay.compat.crossover import crossover_env, ensure_bottle_registered

            ensure_bottle_registered(self.bottle)
            env = crossover_env(env)
        return env

    def apply(self, callback: ProgressCallback | None = None) -> list[str]:
        """Run all compatibility fixes. Returns list of actions taken."""
        actions: list[str] = []

        _log("Compat layer: applying registry tweaks...", callback)
        bottle_env = self._bottle_env()
        display = apply_registry(self.runtime, self.bottle, bottle_env, callback)
        self._last_display_settings = display
        actions.append("registry")

        if ensure_ca_bundle(self.bottle):
            actions.append("cacert")
            _log("Compat layer: installed TLS CA bundle (cacert.pem)", callback)

        reg_actions = apply_controller_registry(
            self.runtime, self.bottle, bottle_env, callback,
        )
        actions.extend(reg_actions)

        fixed = restore_wine_graphics_dlls(self.runtime.root, self.bottle)
        if fixed:
            _log(f"Compat layer: restored {len(fixed)} Wine graphics DLL(s)", callback)
            actions.append(f"graphics:{len(fixed)}")

        nfonts = install_ui_fonts(self.bottle)
        if nfonts:
            _log(f"Compat layer: installed {nfonts} UI font(s)", callback)
            actions.append(f"fonts:{nfonts}")

        from metalplay.steam.client import steam_dir
        from metalplay.steam.install_layout import ensure_steam_library
        from metalplay.steam.webhelper_wrapper import (
            install_into_steam,
            restore_original_webhelpers,
        )

        sd = steam_dir(self.bottle)
        if sd:
            if ensure_steam_library(sd, self.bottle):
                _log("Compat layer: created Steam library folder (steamapps)", callback)
            restore_original_webhelpers(sd, callback)
            try:
                install_into_steam(sd, callback)
                actions.append("webhelper-wrapper")
                _log("Compat layer: installed steamwebhelper wrapper", callback)
            except RuntimeError as exc:
                _log(f"Compat layer: webhelper wrapper skipped ({exc})", callback)

        return actions

    def prepare_launch(self, callback: ProgressCallback | None = None) -> None:
        """Pre-launch cleanup and ensure wrapper is current."""
        global _last_display_sync
        from metalplay.compat.process import is_steam_running, kill_stale_steam_processes, kill_wineserver

        kill_wineserver(self.runtime.wineserver_bin, self.bottle)
        stale = kill_stale_steam_processes()
        if stale:
            _log(f"Compat layer: killed {stale} stale Steam process(es)", callback)
            kill_wineserver(self.runtime.wineserver_bin, self.bottle)
        elif is_steam_running():
            _log("Compat layer: Steam already running — skipping wineserver reset", callback)

        removed = purge_chromium_locks(self.bottle)
        if removed:
            _log(f"Compat layer: purged {removed} Chromium lock file(s)", callback)

        restored = restore_steam_client_graphics(self.runtime.root, self.bottle)
        if restored:
            _log(
                f"Compat layer: restored {len(restored)} graphics DLL(s) for Steam UI",
                callback,
            )

        now = __import__("time").monotonic()
        sync_display_registry(self.runtime, self.bottle, self._bottle_env(), callback)
        _last_display_sync = now

        from metalplay.compat.games import prepare_all_installed_games

        prepare_all_installed_games(
            self.runtime, self.bottle, self._bottle_env(), callback,
            files_only=True,
        )

        from metalplay.steam.client import steam_dir
        from metalplay.steam.install_layout import ensure_steam_library
        from metalplay.steam.webhelper_wrapper import (
            install_into_steam,
            wrapper_needs_redeploy,
        )

        sd = steam_dir(self.bottle)
        if sd:
            if ensure_steam_library(sd, self.bottle):
                _log("Compat layer: created Steam library folder (steamapps)", callback)
            if wrapper_needs_redeploy(sd):
                _log("Compat layer: redeploying steamwebhelper wrapper...", callback)
                install_into_steam(sd, callback)

    def launch_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        from metalplay.tune.apply import performance_env

        env = {**os.environ, **STEAM_CLIENT_ENV, **self._bottle_env()}
        env.update(performance_env())
        display = steam_display_settings()
        factor = display.cef_device_scale_factor
        if factor:
            env["METALPLAY_CEF_DEVICE_SCALE_FACTOR"] = factor
        else:
            env.pop("METALPLAY_CEF_DEVICE_SCALE_FACTOR", None)
        if extra:
            env.update(extra)
        return env

    def build_launch_command(
        self,
        steam_exe: Path,
        extra_args: list[str] | None = None,
        callback: ProgressCallback | None = None,
    ) -> list[str]:
        args = list(STEAM_LAUNCH_ARGS)
        if extra_args:
            args.extend(extra_args)
        win_path = to_wine_path(steam_exe, self.runtime, self.bottle)
        resolution = None
        if virtual_desktop_enabled():
            from metalplay.compat.display import virtual_desktop_resolution_for_steam

            resolution = virtual_desktop_resolution() or virtual_desktop_resolution_for_steam()
            _log(
                f"Compat layer: virtual desktop {resolution or 'auto'}",
                callback,
            )
        elif __import__("sys").platform == "darwin":
            _log(
                "Compat layer: direct Steam window (multi-monitor — virtual desktop disabled)",
                callback,
            )
        return build_steam_command(
            self.runtime,
            win_path,
            args,
            resolution=resolution,
        )

    def diagnose(self) -> CompatReport:
        """Check bottle health for Steam UI."""
        report = CompatReport(ok=True)
        windows = self.bottle / "drive_c" / "windows"

        wm64 = windows / "system32" / "winemetal.dll"
        if wm64.is_file():
            machine = pe_machine(wm64)
            if machine == _PE_I386:
                report.ok = False
                report.checks.append("FAIL: system32/winemetal.dll is 32-bit (must be x86-64)")
            elif machine == _PE_AMD64:
                report.checks.append("OK: system32/winemetal.dll is x86-64")
            else:
                report.warnings.append(f"WARN: system32/winemetal.dll unknown PE machine {machine}")
        else:
            report.ok = False
            report.checks.append("FAIL: system32/winemetal.dll missing")

        dxgi = windows / "system32" / "dxgi.dll"
        if dxgi.is_file():
            report.checks.append(f"OK: system32/dxgi.dll present ({dxgi.stat().st_size} bytes)")
        else:
            report.ok = False
            report.checks.append("FAIL: system32/dxgi.dll missing")

        from metalplay.steam.client import steam_dir
        from metalplay.steam.install_layout import library_ready
        from metalplay.steam.webhelper_wrapper import wrapper_needs_redeploy

        sd = steam_dir(self.bottle)
        if sd:
            if wrapper_needs_redeploy(sd):
                report.warnings.append("WARN: steamwebhelper wrapper missing or outdated")
            else:
                report.checks.append("OK: steamwebhelper wrapper installed")
            if library_ready(self.bottle):
                report.checks.append("OK: Steam library folder (steamapps)")
            else:
                report.ok = False
                report.checks.append("FAIL: steamapps library missing — run: metalplay steam repair")
        else:
            report.warnings.append("WARN: Steam not installed in bottle")

        overrides = os.environ.get("WINEDLLOVERRIDES", "")
        if "dwrite=d" in overrides.lower().replace(" ", ""):
            report.warnings.append(
                "WARN: shell WINEDLLOVERRIDES disables dwrite — CEF needs DWrite.dll; "
                "use metalplay steam launch (do not export dwrite=d)"
            )

        return report


def compat_report(runtime: WineRuntime, bottle: Path) -> CompatReport:
    return SteamUICompatLayer(runtime, bottle).diagnose()
