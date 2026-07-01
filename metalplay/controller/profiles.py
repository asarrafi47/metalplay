"""Per-game controller settings (Phase 1 storage, Phase 4 expansion)."""

from __future__ import annotations

from metalplay.config import Config

GTA_V_APP_ID = "271590"

_DEFAULT_CONTROLLER_PROFILE: dict = {
    "steam_input": False,
    "prefer": "xinput",
}


def controller_profile_for(app_id: str | None, config: Config | None = None) -> dict:
    """Return merged controller profile for an app ID or defaults."""
    config = config or Config.load()
    profile = dict(_DEFAULT_CONTROLLER_PROFILE)
    if app_id:
        game = config.get_game_profile(app_id)
        profile.update(game.get("controller", {}))
    return profile


def _steam_input_enabled(profile: dict) -> bool:
    value = profile.get("steam_input", False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")
    return bool(value)


def set_controller_profile(
    app_id: str,
    *,
    steam_input: bool | None = None,
    prefer: str | None = None,
    name: str = "",
) -> dict:
    """Persist controller settings on a game profile. Returns updated game profile."""
    if steam_input is None and prefer is None:
        raise ValueError("Pass --steam-input and/or --prefer")

    config = Config.load()
    app_id = str(app_id)
    game = dict(config.game_profiles.get(app_id, {}))
    ctrl = dict(game.get("controller", {}))
    if steam_input is not None:
        ctrl["steam_input"] = steam_input
    if prefer is not None:
        ctrl["prefer"] = prefer
    game["controller"] = ctrl
    if name:
        game["name"] = name
    config.game_profiles[app_id] = game
    config.save()
    return game


def steam_launch_args_for_controller(profile: dict) -> list[str]:
    """
    Steam launch arguments for controller preferences.

    Steam Input is configured per-game in the Steam client UI; there is no
    reliable global launch flag. When steam_input is false, MetalPlay sets
    SteamGamepad_EnableConfigSupport=0 via controller_env instead.
    """
    if _steam_input_enabled(profile):
        return []
    return []
