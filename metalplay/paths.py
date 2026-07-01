"""Application paths and constants."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "MetalPlay"
DXMT_VERSION = "v0.80"
DXMT_RELEASE_URL = (
    f"https://github.com/3Shain/dxmt/releases/download/{DXMT_VERSION}/"
    f"dxmt-{DXMT_VERSION}-builtin.tar.gz"
)

# Graphics backends that route DirectX to Apple Metal
GRAPHICS_BACKENDS = ("dxmt", "moltenvk", "wined3d", "auto")

# Known Wine installation locations on macOS
WINE_SEARCH_PATHS: tuple[tuple[str, Path], ...] = (
    ("crossover", Path("/Applications/CrossOver.app/Contents/SharedSupport/CrossOver")),
    ("crossover-alt", Path("/Applications/CrossOver Preview.app/Contents/SharedSupport/CrossOver")),
    ("wine-stable", Path("/Applications/Wine Stable.app/Contents/Resources/wine")),
    ("wine-devel", Path("/Applications/Wine Devel.app/Contents/Resources/wine")),
    ("wine-staging", Path("/Applications/Wine Staging.app/Contents/Resources/wine")),
)


def home() -> Path:
    return Path(os.environ.get("METALPLAY_HOME", Path.home() / ".metalplay")).expanduser()


def config_file() -> Path:
    return home() / "config.json"


def runtimes_dir() -> Path:
    return home() / "runtimes"


def bottles_dir() -> Path:
    return home() / "bottles"


def dxmt_dir() -> Path:
    return runtimes_dir() / "dxmt" / DXMT_VERSION


def cache_dir() -> Path:
    return home() / "cache"


def logs_dir() -> Path:
    return home() / "logs"


def profiles_dir() -> Path:
    return Path(__file__).resolve().parent / "profiles"


def tune_dir() -> Path:
    return home() / "tune"


def ensure_dirs() -> None:
    for path in (home(), runtimes_dir(), bottles_dir(), cache_dir(), logs_dir(), tune_dir()):
        path.mkdir(parents=True, exist_ok=True)
