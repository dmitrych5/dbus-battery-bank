# dbus-battery-bank

A Venus OS service that manages a bank of JBD UP16S BMS packs and publishes them to D-Bus, along with a "virtual" aggregate battery.

Serves the same purpose as a combination of dbus-serialbattery and dbus-aggregate-batteries, but has a more maintainable architecture, is fully covered by unit tests and is more reliable. Currently it supports only the devices I use on my system: multiple JBD UP16S BMS, an optional Victron SmartShunt, and an optional chain of PTC thermistors for overheating detection. Other BMS types or devices can be added in the transport and acquisition layers when needed.

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
