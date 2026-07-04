"""Download and cache DXVK-macOS (D3D11 → Vulkan → MoltenVK → Metal).

Used for the Rockstar Games Launcher UI on free (non-CrossOver) Wine: its engine
needs a D3D11 feature level that wined3d-on-macGL (GL 4.1 → FL 10_1) cannot offer.
The Gcenx repack ships d3d11.dll / d3d10core.dll only — it pairs with Wine's
builtin dxgi, and reaches Metal through winevulkan + libMoltenVK (both present in
the Gcenx Wine runtime).
"""

from __future__ import annotations

import shutil
import tarfile
import urllib.request
from pathlib import Path
from typing import Callable

from metalplay import paths

ProgressCallback = Callable[[str], None]

DXVK_VERSION = "v1.10.3-20230507-repack"
DXVK_URL = (
    "https://github.com/Gcenx/DXVK-macOS/releases/download/"
    f"{DXVK_VERSION}/dxvk-macOS-async-{DXVK_VERSION}.tar.gz"
)
_DXVK_TAR_ROOT = f"dxvk-macOS-async-{DXVK_VERSION}"
_DXVK_DLLS = ("d3d11.dll", "d3d10core.dll")
_DXVK_ARCHES = ("x64", "x32")


def _archive_path() -> Path:
    return paths.cache_dir() / f"dxvk-macOS-{DXVK_VERSION}.tar.gz"


def dxvk_cache_dir() -> Path:
    return paths.cache_dir() / f"dxvk-{DXVK_VERSION}"


def dxvk_cache_ready() -> bool:
    cache = dxvk_cache_dir()
    return all(
        (cache / arch / dll).is_file() for arch in _DXVK_ARCHES for dll in _DXVK_DLLS
    )


def ensure_dxvk_cache(callback: ProgressCallback | None = None) -> Path:
    """Download and extract DXVK DLLs into the cache; idempotent."""
    cache = dxvk_cache_dir()
    if dxvk_cache_ready():
        return cache

    archive = _archive_path()
    if not archive.is_file():
        if callback:
            callback(f"Downloading DXVK-macOS {DXVK_VERSION} (~3 MB)...")
        archive.parent.mkdir(parents=True, exist_ok=True)
        tmp = archive.with_suffix(".part")
        urllib.request.urlretrieve(DXVK_URL, tmp)
        tmp.rename(archive)

    with tarfile.open(archive, "r:gz") as tar:
        for arch in _DXVK_ARCHES:
            out_dir = cache / arch
            out_dir.mkdir(parents=True, exist_ok=True)
            for dll in _DXVK_DLLS:
                member = f"{_DXVK_TAR_ROOT}/{arch}/{dll}"
                try:
                    src = tar.extractfile(member)
                except KeyError:
                    continue
                if src is None:
                    continue
                with (out_dir / dll).open("wb") as f:
                    shutil.copyfileobj(src, f)

    if not dxvk_cache_ready():
        raise RuntimeError(f"DXVK archive {archive} did not contain expected DLLs")
    if callback:
        callback(f"DXVK {DXVK_VERSION} cached at {cache}")
    return cache
