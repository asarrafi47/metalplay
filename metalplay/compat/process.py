"""Process and filesystem cleanup before launching Steam."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from metalplay.runtime.wine import WineRuntime

ProgressCallback = Callable[[str], None] | None

# Match Wine Steam and virtual-desktop hosts — never unrelated Wine apps.
_STEAM_PROCESS_PATTERN = (
    r"Steam[\\/].*steam\.exe|steamwebhelper|steamservice\.exe"
)
_STEAM_SESSION_PATTERN = (
    r"Steam[\\/].*steam\.exe|steamwebhelper|steamservice\.exe|"
    r"C:\\windows\\system32\\explorer\.exe|winedevice\.exe|wine64-preloader"
)
_STEAM_CLIENT_PATTERN = r"Steam[\\/].*steam\.exe"
_UI_WINDOW_CACHE_TTL = 8.0
_MIN_STEAM_WINDOW_WIDTH = 320
_MIN_STEAM_WINDOW_HEIGHT = 200
_ui_window_cache: tuple[float, int] | None = None


def invalidate_ui_window_cache() -> None:
    global _ui_window_cache
    _ui_window_cache = None


def is_steam_running() -> bool:
    """True if the Wine Windows Steam client (steam.exe) is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", _STEAM_CLIENT_PATTERN],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _wine_windows(*, on_screen_only: bool = False) -> list[dict]:
    """Return Wine-owned macOS windows via Quartz (width/height/name/pid/onscreen)."""
    try:
        import Quartz
    except ImportError:
        return []

    opts = Quartz.kCGWindowListExcludeDesktopElements
    if on_screen_only:
        opts |= Quartz.kCGWindowListOptionOnScreenOnly
    else:
        opts |= Quartz.kCGWindowListOptionAll

    windows: list[dict] = []
    for info in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
        owner = str(info.get("kCGWindowOwnerName", ""))
        if owner.lower() != "wine":
            continue
        bounds = info.get("kCGWindowBounds") or {}
        width = int(bounds.get("Width", 0))
        height = int(bounds.get("Height", 0))
        if width < 80 or height < 80:
            continue
        windows.append(
            {
                "name": str(info.get("kCGWindowName", "")),
                "width": width,
                "height": height,
                "x": int(bounds.get("X", 0)),
                "y": int(bounds.get("Y", 0)),
                "onscreen": bool(info.get("kCGWindowIsOnscreen")),
                "pid": int(info.get("kCGWindowOwnerPID", 0)),
            }
        )
    return windows


def _steam_like_windows(*, on_screen_only: bool = False) -> list[dict]:
    """Wine windows large enough to be Steam UI (not tiny helper surfaces)."""
    return [
        w
        for w in _wine_windows(on_screen_only=on_screen_only)
        if w["width"] >= _MIN_STEAM_WINDOW_WIDTH and w["height"] >= _MIN_STEAM_WINDOW_HEIGHT
    ]


def count_steam_ui_windows(*, force: bool = False) -> int:
    """Count visible Steam-sized Wine windows; cached to keep GUI polling fast."""
    global _ui_window_cache
    if not force:
        cached = _ui_window_cache
        if cached and (time.monotonic() - cached[0]) < _UI_WINDOW_CACHE_TTL:
            return cached[1]
    n = len(_steam_like_windows(on_screen_only=True))
    _ui_window_cache = (time.monotonic(), n)
    return n


