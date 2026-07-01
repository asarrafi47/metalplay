"""Game controller detection and Wine compatibility for MetalPlay."""

from metalplay.controller.compat import (
    CONTROLLER_DLL_OVERRIDES,
    apply_controller_registry,
    controller_env,
    doctor,
    merge_dll_overrides,
    test_wine_joystick,
)
from metalplay.controller.detect import GameController, format_controller, format_controller_report, list_controllers
from metalplay.controller.profiles import (
    GTA_V_APP_ID,
    controller_profile_for,
    set_controller_profile,
    steam_launch_args_for_controller,
)

__all__ = [
    "CONTROLLER_DLL_OVERRIDES",
    "GTA_V_APP_ID",
    "GameController",
    "apply_controller_registry",
    "controller_env",
    "controller_profile_for",
    "doctor",
    "format_controller",
    "format_controller_report",
    "list_controllers",
    "merge_dll_overrides",
    "set_controller_profile",
    "steam_launch_args_for_controller",
    "test_wine_joystick",
]
