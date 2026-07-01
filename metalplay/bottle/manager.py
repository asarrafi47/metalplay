"""Wine bottle (prefix) management."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from metalplay import paths
from metalplay.runtime.dxmt import install_into_bottle
from metalplay.runtime.wine import WineRuntime, wine_command

_BOTTLE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


@dataclass
class BottleMeta:
    name: str
    created: str
    windows_version: str = "win10"
    graphics: str = "dxmt"
    notes: str = ""
    programs: list[str] = field(default_factory=list)


class BottleError(RuntimeError):
    pass


def validate_bottle_name(name: str) -> str:
    """Reject empty, traversal, or weird names before any filesystem ops."""
    name = name.strip()
    if not name:
        raise BottleError("Bottle name cannot be empty")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise BottleError(f"Invalid bottle name: {name!r}")
    if not _BOTTLE_NAME_RE.match(name):
        raise BottleError(
            "Invalid bottle name — use 1–64 chars: letters, digits, . _ -",
        )
    root = paths.bottles_dir().resolve()
    resolved = (root / name).resolve()
    if resolved == root or root not in resolved.parents:
        raise BottleError(f"Invalid bottle name: {name!r}")
    return name


def _meta_path(bottle_path: Path) -> Path:
    return bottle_path / ".metalplay" / "bottle.json"


def list_bottles() -> list[tuple[str, Path, BottleMeta | None]]:
    root = paths.bottles_dir()
    if not root.is_dir():
        return []
    results: list[tuple[str, Path, BottleMeta | None]] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (
            (child / "system.reg").exists() or _meta_path(child).exists()
        ):
            meta = load_meta(child)
            results.append((child.name, child, meta))
    return results


def bottle_path(name: str) -> Path:
    return paths.bottles_dir() / validate_bottle_name(name)


def load_meta(bottle: Path) -> BottleMeta | None:
    meta_file = _meta_path(bottle)
    if not meta_file.is_file():
        return None
    try:
        data = json.loads(meta_file.read_text())
        return BottleMeta(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def save_meta(bottle: Path, meta: BottleMeta) -> None:
    meta_dir = _meta_path(bottle).parent
    meta_dir.mkdir(parents=True, exist_ok=True)
    _meta_path(bottle).write_text(json.dumps(asdict(meta), indent=2) + "\n")


def create(
    name: str,
    runtime: WineRuntime,
    windows: str = "win10",
    graphics: str = "dxmt",
    *,
    install_dxmt: bool | None = None,
) -> Path:
    """Create a new Wine bottle configured for Metal gaming."""
    validate_bottle_name(name)
    dest = bottle_path(name)
    if dest.exists():
        raise BottleError(f"Bottle '{name}' already exists at {dest}")

    paths.ensure_dirs()
    dest.mkdir(parents=True)

    env = {"WINEPREFIX": str(dest), "WINEARCH": "win64", "PATH": f"{runtime.bin_dir}:{os.environ.get('PATH', '')}"}
    subprocess.run(
        wine_command(runtime.wine_bin, "wineboot", "--init"),
        env={**os.environ, **env},
        check=True,
        timeout=120,
    )

    should_install_dxmt = install_dxmt if install_dxmt is not None else (graphics == "dxmt")
    if should_install_dxmt:
        install_into_bottle(dest)

    meta = BottleMeta(
        name=name,
        created=datetime.now(timezone.utc).isoformat(),
        windows_version=windows,
        graphics=graphics,
    )
    save_meta(dest, meta)
    return dest


def remove(name: str) -> None:
    import shutil

    dest = bottle_path(name)
    if not dest.is_dir():
        raise BottleError(f"Bottle '{name}' not found")
    shutil.rmtree(dest)


def run_wine(
    runtime: WineRuntime,
    bottle: Path,
    args: list[str],
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["WINEPREFIX"] = str(bottle)
    env["PATH"] = f"{runtime.bin_dir}:{env.get('PATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(wine_command(runtime.wine_bin, *args), env=env)
