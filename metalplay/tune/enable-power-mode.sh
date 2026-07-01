#!/bin/bash
# Enable macOS High Power Mode for gaming (requires admin password once).
# MetalPlay runs this when tune apply cannot change pmset without root.
set -e
echo "Enabling High Power Mode and disabling Low Power Mode for gaming..."
pmset -a lowpowermode 0
pmset -a powermode 1
pmset -c displaysleep 0
echo "Done. Fans will ramp automatically under load."
echo "Restore balanced mode: sudo pmset -a powermode 0"
