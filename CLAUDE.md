# dbus-battery-bank

A single Venus OS service that manages a bank of JBD UP16S BMS packs and publishes one
`com.victronenergy.battery` D-Bus service for DVCC. It replaces the previously used pair of
dbus-serialbattery (fork) + dbus-aggregate-batteries. Reference implementations for behavioral
parity live in `../venus-os_dbus-serialbattery` and `../dbus-aggregate-batteries`; the deployed
configuration lives in `../dbus-battery-configs/dbus-battery-bank-config.ini`.

The project is designed for one specific installation but keeps the BMS protocol behind a driver
interface so other battery types could be added later — only where that costs no maintainability.

## System context

- Cerbo GX, Venus OS, GUI-v2 (GUI-v1 is out of scope), VRM as the monitoring dashboard.
- A configurable number of JBD UP16S015 packs (16S LFP) on one or more RS-485 serial ports.
  Config describes a list of ports, each with its list of Modbus-style pack addresses; a port
  may carry a daisy-chain (the pack at address 0x01 is that chain's master and reports
  chain-aggregated limits) or a single directly connected pack. Neither the pack count nor the
  port count is assumed anywhere in code or docs — both come from config and are validated
  against what is actually found at startup. (The current installation happens to be one port
  with a three-pack daisy chain.)
- 1× Victron SmartShunt read directly over the VE.Direct text protocol on a second serial port
  (not via D-Bus). It provides bank current, SoC, consumed Ah, and — on its Aux input — the
  voltage of a PTC thermistor chain used as an independent overheat-detection layer.
- The service is supervised by daemontools (Venus OS convention): it may exit on unrecoverable
  errors and will be restarted. All serial ports the service owns must be excluded from
  serial-starter.
- One process publishes the aggregate bank service (the DVCC battery monitor) **plus one
  read-only service per pack**, so VRM logs per-pack metrics — essential for debugging why the
  aggregate behaves the way it does. All services should be built in a such a way that they can
  reuse the service names, unique identifiers, and DeviceInstances of the previous stack so VRM history continues seamlessly (aggregate and per-pack alike).

## Reliability principles (non-negotiable)

1. **Reliability starts from properly designed goals and elegant architecture.** The logic must be
   as simple as possible for the requirements. Every conditional multiplies the combinations to
   test; each one must earn its place.
2. **Self-heal when possible; report everything else.** Recovery actions (retry, degrade, restart)
   are explicit and bounded, never accidental.
3. **Fail loud, not silent.** Any important error must reach the operator through VRM alarms
   (immediate) or the VRM dashboard (slow drifts). Logs are for diagnosis, not for notification —
   but anything unusual must still be properly logged.
4. **Metrics and observability are important.** The control loop's inputs and decisions must be
   inspectable live (GUI/VRM) without attaching a debugger.
5. **Proper unit test coverage is a must.** The control core must be exhaustively testable without
   hardware, D-Bus, or real time.

## Workflow

- Commit right after every reasonably distinct change, as long as tests pass.
- Run tests as `.venv/bin/python -m pytest` (the `-m` form puts the repository root on
  `sys.path`; the bare `pytest` binary cannot import `battery_bank`).

## Deployment

The Cerbo is **production**; be deliberate about every change there. Its address and ssh key
are kept out of the repository (Claude: see memory).

- `scripts/deploy.sh root@<cerbo-ip> ["ssh -i ~/.ssh/some-key"]` is the whole procedure for
  every kind of change: it runs
  the tests, rsyncs the working tree to `/data/apps/dbus-battery-bank/`, ships
  `build/wasm/venus-webassembly.zip` when present, then runs `enable.sh` on the device (which
  installs QML/WASM only if content changed and restarts the GUI accordingly) and restarts the
  service with `svc -t`. A WASM change only adds the local rebuild beforehand
  (`scripts/build-wasm-macos.sh`, see `scripts/README.md`) — the deploy itself is unchanged.
- The rsync must never use `--delete`: the device directory holds files that exist only there —
  `config.ini`, `state.json` (written by the running service), `wasm/venus-webassembly.zip`,
  and the old stack's files moved into `old-stack-backup/` by `install.sh`. The flip side:
  files deleted from the repository linger on the device until removed by hand.
