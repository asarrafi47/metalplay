"""Restore Wine builtin graphics DLLs with correct PE architecture per directory."""

from __future__ import annotations

import shutil
import struct
import tarfile
from pathlib import Path

from metalplay import paths

# PE machine types
_PE_I386 = 0x014C
_PE_AMD64 = 0x8664

# Stock Gcenx wined3d DLLs (pre-DXMT overlay) — Rockstar Launcher CEF needs these.
_WINED3D_DLLS: tuple[str, ...] = ("d3d11.dll", "dxgi.dll", "d3d10core.dll")
_WINED3D_BACKUP_TAG = ".metalplay-dxmt.bak"
_WINEMETAL_DLL = "winemetal.dll"
_ROCKSTAR_WINEMETAL_DISABLED = "winemetal.dll.metalplay-rockstar-disabled"
_GCENX_ARCHIVE = paths.cache_dir() / "wine-devel-11.10-osx64.tar.xz"
_GCENX_TAR_PREFIX = "Wine Devel.app/Contents/Resources/wine/lib/wine"

# DLLs CEF/Steam UI needs as Wine builtins (not DXMT)
_STEAM_UI_DLLS = (
    "dxgi.dll",
    "d3d11.dll",
    "d3d10core.dll",
    "d3d12.dll",
    "vulkan-1.dll",
    "winemetal.dll",
)

# system32 = 64-bit, syswow64 = 32-bit (WoW64)
_ARCH_MAP = (
    ("x86_64-windows", "system32", _PE_AMD64),
    ("i386-windows", "syswow64", _PE_I386),
)


def pe_machine(path: Path) -> int | None:
    """Return PE Machine field or None if unreadable."""
    try:
        with path.open("rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3C)
            pe_off = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_off + 4)
            return struct.unpack("<H", f.read(2))[0]
    except OSError:
        return None


def restore_wine_graphics_dlls(wine_root: Path, bottle: Path) -> list[str]:
    """
    Copy Wine graphics DLLs into the bottle with correct arch per directory.
    Fixes DXGI load failures when DXMT or wrong-arch DLLs land in system32.
    """
    fixed: list[str] = []
    windows = bottle / "drive_c" / "windows"
    wine_lib = wine_root / "lib" / "wine"

    for wine_subdir, win_subdir, expected_machine in _ARCH_MAP:
        src_dir = wine_lib / wine_subdir
        dst_dir = windows / win_subdir
        if not src_dir.is_dir() or not dst_dir.is_dir():
            continue
        for dll in _STEAM_UI_DLLS:
            src, dst = src_dir / dll, dst_dir / dll
            if not src.is_file():
                continue
            machine = pe_machine(src)
            if machine != expected_machine:
                continue
            if dst.is_file():
                if pe_machine(dst) == expected_machine and dst.stat().st_size == src.stat().st_size:
                    continue
            shutil.copy2(src, dst)
            fixed.append(f"{win_subdir}/{dll}")
    return fixed


def restore_steam_client_graphics(wine_root: Path, bottle: Path) -> list[str]:
    """
    Undo Rockstar Launcher graphics prep before starting the Steam client.

    Rockstar disables winemetal and swaps in stock wined3d — Steam's CEF UI needs the
    normal Wine graphics stack (winemetal + builtins from the runtime).
    """
    fixed: list[str] = []
    windows = bottle / "drive_c/windows"
    for sub in ("system32", "syswow64"):
        wm = windows / sub / _WINEMETAL_DLL
        disabled = windows / sub / _ROCKSTAR_WINEMETAL_DISABLED
        if not wm.is_file() and disabled.is_file():
            shutil.copy2(disabled, wm)
            fixed.append(f"{sub}/{_WINEMETAL_DLL}")
    fixed.extend(restore_wine_graphics_dlls(wine_root, bottle))
    return fixed


def wined3d_cache_dir() -> Path:
    """Cached stock Gcenx wined3d PE DLLs (extracted once from the installer tarball)."""
    return paths.cache_dir() / "wined3d-gcenx-11.10"


