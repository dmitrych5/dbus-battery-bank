#!/bin/bash
# Builds the custom GUI-v2 browser WASM in an Ubuntu container, since the upstream build
# scripts require Ubuntu. Produces build/wasm/venus-webassembly.zip for custom-gui-install.sh.
#
# Base: mr-manuel's venus-os_gui-v2 fork, branch matching the Venus OS line on the device,
# patched with this project's page changes (scripts/patch-gui-v2-fork.py).

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="dbus-serialbattery/venus-os_v3.6x/gui-v2_v1.1.x"
OUT_DIR="$PROJECT_DIR/build/wasm"
mkdir -p "$OUT_DIR"

# amd64: the upstream toolchain (aqtinstall Qt + emsdk) has no Linux ARM64 build; on Apple
# Silicon this runs through Rosetta (colima start --vm-type vz --vz-rosetta).
docker run --rm --platform linux/amd64 \
    -v "$PROJECT_DIR:/src:ro" \
    -v "$OUT_DIR:/out" \
    -e BRANCH="$BRANCH" \
    ubuntu:24.04 \
    bash /src/scripts/build-wasm-container.sh
