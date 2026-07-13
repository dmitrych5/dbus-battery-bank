#!/bin/bash
# Native macOS build of the custom GUI-v2 browser WASM — no containers or emulation.
# Mirrors the upstream Ubuntu scripts (scripts/build-wasm*.sh in mr-manuel's fork); emscripten
# and the Qt host tools run natively on Apple Silicon. This replaced the Rosetta-emulated
# container build, whose emscripten tools segfault intermittently under Rosetta.
#
# Everything installs under build/toolchain/ (persistent, ~4 GB) so only the first run
# downloads; later runs go straight to compiling. Progress can be watched with:
#     tail -f build/wasm-build.log

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="dbus-serialbattery/venus-os_v3.6x/gui-v2_v1.1.x"
# Toolchain versions pinned by the branch (scripts/.env there); keep in sync when switching.
QT_VERSION=6.6.3
EMSCRIPTEN=3.1.37

TOOLCHAIN_DIR="$PROJECT_DIR/build/toolchain"
WORK_DIR="$PROJECT_DIR/build/gui-v2"
OUT_DIR="$PROJECT_DIR/build/wasm"
QT_WASM_DIR="$TOOLCHAIN_DIR/Qt/$QT_VERSION/wasm_singlethread"
mkdir -p "$TOOLCHAIN_DIR" "$OUT_DIR"

echo "*** Python environment with aqtinstall ***"
if [ ! -x "$TOOLCHAIN_DIR/python/bin/pip" ]; then
    python3 -m venv "$TOOLCHAIN_DIR/python"
fi
source "$TOOLCHAIN_DIR/python/bin/activate"
pip install -q aqtinstall

echo "*** Qt $QT_VERSION: macOS host, WebAssembly target, CMake/Ninja tools ***"
if [ ! -d "$TOOLCHAIN_DIR/Qt/$QT_VERSION/macos" ]; then
    aqt install-qt mac desktop "$QT_VERSION" clang_64 -m qtwebsockets qt5compat qtshadertools --outputdir "$TOOLCHAIN_DIR/Qt"
fi
if [ ! -d "$QT_WASM_DIR" ]; then
    aqt install-qt mac desktop "$QT_VERSION" wasm_singlethread -m qtwebsockets qt5compat qtshadertools --outputdir "$TOOLCHAIN_DIR/Qt"
fi
if [ ! -d "$TOOLCHAIN_DIR/Qt/Tools/CMake" ]; then
    aqt install-tool mac desktop tools_cmake --outputdir "$TOOLCHAIN_DIR/Qt"
fi
if [ ! -d "$TOOLCHAIN_DIR/Qt/Tools/Ninja" ]; then
    aqt install-tool mac desktop tools_ninja --outputdir "$TOOLCHAIN_DIR/Qt"
fi
export PATH="$TOOLCHAIN_DIR/Qt/Tools/CMake/CMake.app/Contents/bin:$TOOLCHAIN_DIR/Qt/Tools/Ninja:$PATH"

