"""Install CEF wrapper for Rockstar Social Club helper (Launcher UI)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Callable

ProgressCallback = Callable[[str], None]

WRAPPER_DIR = Path(__file__).resolve().parent / "cef_wrapper"
HELPER_WRAPPER = WRAPPER_DIR / "SocialClubHelper.exe"
WRAPPER_SIZE_CEILING = 500_000
HELPER_NAME = "SocialClubHelper.exe"
HELPER_REAL = "SocialClubHelper_real.exe"
LAUNCHER_NAME = "Launcher.exe"
LAUNCHER_REAL = "Launcher_real.exe"
# PlayGTAV.exe validates Launcher.exe size/signature — never replace it with a wrapper.
_LAUNCHER_MIN_BYTES = 1_000_000


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


def _mingw_gcc() -> Path | None:
    found = shutil.which("x86_64-w64-mingw32-gcc")
    return Path(found) if found else None


def ensure_mingw(callback: ProgressCallback | None = None) -> Path:
    gcc = _mingw_gcc()
    if gcc:
        return gcc
    brew = shutil.which("brew")
    if not brew:
        raise RuntimeError("mingw-w64 not found. Install with: brew install mingw-w64")
    _log("Installing mingw-w64 (needed for Rockstar CEF wrapper)...", callback)
    subprocess.run([brew, "install", "mingw-w64"], check=True)
    gcc = _mingw_gcc()
    if not gcc:
        raise RuntimeError("mingw-w64 install failed")
    return gcc


def build_helper_wrapper(callback: ProgressCallback | None = None, force: bool = False) -> Path:
    if HELPER_WRAPPER.is_file() and not force:
        return HELPER_WRAPPER
    gcc = ensure_mingw(callback)
    _log("Building Rockstar SocialClubHelper CEF wrapper...", callback)
    subprocess.run(["make", "-C", str(WRAPPER_DIR), "clean"], check=False)
    subprocess.run(["make", "-C", str(WRAPPER_DIR), f"CC={gcc}", "SocialClubHelper.exe"], check=True)
    if not HELPER_WRAPPER.is_file():
        raise RuntimeError("SocialClubHelper CEF wrapper build failed")
    return HELPER_WRAPPER


def _is_wrapper_like(path: Path) -> bool:
    return path.is_file() and path.stat().st_size < WRAPPER_SIZE_CEILING


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def social_club_dir(bottle: Path) -> Path:
    return bottle / "drive_c/Program Files/Rockstar Games/Social Club"


def launcher_dir(bottle: Path) -> Path:
    return bottle / "drive_c/Program Files/Rockstar Games/Launcher"


def restore_launcher_exe(bottle: Path, callback: ProgressCallback | None = None) -> bool:
    """
    Restore the real Rockstar Launcher.exe.

    PlayGTAV.exe refuses to start if Launcher.exe is our small CEF wrapper (~150 KB).
    """
    launcher = launcher_dir(bottle) / LAUNCHER_NAME
    real = launcher_dir(bottle) / LAUNCHER_REAL
    if not launcher.is_file():
        _log("Rockstar: Launcher.exe missing — verify GTA in Steam", callback)
        return False
    if launcher.stat().st_size >= _LAUNCHER_MIN_BYTES and not _is_wrapper_like(launcher):
        return True
    if not real.is_file() or real.stat().st_size < _LAUNCHER_MIN_BYTES:
        _log(
            "Rockstar: Launcher_real.exe missing — in Steam: right-click GTA V Enhanced → "
            "Properties → Installed Files → Verify integrity",
            callback,
        )
        return False
    shutil.copy2(real, launcher)
    _log(
        f"Rockstar: restored real Launcher.exe ({real.stat().st_size // 1_000_000} MB)",
        callback,
    )
    return True


def rockstar_log_paths(bottle: Path) -> dict[str, str | None]:
    """User-visible Rockstar log file paths inside the bottle."""
    users = bottle / "drive_c/users"
    stub: Path | None = None
    launcher_log: Path | None = None
    if users.is_dir():
        for user_dir in users.iterdir():
            if not user_dir.is_dir():
                continue
            candidate_stub = user_dir / "AppData/Local/Rockstar Games/Launcher/stub.log"
            candidate_log = user_dir / "Documents/Rockstar Games/Launcher/launcher.log"
            if candidate_stub.is_file():
                stub = candidate_stub
            if candidate_log.is_file():
                launcher_log = candidate_log
            if stub or launcher_log:
                break
    return {
        "stub_log": str(stub) if stub else None,
        "launcher_log": str(launcher_log) if launcher_log else None,
    }


def tail_log(path: Path, lines: int = 40) -> list[str]:
    if not path.is_file():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return content.splitlines()[-lines:]


def diagnose_launch_failure(
    stub_lines: list[str],
    launcher_lines: list[str],
) -> str | None:
    """Human-readable diagnosis when stub.log shows a reinstall error."""
    stub = "\n".join(stub_lines)
    log = "\n".join(launcher_lines)
    if "Unable to launch game, please try reinstalling" not in stub:
        return None
    if "Initializing group 1" in log and "Initializing group 2" not in log:
        extra = ""
        if "exited with code 4294930433" in stub or "0xffff" in stub.lower():
            extra = (
                " Exit code 4294930433 usually means the launcher crashed immediately "
                "(often while Steam still has DXMT graphics loaded). "
            )
        return (
            "Rockstar Launcher web UI crashed during startup (CEF dies at 'Initializing group 1')."
            + extra
            + " Your install is not corrupt — the launcher quit before GTA could run. "
            "With CrossOver installed, MetalPlay launches the Rockstar Launcher via CrossOver "
            "(Steam closes briefly — log in via MetalPlay first). If launch hangs on wineboot or "
            "winedbg, the bottle may be damaged from mixing Gcenx and CrossOver Wine: "
            "metalplay steam reset --force && metalplay steam setup, then reinstall GTA. "
            "On free Gcenx Wine alone, libcef often still crashes; CrossOver 24+ is the "
            "most reported working path for GTA V Enhanced on Mac."
        )
    if "exited with code 3" in stub:
        return (
            "Rockstar Launcher exited with code 3. This is usually a launcher/Wine issue, not "
            "missing game files. Run metalplay steam repair and launch from Steam Library → Play."
        )
    return (
        "Rockstar Launcher failed before the game started. Run metalplay steam repair, then "
        "launch from Steam Library → Play."
    )


def wrapper_needs_redeploy(bottle: Path) -> bool:
    if not HELPER_WRAPPER.is_file():
        return True
    target = social_club_dir(bottle) / HELPER_NAME
    if not target.is_file():
        return True
    return _md5(target) != _md5(HELPER_WRAPPER)


def purge_rockstar_cef_locks(bottle: Path) -> int:
    removed = 0
    users = bottle / "drive_c/users"
    if not users.is_dir():
        return 0
    patterns = ("Singleton*", "*.lock", "CrashpadMetrics*.pma")
    for cache in users.glob("*/AppData/Local/Rockstar Games/Social Club/GPUCache"):
        for pattern in patterns:
            for path in cache.glob(pattern):
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
    for cache in users.glob("*/AppData/Local/Rockstar Games/Launcher/cache"):
        for pattern in patterns:
            for path in cache.glob(pattern):
                try:
                    path.unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
    return removed


def install_into_bottle(
    bottle: Path,
    callback: ProgressCallback | None = None,
    force_build: bool = False,
) -> bool:
    """Restore real Launcher.exe and install SocialClubHelper CEF wrapper only."""
    restore_launcher_exe(bottle, callback)
    sc_dir = social_club_dir(bottle)
    target = sc_dir / HELPER_NAME
    real = sc_dir / HELPER_REAL
    if not target.is_file():
        _log("SocialClubHelper.exe not found — install GTA / Rockstar Launcher first", callback)
        return restore_launcher_exe(bottle, callback)

    wrapper_bin = build_helper_wrapper(callback, force=force_build)
    wrapper_bytes = wrapper_bin.read_bytes()

    if _is_wrapper_like(target):
        if not real.is_file():
            raise RuntimeError(f"{sc_dir}: wrapper without {HELPER_REAL} backup")
    else:
        if not real.is_file() or _is_wrapper_like(real):
            _log("Rockstar: saving SocialClubHelper binary as SocialClubHelper_real.exe", callback)
            shutil.copy2(target, real)
        elif _md5(target) != _md5(real):
            shutil.copy2(target, real)

    target.write_bytes(wrapper_bytes)
    _log("Rockstar: installed SocialClubHelper CEF wrapper", callback)
    return True
