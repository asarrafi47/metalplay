"""Free Wine runtime installers."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable

from metalplay import paths
from metalplay.runtime.dxmt import install_into_wine, setup as setup_dxmt
from metalplay.runtime.wine import WineRuntime, register_runtime

ProgressCallback = Callable[[str], None]

GCENX_VERSION = "11.10"
GCENX_WINE_URL = (
    f"https://github.com/Gcenx/macOS_Wine_builds/releases/download/"
    f"{GCENX_VERSION}/wine-devel-{GCENX_VERSION}-osx64.tar.xz"
)


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


def install_gcenx(callback: ProgressCallback | None = None) -> WineRuntime:
    """
    Download Gcenx Wine devel (free, DXMT-compatible) to ~/.metalplay/runtimes/wine/.
    Runs under Rosetta on Apple Silicon.
    """
    dest_root = paths.runtimes_dir() / "wine" / f"gcenx-devel-{GCENX_VERSION}"
    wine_root = dest_root / "Wine Devel.app" / "Contents" / "Resources" / "wine"

    if wine_root.is_dir() and (wine_root / "bin" / "wine").is_file():
        _log(f"Gcenx Wine already installed at {wine_root}", callback)
        runtime = register_runtime(wine_root)
        return runtime

    paths.ensure_dirs()
    archive = paths.cache_dir() / f"wine-devel-{GCENX_VERSION}-osx64.tar.xz"
    if not archive.is_file():
        _log(f"Downloading Gcenx Wine {GCENX_VERSION} (~180 MB)...", callback)
        urllib.request.urlretrieve(GCENX_WINE_URL, archive)

    dest_root.mkdir(parents=True, exist_ok=True)
    _log("Extracting Wine...", callback)
    with tarfile.open(archive, "r:xz") as tar:
        tar.extractall(path=dest_root, filter="data")

    if not (wine_root / "bin" / "wine").is_file():
        raise RuntimeError(f"Wine binary not found after extract at {wine_root}")

    runtime = register_runtime(wine_root)
    _log(f"Installed Gcenx Wine → {wine_root}", callback)
    return runtime


def install_brew_wine_stable(callback: ProgressCallback | None = None) -> WineRuntime | None:
    """Install Wine Stable via Homebrew (free). May need DXMT overlay for Metal."""
    if not shutil.which("brew"):
        _log("Homebrew not found — skipping brew install", callback)
        return None

    app_path = Path("/Applications/Wine Stable.app/Contents/Resources/wine")
    if not app_path.is_dir():
        _log("Installing Wine Stable via Homebrew...", callback)
        subprocess.run(
            ["brew", "install", "--cask", "wine-stable", "gstreamer-runtime"],
            check=True,
        )

    if not app_path.is_dir():
        _log("Wine Stable install did not produce expected app bundle", callback)
        return None

    runtime = register_runtime(app_path)
    _log(f"Installed Wine Stable → {app_path}", callback)
    return runtime


def install_free_runtime(
    prefer: str = "gcenx",
    callback: ProgressCallback | None = None,
) -> WineRuntime:
    """
    Install a free Wine runtime. Tries Gcenx first (best DXMT support),
    falls back to Homebrew Wine Stable.
    """
    if prefer == "brew":
        runtime = install_brew_wine_stable(callback)
        if runtime:
            return runtime
        return install_gcenx(callback)

    try:
        return install_gcenx(callback)
    except Exception as exc:
        _log(f"Gcenx install failed: {exc}", callback)
        runtime = install_brew_wine_stable(callback)
        if runtime:
            return runtime
        raise RuntimeError("Could not install any free Wine runtime") from exc


def setup_all(callback: ProgressCallback | None = None) -> dict[str, str]:
    """Full free setup: DXMT + Wine + overlay."""
    _log("Installing DXMT...", callback)
    setup_dxmt()

    _log("Installing free Wine runtime (Gcenx)...", callback)
    runtime = install_free_runtime(prefer="gcenx", callback=callback)

    _log("Overlaying DXMT onto Wine...", callback)
    install_into_wine(runtime)

    return {
        "wine": str(runtime.root),
        "version": runtime.version(),
        "dxmt": str(paths.dxmt_dir()),
    }
