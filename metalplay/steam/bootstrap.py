"""Steam client bootstrap — download full client after stub installer."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

from metalplay.runtime.wine import WineRuntime, wine_command

ProgressCallback = Callable[[str], None]

# Full client is hundreds of MB; stub-only install is ~10MB.
_MIN_COMPLETE_BYTES = 80 * 1024 * 1024
_BOOTSTRAP_MARKER = ".metalplay-bootstrap-ok"


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


def steam_root(bottle: Path) -> Path | None:
    for rel in (
        "drive_c/Program Files (x86)/Steam",
        "drive_c/Program Files/Steam",
    ):
        root = bottle / rel
        if root.is_dir() and (
            (root / "steam.exe").is_file()
            or (root / "Steam.exe").is_file()
        ):
            return root
    return None


def steam_exe_path(root: Path) -> Path | None:
    for name in ("steam.exe", "Steam.exe"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def is_bootstrap_complete(root: Path) -> bool:
    """True when the full Steam client (not just the NSIS stub) is present."""
    if not steam_exe_path(root):
        return False
    marker = root / _BOOTSTRAP_MARKER
    if marker.is_file():
        return (root / "bin" / "cef").is_dir() and (
            (root / "SteamUI.dll").is_file()
            or (root / "steamui.dll").is_file()
            or (root / "clientui").is_dir()
        )
    try:
        total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    except OSError:
        total = 0
    if total < _MIN_COMPLETE_BYTES:
        return False
    complete = (root / "bin" / "cef").is_dir() and (
        (root / "SteamUI.dll").is_file()
        or (root / "steamui.dll").is_file()
        or (root / "clientui").is_dir()
    )
    if complete:
        try:
            (root / _BOOTSTRAP_MARKER).write_text("ok\n")
        except OSError:
            pass
    return complete


def bootstrap_log_tail(root: Path, n: int = 8) -> str:
    log = root / "logs" / "bootstrap_log.txt"
    if not log.is_file():
        return ""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def run_bootstrap(
    runtime: WineRuntime,
    bottle: Path,
    callback: ProgressCallback | None = None,
    *,
    timeout: int = 900,
) -> None:
    """
    Run Steam.exe once so the bootstrapper downloads the full client (~300MB+).
    Required after SteamSetup.exe /S, which only installs a small stub.
    """
    root = steam_root(bottle)
    if not root:
        raise FileNotFoundError("Steam not found in bottle. Run: metalplay steam install")

    if is_bootstrap_complete(root):
        _log("Steam client already bootstrapped.", callback)
        return

    exe = steam_exe_path(root)
    assert exe is not None

    env = {
        **dict(__import__("os").environ),
        "WINEPREFIX": str(bottle),
        "WINEARCH": "win64",
        "WINEDEBUG": "-all",
        "PATH": f"{runtime.bin_dir}:{__import__('os').environ.get('PATH', '')}",
    }

    _log("Downloading full Steam client (first run, ~300MB — may take a few minutes)...", callback)
    proc = subprocess.Popen(
        wine_command(runtime.wine_bin, str(exe), "-no-cef-sandbox", "-noverifyfiles"),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + timeout
    last_msg = ""
    while time.time() < deadline:
        if proc.poll() is not None and is_bootstrap_complete(root):
            break
        if is_bootstrap_complete(root):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            break
        log = bootstrap_log_tail(root, 3)
        for line in log.splitlines():
            if "Downloading update" in line and line != last_msg:
                _log(line.strip(), callback)
                last_msg = line
            if "Update complete" in line:
                _log(line.strip(), callback)
        time.sleep(3)
    else:
        proc.terminate()
        raise TimeoutError(
            "Steam bootstrap timed out. Check network and retry: metalplay steam bootstrap"
        )

    if not is_bootstrap_complete(root):
        tail = bootstrap_log_tail(root, 15)
        raise RuntimeError(
            "Steam bootstrap did not finish. "
            f"Log tail:\n{tail or '(no bootstrap log)'}"
        )

    _log("Steam client download complete.", callback)
