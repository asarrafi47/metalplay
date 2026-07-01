"""Wine runtime detection and management."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from metalplay import paths


@dataclass(frozen=True)
class WineRuntime:
    name: str
    root: Path
    wine_bin: Path
    wineserver_bin: Path
    wine_lib: Path
    source: str

    @property
    def bin_dir(self) -> Path:
        return self.wine_bin.parent

    def is_metal_capable(self) -> bool:
        """Check if this Wine build can load winemetal (CrossOver-lineage)."""
        winemetal_unix = self.wine_lib / "x86_64-unix" / "winemetal.so"
        winemetal_win = self.wine_lib / "x86_64-windows" / "winemetal.dll"
        # CrossOver bundles dxmt; custom installs place winemetal after DXMT overlay
        if winemetal_unix.exists() or winemetal_win.exists():
            return True
        # CrossOver stores DXMT separately
        dxmt_lib = self.root / "lib" / "dxmt"
        if dxmt_lib.is_dir() and any(dxmt_lib.iterdir()):
            return True
        # winemac.drv present is a weak signal for Metal support
        winemac = self.wine_lib / "x86_64-unix" / "winemac.so"
        return winemac.exists()

    def version(self) -> str:
        try:
            result = subprocess.run(
                [str(self.wine_bin), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip() or "unknown"
        except (subprocess.SubprocessError, OSError):
            return "unknown"


def _find_wine_in_tree(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    candidates = [
        root / "bin" / "wine",
        root / "wine" / "bin" / "wine",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # Search one level deep for bin/wine
    try:
        children = list(root.iterdir())
    except OSError:
        return None
    for child in children:
        if child.is_dir():
            wine = child / "bin" / "wine"
            if wine.is_file():
                return wine
    return None


class WineNotFoundError(RuntimeError):
    """No usable Wine runtime is installed."""


def _runtime_from_wine(name: str, wine_bin: Path, source: str) -> WineRuntime | None:
    if not wine_bin.is_file():
        return None
    root = wine_bin.parent.parent
    wine_lib = root / "lib" / "wine"
    if not wine_lib.is_dir():
        return None
    wineserver = wine_bin.parent / "wineserver"
    return WineRuntime(
        name=name,
        root=root,
        wine_bin=wine_bin,
        wineserver_bin=wineserver,
        wine_lib=wine_lib,
        source=source,
    )


def detect_installed_runtimes() -> list[WineRuntime]:
    found: list[WineRuntime] = []
    seen: set[Path] = set()

    for name, install_path in paths.WINE_SEARCH_PATHS:
        wine_bin = _find_wine_in_tree(install_path)
        if wine_bin and wine_bin not in seen:
            runtime = _runtime_from_wine(name, wine_bin, "system")
            if runtime:
                found.append(runtime)
                seen.add(wine_bin)

    # User-managed runtimes in ~/.metalplay/runtimes/wine/
    user_wine = paths.runtimes_dir() / "wine"
    if user_wine.is_dir():
        for child in sorted(user_wine.iterdir()):
            wine_bin = _find_wine_in_tree(child)
            if wine_bin and wine_bin not in seen:
                runtime = _runtime_from_wine(child.name, wine_bin, "user")
                if runtime:
                    found.append(runtime)
                    seen.add(wine_bin)

    # PATH lookup
    which_wine = shutil.which("wine")
    if which_wine:
        wine_path = Path(which_wine).resolve()
        if wine_path not in seen:
            runtime = _runtime_from_wine("path", wine_path, "path")
            if runtime:
                found.append(runtime)
                seen.add(wine_path)

    return found


def get_runtime(name_or_path: str | None = None) -> WineRuntime | None:
    runtimes = detect_installed_runtimes()
    if not runtimes:
        return None
    if name_or_path is None:
        # CrossOver is registered for Rockstar launcher CEF; Gcenx remains the default.
        for runtime in runtimes:
            if not runtime.name.startswith("crossover"):
                return runtime
        return runtimes[0]
    for runtime in runtimes:
        if runtime.name == name_or_path or str(runtime.root) == name_or_path:
            return runtime
    candidate = Path(name_or_path).expanduser()
    if candidate.is_dir():
        wine_bin = _find_wine_in_tree(candidate)
        if wine_bin:
            return _runtime_from_wine(candidate.name, wine_bin, "custom")
    return None


def require_runtime(name_or_path: str | None = None) -> WineRuntime:
    """Return a Wine runtime or raise with an actionable error."""
    runtime = get_runtime(name_or_path)
    if runtime is not None:
        return runtime
    raise WineNotFoundError(
        "No Wine runtime found. Install one with: metalplay install wine",
    )


def register_runtime(wine_root: Path) -> WineRuntime:
    """Copy or symlink a Wine tree into ~/.metalplay/runtimes/wine/."""
    wine_bin = _find_wine_in_tree(wine_root)
    if not wine_bin:
        raise FileNotFoundError(f"No wine binary found under {wine_root}")

    dest = paths.runtimes_dir() / "wine" / wine_root.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.symlink_to(wine_root.resolve())
    runtime = _runtime_from_wine(wine_root.name, _find_wine_in_tree(dest), "user")
    if not runtime:
        raise RuntimeError(f"Failed to register Wine runtime at {wine_root}")
    return runtime


def system_info() -> dict[str, str]:
    arch = platform.machine()
    return {
        "arch": arch,
        "macos": platform.mac_ver()[0],
        "needs_rosetta": str(arch == "arm64"),
    }


def check_rosetta() -> bool:
    if platform.machine() != "arm64":
        return True
    try:
        result = subprocess.run(
            ["pgrep", "-q", "oahd"],
            capture_output=True,
        )
        return result.returncode == 0
    except OSError:
        return False


def wine_command(wine_bin: Path, *args: str, use_rosetta: bool = True) -> list[str]:
    """Build wine command, wrapping with Rosetta on Apple Silicon when needed."""
    cmd = [str(wine_bin), *args]
    if use_rosetta and platform.machine() == "arm64":
        return ["arch", "-x86_64", *cmd]
    return cmd


def rosetta_install_hint() -> str:
    return "Install Rosetta 2: softwareupdate --install-rosetta --agree-to-license"