- Service restarts are safe by design (startup warmup publishes nothing until the picture is
  complete), but after any deploy watch
  `tail -f /var/log/dbus-battery-bank/current | tai64nlocal` until the services come back up.
- First-time installation on a device is `install.sh` (see `COMMISSIONING.md`), not deploy.

## Code quality standards

- The quality bar is deliberately high: the code must be well-designed, maintainable, concise,
  readable and well-structured, far above the typical quality seen in most codebases.
- No pattern duplication or code duplication, to a reasonable extent. Charge/discharge symmetry,
  repeated limit curves, repeated error handling — all expressed once, parameterized.
- Clear, concise, unambiguous naming supersedes comments: name things first; add a comment only
  for what the name cannot express.
- Never restate a constant's current value in a comment — refer to the constant by name.
- No "how it used to work" in comments or docs; that belongs in git commit messages. Exception:
  non-obvious traps likely to raise the same question again (e.g. BMS firmware quirks).
- Type hints everywhere. Frozen dataclasses for values crossing layer boundaries.
- No module-level mutable state. Clock, serial ports, D-Bus, and filesystem are injected.
- Every `except` clause implements exactly one item of the error taxonomy (below) and says which.

## Architecture

Five layers with typed, timestamped values flowing in one direction. No layer reaches around
another; no layer below "publishing" touches D-Bus; no layer except "transport" touches serial.

```
 serial ports                     D-Bus / VRM
     │                                 ▲
 ┌───▼──────────┐   ┌──────────────────┴─┐
 │ 1. Transport │   │ 5. Publishing      │
 │  UP16S codec │   │  dbus paths, VRM   │
 │  VE.Direct   │   │  metric mapping,   │
 │  port mgmt   │   │  alarms            │
 └───┬──────────┘   └──────────▲─────────┘
 ┌───▼──────────────┐  ┌───────┴─────────┐
 │ 2. Acquisition   │  │ 4. Persistence  │
 │  pollers →       │  │  latched trips, │
 │  BatterySnapshot │  │  control state, │
 │  ShuntSnapshot   │  │  settings/VRM   │
 │  (+ data age)    │  │  instance       │
 └───┬──────────────┘  └───────▲─────────┘
     │   ┌─────────────────────┴─────────┐
     └───▶ 3. Control core (pure)        │
         │  step(snapshots, state,       │
         │       config, now) →          │
         │  (state', outputs, events)    │
         └───────────────────────────────┘
```

### 1. Transport

- UP16S protocol client: frame codec (pure functions: build request, parse/validate response —
  address, function code, length, CRC16), command definitions as frozen dataclasses (PackStatus,
  PackParams1, PackParams2, IndividualPackStatus, ProductInformation, SetSoc). Ported from the
  existing `lltjbd_up16s.py`, which is the proven part of the old stack.
- VE.Direct text-protocol parser (pure: bytes in, checksum-validated frames out). Ported from
  `vedirect_shunt_monitor.py`.
- Serial port lifecycle: open/reopen, timeouts, interference detection via termios (needed until
  serial-starter exclusion is confirmed to make it obsolete — then delete it).

### 2. Acquisition

- One poller per serial port; pollers on different ports are independent and *could* run
  concurrently (today they run sequentially inside the main-loop cycle — see the open design
  question on synchronous serial I/O) — immutable snapshot handoff to the control loop is the isolation boundary, so
  adding ports never changes the control side. Snapshots are stamped with a monotonic timestamp
  and carry a stable pack identity (BMS unique ID), so nothing downstream keys on port or
  address: `BatterySnapshot` (per pack: voltage, current, hi-res SoC, SoH, capacities, cell
  voltages, balancing flags, cell/MOSFET/ambient temperatures, FET states, fault/alarm flags,
  CCL/DCL/CVL/DVL from BMS, chain-aggregated CCL/DCL when polling a chain master) and
  `ShuntSnapshot` (current, SoC, consumed Ah, PTC aux voltage).
- Per-command availability learning (UNKNOWN → AVAILABLE/UNAVAILABLE after N retries) so
  unsupported commands don't add timeouts every cycle.
- Never blocks the control cadence indefinitely: a failed poll yields "no new snapshot"; staleness
  is the control core's input, not an exception.

### 3. Control core — the heart, and a pure function

`step(BankInputs, ControlState, Config, now) -> (ControlState, ControlOutputs, list[Event])`

