"""The control core's entry point: one pure step from snapshots to a bank decision.

step_bank() composes staleness policy, latched protections, the charge stage machine, and the
current limit calculation. The outer loop only feeds it inputs, persists the returned state,
publishes the decision, and logs/alarms the events — all policy lives here, testable without
hardware, D-Bus, or real time.

Staleness policy: the bank controls only on a complete, fresh picture. If any configured pack
is stale or missing, the charge stage and limit states freeze, published limits drop to zero,
and the cable alarm raises. A stale shunt zeroes limits too (it carries the PTC protection
input and the authoritative current), while SoC falls back to the BMS values.

Zeroing the limits tells the inverter to stop, which can black out an off-grid house — so the
staleness thresholds are sized to tolerate multiple consecutive failed polls, and startup gets
special treatment: until the first complete picture arrives, the bank is not ready (nothing
published, no alarms, no errors), because data sources warming up is normal, not a fault. Only
when STARTUP_GRACE_SECONDS pass without a complete picture does the incompleteness fail loud.
"""

from dataclasses import dataclass
from enum import Enum
from statistics import mean
from typing import Sequence

from battery_bank.config import Config
from battery_bank.core.charge_stage import ChargeStage, ChargeStageState, step_charge_stage
from battery_bank.core.current_limits import (
    BankCurrentLimit,
    CurrentDirection,
    CurrentLimitState,
    compute_bank_current_limit,
)
from battery_bank.core.protections import ProtectionState, ProtectionsResult, step_protections
from battery_bank.core.values import AlarmSeverity, BatterySnapshot, PackAlarms, ShuntSnapshot

ALARM_CATEGORIES = tuple(PackAlarms.__dataclass_fields__)

STARTUP_GRACE_SECONDS = 60.0


class EventSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Event:
    severity: EventSeverity
    message: str


class SocSource(Enum):
    SHUNT = "shunt"
    BMS = "bms"


@dataclass(frozen=True)
class StalenessTracking:
    """Previous staleness picture, kept to emit events only on changes."""

    fresh_pack_count: int = -1
    shunt_fresh: bool = True


@dataclass(frozen=True)
class ControlState:
    charge_stage: ChargeStageState = ChargeStageState()
    charge_limit: CurrentLimitState = CurrentLimitState()
    discharge_limit: CurrentLimitState = CurrentLimitState()
    protections: ProtectionState = ProtectionState()
    staleness: StalenessTracking = StalenessTracking()
    startup_complete: bool = False
    """Set once the first complete fresh picture arrived; the bank publishes nothing before."""
    first_step_at: float | None = None


@dataclass(frozen=True)
class BankInputs:
    packs: tuple[BatterySnapshot, ...]
    """The latest snapshot of each pack seen so far; packs never seen are simply absent."""
    shunt: ShuntSnapshot | None


@dataclass(frozen=True)
class BankDecision:
    ready: bool
    """False until the first complete picture arrives after startup; the publishing layer
    keeps the D-Bus services unregistered (or their control values unpublished) while False,
    so a restarting service never momentarily commands the inverter to stop."""
    cvl_volts: float | None
    """None until the bank controlled at least once; the charger offset is added at publishing."""
    ccl_amps: float
    dcl_amps: float
    allow_to_charge: bool
    allow_to_discharge: bool
    charge_stage: ChargeStage

    voltage_volts: float | None
    current_amps: float | None
    power_watts: float | None
    soc_percent: float | None
    soc_source: SocSource
    consumed_ah: float | None
    """Negative by the Victron convention; None until a source is available."""

    alarms: PackAlarms
    cable_alarm: AlarmSeverity
    all_packs_fresh: bool
    fresh_pack_count: int
    shunt_fresh: bool

    request_soc_reset_pack_ids: tuple[str, ...]
    charge_limit_detail: BankCurrentLimit | None
    discharge_limit_detail: BankCurrentLimit | None
    protections: ProtectionsResult


