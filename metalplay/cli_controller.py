"""CLI handlers for metalplay controller subcommands."""

from __future__ import annotations

import json

from metalplay.bottle import manager as bottles
from metalplay.config import Config
from metalplay.controller.compat import doctor, test_wine_joystick
from metalplay.controller.detect import format_controller, list_controllers
from metalplay.controller.profiles import set_controller_profile
from metalplay.runtime.wine import get_runtime
from metalplay.steam import STEAM_BOTTLE_NAME


def cmd_controller(args) -> int:
    action = args.controller_action

    if action == "list":
        controllers = list_controllers()
        if getattr(args, "json", False):
            print(json.dumps([c.to_dict() for c in controllers], indent=2))
            return 0
        if not controllers:
            print("No game controllers detected.")
            print("Connect a controller via USB or Bluetooth and try again.")
            return 1
        for c in controllers:
            print(format_controller(c))
        return 0

    config = Config.load()
    runtime = get_runtime(config.wine_runtime)
    bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)

    if action == "doctor":
        if not runtime:
            print("No Wine runtime found. Run: metalplay doctor")
            return 1
        if not bottle.is_dir():
            print(f"Steam bottle '{STEAM_BOTTLE_NAME}' not found. Run: metalplay steam setup")
            return 1
        report = doctor(bottle, runtime)
        for line in report.get("checks", []):
            print(line)
        for line in report.get("recommendations", []):
            print(f"  → {line}")
        if report.get("ok"):
            print("\nController compatibility: OK")
            return 0
        print("\nController compatibility: review recommendations above")
        return 1

    if action == "test":
        if not runtime:
            print("No Wine runtime found. Run: metalplay doctor")
            return 1
        if not bottle.is_dir():
            print(f"Steam bottle '{STEAM_BOTTLE_NAME}' not found. Run: metalplay steam setup")
            return 1
        ok, msg = test_wine_joystick(runtime, bottle)
        if ok:
            print(f"Wine joystick check: OK — {msg}")
            return 0
        print(f"Wine joystick check: FAILED — {msg}")
        return 1

    if action == "set-profile":
        steam_arg = getattr(args, "steam_input", None)
        prefer = getattr(args, "prefer", None)
        steam_input = None
        if steam_arg == "on":
            steam_input = True
        elif steam_arg == "off":
            steam_input = False
        try:
            profile = set_controller_profile(
                args.app_id,
                steam_input=steam_input,
                prefer=prefer,
                name=getattr(args, "name", "") or "",
            )
        except ValueError as exc:
            print(exc)
            return 1
        ctrl = profile.get("controller", {})
        print(f"App {args.app_id} controller profile:")
        print(json.dumps(ctrl, indent=2))
        return 0

    return 1
