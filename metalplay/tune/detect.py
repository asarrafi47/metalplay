"""Detect Mac hardware and recommend game settings."""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import asdict, dataclass, field


@dataclass
class DisplayInfo:
    name: str = "Unknown"
    physical_width: int = 1920
    physical_height: int = 1080
    logical_width: int = 1920
    logical_height: int = 1080
    retina: bool = True
    gpu_cores: int = 0


@dataclass
class HardwareProfile:
    chip: str = "Unknown"
    cpu_cores: int = 0
    performance_cores: int = 0
    efficiency_cores: int = 0
    memory_gb: int = 0
    arch: str = platform.machine()
    display: DisplayInfo = field(default_factory=DisplayInfo)
    recommended_game_resolution: str = "1920x1080"
    recommended_steam_desktop: str = "0"
    tier: str = "generic"  # generic, m4, m4-pro, m4-max, m3-max, etc.

    def to_dict(self) -> dict:
        return asdict(self)


def _sysctl(key: str, default: str = "") -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return default


def _primary_screen_geometry() -> tuple[int, int, float]:
    """
    Logical size and backing scale of the menu-bar screen.

    Finder desktop bounds span all monitors (e.g. 5568x1117 with a laptop + external),
    which breaks Wine DPI if used as the Steam desktop size.
    """
    try:
        import Quartz

        screen = Quartz.NSScreen.mainScreen()
        if screen is not None:
            frame = screen.frame()
            scale = float(screen.backingScaleFactor())
            w, h = int(frame.size.width), int(frame.size.height)
            if w > 0 and h > 0:
                return w, h, scale
    except Exception:
        pass
    return 1920, 1080, 1.0


def _logical_desktop_size() -> tuple[int, int]:
    w, h, _ = _primary_screen_geometry()
    return w, h


def _parse_system_profiler() -> tuple[str, DisplayInfo, int]:
    chip = _sysctl("machdep.cpu.brand_string", platform.processor() or "Apple Silicon")
    display = DisplayInfo(name="Built-in Display")
    gpu_cores = 0
    memory_gb = 0

    try:
        hw_result = subprocess.run(
            ["system_profiler", "SPHardwareDataType"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        disp_result = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        hw_text = hw_result.stdout if hw_result.returncode == 0 else ""
        disp_text = disp_result.stdout if disp_result.returncode == 0 else ""
        text = hw_text + disp_text
    except (OSError, subprocess.SubprocessError):
        hw_text = disp_text = text = ""

    if hw_text:
        chip_m = re.search(r"Chip:\s*(.+)", hw_text)
        if chip_m:
            chip = chip_m.group(1).strip()
        mem_m = re.search(r"Memory:\s*(\d+)\s*GB", hw_text)
        if mem_m:
            memory_gb = int(mem_m.group(1))

    if disp_text:
        gpu_m = re.search(
            r"Chipset Model:.*?\n(?:.*\n)*?\s*Total Number of Cores:\s*(\d+)",
            disp_text,
            re.MULTILINE,
        )
        if gpu_m:
            gpu_cores = int(gpu_m.group(1))
        res_m = re.search(r"Resolution:\s*(\d+)\s*x\s*(\d+)", disp_text)
        if res_m:
            display.physical_width = int(res_m.group(1))
            display.physical_height = int(res_m.group(2))
            display.retina = "Retina" in disp_text
        name_m = re.search(r"Display Type:\s*(.+)", disp_text)
        if name_m:
            display.name = name_m.group(1).strip()

    lw, lh = _logical_desktop_size()
    display.logical_width = lw
    display.logical_height = lh
    display.gpu_cores = gpu_cores

    if memory_gb == 0:
        mem_bytes = _sysctl("hw.memsize", "0")
        try:
            memory_gb = max(1, int(mem_bytes) // (1024**3))
        except ValueError:
            memory_gb = 16

    return chip, display, memory_gb


def _parse_cpu_cores() -> tuple[int, int, int]:
    total = int(_sysctl("hw.ncpu", "8") or "8")
    # Apple doesn't expose P/E split via sysctl consistently; parse from profiler
    perf, eff = total, 0
    try:
        result = subprocess.run(
            ["system_profiler", "SPHardwareDataType"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        m = re.search(r"(\d+)\s*\((\d+)\s*Performance.*?(\d+)\s*Efficiency", result.stdout)
        if m:
            total = int(m.group(1))
            perf = int(m.group(2))
            eff = int(m.group(3))
    except (OSError, subprocess.SubprocessError):
        pass
    return total, perf, eff


def _classify_tier(chip: str, gpu_cores: int, memory_gb: int) -> str:
    chip_l = chip.lower()
    if "m4 max" in chip_l:
        return "m4-max"
    if "m4 pro" in chip_l:
        return "m4-pro"
    if "m4" in chip_l:
        return "m4"
    if "m3 max" in chip_l or "m2 max" in chip_l or "m1 max" in chip_l:
        return "apple-max"
    if gpu_cores >= 30 or memory_gb >= 36:
        return "apple-max"
    if gpu_cores >= 16 or memory_gb >= 24:
        return "apple-pro"
    return "generic"


def _recommend_resolution(tier: str, display: DisplayInfo) -> str:
    lw, lh = _logical_desktop_size()
    if lw > 0 and lh > 0:
        display = DisplayInfo(
            name=display.name,
            physical_width=display.physical_width,
            physical_height=display.physical_height,
            logical_width=lw,
            logical_height=lh,
            retina=display.retina,
            gpu_cores=display.gpu_cores,
        )
    lw, lh = display.logical_width, display.logical_height
    if tier in ("m4-max", "apple-max", "m4-pro", "m4", "apple-pro") and lw >= 1280:
        return f"{lw}x{lh}"
    # Fall back slightly below native logical for headroom
    if lw > 1920:
        scale = 1920 / lw
        return f"{1920}x{max(1080, int(lh * scale))}"
    return f"{lw}x{lh}"


def detect_hardware() -> HardwareProfile:
    chip, display, memory_gb = _parse_system_profiler()
    total, perf, eff = _parse_cpu_cores()
    tier = _classify_tier(chip, display.gpu_cores, memory_gb)
    game_res = _recommend_resolution(tier, display)

    return HardwareProfile(
        chip=chip,
        cpu_cores=total,
        performance_cores=perf,
        efficiency_cores=eff,
        memory_gb=memory_gb,
        arch=platform.machine(),
        display=display,
        recommended_game_resolution=game_res,
        recommended_steam_desktop="0",
        tier=tier,
    )


def format_report(hw: HardwareProfile, applied: dict | None = None) -> str:
    d = hw.display
    lines = [
        f"Chip:        {hw.chip} ({hw.tier})",
        f"CPU:         {hw.cpu_cores} cores ({hw.performance_cores}P + {hw.efficiency_cores}E)",
        f"GPU:         {d.gpu_cores or '?'} cores (Metal)",
        f"Memory:      {hw.memory_gb} GB",
        f"Display:     {d.name}",
        f"  Physical:  {d.physical_width}x{d.physical_height}{' Retina' if d.retina else ''}",
        f"  Logical:   {d.logical_width}x{d.logical_height} (macOS UI points)",
        f"  Recommend: {hw.recommended_game_resolution} in-game resolution",
    ]
    if applied:
        lines.append("")
        lines.append("Applied tune:")
        for key in (
            "performance_mode",
            "game_resolution",
            "virtual_desktop",
            "caffeinate_gaming",
            "high_power_mode",
            "retina_mode",
            "wine_cpu_count",
        ):
            if key in applied:
                lines.append(f"  {key}: {applied[key]}")
    return "\n".join(lines)
