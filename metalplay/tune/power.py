"""macOS power and thermal settings for sustained gaming loads."""

from __future__ import annotations

import subprocess
from typing import Callable

ProgressCallback = Callable[[str], None]


def _log(msg: str, callback: ProgressCallback | None = None) -> None:
    if callback:
        callback(msg)
    else:
        print(msg)


def _run_pmset(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["pmset", *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode == 0, out.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def read_power_state() -> dict[str, str]:
    """Read current pmset values relevant to gaming."""
    state: dict[str, str] = {}
    ok, out = _run_pmset(["-g", "custom"])
    if ok:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in ("powermode", "lowpowermode"):
                state[parts[0]] = parts[1]
    return state


def enable_high_performance(callback: ProgressCallback | None = None) -> list[str]:
    """
    Enable macOS High Power Mode and disable Low Power Mode where supported.

    Fan RPM is controlled by macOS thermals — this lets the system use more
    power (and spin fans harder when needed) during sustained loads.
    """
    actions: list[str] = []

    for args, label in (
        (["-a", "lowpowermode", "0"], "Low Power Mode off (AC + battery)"),
        (["-a", "powermode", "1"], "High Power Mode on (AC + battery)"),
        (["-c", "displaysleep", "0"], "Display sleep disabled on AC while gaming"),
        (["-b", "displaysleep", "30"], "Display sleep 30 min on battery"),
    ):
        ok, detail = _run_pmset(args)
        if ok:
            actions.append(label)
            _log(f"Power: {label}", callback)
        else:
            _log(f"Power: skipped {label} ({detail or 'not supported'})", callback)

    return actions


def restore_balanced_power(callback: ProgressCallback | None = None) -> list[str]:
    """Restore default power settings after gaming."""
    actions: list[str] = []
    for args, label in (
        (["-a", "powermode", "0"], "Balanced power mode"),
        (["-c", "displaysleep", "10"], "Display sleep 10 min on AC"),
    ):
        ok, _ = _run_pmset(args)
        if ok:
            actions.append(label)
            _log(f"Power: restored {label}", callback)
    return actions


def wrap_caffeinate(cmd: list[str]) -> list[str]:
    """Prevent idle sleep and display dim while a game runs."""
    return ["caffeinate", "-dims", "--", *cmd]


def cooling_notes() -> str:
    return (
        "Cooling: macOS controls fan speed automatically from temperature sensors. "
        "High Power Mode allows higher sustained power draw so fans can ramp up under load. "
        "MetalPlay cannot set fan RPM directly — keep vents clear and use a hard surface."
    )