- No I/O, no globals, no wall-clock reads, no logging side effects. Events carry everything the
  outer loop should log, alarm, or persist.
- Contents:
  - **Bank charge-mode state machine** (single, bank-level — replaces per-battery state machines
    plus cross-process string aggregation): Bulk → Absorption (all packs full and balanced, timer)
    → FloatTransition (voltage ramp) → Float; back to Bulk on SoC threshold. Per-pack "full and
    balanced" sub-conditions feed the bank state.
  - **CVL**: bank target per mode, minus the cell-overvoltage I-controller clamp (the controller
    setpoint offset is its own config value, not shared with the float-switch threshold).
  - **CCL/DCL**: `min` over — config maximum, per-pack BMS limit, chain-aggregated limit from
    each daisy-chain master (where a chain exists),
    and one generic `LimitCurve` (input value → current fraction) instantiated for cell voltage,
    cell temperature, **ambient temperature**, and MOSFET temperature, for charge and discharge.
    One curve mechanism, eight configurations — not eight code paths. Update rate limiting and
    zero-recovery hysteresis preserved from the current system.
  - **Protections** (latched until operator reset): PTC aux-voltage deviation vs. expected voltage
    from the Kalman-filtered, thermal-inertia-corrected cell-sensor average temperature;
    max temperature spread across all packs; shunt-absence → zero limits.
  - **SoC**: bank SoC/consumed-Ah from the shunt when fresh, capacity-weighted BMS SoC as
    fallback; per-pack SetSoc(100%) trigger at the pack's charge completion (AUTO_RESET_SOC).
  - **Alarm aggregation**: max severity across packs, plus staleness/cable alarms derived from
    snapshot age, plus internal-failure mapping from BMS fault flags.
- The Kalman filter and interpolation helpers are pure library code beside the core.

### 4. Persistence

- Latched protection trips are persisted (atomic write) and survive restarts — a trip can only be
  cleared deliberately, never by a crash.
- **Flash wear is a design constraint**: the state file is saved only when its serialized content
  changed, and no persisted value may change it every control cycle. Continuously-varying values
  must be quantized (CVL: `CVL_PERSIST_QUANTUM_VOLTS`) or cadence-limited (thermal filter:
  `THERMAL_SAVE_INTERVAL_SECONDS`; history: `HISTORY_SAVE_INTERVAL_SECONDS`, plus an immediate
  save on operator clear and a final save on clean shutdown) before persisting; any newly
  persisted value must obey the same rule. Safety latches are exempt — a trip change always
  writes immediately.
- Charge-mode state and last-SoC-reset persist across restarts (as the old system did via
  com.victronenergy.settings).
- DeviceInstance/CustomName via com.victronenergy.settings, Victron-style.

### 5. Publishing

- One `com.victronenergy.battery` service for the aggregate bank plus one per configured pack,
  all from one process. Each service lives on its own **private D-Bus connection** — D-Bus object paths are
  per-connection, not per service name, so multiple services with identical path layouts cannot
  share a connection (a trap that will resurface; this is why connections are constructed
  directly rather than via the shared `dbus.SystemBus()` singleton).
- The aggregate service is the DVCC battery monitor. Per-pack services are read-only projections
  of `BatterySnapshot` plus the control core's per-pack diagnostics (per-pack CVL/CCL/DCL
  contributions, charge sub-state, alarms) so VRM records the data needed to explain aggregate
  decisions.
- Paths: the subset GUI-v2, DVCC, and VRM actually consume, plus History and TimeToGo (both in
  active use).
- Ambient temperature is published on the standard `/AirTemperature` path: stock GUI-v2 renders
  it as a native "Air temperature" row next to "Battery temperature" (the row is hidden unless
  the path exists), so no GUI patching is needed and all cell-sensor slots stay intact.
  `/Dc/0/Temperature` remains the cell-sensor aggregate.
- There are three GUI surfaces, and knowing which is which matters (a repeated confusion):
  GUI-v1 (old Qt app — what this installation's Remote Console runs), the GUI-v2 Qt app (GX
  Touch displays / Remote Console when switched to GUI-v2 — fed by QML files under
  `/opt/victronenergy/gui-v2`), and the GUI-v2 browser WASM at `http://<gx>/gui-v2/` — a
  compiled bundle that QML files cannot patch. **The operator's primary UI is the browser
  WASM.**
