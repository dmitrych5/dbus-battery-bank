"""Bank-level protections that latch CCL and DCL to zero until an operator reset.

Latched trips survive restarts (the outer loop persists ProtectionState), so a crash can never
silently clear a safety response. Diagnostics keep being produced while tripped, so the
operator can watch the condition on VRM before resetting.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Sequence

from battery_bank.config import ProtectionConfig, PtcProtectionConfig
from battery_bank.core.kalman import KalmanFilterState, kalman_step
from battery_bank.core.stats import mean
from battery_bank.core.values import BatterySnapshot

KALMAN_MEASUREMENT_VARIANCE = 0.005
THERMAL_FILTER_WARMUP_UPDATES = 10
"""The rate estimate is too noisy at first; inertia correction stays off until this many
temperature samples were absorbed."""
PTC_AUX_MISSING_ALARM_SECONDS = 600.0
"""A configured shunt Aux input reports with every frame, so a prolonged absence of the aux
voltage while the shunt data itself stays fresh means the PTC deviation check is silently
inoperative (e.g. the Aux input was reconfigured on the shunt) — an independent protection
layer must not disappear quietly, so this raises an alarm instead of just skipping."""


class TripKind(Enum):
    TEMPERATURE_SPREAD = "temperature spread across the bank too high"
    PTC_DEVIATION = "PTC aux voltage deviates from the expected value"


@dataclass(frozen=True)
class ThermalInertiaState:
    kalman: KalmanFilterState = KalmanFilterState()
    updates_count: int = 0
    last_sample_at: float | None = None


@dataclass(frozen=True)
class ProtectionState:
    tripped: frozenset[TripKind] = frozenset()
    thermal: ThermalInertiaState = ThermalInertiaState()
    aux_absent_since: float | None = None
    """When the PTC aux voltage went missing from otherwise-fresh shunt data; None while
    present (or while its absence is already explained by shunt staleness)."""
    aux_missing_alarm: bool = False


@dataclass(frozen=True)
class PtcDiagnostics:
    corrected_temperature_celsius: float
    expected_aux_voltage_volts: float | None
    deviation_percent: float | None
    """Expected voltage and deviation are None while no aux voltage reading is available."""


@dataclass(frozen=True)
class ProtectionsResult:
    state: ProtectionState
    zero_limits_required: bool
    newly_tripped: tuple[TripKind, ...]
    temperature_spread_celsius: float | None
    ptc: PtcDiagnostics | None


THERMAL_RESTORE_MAX_AGE_SECONDS = 6 * 3600.0
"""Beyond this age a persisted thermal state says nothing useful; start cold. Must stay
comfortably above the main loop's thermal save cadence, since the persisted snapshot is up to
one cadence old even before any downtime is added."""
RESTORED_VALUE_VARIANCE = 1.0
RESTORED_RATE_VARIANCE = 1e-8
"""Covariance priors after a restart (about ±1 C on the value, ±1e-4 C/s on the rate):
uncertain enough to re-converge within minutes of samples, certain enough that the persisted
rate keeps the inertia correction meaningful immediately."""


def reset_trips(state: ProtectionState) -> ProtectionState:
    """Operator-initiated reset; the only way a latched trip clears."""
    return replace(state, tripped=frozenset())


def restored_thermal_state(
    value_estimate: float,
    rate_estimate: float,
    updates_count: int,
    age_seconds: float,
    now_monotonic: float,
) -> ThermalInertiaState:
    """Reconstructs the thermal filter from a persisted snapshot taken age_seconds ago.

    The filter's own predict step is the restore operator for the value (value plus rate times
    the gap). The covariance is deliberately reset to the restore priors instead of being
    Q-propagated across the gap: the tiny process variance tuned for continuous smoothing
    would claim near-certainty about a rate that may well have changed while the service was
    down, making the filter unlearn a wrong rate far too slowly. The warmup counter is
    preserved so the inertia correction is active immediately after the restart."""
    age_seconds = max(0.0, age_seconds)
    if age_seconds > THERMAL_RESTORE_MAX_AGE_SECONDS:
        return ThermalInertiaState()
    kalman = KalmanFilterState(
        value_estimate=value_estimate + rate_estimate * age_seconds,
        rate_estimate=rate_estimate,
        last_time=now_monotonic,
        p00=RESTORED_VALUE_VARIANCE,
        p01=0.0,
        p10=0.0,
        p11=RESTORED_RATE_VARIANCE,
    )
    return ThermalInertiaState(kalman=kalman, updates_count=updates_count, last_sample_at=None)


def step_protections(
    config: ProtectionConfig,
    state: ProtectionState,
    packs: Sequence[BatterySnapshot],
    aux_voltage_volts: float | None,
    shunt_fresh: bool,
    now_monotonic: float,
) -> ProtectionsResult:
    tripped = set(state.tripped)
    newly_tripped: list[TripKind] = []
    aux_absent_since, aux_missing_alarm = _track_aux_presence(config, state, aux_voltage_volts, shunt_fresh, now_monotonic)

    temperature_spread = _temperature_spread_celsius(packs)
    if (
        config.max_temperature_spread_celsius is not None
        and temperature_spread is not None
        and temperature_spread > config.max_temperature_spread_celsius
        and TripKind.TEMPERATURE_SPREAD not in tripped
    ):
        tripped.add(TripKind.TEMPERATURE_SPREAD)
        newly_tripped.append(TripKind.TEMPERATURE_SPREAD)

    thermal = state.thermal
    ptc_diagnostics = None
    if config.ptc is not None and packs:
        bank_temperature = mean(mean(pack.cell_temperatures_celsius) for pack in packs)
        thermal, corrected_temperature = _correct_thermal_inertia(config.ptc, thermal, bank_temperature, now_monotonic)
        ptc_diagnostics = _check_ptc_deviation(config.ptc, corrected_temperature, aux_voltage_volts)
        if (
            ptc_diagnostics.deviation_percent is not None
            and ptc_diagnostics.deviation_percent > config.ptc.max_deviation_percent
            and TripKind.PTC_DEVIATION not in tripped
        ):
            tripped.add(TripKind.PTC_DEVIATION)
            newly_tripped.append(TripKind.PTC_DEVIATION)

    return ProtectionsResult(
        state=ProtectionState(
            tripped=frozenset(tripped), thermal=thermal, aux_absent_since=aux_absent_since, aux_missing_alarm=aux_missing_alarm
        ),
        zero_limits_required=bool(tripped),
        newly_tripped=tuple(newly_tripped),
        temperature_spread_celsius=temperature_spread,
        ptc=ptc_diagnostics,
    )


def _track_aux_presence(
    config: ProtectionConfig,
    state: ProtectionState,
    aux_voltage_volts: float | None,
    shunt_fresh: bool,
    now_monotonic: float,
) -> tuple[float | None, bool]:
    """Watches for the PTC aux voltage silently disappearing from fresh shunt data. Returns the
    new (aux_absent_since, aux_missing_alarm). Shunt staleness pauses the tracking: it already
    alarms on its own and says nothing about the Aux input."""
    if config.ptc is None or aux_voltage_volts is not None:
        return None, False
    if not shunt_fresh:
        return state.aux_absent_since, state.aux_missing_alarm
    absent_since = state.aux_absent_since if state.aux_absent_since is not None else now_monotonic
    return absent_since, now_monotonic - absent_since > PTC_AUX_MISSING_ALARM_SECONDS


def _temperature_spread_celsius(packs: Sequence[BatterySnapshot]) -> float | None:
    all_temperatures = [temperature for pack in packs for temperature in pack.cell_temperatures_celsius]
    if not all_temperatures:
        return None
    return max(all_temperatures) - min(all_temperatures)


def _correct_thermal_inertia(
    ptc_config: PtcProtectionConfig,
    thermal: ThermalInertiaState,
    measured_temperature: float,
    now_monotonic: float,
) -> tuple[ThermalInertiaState, float]:
    """Estimates the actual current temperature ahead of the slow sensors: the smoothed reading
    plus its rate of change times the sensor time constant."""
    has_new_sample = thermal.last_sample_at is None or now_monotonic - thermal.last_sample_at >= ptc_config.temperature_sample_interval_seconds
    kalman = kalman_step(
        thermal.kalman,
        KALMAN_MEASUREMENT_VARIANCE,
        ptc_config.temperature_filter_process_variance,
        now_monotonic,
        measured_temperature,
        has_new_sample,
    )
    new_thermal = ThermalInertiaState(
        kalman=kalman,
        updates_count=thermal.updates_count + (1 if has_new_sample else 0),
        last_sample_at=now_monotonic if has_new_sample else thermal.last_sample_at,
    )
    if new_thermal.updates_count < THERMAL_FILTER_WARMUP_UPDATES:
        return new_thermal, kalman.value_estimate
    corrected = kalman.value_estimate + ptc_config.temperature_sensor_time_constant_minutes * 60.0 * kalman.rate_estimate
    return new_thermal, corrected


def _check_ptc_deviation(
    ptc_config: PtcProtectionConfig,
    corrected_temperature: float,
    aux_voltage_volts: float | None,
) -> PtcDiagnostics:
    if aux_voltage_volts is None:
        return PtcDiagnostics(corrected_temperature, expected_aux_voltage_volts=None, deviation_percent=None)
    expected = ptc_config.expected_aux_voltage_by_temperature.value_at(corrected_temperature)
    deviation_percent = 100.0 * abs(aux_voltage_volts - expected) / expected
    return PtcDiagnostics(corrected_temperature, expected, deviation_percent)
