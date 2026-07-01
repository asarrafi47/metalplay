#!/usr/bin/env bash
# Build a Metal-capable Wine from CrossOver FOSS sources (advanced)
#
# This produces a Wine 11 build with winemac.drv that supports DXMT.
# Requires: Xcode CLI tools, ~30-60 minutes, several GB disk space.
#
# Reference: https://www.codeweavers.com/products/more-information/source
set -euo pipefail

CROSSOVER_VERSION="${CROSSOVER_VERSION:-26.2.0}"
BUILD_DIR="${BUILD_DIR:-$HOME/.metalplay/build/wine}"
INSTALL_PREFIX="${INSTALL_PREFIX:-$HOME/.metalplay/runtimes/wine/crossover-foss}"

echo "=== MetalPlay Wine Builder ==="
echo "CrossOver source: $CROSSOVER_VERSION"
echo "Build dir:        $BUILD_DIR"
echo "Install prefix:   $INSTALL_PREFIX"
echo ""

if ! xcode-select -p &>/dev/null; then
  echo "Error: Xcode Command Line Tools required."
  echo "  xcode-select --install"
  exit 1
fi

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

SOURCE_TAR="crossover-sources-${CROSSOVER_VERSION}.tar.gz"
SOURCE_URL="https://media.codeweavers.com/pub/crossover/source/${SOURCE_TAR}"

if [[ ! -f "$SOURCE_TAR" ]]; then
  echo "Downloading CrossOver FOSS source..."
  curl -L -o "$SOURCE_TAR" "$SOURCE_URL"
fi

if [[ ! -d "crossover-sources-${CROSSOVER_VERSION}" ]]; then
  echo "Extracting source..."
  tar -xzf "$SOURCE_TAR"
fi

SRC_ROOT="$BUILD_DIR/crossover-sources-${CROSSOVER_VERSION}/sources/wine"
cd "$SRC_ROOT"

# distversion.h required by Wine build
if [[ ! -f include/distversion.h ]]; then
  cat > include/distversion.h <<'EOF'
#define WINE_DISTVERSION "MetalPlay CrossOver FOSS"
EOF
fi

export PATH="$(pwd):$PATH"
export MACOSX_DEPLOYMENT_TARGET=10.14

if [[ ! -f Makefile ]]; then
  echo "Configuring Wine (this may take a few minutes)..."
  CC="clang" CXX="clang++" MACOSX_DEPLOYMENT_TARGET=10.14 \
    ./configure \
      --prefix="$INSTALL_PREFIX" \
      --enable-win32on64 \
      --enable-archs=i386,x86_64 \
      --disable-winedbg \
      --without-x \
      --disable-mscms
fi

echo "Building Wine (this will take 20-40 minutes)..."
make -j"$(sysctl -n hw.ncpu)"

echo "Installing to $INSTALL_PREFIX..."
make install

echo ""
echo "Registering with MetalPlay..."
python3 -m metalplay runtime register "$INSTALL_PREFIX"
python3 -m metalplay runtime setup

echo ""
echo "=== Wine build complete ==="
echo "Create a bottle: metalplay bottle create gaming"
