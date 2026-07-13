# dbus-battery-bank

A single Venus OS service that manages a bank of JBD UP16S BMS packs and publishes one
`com.victronenergy.battery` D-Bus service for DVCC. It replaces the previously used pair of
dbus-serialbattery (fork) + dbus-aggregate-batteries. Reference implementations for behavioral
parity live in `../venus-os_dbus-serialbattery` and `../dbus-aggregate-batteries`; the deployed
configuration lives in `../dbus-battery-configs`.

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

## Code quality standards

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

- One poller per serial port; pollers on different ports are independent and may run
  concurrently — immutable snapshot handoff to the control loop is the isolation boundary, so
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
- The custom GUI-v2 QML pages are part of the deliverable (per supported Venus OS version, as in
  the previous stack): cell voltages page, parameters page rendering the diagnostics text, and
  the settings page hosting operator controls.
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

Publishing CCL/DCL of zero commands the inverter to stop — off-grid that blacks out the house.
Every zero-limit response must therefore be deliberate:

- Staleness thresholds are sized to tolerate multiple consecutive failed polls before reacting;
  transient read errors never zero the limits.
- Startup warmup is normal operation, not a fault: until the first complete picture arrives the
  bank is "not ready" — nothing is published and nothing is alarmed or logged as an error. Only
  when a startup grace period expires without completeness does it fail loud. A restarting
  service must never momentarily command the inverter to stop.

### Operator-visible alarm channels

A log line alone never reaches the operator; every condition that matters maps to a battery
alarm path, because those are the channel VRM verifiably notifies on:

- Stale/missing pack data → `/Alarms/BmsCable` (ALARM); stale shunt → the same path as WARNING.
- Latched protection trips (all thermal today) → `/Alarms/HighTemperature`, raised for as long
  as the trip is latched.
- Faults of the service itself — corrupt state file, repeated cycle failures, invalid
  configuration → `/Alarms/InternalFailure` (in addition to the Victron error state for
  invalid config, whose own notification behavior is still to be verified on the device).
- BMS-reported alarms pass through their natural categories via worst-per-category
  aggregation.

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
  not carried over (see VOLTAGE_DROP under Roadmap).

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
- SetSoc is assumed available iff PackParams2 is available (same register range).
- The BMS hard-sets DCL to 0 below 2700 mV min cell voltage (not configurable) — discharge curves
  should cut current slightly above that.
- Encodings: current `(raw - 300000)/100` A; temperatures `(raw - 500)/10` °C; CRC16-Modbus with
  low byte first; `total_ah_drawn` is negative by Victron convention; use *rated* (not full)
  capacity in identity fallbacks because full capacity gets recalculated by the BMS.
- PackStatus carries `ambient_temp` — used for ambient-temperature current limiting and UI.
- Auto SoC reset at charge completion is only effective with certain cabling configurations.

## Roadmap

1. **Parity**: reproduce the deployed behavior (current configs in `../dbus-battery-configs`)
   on the new architecture, including VRM continuity (service name, DeviceInstance, workaround
   metric paths). Ambient-temperature limiting and UI exposure are included here since the
   protocol already delivers the value.
2. **Balancer-aware float switch**: model the balancer's cell-voltage cutoff in the
   absorption→float decision so charging reliably completes, and retire the VOLTAGE_DROP offset.
3. **Simplifications enabled by the merge**: revisit whether master-aggregated limits still add
   information once all packs are polled in-process; drop if provably redundant. Likewise
   revisit `require_direct_connection` — it was a workaround for the old per-battery driver
   instances being unable to coordinate when some packs were unreachable; with all packs in one
   process, direct-connection detection can likely be automatic (probe IndividualPackStatus)
   instead of configured.

4. **Diurnal thermal-state restore**: the batteries live in a non-conditioned garage, so the
   temperature at roughly the same time of day yesterday resembles today better than a
   many-hours-old state from today. Idea to explore: every few hours persist per-hour data
   points for the last ~25 hours (25 so the current time of day exists for both today and
   yesterday); on restore, extrapolate the expected change from three known points — the first
   and last points in the window plus the point matching the launch time of day — to
   reconstruct a better thermal estimate than pure rate extrapolation allows.

Behavior changes to proven battery-handling logic beyond this list are discussed before
implementation.

## Decided details

- **Ambient temperature UI**: published on `/AirTemperature`, which stock GUI-v2 renders as a
  native "Air temperature" row (no patching). Additionally, in the project's own
  `PageBatteryDbusSerialbattery.qml` "Temperatures" overview row, the first tile shows ambient
  labeled "Ambient" instead of the `/Dc/0/Temperature` average (which is redundant with the
  Temp 1–4 tiles beside it) — a UI-only substitution; `/Dc/0/Temperature` itself stays the
  cell-sensor aggregate for VRM/DVCC/stock pages. The tile's red-highlight-when-limiting logic
  must recognize ambient as a limitation source (limitation-string labels and the QML check must
  agree). Verify whether VRM logs `/AirTemperature` once live; if not and drift monitoring of
  ambient is wanted, route it through the contained VRM metric-map module.
- **Operator reset of latched trips**: a button in the shipped GUI-v2 settings page backed by a
  writable dbus path with a change callback — the same proven mechanism as the existing
  `/Settings/ResetSocTo` control. The reset emits an Event so it is logged and auditable.
- **Per-pack SoC reset timing**: keep current behavior (reset when the bank enters
  FloatTransition). May be revisited later.
- **Project name**: `dbus-battery-bank` (confirmed).

## Open design questions

- (none currently)
