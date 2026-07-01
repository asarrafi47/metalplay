"""Windows Steam integration for MetalPlay."""

from metalplay.steam.client import (
    STEAM_BOTTLE_NAME,
    STEAM_RESTART_EXIT_CODE,
    install_client,
    is_installed,
    launch_client,
    launch_game,
    launch_game_direct,
    setup,
    steam_exe,
)
from metalplay.steam.library import list_games, status

__all__ = [
    "STEAM_BOTTLE_NAME",
    "STEAM_RESTART_EXIT_CODE",
    "install_client",
    "is_installed",
    "launch_client",
    "launch_game",
    "launch_game_direct",
    "list_games",
    "setup",
    "status",
    "steam_exe",
]