- GUI-v2 QML pages: the project ships its own copies of the six battery QML pages
  (per-Venus-version sets under `qml/gui-v2/`; the 3.6x set serves firmware ≤ v3.69, 3.7x
  serves ≤ v3.79), installed over the stock ones via the overlay-fs app by
  `custom-gui-install.sh`, which `enable.sh` runs on every boot so Cerbo firmware upgrades
  heal themselves. For this project's services (aggregate 0xBA44, packs 0xBA77) `PageBattery`
  carries everything on the main battery page: a seven-tile Overview row (current, voltage,
  power, cell max/min, SoC, consumed Ah), a seven-tile Temperatures row (ambient, MOSFET,
  cell average, Temp 1–4), charge mode, CVL/CCL/DCL with limitation text, allow-to, the PTC
  rows, and active alarms — the stock rows/submenus that layout supersedes are hidden
  (Battery V/I/P group, SoC / battery temperature / air temperature / consumed-Ah rows, IO
  and Parameters submenus, per-pack Details, the aggregate Details' capacity row). The debug
  texts live in a "Debug" submenu (`PageBatteryBankDebug.qml`), which also hosts the per-pack
  "Reset SoC to" control; a confirmed "Reset protection trips" button sits at the very top of
  the aggregate's page while `/ProtectionTripped` is 1 (the only GUI reset; the ssh fallback
  is `dbus -y com.victronenergy.battery.aggregate /Settings/ResetProtectionTrips SetValue 1`).
  Other battery services keep the stock layout. Use plain strings for labels we add — our
  translation ids are not in Victron's compiled catalog.
- Browser WASM: built by `scripts/build-wasm-macos.sh` from mr-manuel's venus-os_gui-v2 fork
  with this project's pages applied by `scripts/patch-gui-v2-fork.py` (which also registers
  new page files in the fork's CMakeLists — replacing a stock page needs no registration,
  adding one does); shipped as `wasm/venus-webassembly.zip` and installed by
  `custom-gui-install.sh`. A QML change reaches the browser only after a WASM rebuild.
- **QML must be compatible with the fork's gui-v2 branch, not newer firmware** (a trap that
  already bit once): base page copies on the fork's sources (`build/gui-v2/pages/...`), never
  on QML sets from newer Venus versions — e.g. `KeyNavigationHighlight` is a plain Item on
  the branch but an attached type later, and the attached syntax makes the whole page fail to
  create. `pushPage` pre-creates the target and on any error silently aborts (just a
  `console.warn`), so a broken page shows up as a menu entry that highlights but does not
  navigate — check the browser devtools console for "Aborted attempt to push page".
- Per-pack GUI pages intentionally show no charge-stage debug texts: the stage machine is
  bank-level, so per-pack float/bulk state has no meaning. The pack "Debug" submenu instead
  shows the pack's own contribution to the bank decision (`pack_diagnostics_values`: its
  CCL/DCL with active sources, BMS and chain-aggregated limits, FET/balancing state, data
  age); the aggregate carries the full bank diagnostics.
