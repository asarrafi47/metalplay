"""Parse Steam library VDF files from a Wine bottle."""

from __future__ import annotations

import re
from pathlib import Path

from metalplay.config import Config
from metalplay.steam.client import SteamGame, steam_dir


def _parse_vdf_quotes(text: str) -> dict[str, str]:
    """Simple VDF parser for Steam acf/library files."""
    result: dict[str, str] = {}
    for match in re.finditer(r'"(\w+)"\s+"([^"]*)"', text):
        result[match.group(1)] = match.group(2)
    return result


def _find_game_exe(install_path: Path, *, app_id: str | None = None) -> Path | None:
    """Find a likely game executable in the install directory."""
    if not install_path.is_dir():
        return None
    from metalplay.compat.rockstar import is_rockstar_steam_app

    if is_rockstar_steam_app(app_id):
        stub = install_path / "PlayGTAV.exe"
        if stub.is_file():
            return stub
    preferred = (
        "GTA5_Enhanced.exe",
        "GTA5.exe",
        "RDR2.exe",
        "PlayGTAV.exe",
    )
    for name in preferred:
        candidate = install_path / name
        if candidate.is_file():
            return candidate
    # Prefer exe in root, then one level deep
    exes = sorted(install_path.glob("*.exe"))
    if exes:
        return exes[0]
    for sub in install_path.iterdir():
        if sub.is_dir():
            sub_exes = sorted(sub.glob("*.exe"))
            if sub_exes:
                return sub_exes[0]
    return None


def _library_paths(steam_root: Path) -> list[Path]:
    """Return all Steam library folders including the default."""
    paths = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if not vdf.is_file():
        return paths
    text = vdf.read_text(errors="replace")
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        p = Path(match.group(1).replace("\\\\", "/"))
        if p.is_dir():
            paths.append(p)
    return paths


def list_games(bottle: Path, config: Config | None = None) -> list[SteamGame]:
    """List installed Steam games from appmanifest files."""
    config = config or Config.load()
    root = steam_dir(bottle)
    if not root:
        return []

    games: list[SteamGame] = []
    seen: set[str] = set()

    for lib in _library_paths(root):
        steamapps = lib / "steamapps"
        if not steamapps.is_dir():
            continue
        for manifest in steamapps.glob("appmanifest_*.acf"):
            app_id = manifest.stem.replace("appmanifest_", "")
            if app_id in seen:
                continue
            seen.add(app_id)
            data = _parse_vdf_quotes(manifest.read_text(errors="replace"))
            state = data.get("StateFlags", "")
            install_dir = data.get("installdir", "")
            install_path = steamapps / "common" / install_dir
            # StateFlags 4 = fully installed; also accept if files exist on disk
            if state and state not in ("4", "6") and not install_path.is_dir():
                continue
            profile = config.get_game_profile(app_id)
            gfx = profile.get("graphics", "auto")
            games.append(
                SteamGame(
                    app_id=app_id,
                    name=data.get("name", f"App {app_id}"),
                    install_dir=install_dir,
                    install_path=install_path if install_path.is_dir() else None,
                    exe_path=_find_game_exe(install_path, app_id=app_id) if install_path.is_dir() else None,
                    graphics=gfx,
                )
            )

    return sorted(games, key=lambda g: g.name.lower())


def status(bottle: Path, *, light: bool = False) -> dict:
    """Steam installation and library summary."""
    from metalplay.compat.process import count_steam_ui_windows, is_steam_running
    from metalplay.steam.bootstrap import is_bootstrap_complete
    from metalplay.steam.client import is_installed, is_stub_installed, steam_exe

    root = steam_dir(bottle)
    ready = is_installed(bottle)
    running = is_steam_running()

    result: dict = {
        "installed": ready,
        "running": running,
        "stub_only": bool(root and is_stub_installed(bottle) and not ready),
        "bootstrap_complete": bool(root and is_bootstrap_complete(root)) if root else False,
        "steam_exe": str(steam_exe(bottle)) if root else None,
        "steam_root": str(root) if root else None,
    }

    if light:
        result["ui_windows"] = count_steam_ui_windows() if running else 0
        return result

    games = list_games(bottle) if root else []
    result["ui_windows"] = count_steam_ui_windows() if running else 0
    result["game_count"] = len(games)
    result["games"] = [
        {
            "app_id": g.app_id,
            "name": g.name,
            "install_dir": g.install_dir,
            "installed": g.install_path is not None,
            "has_exe": g.exe_path is not None,
            "graphics": g.graphics,
        }
        for g in games
    ]
    return result
