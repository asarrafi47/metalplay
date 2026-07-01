"""Build and install the steamwebhelper CEF wrapper (fixes black Steam UI under Wine)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str], None]

WRAPPER_DIR = Path(__file__).resolve().parent / "wrapper"
WRAPPER_EXE = WRAPPER_DIR / "steamwebhelper.exe"
WRAPPER_SIZE_CEILING = 500_000  # bytes — Valve binary is several MB
# Steam uses 64-bit CEF on modern clients; 32-bit cef.win7 must not get the x86_64 wrapper
CEF_64BIT_DIRS = frozenset({"cef.win64", "cef.win7x64"})


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


def _mingw_gcc() -> Path | None:
    for name in ("x86_64-w64-mingw32-gcc",):
        found = shutil.which(name)
        if found:
            return Path(found)
    brew = shutil.which("brew")
    if not brew:
        return None
    prefix = subprocess.run(
        [brew, "--prefix", "mingw-w64"],
        capture_output=True,
        text=True,
        check=False,
    )
    if prefix.returncode != 0:
        return None
    candidate = Path(prefix.stdout.strip()) / "bin" / "x86_64-w64-mingw32-gcc"
    return candidate if candidate.is_file() else None


def ensure_mingw(callback: ProgressCallback | None = None) -> Path:
    """Return path to x86_64-w64-mingw32-gcc, installing via Homebrew if needed."""
    gcc = _mingw_gcc()
    if gcc:
        return gcc
    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError(
            "mingw-w64 not found. Install with: brew install mingw-w64"
        )
    _log("Installing mingw-w64 (needed to build steamwebhelper wrapper)...", callback)
    subprocess.run([brew, "install", "mingw-w64"], check=True)
    gcc = _mingw_gcc()
    if not gcc:
        raise RuntimeError("mingw-w64 install failed")
    return gcc


def build_wrapper(callback: ProgressCallback | None = None, force: bool = False) -> Path:
    """Compile steamwebhelper.exe wrapper into metalplay/steam/wrapper/."""
    if WRAPPER_EXE.is_file() and not force:
        return WRAPPER_EXE

    gcc = ensure_mingw(callback)
    _log("Building steamwebhelper wrapper...", callback)
    subprocess.run(["make", "-C", str(WRAPPER_DIR), "clean"], check=False)
    subprocess.run(
        ["make", "-C", str(WRAPPER_DIR), f"CC={gcc}"],
        check=True,
    )
    if not WRAPPER_EXE.is_file():
        raise RuntimeError(f"Wrapper build failed: {WRAPPER_EXE} missing")
    _log(f"Built wrapper ({WRAPPER_EXE.stat().st_size} bytes)", callback)
    return WRAPPER_EXE


def _is_wrapper_like(path: Path) -> bool:
    return path.is_file() and path.stat().st_size < WRAPPER_SIZE_CEILING


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _restore_32bit_cef(cef_dir: Path, callback: ProgressCallback | None = None) -> None:
    """Undo a mistaken x86_64 wrapper install in the 32-bit cef.win7 tree."""
    if cef_dir.name not in {"cef.win7"}:
        return
    target = cef_dir / "steamwebhelper.exe"
    real = cef_dir / "steamwebhelper_real.exe"
    if real.is_file() and (_is_wrapper_like(target) or not target.is_file()):
        _log(f"{cef_dir.name}: restoring 32-bit steamwebhelper from backup", callback)
        shutil.copy2(real, target)


def restore_original_webhelpers(
    steam_root: Path,
    callback: ProgressCallback | None = None,
) -> int:
    """Remove wrapper and restore Valve's steamwebhelper.exe from backup."""
    cef_root = steam_root / "bin" / "cef"
    restored = 0
    for cef_dir in sorted(cef_root.glob("cef.win*")):
        target = cef_dir / "steamwebhelper.exe"
        real = cef_dir / "steamwebhelper_real.exe"
        if not real.is_file():
            continue
        if _is_wrapper_like(target) or _md5(target) != _md5(real):
            shutil.copy2(real, target)
            _log(f"Restored original webhelper in {cef_dir.name}", callback)
            restored += 1
    return restored


def wrapper_needs_redeploy(steam_root: Path) -> bool:
    """True if any 64-bit CEF dir is missing our wrapper."""
    if not WRAPPER_EXE.is_file():
        return True
    expected = hashlib.md5(WRAPPER_EXE.read_bytes()).hexdigest()
    cef_root = steam_root / "bin" / "cef"
    for cef_dir in cef_root.glob("cef.win*"):
        if cef_dir.name not in CEF_64BIT_DIRS:
            continue
        target = cef_dir / "steamwebhelper.exe"
        if not target.is_file() or _md5(target) != expected:
            return True
    return False


def install_into_steam(
    steam_root: Path,
    callback: ProgressCallback | None = None,
    force_build: bool = False,
) -> int:
    """
    Install wrapper into each cef.win* directory under Steam/bin/cef.
    Returns number of directories updated.
    """
    cef_root = steam_root / "bin" / "cef"
    if not cef_root.is_dir():
        _log(
            f"CEF not ready yet ({cef_root.name}) — wrapper will install after Steam first update",
            callback,
        )
        return 0

    wrapper_bin = build_wrapper(callback, force=force_build)
    wrapper_bytes = wrapper_bin.read_bytes()
    installed = 0

    for cef_dir in sorted(cef_root.glob("cef.win*")):
        if not cef_dir.is_dir():
            continue
        if cef_dir.name not in CEF_64BIT_DIRS:
            _restore_32bit_cef(cef_dir, callback)
            continue
        target = cef_dir / "steamwebhelper.exe"
        real = cef_dir / "steamwebhelper_real.exe"
        if not target.is_file():
            _log(f"Skipping {cef_dir.name} (no steamwebhelper.exe)", callback)
            continue

        if _is_wrapper_like(target):
            if not real.is_file():
                raise RuntimeError(
                    f"{cef_dir}: wrapper present but steamwebhelper_real.exe missing. "
                    "Reinstall Steam: metalplay steam setup"
                )
            if _is_wrapper_like(real):
                raise RuntimeError(
                    f"{cef_dir}: both steamwebhelper.exe and _real.exe are wrappers. "
                    "Reinstall Steam: metalplay steam setup"
                )
        else:
            if not real.is_file() or _is_wrapper_like(real):
                _log(f"{cef_dir.name}: saving Valve binary as steamwebhelper_real.exe", callback)
                shutil.copy2(target, real)
            elif _md5(target) != _md5(real):
                _log(f"{cef_dir.name}: Steam updated webhelper — refreshing stashed copy", callback)
                shutil.copy2(target, real)

        target.write_bytes(wrapper_bytes)
        _log(f"Wrapper installed in {cef_dir.name}", callback)
        installed += 1

    if installed == 0:
        raise RuntimeError(f"No cef.win* directories found under {cef_root}")
    return installed