- History, split by data source (the GUI groups it with headers; see the shipped
  `PageBatteryHistory.qml`):
  - **Shunt-provided** (aggregate only): the SmartShunt's own lifetime history — deepest/last/
    average discharge, charge cycles, full discharges, cumulative Ah drawn, min/max voltage,
    sync count, charged/discharged energy (VE.Direct H1–H8, H10, H17, H18, parsed from the
    alternating history frame into `ShuntSnapshot.history_totals` and published raw). The
    shunt accumulates these internally, keeps counting while the service is down, and they
    are reset from the shunt itself (VictronConnect), never from this driver. H9/H11/H12 are
    deliberately not used: time-since-full-charge keys off the bank's own decision, and the
    shunt's voltage-alarm counts reflect its private thresholds rather than BMS alarms.
  - **Driver-provided** (`core/history.py`, one pure accumulator instance for the bank plus
    one per pack, fed per cycle via `bank_history_sample`/`pack_history_sample`): min/max
    cell voltage and temperature, low/high voltage alarm counts (rising edges of pack+cell
    alarm flags), the bank's time since last full charge (stamped at FloatTransition entry),
    and per-pack min/max voltage (on the aggregate those paths carry the shunt's records
    instead). Each subject publishes `/History/Clear` (any non-zero write clears that
    subject's driver history) and `/History/CanBeCleared = 1`; BMS-provided charge cycles and
    total Ah drawn stay on the pack services.
  `/Settings/HasTemperature = 1` is published because the stock GUI hides the temperature
  history rows without it (and `/Settings/HasStarterVoltage` deliberately stays absent, so the
  repurposed starter-voltage VRM workaround paths never render as GUI rows). BMS-provided
  charge cycles and total Ah drawn publish on the pack services, not the aggregate.
- VRM metric workarounds are confined to one module (`vrm_metric_map` or similar) with honest
  names on the inside: corrected temperature → `/History/MinimumStarterVoltage` etc. This is a
  deliberate, contained accommodation of VRM's fixed schema.
- Live control diagnostics: the control core emits a structured `Diagnostics` value (its inputs,
  per-limit contributions, state-machine conditions); the publishing layer renders it into the
  concise multi-line text paths the GUI shows today (`/Info/ChargeModeDebug` and friends), which
  read far better on screen than one dbus row per value. The renderer is a pure function of
  `Diagnostics`, testable on its own; the core never formats strings.
- Alarms publish through `/Alarms/...` so VRM notifies immediately.

### Zeroing current limits is itself a hazard

Publishing CCL/DCL of zero commands the inverter to stop — when off-grid that blacks out the house.
Every zero-limit response must therefore be deliberate:

- Staleness thresholds are sized to tolerate multiple consecutive failed polls before reacting;
  transient read errors never zero the limits.
- Startup warmup is normal operation, not a fault: until the first complete picture arrives the
  bank is "not ready" — nothing is published and nothing is alarmed or logged as an error. Only
  when a startup grace period expires without completeness does it fail loud. A restarting
  service must never momentarily command the inverter to stop.
- If the picture never completes after a start (a pack or the shunt dark since boot), the D-Bus
  services stay unregistered entirely — no values, current or stale, ever sit on the bus.
  Operator alerting then deliberately relies on Venus OS itself: with the battery monitor
  absent, the ESS raises its "BMS connection lost" alarm within a few minutes — the same
  channel that fires if the service crashes outright. The grace-expiry ERROR log events remain
  for diagnosis.

### Operator-visible alarm channels

A log line alone never reaches the operator; every condition that matters maps to a battery
alarm path, because those are the channel VRM verifiably notifies on:

- Stale/missing pack data → `/Alarms/BmsCable` (ALARM); stale shunt → the same path as WARNING.
- Latched protection trips (all thermal today) → `/Alarms/HighTemperature`, raised for as long
  as the trip is latched.
- Faults of the service itself — corrupt state file, repeated cycle failures, invalid
  configuration → `/Alarms/InternalFailure` (in addition to the Victron error state for
  invalid config, whose own notification behavior is still to be verified on the device).
- PTC aux voltage absent from otherwise-fresh shunt data (the PTC protection layer silently
  inoperative, e.g. the shunt Aux input reconfigured) → `/Alarms/InternalFailure` (WARNING)
  after a grace period, until the reading returns.
- BMS-reported alarms pass through their natural categories via worst-per-category
  aggregation.
- Zero CCL/DCL is itself a loud channel: the operator has a VRM notification configured
  specifically for the published limits reaching zero, independent of any alarm path. Every
  zero-limit response is therefore noticed even if its accompanying alarm were missed — but
  each still carries its specific alarm so the notification explains itself.

### Error taxonomy

Every failure is handled as exactly one of:

1. **Self-heal** — bounded retry/degradation with a defined safe value (e.g. stale pack data →
   staleness alarm + conservative limits; shunt data expired → fall back to BMS SoC).
2. **Latch** — safety trips: persist, alarm, hold zero limits until operator intervention.
3. **Report & restart** — unrecoverable states (config invalid, port permanently gone): publish
   the error state and alarm *first*, then exit for the supervisor. Never restart out of a latch.

An unhandled exception anywhere must never leave the service half-alive: the main loop guards the
full cycle; a cycle failure is itself an event (logged, counted, alarmed on repetition), and
repeated failures escalate to Report & restart.

### Time handling

- `time.monotonic()` for all intervals and staleness; wall clock only for display/logging.
- The control core receives `now` as an argument — tests drive time explicitly.

## Configuration

- One INI file (Venus OS Python compatibility), parsed at startup into a single frozen, validated
  `Config` dataclass. No config access anywhere except through that object; no defaults scattered
  at call sites.
- The battery topology is expressed as a list of serial ports, each with its pack address list
  (INI sections per port). Nothing else in the config, code, or docs assumes a particular number
  of ports or packs.
- Validation failures: start in error state, publish the config alarm so VRM notifies, do not
  control anything. Never silently substitute values.
- Each option has exactly one meaning. Options that existed only to work around old behavior are
  not carried over (e.g. the old stack's VOLTAGE_DROP actuation offset became
  `full_detection_tolerance_volts`, which states the real meaning: the published CVL is the true
  charge target, and a pack counts as full when its cell sum is within the tolerance of it).

## Testing

- pytest; CI on GitHub Actions.
- Control core: exhaustive unit tests — mode transitions under simulated time, limit-curve edges,
  latch/restore behavior, staleness handling, SoC source selection. This is where the test budget
  goes, because this is what must never be wrong.
- Codec: round-trip tests against captured real frames from the deployed system (fixtures checked
  in), including malformed/truncated/CRC-error cases.
- Acquisition & publishing: thin; tested with fake ports / fake bus.
- Characterization tests: encode the deployed system's observed behavior (from its config and
  logs) as expectations for phase-1 parity.

