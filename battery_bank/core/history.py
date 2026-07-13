"""Lifetime battery history: a pure accumulator fed once per control step from the bank
decision and the snapshots. It only records — nothing in it feeds back into control.

Discharge bookkeeping is cycle-based, in the BMV tradition: one cycle runs from full charge to
full charge (the decision's FloatTransition entry). The running depth of the current cycle is
the largest consumed-Ah magnitude seen since the last full charge; at the full-charge moment it
is finalized as the "last discharge" and folded into the average, and the lifetime deepest
record tracks it continuously. Depths are stored as positive magnitudes; the publishing layer
negates them per the Victron convention.

Charged/discharged energy comes from the shunt's own lifetime counters (it integrates
internally at high rate and keeps counting while this service is down), published relative to
a baseline that an operator clear moves up to the current totals.

Alarm counters count rising edges of the aggregated alarms; an edge tracker of None means "not
observed yet" (fresh start or restart), so an alarm already active at startup is adopted
without counting it again.
"""

from dataclasses import dataclass, fields, replace
from typing import Sequence

from battery_bank.core.bank import BankDecision
from battery_bank.core.values import AlarmSeverity, BatterySnapshot, ShuntSnapshot


@dataclass(frozen=True)
class HistoryValues:
    """The operator-visible accumulated history; persisted field-for-field."""

    minimum_voltage_volts: float | None = None
    maximum_voltage_volts: float | None = None
    minimum_cell_voltage_volts: float | None = None
    maximum_cell_voltage_volts: float | None = None
    minimum_temperature_celsius: float | None = None
    maximum_temperature_celsius: float | None = None
    low_voltage_alarm_count: int = 0
    high_voltage_alarm_count: int = 0
    deepest_discharge_ah: float | None = None
    last_discharge_ah: float | None = None
    """Depth of the last completed cycle; the running cycle shows live on /ConsumedAmphours."""
    cycle_discharge_ah: float = 0.0
    """Largest consumed-Ah magnitude since the last full charge — the running cycle depth."""
    discharge_cycle_count: int = 0
    discharge_cycle_ah_total: float = 0.0
    charged_energy_kwh: float | None = None
    discharged_energy_kwh: float | None = None
    """Shunt lifetime total minus the clear baseline; None until totals have been seen."""
    charged_energy_baseline_kwh: float = 0.0
    discharged_energy_baseline_kwh: float = 0.0
    last_full_charge_at_wall_seconds: float | None = None
    """Wall clock, not monotonic: the value must stay meaningful across restarts."""

    def average_discharge_ah(self) -> float | None:
        if self.discharge_cycle_count == 0:
            return None
        return self.discharge_cycle_ah_total / self.discharge_cycle_count


HISTORY_FIELD_NAMES = tuple(field.name for field in fields(HistoryValues))


@dataclass(frozen=True)
class HistoryState:
    values: HistoryValues = HistoryValues()
    low_voltage_alarm_active: bool | None = None
    high_voltage_alarm_active: bool | None = None


def clear_history(state: HistoryState) -> HistoryState:
    """Resets everything (the GUI's Clear button writes 1); cleared values re-accumulate from
    the very next step. The shunt's lifetime energy counters cannot be reset from here, so
    clearing moves their baselines up to the current totals instead."""
    values = state.values
    return replace(
        state,
        values=HistoryValues(
            charged_energy_baseline_kwh=values.charged_energy_baseline_kwh + (values.charged_energy_kwh or 0.0),
            discharged_energy_baseline_kwh=values.discharged_energy_baseline_kwh + (values.discharged_energy_kwh or 0.0),
        ),
    )


def step_history(
    state: HistoryState,
    decision: BankDecision,
    packs: Sequence[BatterySnapshot],
    shunt: ShuntSnapshot | None,
    now_wall_seconds: float,
) -> HistoryState:
    values = _track_extremes(state.values, decision, packs)
    values, low_active = _count_alarm_edge(
        values, "low_voltage_alarm_count", state.low_voltage_alarm_active, decision.alarms.low_voltage, decision.alarms.low_cell_voltage
    )
    values, high_active = _count_alarm_edge(
        values, "high_voltage_alarm_count", state.high_voltage_alarm_active, decision.alarms.high_voltage, decision.alarms.high_cell_voltage
    )
    values = _track_discharge_depth(values, decision)
    if shunt is not None and decision.shunt_fresh:
        values = _track_energy(values, shunt)
    if decision.entered_float_transition:
        values = _complete_charge_cycle(values, now_wall_seconds)
    return HistoryState(
        values=values,
        low_voltage_alarm_active=low_active,
        high_voltage_alarm_active=high_active,
    )