# aqt installs some Qt entry-point scripts without the executable bit (upstream fixes this
# with its .github/patches/qt-fixes.sh); restore it.
chmod +x "$TOOLCHAIN_DIR/Qt/$QT_VERSION"/*/bin/* "$TOOLCHAIN_DIR/Qt/$QT_VERSION"/*/libexec/* 2>/dev/null || true

# The 6.6.x WebAssembly packages are built on a Windows host, and aqt's macOS install leaves
# Windows-style backslash paths inside the POSIX shell wrappers; normalize them. The host Qt
# location is pinned explicitly for the same reason (qt.toolchain.cmake honors QT_HOST_PATH).
for wrapper in "$QT_WASM_DIR"/bin/qt-* "$QT_WASM_DIR"/libexec/qt-*; do
    case "$wrapper" in
        *.bat) continue ;;
    esac
    [ -f "$wrapper" ] && sed -i '' 's|\\|/|g' "$wrapper"
done
export QT_HOST_PATH="$TOOLCHAIN_DIR/Qt/$QT_VERSION/macos"
# The same Windows-built package bakes its original install prefix into its CMake package
# files and target_qt.conf; point them at the actual location.
grep -rl "C:/Qt/Qt-$QT_VERSION" "$QT_WASM_DIR/lib/cmake" "$QT_WASM_DIR/bin" "$QT_WASM_DIR/libexec" 2>/dev/null | while read -r file; do
    sed -i '' "s|C:/Qt/Qt-$QT_VERSION|$QT_WASM_DIR|g" "$file"
done

echo "*** Emscripten $EMSCRIPTEN ***"
if [ ! -d "$TOOLCHAIN_DIR/emsdk" ]; then
    git clone -q https://github.com/emscripten-core/emsdk.git "$TOOLCHAIN_DIR/emsdk"
fi
if [ ! -f "$TOOLCHAIN_DIR/emsdk/.installed-$EMSCRIPTEN" ]; then
    (cd "$TOOLCHAIN_DIR/emsdk" && ./emsdk install "$EMSCRIPTEN" && ./emsdk activate "$EMSCRIPTEN" && touch ".installed-$EMSCRIPTEN")
fi
source "$TOOLCHAIN_DIR/emsdk/emsdk_env.sh" >/dev/null

echo "*** QtMqtt (not distributed with Qt; built from source into the wasm Qt) ***"
if [ ! -f "$QT_WASM_DIR/lib/cmake/Qt6Mqtt/Qt6MqttConfig.cmake" ]; then
    rm -rf "$TOOLCHAIN_DIR/qtmqtt"
    git clone -q https://github.com/qt/qtmqtt.git "$TOOLCHAIN_DIR/qtmqtt"
    (
        cd "$TOOLCHAIN_DIR/qtmqtt"
        git checkout -q "v$QT_VERSION"
        mkdir -p build-qtmqtt && cd build-qtmqtt
        "$QT_WASM_DIR/bin/qt-configure-module" ..
        cmake --build . --parallel "$(sysctl -n hw.ncpu)"
        cmake --install . --prefix "$QT_WASM_DIR"
    )
fi

echo "*** Checking out $BRANCH ***"
if [ ! -d "$WORK_DIR" ]; then
    git clone -q --branch "$BRANCH" --recurse-submodules --shallow-submodules --depth 1 \
        https://github.com/mr-manuel/venus-os_gui-v2.git "$WORK_DIR"
else
    (cd "$WORK_DIR" && git checkout -q -- . && git submodule update --init)
fi
python3 "$PROJECT_DIR/scripts/patch-gui-v2-fork.py" "$WORK_DIR"

echo "*** Building venus-gui-v2 for WebAssembly ***"
cd "$WORK_DIR"
rm -rf build-wasm && mkdir build-wasm && cd build-wasm
"$QT_WASM_DIR/bin/qt-cmake" -DCMAKE_BUILD_TYPE=MinSizeRel ..
cmake --build . --parallel "$(sysctl -n hw.ncpu)"

echo "*** Assembling venus-webassembly.zip (mirrors the upstream packaging) ***"
STAGE_DIR="$WORK_DIR/build-wasm_files_to_copy"
rm -rf "$STAGE_DIR" && mkdir -p "$STAGE_DIR/wasm"
cp venus-gui-v2.{html,js,wasm} qtloader.js "$STAGE_DIR/wasm/"
cp ../images/victronenergy.svg ../LICENSE.txt ../.github/patches/Makefile "$STAGE_DIR/wasm/"
cp -r ../wasm/icons "$STAGE_DIR/wasm/"
mv "$STAGE_DIR/wasm/venus-gui-v2.html" "$STAGE_DIR/wasm/index.html"
grep -q -E '^var createQtAppInstance' "$STAGE_DIR/wasm/venus-gui-v2.js"
sed -i '' "s%^var \(createQtAppInstance\)%window.\1%" "$STAGE_DIR/wasm/venus-gui-v2.js"
(
    cd "$STAGE_DIR/wasm"
    shasum -a 256 venus-gui-v2.wasm > venus-gui-v2.wasm.sha256
    gzip -k -9 venus-gui-v2.wasm
    # Only the gzip ships; the device's web server serves it in place of the raw file.
    rm venus-gui-v2.wasm
)
rm -f "$OUT_DIR/venus-webassembly.zip"
(cd "$STAGE_DIR" && zip -qr "$OUT_DIR/venus-webassembly.zip" wasm)
shasum -a 256 "$OUT_DIR/venus-webassembly.zip"
echo "Build complete: $OUT_DIR/venus-webassembly.zip"
