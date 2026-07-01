"""Build and install MetalPlay.app for macOS."""

from __future__ import annotations

import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

APP_NAME = "MetalPlay"
BUNDLE_ID = "dev.metalplay.app"
DEFAULT_PORT = 8765


def project_root() -> Path:
    """MetalPlay source tree (parent of metalplay package)."""
    return Path(__file__).resolve().parents[2]


def app_bundle_path(install_dir: Path | None = None) -> Path:
    return (install_dir or Path("/Applications")) / f"{APP_NAME}.app"


def _launcher_script(root: Path, port: int) -> str:
    from metalplay.gui.revision import GUI_API_REVISION

    return f"""#!/bin/bash
# MetalPlay macOS app launcher — opens the game library UI (no Terminal needed).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT_FILE="$APP_DIR/Resources/metalplay-root"
PORT_FILE="$APP_DIR/Resources/metalplay-port"
EXPECTED_REV={GUI_API_REVISION}

if [[ -f "$ROOT_FILE" ]]; then
  METALPLAY_ROOT="$(cat "$ROOT_FILE")"
else
  METALPLAY_ROOT="{root}"
fi

if [[ -f "$PORT_FILE" ]]; then
  PORT="$(cat "$PORT_FILE")"
else
  PORT="{port}"
fi

VENV="$METALPLAY_ROOT/.venv/bin/activate"
if [[ -f "$VENV" ]]; then
  # shellcheck disable=SC1090
  source "$VENV"
elif command -v metalplay-gui >/dev/null 2>&1; then
  :
else
  osascript -e 'display alert "MetalPlay not found" message "Reinstall the app from your MetalPlay project: metalplay app install" as critical'
  exit 1
fi

export PATH="$METALPLAY_ROOT/.venv/bin:${{PATH:-}}"
cd "$METALPLAY_ROOT"

mkdir -p "$HOME/.metalplay/logs"

# Attach to a current server, or restart if an old GUI process is still running.
if curl -sf "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
  REV="$(curl -sf "http://127.0.0.1:$PORT/api/status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_revision',0))" 2>/dev/null || echo 0)"
  if [[ "$REV" == "$EXPECTED_REV" ]]; then
    exec metalplay gui --native --port "$PORT"
  fi
  pkill -f 'metalplay gui' 2>/dev/null || true
  sleep 1.5
fi

# Start GUI server in background; native window opens inside that process.
METALPLAY_KEEP_SERVER=1 nohup metalplay gui --native --port "$PORT" >>"$HOME/.metalplay/logs/gui.log" 2>&1 &
disown

# Wait for server (window opens inside gui process).
for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:$PORT/api/status" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 0.25
done

osascript -e 'display alert "MetalPlay failed to start" message "Check ~/.metalplay/logs/gui.log" as warning'
exit 1
"""


def _info_plist(port: int) -> dict:
    return {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "13.0",
        "CFBundleIconFile": "AppIcon",
        "CFBundleIconName": "AppIcon",
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": BUNDLE_ID,
                "CFBundleURLSchemes": ["metalplay"],
            }
        ],
        "MetalPlayDefaultPort": port,
    }


def build_app(
    *,
    root: Path | None = None,
    dest: Path | None = None,
    port: int = DEFAULT_PORT,
) -> Path:
    """Create MetalPlay.app bundle at dest (default: dist/MetalPlay.app in project)."""
    from metalplay import paths

    root = (root or project_root()).resolve()
    app_path = dest or (root / "dist" / f"{APP_NAME}.app")
    contents = app_path / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"

    if app_path.exists():
        shutil.rmtree(app_path)

    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    paths.ensure_dirs()

    launcher = macos / APP_NAME
    launcher.write_text(_launcher_script(root, port), encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (resources / "metalplay-root").write_text(str(root) + "\n", encoding="utf-8")
    (resources / "metalplay-port").write_text(str(port) + "\n", encoding="utf-8")

    icns = Path(__file__).resolve().parent / "AppIcon.icns"
    if icns.is_file():
        shutil.copy2(icns, resources / "AppIcon.icns")

    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(_info_plist(port), fh)

    return app_path


def install_app(
    *,
    root: Path | None = None,
    install_dir: Path | None = None,
    port: int = DEFAULT_PORT,
    callback=None,
) -> Path:
    """Build MetalPlay.app and install to /Applications."""
    root = (root or project_root()).resolve()
    target = app_bundle_path(install_dir)

    build_app(root=root, dest=target, port=port)

    venv_pip = root / ".venv" / "bin" / "pip"
    if venv_pip.is_file():
        subprocess.run(
            [str(venv_pip), "install", "-q", "pywebview>=5.0"],
            check=False,
            capture_output=True,
        )

    _msg = f"Installed {target}\nOpen from Applications or: open -a MetalPlay"
    if callback:
        callback(_msg)
    else:
        print(_msg)
    return target


def uninstall_app(install_dir: Path | None = None) -> bool:
    app = app_bundle_path(install_dir)
    if not app.is_dir():
        return False
    shutil.rmtree(app)
    return True


def open_app(install_dir: Path | None = None) -> None:
    app = app_bundle_path(install_dir)
    subprocess.run(["open", str(app)], check=False)
