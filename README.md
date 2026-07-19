# dbus-battery-bank

A Venus OS service that manages a bank of JBD UP16S BMS packs and publishes them to D-Bus, along with a "virtual" aggregate battery.

Serves the same purpose as a combination of dbus-serialbattery and dbus-aggregate-batteries, but has a more maintainable architecture, is fully covered by unit tests and is more reliable. Disclaimer: no guarantees it will work on your system, do your own testing, and improper configuration can be a safety issue too.

Currently it supports only the devices I use on my system: multiple JBD UP16S BMS, an optional Victron SmartShunt, and an optional chain of PTC thermistors for overheating detection. Other BMS types or devices can be added in the transport and acquisition layers when needed.

Requirements, architecture, and design decisions live in [CLAUDE.md](CLAUDE.md).
Building the custom browser GUI (WASM) is documented in [scripts/README.md](scripts/README.md).

## Operations

```sh
# deploy: tests, code + QML sync, WASM bundle (when built), GUI install, service restart. The ssh command in quotes is optional and allows to customize the parameters provided to ssh.
scripts/deploy.sh root@your-cerbo-ip "ssh -i ~/.ssh/some-key"

svc -t /service/dbus-battery-bank    # restart
svc -d /service/dbus-battery-bank    # stop
svc -u /service/dbus-battery-bank    # start
tail -f /var/log/dbus-battery-bank/current | tai64nlocal
```

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest    # -m puts the repository root on sys.path
```

## License

Required Notice: Copyright (c) 2026 dmitrych5

Everything in this repository except `qml/gui-v2/` is licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE): free to use, modify, and share for
noncommercial purposes. Commercial use requires a separate license — open an issue to get in
touch. (This is a source-available license, not an OSI-approved open-source license.)

`qml/gui-v2/` contains GUI pages derived from Victron Energy's
[gui-v2](https://github.com/victronenergy/gui-v2) and is licensed separately under the Victron
Energy OS license v1 — see [qml/gui-v2/LICENSE.txt](qml/gui-v2/LICENSE.txt). The same license
covers the browser-GUI WASM bundle produced by `scripts/build-wasm-macos.sh`, which contains
the entire compiled gui-v2.

By submitting a contribution you license it under the project license and additionally grant
the copyright holder the right to license it under other terms, including commercially.
