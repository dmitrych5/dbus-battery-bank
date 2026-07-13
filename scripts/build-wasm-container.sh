#!/bin/bash
# Runs inside the Ubuntu build container (see build-wasm-docker.sh). The upstream scripts
# refuse to run as root, so the build happens as a dedicated user with passwordless sudo.

set -e

apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -yq sudo git lsb-release python3 python3-pip curl unzip zip
useradd -m builder
echo "builder ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/builder

sudo --preserve-env=BRANCH -u builder bash <<'BUILD'
set -e
cd /home/builder
git clone --depth 1 --branch "$BRANCH" --recurse-submodules --shallow-submodules \
    https://github.com/mr-manuel/venus-os_gui-v2.git gui-v2
python3 /src/scripts/patch-gui-v2-fork.py /home/builder/gui-v2
cd gui-v2
bash scripts/build-wasm-install-requirements.sh
bash scripts/build-wasm.sh --preserve
cd build-wasm_files_to_copy
zip -qr /tmp/venus-webassembly.zip wasm
BUILD

cp /tmp/venus-webassembly.zip /out/
sha256sum /out/venus-webassembly.zip
echo "Build complete: venus-webassembly.zip"
