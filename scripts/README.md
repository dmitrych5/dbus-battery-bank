# Building the custom browser GUI (WASM)

The GUI-v2 web app at `http://<gx>/gui-v2/` is a compiled WebAssembly bundle — QML files
cannot patch it, so this project builds its own bundle containing the same page changes that
`qml/gui-v2/` carries for the Qt GUI (the settings entry for the aggregate, the
"Reset protection trips" control, and the PTC row retitles on the aggregate).

The base is mr-manuel's fork of Victron's gui-v2 (`mr-manuel/venus-os_gui-v2`), which already
contains the dbus-serialbattery pages this project's services rely on.

## Build

Prerequisites: Docker. On Apple Silicon the upstream toolchain has no Linux ARM64 build, so
the container must run amd64 through Rosetta:

```sh
brew install colima docker
colima start --vm-type vz --vz-rosetta --cpu 4 --memory 8 --disk 40
```

Then:

```sh
bash scripts/build-wasm-docker.sh
```

This clones the fork branch, applies `scripts/patch-gui-v2-fork.py`, and runs the fork's own
build scripts (they pin the Qt and emscripten versions per branch in `scripts/.env`) inside an
`ubuntu:24.04` container. Expect roughly an hour; the output is
`build/wasm/venus-webassembly.zip` (untracked — rebuild rather than version binaries).

## Picking the branch for a Venus OS version

The fork keeps one branch per Venus OS line; `BRANCH` in `build-wasm-docker.sh` must match the
firmware on the device:

| Venus OS      | fork branch                                      |
|---------------|--------------------------------------------------|
| ≤ v3.59       | `dbus-serialbattery/venus-os_v3.5x/gui-v2_v1.0.x` |
| v3.60 – v3.69 | `dbus-serialbattery/venus-os_v3.6x/gui-v2_v1.1.x` |
| v3.70 – v3.79 | `dbus-serialbattery/venus-os_v3.7x/gui-v2_v1.2.x` |

List current branches: `https://api.github.com/repos/mr-manuel/venus-os_gui-v2/branches`.

When switching branches, also review `scripts/patch-gui-v2-fork.py`: it replaces
`PageBattery.qml` with the copy from `qml/gui-v2/3.6x/` (verified byte-identical to that
branch's base before our edits) and inserts the trip-reset control at a structural anchor in
the settings page. Both steps fail loud if the branch layout diverged — re-verify against the
new branch's files and adjust the version directory and anchor.

## Deploy and verify

The zip ships with the normal app deploy (rsync includes `build/`); on the device,
`custom-gui-install.sh` (run by `enable.sh`, also on every boot) installs it into
`/var/www/venus/gui-v2` through the overlay-fs overlay, guarded by a zip-hash marker, and
regenerates the gzip and hash files the VRM portal check expects.

Verify in the browser with a hard refresh (browsers cache the WASM aggressively): the battery
pages should work as before, plus "Battery service settings" with the trip-reset button on the
aggregate and the "PTC voltage ×10" / "PTC deviation" row titles.

## Rollback

mr-manuel's original bundle is still on the device under the old driver's files:
`/data/apps/dbus-serialbattery/ext/venus-os_dbus-serialbattery_gui-v2/archive/venus-os_v3.6x/venus-webassembly.zip`.
Unzip it over `/var/www/venus/gui-v2` (same steps as in `custom-gui-install.sh`) and delete the
`.dbus-battery-bank-wasm.sha256` marker so the installer does not immediately re-replace it —
or simply remove `wasm/venus-webassembly.zip` from the app directory first.
