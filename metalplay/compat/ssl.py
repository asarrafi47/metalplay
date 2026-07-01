"""TLS certificate bundle for Wine/Chromium under MetalPlay."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _system_ca_bundle() -> Path | None:
    for candidate in (
        Path("/etc/ssl/cert.pem"),
        Path("/etc/ssl/certs/ca-certificates.crt"),
    ):
        if candidate.is_file():
            return candidate
    try:
        import certifi

        path = Path(certifi.where())
        if path.is_file():
            return path
    except ImportError:
        pass
    brew = shutil.which("brew")
    if brew:
        for pkg in ("openssl@3", "openssl@1.1", "ca-certificates"):
            result = subprocess.run(
                [brew, "--prefix", pkg],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                continue
            prefix = Path(result.stdout.strip())
            for rel in ("etc/openssl@3/cert.pem", "etc/openssl/cert.pem", "share/ca-certificates/cacert.pem"):
                path = prefix / rel
                if path.is_file():
                    return path
    return None


def ensure_ca_bundle(bottle: Path) -> bool:
    """
    Copy a macOS CA bundle into the bottle as drive_c/windows/cacert.pem.

    Chromium/Steam CEF needs this for TLS; without it the login UI often stays blank.
    """
    dest = bottle / "drive_c" / "windows" / "cacert.pem"
    source = _system_ca_bundle()
    if source is None:
        return dest.is_file()
    if dest.is_file() and dest.stat().st_size == source.stat().st_size:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return True
