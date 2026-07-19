# Commissioning checklist

The swap from dbus-serialbattery and dbus-aggregate-batteries is reversible at every step: `revert.sh` restores the old stack exactly, and
the old stack is started again with its usual `enable.sh` / `restart.sh`.

Commissioning findings: a udev `VE_SERVICE=ignore` rule for the battery port is required
(it can be created on the device as
`/data/etc/udev/rules.d/11-ignore-battery-bank-battery-port.rules`, symlinked from
`/data/rc.local`) — serial-starter's probing of the freed port makes reads fail outright.

The project ships its own GUI-v2 pages, installed by its own
`custom-gui-install.sh` from `enable.sh` on every boot, so firmware upgrades heal themselves.

## Prepare (old stack still running)

- [ ] Copy the repository to the Cerbo: the rsync from `scripts/deploy.sh` (the script itself
      is for updating an installed system — its enable/restart steps assume `install.sh` ran)
- [ ] Create `config.ini` from `config.example.ini`; port the deployed values from
      `dbus-battery-configs` (they are the example's baseline already) and set the real
      serial device paths in `[battery_port:...]` and `[shunt]`.
- [ ] `python3 -c "from battery_bank.config import load_config; load_config(__import__('pathlib').Path('/data/apps/dbus-battery-bank/config.ini'))"`
      on the Cerbo — must print nothing (config valid, and proves the Python version works).

## Swap

- [ ] `bash /data/apps/dbus-battery-bank/install.sh`
- [ ] `tail -f /var/log/dbus-battery-bank/current | tai64nlocal` — expect: discovery of every
      pack with the correct unique IDs, then "All data sources reporting; bank control active".
      No errors during the startup warmup (that silence is by design).

## Verify function

- [ ] `dbus-spy`: aggregate service (`com.victronenergy.battery.aggregate`, instance 99) plus
      one service per pack, with the packs' previous DeviceInstances reclaimed.
- [ ] CVL/CCL/DCL on the aggregate match what the old stack published in the same conditions.
- [ ] DVCC can use the aggregate as the battery monitor, in which case the Multi follows the published limits.
- [ ] SoC, current, and consumed Ah come from the shunt (compare against the shunt's own
      values); voltage is the pack average.
- [ ] GUI-v2: pack pages show cells/temperatures as before.
- [ ] VRM: aggregate and per-pack graphs continue their old history.

## Rollback procedure

- [ ] `bash /data/apps/dbus-battery-bank/revert.sh`, then start the old stack
      (`enable.sh` + `restart.sh`) and confirm it works as before.
- [ ] Re-run `install.sh` to switch back once satisfied.