def step_bank(config: Config, state: ControlState, inputs: BankInputs, now_monotonic: float) -> tuple[ControlState, BankDecision, tuple[Event, ...]]:
    events: list[Event] = []
    expected_pack_count = sum(len(port.pack_addresses) for port in config.battery_ports)
    first_step_at = state.first_step_at if state.first_step_at is not None else now_monotonic

    fresh_packs = tuple(pack for pack in inputs.packs if now_monotonic - pack.taken_at_monotonic <= config.staleness.pack_data_max_age_seconds)
    all_packs_fresh = len(fresh_packs) == expected_pack_count
    shunt_configured = config.shunt_port is not None
    shunt_fresh = (
        not shunt_configured
        or (inputs.shunt is not None and now_monotonic - inputs.shunt.taken_at_monotonic <= config.staleness.shunt_data_max_age_seconds)
    )

    picture_complete = all_packs_fresh and shunt_fresh
    startup_complete = state.startup_complete or picture_complete
    in_startup_grace = not startup_complete and now_monotonic - first_step_at <= STARTUP_GRACE_SECONDS
    if picture_complete and not state.startup_complete:
        events.append(Event(EventSeverity.INFO, "All data sources reporting; bank control active"))

    if in_startup_grace:
        # Data sources warming up after a start is normal operation: publish nothing and keep
        # quiet instead of alarming or commanding the inverter to stop.
        staleness_tracking = state.staleness
    else:
        events += _staleness_edge_events(state.staleness, len(fresh_packs), expected_pack_count, shunt_fresh, shunt_configured)
        staleness_tracking = StalenessTracking(fresh_pack_count=len(fresh_packs), shunt_fresh=shunt_fresh)

    aux_voltage = inputs.shunt.aux_voltage_volts if shunt_fresh and inputs.shunt is not None else None
    protections = step_protections(config.protection, state.protections, fresh_packs, aux_voltage, now_monotonic)
    for trip in protections.newly_tripped:
        events.append(Event(EventSeverity.ERROR, f"Protection tripped, limits latched to zero until operator reset: {trip.value}"))

    request_soc_reset_pack_ids: tuple[str, ...] = ()
    if all_packs_fresh:
        stage_result = step_charge_stage(
            config.cell_voltage, config.charge_stage, config.cvl_controller, config.cells_per_pack, fresh_packs, state.charge_stage, now_monotonic
        )
        charge_stage_state = stage_result.state
        cvl_volts = stage_result.cvl_volts
        stage = stage_result.stage
        if stage is not state.charge_stage.stage:
            events.append(Event(EventSeverity.INFO, f"Charge stage: {state.charge_stage.stage.value} -> {stage.value}"))
        if stage_result.entered_float_transition and config.auto_reset_soc_on_float_transition:
            request_soc_reset_pack_ids = tuple(pack.identity.unique_id for pack in fresh_packs)

        charge_limit = compute_bank_current_limit(
            config.charge_limit, config.limit_update_policy, CurrentDirection.CHARGE, fresh_packs, state.charge_limit, now_monotonic
        )
        discharge_limit = compute_bank_current_limit(
            config.discharge_limit, config.limit_update_policy, CurrentDirection.DISCHARGE, fresh_packs, state.discharge_limit, now_monotonic
        )
    else:
        # Freeze control on an incomplete picture; limits below are forced to zero anyway.
        charge_stage_state = state.charge_stage
        cvl_volts = state.charge_stage.cvl_volts
        stage = state.charge_stage.stage
        charge_limit = None
        discharge_limit = None

    zero_limits = not all_packs_fresh or not shunt_fresh or protections.zero_limits_required
    ccl_amps = 0.0 if zero_limits or charge_limit is None else charge_limit.published_amps
    dcl_amps = 0.0 if zero_limits or discharge_limit is None else discharge_limit.published_amps

    new_state = ControlState(
        charge_stage=charge_stage_state,
        charge_limit=charge_limit.state if charge_limit is not None else state.charge_limit,
        discharge_limit=discharge_limit.state if discharge_limit is not None else state.discharge_limit,
        protections=protections.state,
        staleness=staleness_tracking,
        startup_complete=startup_complete,
        first_step_at=first_step_at,
    )
    decision = BankDecision(
        ready=startup_complete,
        cvl_volts=cvl_volts,
        ccl_amps=ccl_amps,
        dcl_amps=dcl_amps,
        allow_to_charge=ccl_amps > 0.0,
        allow_to_discharge=dcl_amps > 0.0,
        charge_stage=stage,
        **_measurements(inputs, fresh_packs, shunt_fresh),
        alarms=_aggregate_alarms(inputs.packs),
        cable_alarm=AlarmSeverity.OK if in_startup_grace else _cable_alarm(all_packs_fresh, shunt_configured, shunt_fresh),
        all_packs_fresh=all_packs_fresh,
        fresh_pack_count=len(fresh_packs),
        shunt_fresh=shunt_fresh,
        request_soc_reset_pack_ids=request_soc_reset_pack_ids,
        charge_limit_detail=charge_limit,
        discharge_limit_detail=discharge_limit,
        protections=protections,
    )
    return new_state, decision, tuple(events)


