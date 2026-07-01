"""Detect connected game controllers on macOS."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

ControllerKind = str  # xbox | dualsense | dualshock | generic
ConnectionKind = str  # bluetooth | usb | unknown


@dataclass(frozen=True)
class GameController:
    name: str
    kind: ControllerKind
    connection: ConnectionKind
    connected: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "connection": self.connection,
            "connected": self.connected,
        }


def format_controller(ctrl: GameController) -> str:
    """Single-controller line for CLI."""
    status = "connected" if ctrl.connected else "paired (not connected)"
    return f"{ctrl.name} [{ctrl.kind}, {ctrl.connection}, {status}]"


_XBOX_RE = re.compile(r"xbox\s*(wireless\s*)?controller|xbox\s*series", re.I)
_DUALSENSE_RE = re.compile(r"dualsense", re.I)
_DUALSHOCK_RE = re.compile(r"dualshock|wireless\s*controller", re.I)
_GAMEPAD_HINT_RE = re.compile(r"gamepad|controller|joystick|xinput|dualsense|dualshock|xbox", re.I)


def _classify_kind(name: str) -> ControllerKind:
    if _XBOX_RE.search(name):
        return "xbox"
    if _DUALSENSE_RE.search(name):
        return "dualsense"
    if _DUALSHOCK_RE.search(name):
        return "dualshock"
    return "generic"


def _is_controller_name(name: str) -> bool:
    return bool(_GAMEPAD_HINT_RE.search(name))


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _parse_bluetooth(text: str) -> list[GameController]:
    controllers: list[GameController] = []
    section_connected: bool | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "Connected:":
            section_connected = True
            continue
        if stripped == "Not Connected:":
            section_connected = False
            continue
        if section_connected is None:
            continue

        if line.startswith("          ") and not line.startswith("              ") and stripped.endswith(":"):
            name = stripped[:-1].strip()
            if not _is_controller_name(name):
                continue
            controllers.append(
                GameController(
                    name=name,
                    kind=_classify_kind(name),
                    connection="bluetooth",
                    connected=section_connected,
                )
            )

    return controllers


def _connection_from_transport(transport: str) -> ConnectionKind:
    lowered = transport.lower()
    if "usb" in lowered:
        return "usb"
    if "bluetooth" in lowered or lowered == "fifo":
        return "bluetooth"
    return "unknown"


def _parse_ioreg_hid(text: str) -> list[GameController]:
    controllers: list[GameController] = []
    product: str | None = None
    transport: str = ""

    for line in text.splitlines():
        match = re.search(r'"Product"\s*=\s*"([^"]+)"', line)
        if match:
            product = match.group(1)
            continue
        match = re.search(r'"Transport"\s*=\s*"([^"]+)"', line)
        if match:
            transport = match.group(1)
            continue
        if line.strip() == "}" and product:
            if _is_controller_name(product):
                controllers.append(
                    GameController(
                        name=product,
                        kind=_classify_kind(product),
                        connection=_connection_from_transport(transport),
                        connected=True,
                    )
                )
            product = None
            transport = ""

    return controllers


def _merge_controllers(found: list[GameController]) -> list[GameController]:
    merged: dict[str, GameController] = {}
    for ctrl in found:
        key = ctrl.name.lower()
        existing = merged.get(key)
        if existing is None:
            merged[key] = ctrl
            continue
        if ctrl.connected and not existing.connected:
            merged[key] = ctrl
        elif ctrl.connected == existing.connected:
            if existing.connection == "unknown" and ctrl.connection != "unknown":
                merged[key] = ctrl
    return sorted(merged.values(), key=lambda c: (not c.connected, c.name.lower()))


def list_controllers(*, include_bluetooth: bool = True) -> list[GameController]:
    """Return game controllers via IOHID/USB (fast) and optional Bluetooth scan."""
    found: list[GameController] = []

    # Fast paths first — USB / HID show up immediately when a pad is turned on.
    hid_text = _run(["ioreg", "-r", "-c", "IOHIDDevice", "-l"], timeout=4)
    if hid_text:
        found.extend(_parse_ioreg_hid(hid_text))

    usb_text = _run(["ioreg", "-p", "IOUSB", "-l"], timeout=4)
    if usb_text:
        for match in re.finditer(r'"USB Product Name"\s*=\s*"([^"]+)"', usb_text):
            name = match.group(1)
            if _is_controller_name(name):
                found.append(
                    GameController(
                        name=name,
                        kind=_classify_kind(name),
                        connection="usb",
                        connected=True,
                    )
                )

    if include_bluetooth:
        bt_text = _run(["system_profiler", "SPBluetoothDataType"], timeout=4)
        if bt_text:
            found.extend(_parse_bluetooth(bt_text))

    return _merge_controllers(found)


def format_controller_report() -> str:
    """Human-readable controller list for CLI."""
    controllers = list_controllers()
    lines = ["Game controllers", "=" * 40]
    if not controllers:
        lines.append("  (none detected)")
        return "\n".join(lines)

    for ctrl in controllers:
        status = "connected" if ctrl.connected else "paired (not connected)"
        lines.append(f"  • {ctrl.name}")
        lines.append(f"    kind={ctrl.kind}, connection={ctrl.connection}, {status}")
    return "\n".join(lines)
