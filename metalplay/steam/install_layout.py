"""Steam install paths and library folder layout inside a Wine bottle."""

from __future__ import annotations

import time
from pathlib import Path

# NSIS silent install targets this path on 64-bit prefixes (same as Lutris / Valve default).
STEAM_INSTALL_REL = Path("drive_c/Program Files (x86)/Steam")
STEAM_INSTALL_WIN = r"C:\Program Files (x86)\Steam"

# SteamSetup.exe often exits 512 under Wine even on success (Lutris treats it as OK).
STEAM_INSTALL_OK_CODES = frozenset({0, 512, 3010})


def steam_install_dir(bottle: Path) -> Path:
    return bottle / STEAM_INSTALL_REL


def prepare_install_dirs(bottle: Path) -> Path:
    """Create the default Steam install tree with writable permissions."""
    root = steam_install_dir(bottle)
    for sub in ("", "steamapps", "steamapps/common", "steamapps/downloading", "steamapps/temp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def steam_root_to_win_path(steam_root: Path, bottle: Path) -> str:
    drive_c = (bottle / "drive_c").resolve()
    rel = steam_root.resolve().relative_to(drive_c)
    return "C:\\" + str(rel).replace("/", "\\")


def libraryfolders_vdf(win_library_path: str) -> str:
    ts = int(time.time())
    return (
        '"libraryfolders"\n'
        "{\n"
        '\t"0"\n'
        "\t{\n"
        f'\t\t"path"\t\t"{win_library_path}"\n'
        '\t\t"label"\t\t""\n'
        '\t\t"contentid"\t\t""\n'
        '\t\t"totalsize"\t\t"0"\n'
        '\t\t"updateclean"\t\t"0"\n'
        f'\t\t"timeupdated"\t\t"{ts}"\n'
        "\t}\n"
        "}\n"
    )


def ensure_steam_library(steam_root: Path, bottle: Path) -> bool:
    """
    Ensure steamapps/ and libraryfolders.vdf exist.
    Returns True if anything was created or repaired.
    """
    changed = False
    steamapps = steam_root / "steamapps"
    if not steamapps.is_dir():
        steamapps.mkdir(parents=True, exist_ok=True)
        changed = True
    for sub in ("common", "downloading", "temp"):
        subdir = steamapps / sub
        if not subdir.is_dir():
            subdir.mkdir(parents=True, exist_ok=True)
            changed = True

    vdf = steamapps / "libraryfolders.vdf"
    win_path = steam_root_to_win_path(steam_root, bottle)
    if not vdf.is_file() or win_path not in vdf.read_text(encoding="utf-8", errors="replace"):
        vdf.write_text(libraryfolders_vdf(win_path), encoding="utf-8")
        changed = True
    return changed


def library_ready(bottle: Path) -> bool:
    sd = bottle / STEAM_INSTALL_REL
    if not (sd / "steam.exe").is_file():
        return False
    steamapps = sd / "steamapps"
    return steamapps.is_dir() and (steamapps / "libraryfolders.vdf").is_file()