def _measurements(inputs: BankInputs, fresh_packs: Sequence[BatterySnapshot], shunt_fresh: bool) -> dict:
    """Bank voltage/current/SoC selection: the shunt is authoritative when fresh, the BMS
    values are the fallback."""
    voltage = mean(pack.voltage_volts for pack in fresh_packs) if fresh_packs else None
    use_shunt = shunt_fresh and inputs.shunt is not None
    if use_shunt:
        current = inputs.shunt.current_amps
        soc = inputs.shunt.soc_percent
        consumed_ah = inputs.shunt.consumed_ah
    else:
        current = sum(pack.current_amps for pack in fresh_packs) if fresh_packs else None
        soc = _capacity_weighted_soc(fresh_packs)
        consumed_ah = -(sum(pack.full_capacity_ah for pack in fresh_packs) - sum(pack.remaining_capacity_ah for pack in fresh_packs)) if fresh_packs else None
    return dict(
        voltage_volts=voltage,
        current_amps=current,
        power_watts=voltage * current if voltage is not None and current is not None else None,
        soc_percent=soc,
        soc_source=SocSource.SHUNT if use_shunt else SocSource.BMS,
        consumed_ah=consumed_ah,
    )


def _capacity_weighted_soc(packs: Sequence[BatterySnapshot]) -> float | None:
    total_capacity = sum(pack.full_capacity_ah for pack in packs)
    if not packs or total_capacity == 0:
        return None
    return sum(pack.soc_percent * pack.full_capacity_ah for pack in packs) / total_capacity


def _aggregate_alarms(packs: Sequence[BatterySnapshot]) -> PackAlarms:
    """Worst severity per category across all known packs, including stale ones: a pack that
    went dark with an active alarm must not clear it."""
    severities = {
        category: max((getattr(pack.alarms, category) for pack in packs), default=AlarmSeverity.OK) for category in ALARM_CATEGORIES
    }
    return PackAlarms(**severities)


def _cable_alarm(all_packs_fresh: bool, shunt_configured: bool, shunt_fresh: bool) -> AlarmSeverity:
    if not all_packs_fresh:
        return AlarmSeverity.ALARM
    if shunt_configured and not shunt_fresh:
        return AlarmSeverity.WARNING
    return AlarmSeverity.OK


def _staleness_edge_events(
    previous: StalenessTracking, fresh_pack_count: int, expected_pack_count: int, shunt_fresh: bool, shunt_configured: bool
) -> list[Event]:
    events = []
    if fresh_pack_count != previous.fresh_pack_count:
        if fresh_pack_count < expected_pack_count:
            events.append(
                Event(
                    EventSeverity.ERROR,
                    f"Fresh data from {fresh_pack_count} of {expected_pack_count} packs; forcing zero current limits until all packs report",
                )
            )
        elif previous.fresh_pack_count != -1:
            events.append(Event(EventSeverity.INFO, "All packs reporting fresh data again"))
    if shunt_configured and shunt_fresh != previous.shunt_fresh:
        if not shunt_fresh:
            events.append(Event(EventSeverity.ERROR, "Shunt data stale; forcing zero current limits and falling back to BMS SoC"))
        else:
            events.append(Event(EventSeverity.INFO, "Shunt data fresh again"))
    return events
