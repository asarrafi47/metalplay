"""Persistent configuration."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from metalplay import paths


@dataclass
class Config:
    """User configuration stored in ~/.metalplay/config.json."""

    wine_runtime: str | None = None
    default_graphics: str = "dxmt"
    default_bottle: str | None = None
    dxmt_log_level: str = "warn"
    use_rosetta: bool = True
    extra_env: dict[str, str] = field(default_factory=dict)
    game_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> Config:
        config_path = paths.config_file()
        if not config_path.exists():
            return cls()
        try:
            data = json.loads(config_path.read_text())
            return cls(
                wine_runtime=data.get("wine_runtime"),
                default_graphics=data.get("default_graphics", "dxmt"),
                default_bottle=data.get("default_bottle"),
                dxmt_log_level=data.get("dxmt_log_level", "warn"),
                use_rosetta=data.get("use_rosetta", True),
                extra_env=data.get("extra_env", {}),
                game_profiles=data.get("game_profiles", {}),
            )
        except (json.JSONDecodeError, TypeError) as exc:
            backup = config_path.with_suffix(".json.bak")
            try:
                backup.write_text(config_path.read_text())
            except OSError:
                pass
            import sys

            print(
                f"Warning: corrupt config at {config_path} ({exc}); "
                f"using defaults (backup: {backup})",
                file=sys.stderr,
            )
            return cls()

    def save(self) -> None:
        paths.ensure_dirs()
        paths.config_file().write_text(json.dumps(asdict(self), indent=2) + "\n")

    def get_game_profile(self, name: str) -> dict[str, Any]:
        return self.game_profiles.get(name, {})