## UP16S protocol knowledge that must not be lost

Hard-won facts from the deployed system; each is a requirement, not trivia:

- **PackParams2 is read as a partial request starting at 0x2006** (not 0x2000) to avoid a BMS
  firmware race that briefly resets DCL to 0 across all requests when short-circuit-protection
  registers are read. Partial reads require firmware ≈v12+; older firmware ignores them, which the
  availability-learning mechanism absorbs.
- The master (address 0x01) returns **aggregated** CCL/DCL/CVL/DVL in PackStatus;
  IndividualPackStatus (function 0x45) retrieves its own non-aggregated CCL/DCL and only works on
  a direct connection. Bank logic uses `min(per-pack, master-aggregated)` for current limits.
- Slave SoC via the master is whole-percent; PackParams2 provides hi-res SoC. When PackParams2
  times out transiently, keep the last hi-res SoC unless it diverges >1% from PackStatus SoC
  (prevents SoC flapping on unstable links).
- SetSoc availability must be learned from its own write attempts, never inferred from
  PackParams2: PackParams2 can be unavailable merely because its partial-read request format is
  ignored by firmware < ~v12, while the plain SetSoc write on the same registers still works.
- The BMS hard-sets DCL to 0 below 2700 mV min cell voltage (not configurable) — discharge curves
  should cut current slightly above that.
- Encodings: current `(raw - 300000)/100` A; temperatures `(raw - 500)/10` °C; CRC16-Modbus with
  low byte first; `total_ah_drawn` is negative by Victron convention; use *rated* (not full)
  capacity in identity fallbacks because full capacity gets recalculated by the BMS.
- PackStatus carries `ambient_temp` — used for ambient-temperature current limiting and UI.
- Auto SoC reset at charge completion is only effective with certain cabling configurations.
- An **undocumented raw status window** (0x78 at 0x3000+, see `docs/up16s-raw-window.md`)
  reads the BMS status array directly and bypasses the master's ~50 s cache of slave state —
  the only known way to get fresh slave data every cycle. It is UNTRUSTED until validated
  against the proven commands (the docs file records the validation strategy); its encodings
  differ from the serialized commands for the same quantities (current offset 30000, not
  300000; 0xF6 is deci-Kelvin), and the protection/warning bitmasks are NOT in the window.
  Currently only dumped to the log once per pack at discovery
  (`transport/up16s_raw_window.py`).

## Roadmap

1. **Simplifications enabled by the merge**: revisit whether master-aggregated limits still add
   information once all packs are polled in-process; drop if provably redundant. Likewise
   revisit `require_direct_connection` — it was a workaround for the old per-battery driver
   instances being unable to coordinate when some packs were unreachable; with all packs in one
   process, direct-connection detection can likely be automatic (probe IndividualPackStatus)
   instead of configured.
