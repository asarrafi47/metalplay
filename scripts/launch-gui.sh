#!/usr/bin/env bash
# Launch MetalPlay GUI (opens in your browser)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../.venv/bin/metalplay" gui "$@"