def ensure_wined3d_graphics_cache() -> Path:
    """
    Extract stock wined3d d3d11/dxgi/d3d10core from the Gcenx Wine tarball.

    DXMT replaces these in the live Wine tree; Rockstar Launcher's CEF UI needs the
    original wined3d versions (~400 KB d3d11, not the 5 MB DXMT build).
    """
    cache = wined3d_cache_dir()
    ready = all(
        (cache / subdir / dll).is_file()
        for subdir, dll in (
            ("x86_64-windows", "d3d11.dll"),
            ("x86_64-windows", "dxgi.dll"),
            ("i386-windows", "d3d11.dll"),
        )
    )
    if ready:
        return cache

    if not _GCENX_ARCHIVE.is_file():
        raise FileNotFoundError(
            f"Gcenx Wine archive not found at {_GCENX_ARCHIVE}. "
            "Run: metalplay install wine"
        )

    cache.mkdir(parents=True, exist_ok=True)
    with tarfile.open(_GCENX_ARCHIVE, "r:xz") as tar:
        for wine_subdir in ("x86_64-windows", "i386-windows"):
            out_dir = cache / wine_subdir
            out_dir.mkdir(parents=True, exist_ok=True)
            for dll in _WINED3D_DLLS:
                member = f"{_GCENX_TAR_PREFIX}/{wine_subdir}/{dll}"
                try:
                    src = tar.extractfile(member)
                except KeyError:
                    continue
                if src is None:
                    continue
                dst = out_dir / dll
                with dst.open("wb") as f:
                    shutil.copyfileobj(src, f)
    return cache


def _is_dxmt_d3d11(path: Path) -> bool:
    """DXMT d3d11.dll is ~5 MB; stock wined3d d3d11 is ~400 KB."""
    return path.is_file() and path.stat().st_size > 1_000_000


def bottle_uses_dxmt_graphics(bottle: Path) -> bool:
    d3d11 = bottle / "drive_c/windows/system32/d3d11.dll"
    return _is_dxmt_d3d11(d3d11)


def _restore_hidden_winemetal(bottle: Path) -> None:
    """Undo an older swap that renamed winemetal.dll away."""
    windows = bottle / "drive_c/windows"
    for win_subdir in ("system32", "syswow64"):
        dst_dir = windows / win_subdir
        winemetal = dst_dir / _WINEMETAL_DLL
        backup = dst_dir / f"{_WINEMETAL_DLL}{_WINED3D_BACKUP_TAG}"
        if backup.is_file() and not winemetal.is_file():
            backup.rename(winemetal)


def swap_bottle_to_wined3d(bottle: Path) -> list[str]:
    """
    Put stock wined3d graphics DLLs in the bottle for CEF / Rockstar Launcher.

    Backs up the current DXMT system32 copies as *.metalplay-dxmt.bak.
    """
    cache = ensure_wined3d_graphics_cache()
    d3d11 = bottle / "drive_c/windows/system32/d3d11.dll"
    expected = cache / "x86_64-windows" / "d3d11.dll"
    needs_swap = bottle_uses_dxmt_graphics(bottle)
    if not needs_swap and d3d11.is_file() and expected.is_file():
        needs_swap = d3d11.stat().st_size != expected.stat().st_size
    if not needs_swap:
        return []
    windows = bottle / "drive_c/windows"
    changed: list[str] = []

    for wine_subdir, win_subdir in (("x86_64-windows", "system32"), ("i386-windows", "syswow64")):
        src_dir = cache / wine_subdir
        dst_dir = windows / win_subdir
        if not src_dir.is_dir() or not dst_dir.is_dir():
            continue
        for dll in _WINED3D_DLLS:
            src = src_dir / dll
            dst = dst_dir / dll
            if not src.is_file():
                continue
            backup = dst_dir / f"{dll}{_WINED3D_BACKUP_TAG}"
            if dst.is_file() and not backup.is_file():
                dst.rename(backup)
            shutil.copy2(src, dst)
            changed.append(f"{win_subdir}/{dll}")

    return changed


def swap_bottle_to_dxmt(bottle: Path) -> list[str]:
    """Restore DXMT graphics DLLs in the bottle after a wined3d swap."""
    windows = bottle / "drive_c/windows"
    restored: list[str] = []

    for win_subdir in ("system32", "syswow64"):
        dst_dir = windows / win_subdir
        if not dst_dir.is_dir():
            continue
        for dll in _WINED3D_DLLS:
            dst = dst_dir / dll
            backup = dst_dir / f"{dll}{_WINED3D_BACKUP_TAG}"
            if not backup.is_file():
                continue
            if dst.is_file():
                dst.unlink()
            backup.rename(dst)
            restored.append(f"{win_subdir}/{dll}")

    if not restored and bottle_uses_dxmt_graphics(bottle):
        from metalplay.runtime.dxmt import install_into_bottle

        install_into_bottle(bottle)
        restored.append("system32+syswow64 (from DXMT cache)")

    return restored
