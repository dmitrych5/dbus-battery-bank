# Commissioning checklist

The swap is fully reversible at every step: `revert.sh` restores the old stack exactly, and
the old stack is started again with its usual `enable.sh` / `restart.sh`. The system runs on
grid power during commissioning, so brief zero-limit periods are acceptable.

Status 2026-07-13: the swap was performed and the system runs on dbus-battery-bank. Items
below are marked done where verified; the remaining ones need the operator (physical access,
VRM account, GUI screen). Two commissioning findings are folded back into the project: Venus
OS ships Python without the `statistics` module, and a udev `VE_SERVICE=ignore` rule for the
battery port is required (created on the device as
`/data/etc/udev/rules.d/11-ignore-battery-bank-battery-port.rules`, symlinked from
`/data/rc.local`) — serial-starter's probing of the freed port makes reads fail outright.
Since resolved: the project now ships its own GUI-v2 pages, installed by its own
`custom-gui-install.sh` from `enable.sh` on every boot, so firmware upgrades heal themselves
and nothing depends on the old driver's GUI install anymore. Further verified by the operator:
a service restart (~30 s gap) does not trigger the system's monitor-lost alarm (that takes
3-4 minutes of absence), the invalid-config error state shows "#119 Settings invalid" plus the
internal-failure alarm on the Cerbo, and VRM metrics continue normally. The operator declared
the switch permanent; no rollback rehearsal.

## Prepare (old stack still running)

- [ ] Copy the repository to the Cerbo: `rsync -r --exclude .venv --exclude .git . root@<cerbo>:/data/apps/dbus-battery-bank/`
- [ ] Create `config.ini` from `config.example.ini`; port the deployed values from
      `dbus-battery-configs` (they are the example's baseline already) and set the real
      serial device paths in `[battery_port:...]` and `[shunt]`.
- [ ] `python3 -c "from battery_bank.config import load_config; load_config(__import__('pathlib').Path('/data/apps/dbus-battery-bank/config.ini'))"`
      on the Cerbo — must print nothing (config valid, and proves the Python version works).
- [ ] Note the current per-pack VRM DeviceInstances and the packs' unique IDs
      (`dbus-spy` → each battery service → `/DeviceInstance`, `/Serial`) for comparison.

## Swap

- [ ] `bash /data/apps/dbus-battery-bank/install.sh`
- [ ] `tail -f /var/log/dbus-battery-bank/current | tai64nlocal` — expect: discovery of every
      pack with the correct unique IDs, then "All data sources reporting; bank control active".
      No errors during the startup warmup (that silence is by design).

## Verify function

- [ ] `dbus-spy`: aggregate service (`com.victronenergy.battery.aggregate`, instance 99) plus
      one service per pack, with the packs' previous DeviceInstances reclaimed.
- [ ] CVL/CCL/DCL on the aggregate match what the old stack published in the same conditions
      (CVL including the charger offset; CCL/DCL = per-pack limit × pack count).
- [ ] DVCC uses the aggregate as the battery monitor; the Multi follows the published limits.
- [ ] SoC, current, and consumed Ah come from the shunt (compare against the shunt's own
      values); voltage is the pack average.
- [ ] GUI-v2: pack pages show cells/temperatures as before; the aggregate's parameters page
      shows the ChargeModeDebug texts; "Air temperature" rows appear on the aggregate (hottest
      pack) and each pack page.
- [ ] VRM (allow an hour): aggregate and per-pack graphs continue their old history; the PTC
      workaround metrics update (`/Dc/1/Voltage` ×10, deviation, corrected temperature).

## Verify failure behavior (each is reversible)

- [ ] Unplug the shunt's serial adapter: within the staleness timeout, CCL/DCL drop to zero,
      `/Alarms/BmsCable` goes to WARNING, the zero-limits VRM notification fires, SoC falls
      back to the BMS values. Replug: limits recover, "Shunt data fresh again" in the log.
- [ ] Trip reset: write 1 to `/Settings/ResetProtectionTrips` on the aggregate via dbus-spy;
      the log records the operator reset (test while nothing is tripped — it must be a no-op).
- [ ] Per-pack "Reset SoC to" row appears on the pack settings page (only where the BMS
      accepts SoC writes, i.e. where PackParams2 is available).
- [ ] Victron error state: temporarily rename `config.ini` and restart the service — the
      aggregate must appear in an error state with `/Alarms/InternalFailure` raised; check
      whether VRM notifies on the error state itself (open question), then restore the config.
- [ ] Restart the service: no zero-limit blip at the inverter, charge stage and any latched
      trips restored from the state file, PTC correction active immediately (log shows no
      warmup gap).

## Rollback rehearsal

- [ ] `bash /data/apps/dbus-battery-bank/revert.sh`, then start the old stack
      (`enable.sh` + `restart.sh`) and confirm it works as before.
- [ ] Re-run `install.sh` to switch back once satisfied.

## Follow-ups recorded in CLAUDE.md

- Whether VRM logs `/AirTemperature` (if not and drift monitoring of ambient is wanted, route
  it through the VRM metric map).
- Whether the Victron error state alone produces a VRM notification.
- Phase-2 GUI QML (Ambient tile, trip-reset button) and the roadmap simplifications.