def _track_extremes(values: HistoryValues, decision: BankDecision, packs: Sequence[BatterySnapshot]) -> HistoryValues:
    updates: dict[str, float] = {}
    if decision.voltage_volts is not None:
        updates["minimum_voltage_volts"] = _lower(values.minimum_voltage_volts, decision.voltage_volts)
        updates["maximum_voltage_volts"] = _higher(values.maximum_voltage_volts, decision.voltage_volts)
    if packs:
        updates["minimum_cell_voltage_volts"] = _lower(values.minimum_cell_voltage_volts, min(pack.min_cell_voltage_volts() for pack in packs))
        updates["maximum_cell_voltage_volts"] = _higher(values.maximum_cell_voltage_volts, max(pack.max_cell_voltage_volts() for pack in packs))
        updates["minimum_temperature_celsius"] = _lower(
            values.minimum_temperature_celsius, min(min(pack.cell_temperatures_celsius) for pack in packs)
        )
        updates["maximum_temperature_celsius"] = _higher(
            values.maximum_temperature_celsius, max(max(pack.cell_temperatures_celsius) for pack in packs)
        )
    return replace(values, **updates) if updates else values


def _count_alarm_edge(
    values: HistoryValues, count_field: str, previously_active: bool | None, *severities: AlarmSeverity
) -> tuple[HistoryValues, bool]:
    active = any(severity > AlarmSeverity.OK for severity in severities)
    if active and previously_active is False:
        values = replace(values, **{count_field: getattr(values, count_field) + 1})
    return values, active


def _track_discharge_depth(values: HistoryValues, decision: BankDecision) -> HistoryValues:
    """Only shunt-fresh consumed Ah feeds the depth records: the BMS fallback counts from a
    different zero, and a momentary source switch must not fake a deeper discharge. (With no
    shunt configured, shunt_fresh is always True and the BMS values are used consistently.)"""
    if decision.consumed_ah is None or not decision.shunt_fresh:
        return values
    depth_ah = max(0.0, -decision.consumed_ah)
    return replace(
        values,
        cycle_discharge_ah=max(values.cycle_discharge_ah, depth_ah),
        deepest_discharge_ah=_higher(values.deepest_discharge_ah, depth_ah),
    )


def _track_energy(values: HistoryValues, shunt: ShuntSnapshot) -> HistoryValues:
    updates: dict[str, float] = {}
    for direction, total in (("charged", shunt.charged_energy_total_kwh), ("discharged", shunt.discharged_energy_total_kwh)):
        if total is None:
            continue
        baseline = getattr(values, f"{direction}_energy_baseline_kwh")
        if total < baseline:
            # The shunt's counters restarted (device replaced or reset): treat it as a new
            # meter rather than publishing a negative energy.
            baseline = 0.0
            updates[f"{direction}_energy_baseline_kwh"] = baseline
        updates[f"{direction}_energy_kwh"] = total - baseline
    return replace(values, **updates) if updates else values


def _complete_charge_cycle(values: HistoryValues, now_wall_seconds: float) -> HistoryValues:
    """The bank reached full charge: stamp it and finalize the running discharge cycle."""
    updates: dict[str, object] = {"last_full_charge_at_wall_seconds": now_wall_seconds, "cycle_discharge_ah": 0.0}
    if values.cycle_discharge_ah > 0:
        updates["last_discharge_ah"] = values.cycle_discharge_ah
        updates["discharge_cycle_count"] = values.discharge_cycle_count + 1
        updates["discharge_cycle_ah_total"] = values.discharge_cycle_ah_total + values.cycle_discharge_ah
    return replace(values, **updates)


def _lower(current: float | None, sample: float) -> float:
    return sample if current is None or sample < current else current


def _higher(current: float | None, sample: float) -> float:
    return sample if current is None or sample > current else current
