#!/usr/bin/env bash
# Build the Open CASCADE modules mgeom needs, as static libraries.
#
#   src/geom/scripts/build-occt.sh [PREFIX]
#
# Installs into PREFIX (default: build/deps/occt-<version> in the repository)
# and prints the prefix to pass as CMAKE_PREFIX_PATH. Only the foundation
# classes, modelling data and modelling algorithms are built: no
# visualization, application framework, data exchange or Draw. A prefix that
# already holds this version is reused, so CI can cache it.
set -euo pipefail

VERSION="${OCCT_VERSION:-8_0_1}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PREFIX="${1:-$ROOT/build/deps/occt-$VERSION}"
STAMP="$PREFIX/.mgeom-occt-$VERSION"

if [[ -f "$STAMP" ]]; then
    echo "$PREFIX"
    exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git clone --quiet --depth 1 --branch "V$VERSION" \
    https://github.com/Open-Cascade-SAS/OCCT.git "$WORK/src" >&2

cmake -S "$WORK/src" -B "$WORK/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DBUILD_LIBRARY_TYPE=Static \
    -DBUILD_MODULE_FoundationClasses=ON \
    -DBUILD_MODULE_ModelingData=ON \
    -DBUILD_MODULE_ModelingAlgorithms=ON \
    -DBUILD_MODULE_Visualization=OFF \
    -DBUILD_MODULE_ApplicationFramework=OFF \
    -DBUILD_MODULE_DataExchange=OFF \
    -DBUILD_MODULE_Draw=OFF \
    -DBUILD_DOC_Overview=OFF \
    -DBUILD_GTEST=OFF \
    -DUSE_TK=OFF -DUSE_TCL=OFF -DUSE_FREETYPE=OFF -DUSE_FREEIMAGE=OFF \
    -DUSE_OPENGL=OFF -DUSE_GLES2=OFF -DUSE_XLIB=OFF -DUSE_TBB=OFF \
    -DUSE_VTK=OFF -DUSE_RAPIDJSON=OFF -DUSE_DRACO=OFF -DUSE_FFMPEG=OFF \
    -DUSE_OPENVR=OFF -DUSE_EIGEN=OFF >&2
cmake --build "$WORK/build" >&2
cmake --install "$WORK/build" >&2

touch "$STAMP"
echo "$PREFIX"
