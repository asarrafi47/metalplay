#!/usr/bin/env bash
# MetalPlay setup — install CLI and download DXMT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== MetalPlay Setup ==="
echo ""

# Python 3.10+
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 is required."
  exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python: $PY_VERSION"

# Install MetalPlay CLI in a virtual environment
echo ""
echo "Creating virtual environment..."
python3 -m venv "$PROJECT_ROOT/.venv"
# shellcheck disable=SC1091
source "$PROJECT_ROOT/.venv/bin/activate"
pip install -e "$PROJECT_ROOT" -q

echo ""
echo "Add to your shell profile (~/.zshrc):"
echo "  alias metalplay='$PROJECT_ROOT/.venv/bin/metalplay'"
echo "  # or: export PATH=\"$PROJECT_ROOT/.venv/bin:\$PATH\""

# Download DXMT
echo ""
echo "Downloading DXMT (Direct3D → Metal translation layer)..."
"$PROJECT_ROOT/.venv/bin/metalplay" install dxmt

# Check for Wine
echo ""
echo "Checking for Wine..."
if "$PROJECT_ROOT/.venv/bin/metalplay" runtime list 2>/dev/null | grep -q .; then
  echo "Wine runtime detected."
  "$PROJECT_ROOT/.venv/bin/metalplay" runtime setup || true
else
  echo ""
  echo "No Wine runtime found. Install one of:"
  echo "  brew install --cask crossover    # Recommended (includes Metal support)"
  echo "  brew install --cask wine-stable    # Free WineHQ build"
  echo ""
  echo "After installing Wine, run:"
  echo "  metalplay runtime setup"
  echo "  metalplay bottle create gaming"
fi

# Rosetta on Apple Silicon
if [[ "$(uname -m)" == "arm64" ]]; then
  if ! pgrep -q oahd 2>/dev/null; then
    echo ""
    echo "Rosetta 2 is recommended for x86_64 Wine builds:"
    echo "  softwareupdate --install-rosetta --agree-to-license"
  fi
fi

echo ""
echo "=== Setup complete ==="
echo "Run: metalplay doctor"