2. **Diurnal thermal-state restore**: the batteries live in a non-conditioned garage, so the
   temperature at roughly the same time of day yesterday resembles today better than a
   many-hours-old state from today. Idea to explore: every few hours persist per-hour data
   points for the last ~25 hours (25 so the current time of day exists for both today and
   yesterday); on restore, extrapolate the expected change from three known points — the first
   and last points in the window plus the point matching the launch time of day — to
   reconstruct a better thermal estimate than pure rate extrapolation allows.

Behavior changes to proven battery-handling logic beyond this list are discussed before
implementation.

## Current status and next steps (as of 2026-07-13)

The system is commissioned and permanent: the service runs on the Cerbo, all parity checks
passed, and the custom browser WASM is built (natively via `scripts/build-wasm-macos.sh`,
minutes per rebuild thanks to the persistent toolchain) and installed. The operator verified
the browser UI (the battery pages have since been restructured — see the GUI-v2 QML bullet
under Publishing).

Next tasks, in priority order:

1. **Verify the history module and the reworked battery pages on the device** (implemented;
   see "History, split by data source" and the GUI-v2 QML bullet under Publishing): after a
   WASM rebuild and deploy, check the aggregate's and packs' History pages fill in, the
   restructured main battery page reads well, the trip-reset button appears when a trip is
   latched, and that `state.json` grows its history blocks without excessive write churn.
2. **Verify the tolerance-based full detection on the device**: the float switch now compares
   each pack's cell sum against the target minus `full_detection_tolerance_volts` and the
   published CVL no longer carries the charger offset — watch the first full charge cycle
   after deploy to confirm the bank still reaches Float, and check the steady-state gap
   between the published CVL and the BMS-measured sum leaves real margin within the 0.08 V
   tolerance. The device `config.ini` must be updated at deploy time (the option rename and
   `absorption_hold_seconds = 300`), or the service starts in config-error state.
3. Roadmap items below (diurnal thermal restore, master-limit/`require_direct_connection`
   simplifications).

## Decided details

- **Ambient temperature UI**: published on `/AirTemperature`, shown for our services as the
  first tile ("Ambient") of the Temperatures row (other products keep the stock "Air
  temperature" row); `/Dc/0/Temperature` stays the cell-sensor aggregate ("Cell avg" tile)
  for VRM/DVCC/stock pages. The temperature tiles' red-highlight-when-limiting checks must
  keep matching the `LimitSource` limitation strings (they compare lowercased text:
  "ambient", "cell temperature", "mosfet"); the cell max/min tiles are deliberately not
  highlighted (the Cell Voltages page colors the extreme cells instead: balancing orange
  takes precedence, then maximum red / minimum blue). Verify whether VRM logs
  `/AirTemperature` once live; if not and drift monitoring of ambient is wanted, route it
  through the contained VRM metric-map module.
- **Operator reset of latched trips**: a confirmed button on the aggregate's battery page,
  visible only while `/ProtectionTripped` is 1, backed by a writable dbus path with a change
  callback — the same proven mechanism as the `/Settings/ResetSocTo` control (which lives at
  the bottom of the pack "Debug" submenu). The reset emits an Event so it is logged and
  auditable.
- **Per-pack SoC reset timing**: keep current behavior (reset when the bank enters
  FloatTransition).

## Open design questions

- **Synchronous serial I/O in the GLib main loop.** All polling runs inside the 1 s cycle
  callback. Bounds: a dark pack blocks the loop ~1.5–3 s per cycle (the `SerialLink` deadline
  is only checked after a blocking read, so the worst case is about twice the 1.5 s timeout);
  discovery retries under serial interference can block up to 60 s per required command and
  re-run every 30 s while any configured pack is undiscovered. While blocked, D-Bus requests
  (GUI/VRM reads, the trip-reset write) wait, SIGTERM handling is delayed, and the shunt is not
  read — its 30 s staleness budget absorbs realistic stalls. Interference is rare outside the
  first minute after service start (udev keeps serial-starter off the ports), and with one
  port the bounds are acceptable; but worst-case stalls stack linearly with added ports. If it
  ever bites: thread the pollers behind the immutable-snapshot handoff (the architecture
  already isolates them for exactly this), and/or shorten the discovery interference wait.
  Deliberately not decided yet; revisit before adding a second battery port.
