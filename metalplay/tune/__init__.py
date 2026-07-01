"""Hardware-aware performance tuning for MetalPlay."""

from metalplay.tune.apply import apply_tune, load_applied_profile
from metalplay.tune.detect import detect_hardware, format_report

__all__ = ["apply_tune", "detect_hardware", "format_report", "load_applied_profile"]
