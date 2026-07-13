# dbus-battery-bank

A Venus OS service that manages a bank of JBD UP16S BMS packs and publishes them to D-Bus as one
Victron battery (plus read-only per-pack services for VRM logging).

Requirements, architecture, and design decisions live in [CLAUDE.md](CLAUDE.md).
Building the custom browser GUI (WASM) is documented in [scripts/README.md](scripts/README.md).

## Operations

```sh
svc -t /service/dbus-battery-bank    # restart
svc -d /service/dbus-battery-bank    # stop
svc -u /service/dbus-battery-bank    # start
tail -f /var/log/dbus-battery-bank/current | tai64nlocal
```

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/pytest
```
