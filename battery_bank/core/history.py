"""Lifetime battery history: a pure accumulator fed once per control step from the bank
decision and pack snapshots. It only records — nothing in it feeds back into control.

Discharge bookkeeping is cycle-based, in the BMV tradition: one cycle runs from full charge to
full charge (the decision's FloatTransition entry). The running depth of the current cycle is
the largest consumed-Ah magnitude seen since the last full charge; at the full-charge moment it
is finalized as the "last discharge" and folded into the average, and the lifetime deepest
record tracks it continuously. Depths are stored as positive magnitudes; the publishing layer
negates them per the Victron convention.

Alarm counters count rising edges of the aggregated alarms; an edge tracker of None means "not
observed yet" (fresh start or restart), so an alarm already active at startup is adopted
without counting it again.
"""

from dataclasses import dataclass, fields, replace
from typing import Sequence

from battery_bank.core.bank import BankDecision
from battery_bank.core.values import AlarmSeverity, BatterySnapshot

ENERGY_INTEGRATION_MAX_GAP_SECONDS = 30.0
"""Steps further apart than this (stalled cycles, restarts) contribute no energy: the power in
between is unknown, and guessing across a long gap would corrupt the counters more than the
skipped interval loses. Sized to the staleness budget, within which data still counts as live."""


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
    charged_energy_kwh: float = 0.0
    discharged_energy_kwh: float = 0.0
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
    last_step_at_monotonic: float | None = None


_CLEAR_CATEGORY_FIELDS: dict[int, tuple[str, ...]] = {
    2: ("deepest_discharge_ah", "last_discharge_ah", "cycle_discharge_ah", "discharge_cycle_count", "discharge_cycle_ah_total"),
    3: ("minimum_voltage_volts", "maximum_voltage_volts", "minimum_cell_voltage_volts", "maximum_cell_voltage_volts"),
    4: ("last_full_charge_at_wall_seconds",),
    5: ("low_voltage_alarm_count", "high_voltage_alarm_count"),
    6: ("minimum_temperature_celsius", "maximum_temperature_celsius"),
    7: ("charged_energy_kwh", "discharged_energy_kwh"),
}
CLEAR_EVERYTHING = 1
"""/History/Clear values follow the old driver: 1 clears everything, 2 capacity, 3 voltage,
4 time, 5 alarms, 6 temperature, 7 energy. The GUI's Clear button writes 1; the categories
remain reachable via dbus for selective resets."""


def clear_history(state: HistoryState, category: int) -> HistoryState:
    """Resets one category (or everything) to defaults; unknown categories clear nothing.
    Cleared values re-accumulate from the very next step."""
    if category == CLEAR_EVERYTHING:
        return replace(state, values=HistoryValues())
    field_names = _CLEAR_CATEGORY_FIELDS.get(category)
    if field_names is None:
        return state
    defaults = HistoryValues()
    return replace(state, values=replace(state.values, **{name: getattr(defaults, name) for name in field_names}))


def step_history(
    state: HistoryState,
    decision: BankDecision,
    packs: Sequence[BatterySnapshot],
    now_monotonic: float,
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
    values = _integrate_energy(values, decision, state.last_step_at_monotonic, now_monotonic)
    if decision.entered_float_transition:
        values = _complete_charge_cycle(values, now_wall_seconds)
    return HistoryState(
        values=values,
        low_voltage_alarm_active=low_active,
        high_voltage_alarm_active=high_active,
        last_step_at_monotonic=now_monotonic,
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


def _integrate_energy(values: HistoryValues, decision: BankDecision, last_step_at: float | None, now_monotonic: float) -> HistoryValues:
    if decision.power_watts is None or last_step_at is None:
        return values
    elapsed = now_monotonic - last_step_at
    if not 0.0 < elapsed <= ENERGY_INTEGRATION_MAX_GAP_SECONDS:
        return values
    energy_kwh = decision.power_watts * elapsed / 3_600_000
    if energy_kwh >= 0:
        return replace(values, charged_energy_kwh=values.charged_energy_kwh + energy_kwh)
    return replace(values, discharged_energy_kwh=values.discharged_energy_kwh - energy_kwh)


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
