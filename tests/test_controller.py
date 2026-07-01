"""Unit tests for MetalPlay controller support (no Wine required)."""

from __future__ import annotations

from unittest.mock import patch

from metalplay.config import Config
from metalplay.controller.compat import CONTROLLER_DLL_OVERRIDES, controller_env, merge_dll_overrides
from metalplay.controller.detect import (
    GameController,
    _classify_kind,
    _parse_bluetooth,
    list_controllers,
)
from metalplay.controller.profiles import controller_profile_for


XBOX_BLUETOOTH_SAMPLE = """
Bluetooth:

      Connected:
          Xbox Wireless Controller:
              Address: AA:BB:CC:DD:EE:FF
              Minor Type: Gamepad
      Not Connected:
          Arman's AirPods Pro:
              Minor Type: Headphones
"""

DUALSENSE_BLUETOOTH_SAMPLE = """
Bluetooth:

      Not Connected:
          DualSense Wireless Controller:
              Address: D0:BC:C1:29:B5:E1
              Minor Type: Gamepad
"""

IOREG_HID_SAMPLE = """
  +-o Xbox Wireless Controller@123
  | {
  |   "Product" = "Xbox Wireless Controller"
  |   "Transport" = "Bluetooth"
  | }
"""


def test_merge_dll_overrides_doesnt_duplicate():
    existing = "dinput8=n,b;dxgi,d3d11,d3d10core=n,b"
    merged = merge_dll_overrides(existing, CONTROLLER_DLL_OVERRIDES)
    assert merged.count("dinput8") == 1
    assert "xinput9_1_0" in merged
    assert "d3d11" in merged

    remerged = merge_dll_overrides(merged, CONTROLLER_DLL_OVERRIDES)
    assert remerged.count("dinput8") == 1
    assert remerged.count("xinput9_1_0") == 1


def test_controller_env_merges_with_existing_winedlloverrides():
    profile = {"prefer": "xinput"}
    existing = "dxgi,d3d11,d3d10core=n,b;winemenubuilder.exe=d"
    env = controller_env(profile, existing_overrides=existing)
    overrides = env["WINEDLLOVERRIDES"]

    assert "d3d11" in overrides
    assert "xinput9_1_0" in overrides
    assert "dinput8" in overrides
    assert overrides.count("dinput8") == 1
    assert env["SteamGamepad_EnableConfigSupport"] == "0"


def test_controller_profile_for_defaults():
    config = Config()
    profile = controller_profile_for("999999", config)
    assert profile["steam_input"] is False
    assert profile["prefer"] == "xinput"


def test_controller_profile_for_merges_stored_settings():
    config = Config()
    config.game_profiles["271590"] = {
        "name": "Grand Theft Auto V",
        "controller": {"steam_input": True, "prefer": "dinput"},
    }
    profile = controller_profile_for("271590", config)
    assert profile["steam_input"] is True
    assert profile["prefer"] == "dinput"


@patch("metalplay.controller.detect._run")
def test_list_controllers_returns_list(mock_run):
    mock_run.side_effect = lambda cmd, timeout=15: {
        ("system_profiler", "SPBluetoothDataType"): XBOX_BLUETOOTH_SAMPLE,
        ("ioreg", "-r", "-c", "IOHIDDevice", "-l"): IOREG_HID_SAMPLE,
        ("ioreg", "-p", "IOUSB", "-l"): "",
    }.get(tuple(cmd), "")

    controllers = list_controllers()

    assert isinstance(controllers, list)
    assert all(isinstance(c, GameController) for c in controllers)
    kinds = {c.kind for c in controllers}
    assert "xbox" in kinds


def test_classify_kind_xbox_and_dualsense():
    assert _classify_kind("Xbox Wireless Controller") == "xbox"
    assert _classify_kind("Xbox Series X Controller") == "xbox"
    assert _classify_kind("DualSense Wireless Controller") == "dualsense"
    assert _classify_kind("DualShock 4 Wireless Controller") == "dualshock"


def test_parse_bluetooth_classifies_xbox_and_dualsense():
    xbox = _parse_bluetooth(XBOX_BLUETOOTH_SAMPLE)
    assert len(xbox) == 1
    assert xbox[0].kind == "xbox"
    assert xbox[0].connection == "bluetooth"
    assert xbox[0].connected is True

    dualsense = _parse_bluetooth(DUALSENSE_BLUETOOTH_SAMPLE)
    assert len(dualsense) == 1
    assert dualsense[0].kind == "dualsense"
    assert dualsense[0].connected is False
