"""Install macOS system fonts into Wine prefix for CEF/Steam UI glyphs."""

from __future__ import annotations

import shutil
from pathlib import Path

_FONT_SOURCES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)


def install_ui_fonts(bottle: Path) -> int:
    """Copy readable host fonts into C:\\windows\\Fonts. Returns count copied."""
    fonts_dir = bottle / "drive_c" / "windows" / "Fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src_path in _FONT_SOURCES:
        src = Path(src_path)
        if not src.is_file():
            continue
        dst = fonts_dir / src.name
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            continue
        try:
            shutil.copyfile(src, dst)
            copied += 1
        except OSError:
            continue
    return copied
