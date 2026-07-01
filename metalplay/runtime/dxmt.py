"""DXMT download and installation (Direct3D 10/11 → Apple Metal)."""

from __future__ import annotations

import shutil
import tarfile
import urllib.request
from pathlib import Path

from metalplay import paths
from metalplay.runtime.wine import WineRuntime


class DxmtError(RuntimeError):
    pass


def is_installed() -> bool:
    dxmt = paths.dxmt_dir()
    return (dxmt / "x86_64-unix" / "winemetal.so").is_file()


def download(force: bool = False) -> Path:
    """Download DXMT builtin release to cache."""
    paths.ensure_dirs()
    archive = paths.cache_dir() / f"dxmt-{paths.DXMT_VERSION}-builtin.tar.gz"
    if archive.exists() and not force:
        return archive

    print(f"Downloading DXMT {paths.DXMT_VERSION}...")
    urllib.request.urlretrieve(paths.DXMT_RELEASE_URL, archive)
    return archive


def extract(archive: Path | None = None) -> Path:
    """Extract DXMT files to ~/.metalplay/runtimes/dxmt/v0.80/."""
    if archive is None:
        archive = download()
    dest = paths.dxmt_dir()
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(path=dest.parent, filter="data")

    # Tarball root is named v0.80/
    versioned = dest.parent / paths.DXMT_VERSION
    if versioned.is_dir() and versioned != dest:
        versioned.rename(dest)

    if not is_installed():
        raise DxmtError("DXMT extraction failed — winemetal.so not found")
    return dest


def install_into_wine(runtime: WineRuntime, dxmt_root: Path | None = None) -> None:
    """
    Overlay DXMT onto a Metal-capable Wine installation.

    Follows: https://github.com/3Shain/dxmt/wiki/DXMT-Installation-Guide-for-Geeks
    """
    dxmt_root = dxmt_root or paths.dxmt_dir()
    if not (dxmt_root / "x86_64-unix" / "winemetal.so").is_file():
        raise DxmtError(f"DXMT not found at {dxmt_root}. Run: metalplay install dxmt")

    wine_lib = runtime.wine_lib
    backup_root = wine_lib / ".metalplay-wined3d-backup"
    for wine_subdir in ("x86_64-windows", "i386-windows"):
        src_dir = wine_lib / wine_subdir
        dst_dir = backup_root / wine_subdir
        if not src_dir.is_dir():
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        for dll in ("d3d11.dll", "dxgi.dll", "d3d10core.dll"):
            src, dst = src_dir / dll, dst_dir / dll
            if src.is_file() and not dst.is_file() and src.stat().st_size < 1_000_000:
                shutil.copy2(src, dst)

    mappings = [
        (dxmt_root / "x86_64-unix" / "winemetal.so", wine_lib / "x86_64-unix" / "winemetal.so"),
        (dxmt_root / "x86_64-windows" / "winemetal.dll", wine_lib / "x86_64-windows" / "winemetal.dll"),
        (dxmt_root / "x86_64-windows" / "d3d11.dll", wine_lib / "x86_64-windows" / "d3d11.dll"),
        (dxmt_root / "x86_64-windows" / "dxgi.dll", wine_lib / "x86_64-windows" / "dxgi.dll"),
        (dxmt_root / "x86_64-windows" / "d3d10core.dll", wine_lib / "x86_64-windows" / "d3d10core.dll"),
    ]

    for src, dst in mappings:
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # 32-bit Windows DLLs
    i386_src = dxmt_root / "i386-windows"
    if i386_src.is_dir():
        i386_dst = wine_lib / "i386-windows"
        i386_dst.mkdir(parents=True, exist_ok=True)
        for dll in i386_src.glob("*.dll"):
            shutil.copy2(dll, i386_dst / dll.name)


def install_into_bottle(bottle_path: Path, dxmt_root: Path | None = None) -> None:
    """Copy DXMT DLLs into system32 and syswow64 (32-bit games need syswow64)."""
    dxmt_root = dxmt_root or paths.dxmt_dir()
    system32 = bottle_path / "drive_c" / "windows" / "system32"
    syswow64 = bottle_path / "drive_c" / "windows" / "syswow64"
    system32.mkdir(parents=True, exist_ok=True)
    syswow64.mkdir(parents=True, exist_ok=True)

    # 64-bit
    for dll in (dxmt_root / "x86_64-windows").glob("*.dll"):
        shutil.copy2(dll, system32 / dll.name)

    # 32-bit — required for WoW64 / 32-bit Steam games
    i386 = dxmt_root / "i386-windows"
    if i386.is_dir():
        for dll in i386.glob("*.dll"):
            shutil.copy2(dll, syswow64 / dll.name)
            # winemetal also goes to system32 for the host side
            if dll.name == "winemetal.dll":
                shutil.copy2(dll, system32 / dll.name)


def setup(force: bool = False) -> Path:
    """Download, extract, and prepare DXMT."""
    archive = download(force=force)
    return extract(archive)
