"""Driver-computed battery history: a pure accumulator fed once per control step. It only
records — nothing in it feeds back into control.

The accumulator runs once per subject: once for the bank (behind the aggregate service's
"Driver-provided data" history section) and once per pack (the per-pack history only the
driver can compute, since the shunt sees the bank as a whole). A HistorySample carries the
per-step observation for one subject; bank_history_sample() and pack_history_sample() build
it from the bank decision and from one pack snapshot respectively.

Only what the shunt cannot provide is computed (and persisted) here: voltage and cell-voltage
extremes, temperature extremes, voltage alarm counts, and the bank's full-charge timestamp
(keyed to the FloatTransition decision). The rest of the aggregate's history page comes
straight from the shunt's lifetime counters (ShuntHistoryTotals), which the shunt accumulates
internally — even while this service is down — and which the operator resets in the shunt
itself, not here. The bank subject therefore skips voltage extremes (the shunt's H7/H8 own
those paths on the aggregate), while packs track their own.

Alarm counters count rising edges; an edge tracker of None means "not observed yet" (fresh
start or restart), so an alarm already active at startup is adopted without counting it again.
"""

from dataclasses import dataclass, fields, replace
from typing import Sequence

from battery_bank.core.bank import BankDecision
from battery_bank.core.values import AlarmSeverity, BatterySnapshot, PackAlarms


@dataclass(frozen=True)
class HistoryValues:
    """The driver-computed history of one subject; persisted field-for-field."""

    minimum_voltage_volts: float | None = None
    maximum_voltage_volts: float | None = None
    minimum_cell_voltage_volts: float | None = None
    maximum_cell_voltage_volts: float | None = None
    minimum_temperature_celsius: float | None = None
    maximum_temperature_celsius: float | None = None
    low_voltage_alarm_count: int = 0
    high_voltage_alarm_count: int = 0
    last_full_charge_at_wall_seconds: float | None = None
    """Wall clock, not monotonic: the value must stay meaningful across restarts."""


HISTORY_FIELD_NAMES = tuple(field.name for field in fields(HistoryValues))


@dataclass(frozen=True)
class HistoryState:
    values: HistoryValues = HistoryValues()
    low_voltage_alarm_active: bool | None = None
    high_voltage_alarm_active: bool | None = None


@dataclass(frozen=True)
class HistorySample:
    """One subject's observation for one step; None values leave their records untouched."""

    voltage_volts: float | None
    minimum_cell_voltage_volts: float | None
    maximum_cell_voltage_volts: float | None
    minimum_temperature_celsius: float | None
    maximum_temperature_celsius: float | None
    low_voltage_alarm: bool
    high_voltage_alarm: bool
    full_charge: bool


def bank_history_sample(decision: BankDecision, packs: Sequence[BatterySnapshot]) -> HistorySample:
    return HistorySample(
        voltage_volts=None,
        minimum_cell_voltage_volts=min(pack.min_cell_voltage_volts() for pack in packs) if packs else None,
        maximum_cell_voltage_volts=max(pack.max_cell_voltage_volts() for pack in packs) if packs else None,
        minimum_temperature_celsius=min(min(pack.cell_temperatures_celsius) for pack in packs) if packs else None,
        maximum_temperature_celsius=max(max(pack.cell_temperatures_celsius) for pack in packs) if packs else None,
        low_voltage_alarm=_voltage_alarm_active(decision.alarms, "low"),
        high_voltage_alarm=_voltage_alarm_active(decision.alarms, "high"),
        full_charge=decision.entered_float_transition,
    )


def pack_history_sample(pack: BatterySnapshot) -> HistorySample:
    """The bank's full-charge moment is deliberately not stamped per pack: the stage machine
    is bank-level, so a per-pack timestamp would only duplicate the aggregate's."""
    return HistorySample(
        voltage_volts=pack.voltage_volts,
        minimum_cell_voltage_volts=pack.min_cell_voltage_volts(),
        maximum_cell_voltage_volts=pack.max_cell_voltage_volts(),
        minimum_temperature_celsius=min(pack.cell_temperatures_celsius),
        maximum_temperature_celsius=max(pack.cell_temperatures_celsius),
        low_voltage_alarm=_voltage_alarm_active(pack.alarms, "low"),
        high_voltage_alarm=_voltage_alarm_active(pack.alarms, "high"),
        full_charge=False,
    )


def clear_history(state: HistoryState) -> HistoryState:
    """Resets one subject's driver-computed history (the GUI's Clear button); cleared values
    re-accumulate from the very next step. The shunt-provided history is not affected — it is
    reset from the shunt itself."""
    return replace(state, values=HistoryValues())


def step_history(state: HistoryState, sample: HistorySample, now_wall_seconds: float) -> HistoryState:
    values = _track_extremes(state.values, sample)
    values, low_active = _count_alarm_edge(values, "low_voltage_alarm_count", state.low_voltage_alarm_active, sample.low_voltage_alarm)
    values, high_active = _count_alarm_edge(values, "high_voltage_alarm_count", state.high_voltage_alarm_active, sample.high_voltage_alarm)
    if sample.full_charge:
        values = replace(values, last_full_charge_at_wall_seconds=now_wall_seconds)
    return HistoryState(
        values=values,
        low_voltage_alarm_active=low_active,
        high_voltage_alarm_active=high_active,
    )


def _track_extremes(values: HistoryValues, sample: HistorySample) -> HistoryValues:
    extreme_fields = (
        ("minimum_voltage_volts", sample.voltage_volts, _lower),
        ("maximum_voltage_volts", sample.voltage_volts, _higher),
        ("minimum_cell_voltage_volts", sample.minimum_cell_voltage_volts, _lower),
        ("maximum_cell_voltage_volts", sample.maximum_cell_voltage_volts, _higher),
        ("minimum_temperature_celsius", sample.minimum_temperature_celsius, _lower),
        ("maximum_temperature_celsius", sample.maximum_temperature_celsius, _higher),
    )
    updates = {name: extreme(getattr(values, name), sample_value) for name, sample_value, extreme in extreme_fields if sample_value is not None}
    return replace(values, **updates) if updates else values


def _voltage_alarm_active(alarms: PackAlarms, direction: str) -> bool:
    """Pack-level and cell-level flags share one counter, matching the GUI's single row."""
    return getattr(alarms, f"{direction}_voltage") > AlarmSeverity.OK or getattr(alarms, f"{direction}_cell_voltage") > AlarmSeverity.OK


def _count_alarm_edge(values: HistoryValues, count_field: str, previously_active: bool | None, active: bool) -> tuple[HistoryValues, bool]:
    if active and previously_active is False:
        values = replace(values, **{count_field: getattr(values, count_field) + 1})
    return values, active


def _lower(current: float | None, sample: float) -> float:
    return sample if current is None or sample < current else current


def _higher(current: float | None, sample: float) -> float:
    return sample if current is None or sample > current else current
