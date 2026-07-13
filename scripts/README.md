# Building the custom browser GUI (WASM)

The GUI-v2 web app at `http://<gx>/gui-v2/` is a compiled WebAssembly bundle — QML files
cannot patch it, so this project builds its own bundle containing the same page changes that
`qml/gui-v2/` carries for the Qt GUI (the settings entry for the aggregate, the
"Reset protection trips" control, and the PTC row retitles on the aggregate).

The base is mr-manuel's fork of Victron's gui-v2 (`mr-manuel/venus-os_gui-v2`), which already
contains the dbus-serialbattery pages this project's services rely on.

## Build (native macOS — the working path)

```sh
bash scripts/build-wasm-macos.sh          # from the repository root
tail -f build/wasm-build.log              # watch progress from another terminal (read-only)
```

The script clones the fork branch, applies `scripts/patch-gui-v2-fork.py`, and mirrors the
fork's own build steps natively: Qt (macOS host + WebAssembly target + CMake/Ninja tools) via
aqtinstall, emscripten via emsdk (native Apple Silicon binaries), QtMqtt built from source,
then the qt-cmake/ninja build and the upstream packaging. Everything installs under
`build/toolchain/` (~4 GB, persistent), so **only the first run downloads — later runs reuse
the toolchain and go straight to compiling** (minutes, not an hour). Output:
`build/wasm/venus-webassembly.zip` (untracked — rebuild rather than version binaries).

Toolchain versions (Qt, emscripten) are pinned at the top of `build-wasm-macos.sh` and must
match the fork branch's `scripts/.env` when switching branches.

## Build (Ubuntu container — fallback, not usable on Apple Silicon)

`scripts/build-wasm-docker.sh` runs the fork's own scripts unmodified in an `ubuntu:24.04`
container (`docker logs -f wasm-build` to watch). It works on x86 hosts; on Apple Silicon the
amd64 container must run through Rosetta, under which the emscripten tools **segfault
intermittently** (observed twice at different steps) — which is why the native build above
exists. The container is started fresh each run and re-downloads the whole toolchain.

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

The zip ships with the normal app deploy (`scripts/deploy.sh` sends it whenever
`build/wasm/venus-webassembly.zip` exists); on the device,
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