def focus_steam_window() -> bool:
    """Bring a Wine/Steam window to the front. Returns True if a window was found."""
    invalidate_ui_window_cache()
    windows = _steam_like_windows(on_screen_only=False)
    if not windows:
        windows = _wine_windows(on_screen_only=False)
    if not windows:
        return False

    # Prefer on-screen windows, then the largest.
    windows.sort(
        key=lambda w: (w["onscreen"], w["width"] * w["height"]),
        reverse=True,
    )
    target = windows[0]
    pid = target["pid"]

    script = f'''
tell application "System Events"
    set found to false
    repeat with p in (every process whose name is "wine")
        if unix id of p is {pid} then
            set frontmost of p to true
            try
                if (count of windows of p) > 0 then
                    perform action "AXRaise" of (first window of p)
                end if
            end try
            set found to true
            exit repeat
        end if
    end repeat
    return found
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=4,
        )
        found = result.returncode == 0 and result.stdout.strip().lower() == "true"
        if found:
            count_steam_ui_windows(force=True)
        return found
    except (OSError, subprocess.SubprocessError):
        return False


def wait_for_steam_window(
    timeout: float = 120.0,
    *,
    callback=None,
) -> bool:
    """Poll until a Steam-sized Wine window appears on screen."""
    deadline = time.monotonic() + timeout
    last_log = 0.0
    while time.monotonic() < deadline:
        if count_steam_ui_windows(force=True) > 0:
            focus_steam_window()
            return True
        now = time.monotonic()
        if callback and now - last_log >= 8.0:
            callback("Waiting for Steam window…")
            last_log = now
        time.sleep(1.0)
    return False


def kill_stale_steam_processes() -> int:
    """Kill lingering Wine/Steam session processes. Returns number of PIDs signaled."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", _STEAM_SESSION_PATTERN],
            capture_output=True,
            text=True,
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        if not pids:
            return 0
        subprocess.run(["kill", "-9", *pids], capture_output=True)
        time.sleep(0.5)
        invalidate_ui_window_cache()
        return len(pids)
    except (OSError, subprocess.SubprocessError):
        return 0


def kill_orphan_wineservers(runtime: WineRuntime) -> int:
    """Kill stray wineserver processes for our Wine runtime (stuck dock icons)."""
    marker = str(runtime.wineserver_bin)
    try:
        result = subprocess.run(
            ["pgrep", "-f", marker],
            capture_output=True,
            text=True,
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        if not pids:
            return 0
        subprocess.run(["kill", "-9", *pids], capture_output=True)
        time.sleep(0.5)
        return len(pids)
    except (OSError, subprocess.SubprocessError):
        return 0


def kill_wineserver(wineserver_bin: Path, bottle: Path, *, timeout: float = 8.0) -> None:
    if wineserver_bin.is_file():
        try:
            subprocess.run(
                [str(wineserver_bin), "-k"],
                env={**dict(__import__("os").environ), "WINEPREFIX": str(bottle)},
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            pass
    time.sleep(0.3)


def purge_chromium_locks(bottle: Path) -> int:
    """Remove stale CEF singleton locks. Returns files removed."""
    removed = 0
    users = bottle / "drive_c" / "users"
    if not users.is_dir():
        return 0
    for htmlcache in users.glob("*/AppData/Local/Steam/htmlcache"):
        for pattern in ("Singleton*", "*.lock", "CrashpadMetrics*.pma"):
            for path_str in glob.glob(str(htmlcache / pattern)):
                try:
                    Path(path_str).unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
    return removed


def purge_steam_htmlcache(bottle: Path) -> bool:
    """Delete Steam CEF htmlcache (fixes black/blank UI after DPI or account changes)."""
    users = bottle / "drive_c" / "users"
    if not users.is_dir():
        return False
    removed_any = False
    for htmlcache in users.glob("*/AppData/Local/Steam/htmlcache"):
        if htmlcache.is_dir():
            shutil.rmtree(htmlcache, ignore_errors=True)
            removed_any = True
    return removed_any


def purge_steam_cef_data(bottle: Path) -> int:
    """Remove Steam CEF profile dirs (htmlcache + CEF User Data). Returns dirs removed."""
    users = bottle / "drive_c" / "users"
    if not users.is_dir():
        return 0
    removed = 0
    for user_dir in users.iterdir():
        if not user_dir.is_dir():
            continue
        local = user_dir / "AppData" / "Local"
        for rel in ("Steam/htmlcache", "CEF/User Data"):
            path = local / rel
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    return removed


def _bottle_env(runtime: WineRuntime, bottle: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["WINEPREFIX"] = str(bottle)
    env["PATH"] = f"{runtime.bin_dir}:{env.get('PATH', '')}"
    return env


def _steam_exe_path(bottle: Path) -> Path | None:
    for rel in (
        "drive_c/Program Files (x86)/Steam/steam.exe",
        "drive_c/Program Files/Steam/steam.exe",
    ):
        path = bottle / rel
        if path.is_file():
            return path
    return None


def request_steam_shutdown(
    runtime: WineRuntime,
    bottle: Path,
    *,
    timeout: float = 25.0,
    callback: ProgressCallback = None,
) -> bool:
    """Ask steam.exe to exit via -shutdown (best-effort)."""
    if not is_steam_running():
        return False
    steam_exe = _steam_exe_path(bottle)
    if steam_exe is None:
        return False

    from metalplay.compat.crossover import without_crossover_conf
    from metalplay.runtime.wine import wine_command

    if callback:
        callback("Sending Steam shutdown request…")
    env = _bottle_env(runtime, bottle)
    try:
        with without_crossover_conf(bottle):
            subprocess.run(
                wine_command(runtime.wine_bin, str(steam_exe), "-shutdown"),
                env=env,
                capture_output=True,
                timeout=timeout,
            )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return False
    return True


def wait_steam_exit(timeout: float = 20.0, *, poll_interval: float = 0.5) -> bool:
    """Wait until steam.exe is no longer running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_steam_running():
            return True
        time.sleep(poll_interval)
    return not is_steam_running()


def _signal_matching_processes(pattern: str, signal: int) -> int:
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        if not pids:
            return 0
        subprocess.run(["kill", f"-{signal}", *pids], capture_output=True)
        return len(pids)
    except (OSError, subprocess.SubprocessError):
        return 0


def terminate_steam_processes() -> int:
    """SIGTERM Wine Steam processes. Returns number of PIDs signaled."""
    n = _signal_matching_processes(_STEAM_PROCESS_PATTERN, 15)
    if n:
        invalidate_ui_window_cache()
    return n


def stop_steam_client(
    runtime: WineRuntime | None = None,
    bottle: Path | None = None,
    *,
    callback: ProgressCallback = None,
) -> int:
    """
    Gracefully stop the Windows Steam client, then clean up webhelpers and wineserver.
    Returns number of processes signaled (TERM/KILL).
    """
    invalidate_ui_window_cache()
    signaled = 0

    if runtime and bottle and is_steam_running():
        request_steam_shutdown(runtime, bottle, callback=callback)
        if callback:
            callback("Waiting for Steam to exit…")
        if wait_steam_exit(timeout=18.0):
            if callback:
                callback("Steam exited cleanly.")
        else:
            if callback:
                callback("Steam did not exit in time — terminating processes…")
            signaled += terminate_steam_processes()
            time.sleep(2.0)

    remaining = kill_stale_steam_processes()
    signaled += remaining

    if runtime and bottle:
        if callback and not remaining and not is_steam_running():
            callback("Stopping Wine server…")
        kill_wineserver(runtime.wineserver_bin, bottle)
        locks = purge_chromium_locks(bottle)
        if locks and callback:
            callback(f"Removed {locks} stale Chromium lock file(s).")
        signaled += kill_orphan_wineservers(runtime)

    if not signaled and not is_steam_running() and callback:
        callback("Steam is not running.")
    return signaled
